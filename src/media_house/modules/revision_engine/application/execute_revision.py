"""Carrying out an approved plan, and checking what it did.

Before anything changes: a person's edits in Resolve are read back (their work is the baseline), the
review is looked at again (an edit since the approval makes the plan stale: nothing runs), a
checkpoint is made and read back to prove it is usable, and the review confirms that exactly this
plan is approved for exactly this version. Then each approved change goes to the owner's
contract, in order; the first failure stops the rest. Afterwards the new version is bound, the
quality checks run on it and the result is compared with the report the plan was made from: a
change is "resolved" only when the finding it answered is gone, and a new finding is a regression.
"""

from dataclasses import dataclass, replace

from media_house.core.application.ports import Clock
from media_house.core.domain import json_codec
from media_house.modules.audio_mix_engine.application.contracts import MixCatalog
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.modules.quality_control.application.contracts import (
    AnalyzeQualityCommand,
    QualityAnalyzer,
    QualityCatalog,
    QualityReport,
    QualityResult,
)
from media_house.modules.resolve_adapter.application.contracts import ResolveCatalog
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    CheckpointCommand,
    ReviewCatalog,
    ReviewSessions,
    ReviewState,
    TimelineRef,
)
from media_house.modules.revision_engine.application.events import RevisionExecuted
from media_house.modules.revision_engine.application.executors import (
    ChangeOutcome,
    ExecutionContext,
    RevisionExecutor,
)
from media_house.modules.revision_engine.application.mix_state import current_overrides
from media_house.modules.revision_engine.application.plan_revisions import RevisionFailure
from media_house.modules.revision_engine.application.ports import ManualChangeSync, PreviewProvider
from media_house.modules.revision_engine.application.store import RevisionStore
from media_house.modules.revision_engine.application.views import issue_facts
from media_house.modules.revision_engine.domain.errors import NothingToRevise, PlanNotCurrent
from media_house.modules.revision_engine.domain.model import (
    ExecutionRecord,
    ProposedChange,
    RevisionPlan,
)
from media_house.modules.revision_engine.domain.plan import seal, validate_revision
from media_house.modules.revision_engine.domain.values import ChangeStatus, PlanStatus
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ExecuteRevisionCommand:
    review_session_id: str
    revision_plan_id: str
    actor: Actor
    #: Check the new version with quality control afterwards.
    revalidate: bool = True
    profile: str | None = None
    #: Go on although the checkpoint could not be verified (a person said so).
    allow_unverified_checkpoint: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    plan: RevisionPlan
    asset: MediaAssetDto
    new_timeline_version: int | None
    #: The quality report of the revised version, when it was checked.
    quality: QualityResult | None


class ExecuteRevisions:
    def __init__(
        self,
        library: MediaLibrary,
        sessions: ReviewSessions,
        reviews: ReviewCatalog,
        store: RevisionStore,
        executors: tuple[RevisionExecutor, ...],
        manual: ManualChangeSync,
        previews: PreviewProvider,
        analyzer: QualityAnalyzer,
        qualities: QualityCatalog,
        mixes: MixCatalog,
        resolve: ResolveCatalog,
        events: EventPublisher,
        clock: Clock,
    ) -> None:
        self._library = library
        self._sessions = sessions
        self._reviews = reviews
        self._store = store
        self._executors = executors
        self._manual = manual
        self._previews = previews
        self._analyzer = analyzer
        self._qualities = qualities
        self._mixes = mixes
        self._resolve = resolve
        self._events = events
        self._clock = clock

    def execute(
        self, command: ExecuteRevisionCommand, ctx: JobContext
    ) -> Result[ExecutionResult, RevisionFailure]:
        found = self._reviews.get(command.review_session_id)
        if isinstance(found, Err):
            return Err(NothingToRevise(str(found.error.user_message)))
        session = found.value
        stored = self._store.find(
            session.anchor_asset_id, command.revision_plan_id, session_id=session.session_id
        )
        if isinstance(stored, Err):
            return stored
        if stored.value is None:
            return Err(
                NothingToRevise(f"There is no plan {command.revision_plan_id} in this review.")
            )
        plan = stored.value[0]
        anchor = self._library.get(session.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor

        # 1. a person's work first: read it back, then look at the review again
        self._manual.read_back(session.anchor_asset_id, session.timeline.timeline_id, ctx)
        synced = self._sessions.sync_timeline(session.session_id)
        if isinstance(synced, Err):
            return Err(NothingToRevise(str(synced.error.user_message)))
        session = synced.value
        if plan.source_timeline_hash != session.timeline.content_hash:
            self._save(anchor.value, replace(plan, execution_status=PlanStatus.STALE))
            return Err(PlanNotCurrent(plan.source_timeline_version, session.timeline.version))
        approved = [c for c in plan.proposed_changes if c.status is ChangeStatus.APPROVED]
        if not approved:
            return Err(NothingToRevise("No proposal of this plan is approved."))

        # 2. a checkpoint that is proven usable, and the review's own permission to begin
        made = self._checkpoint(session.session_id, plan, command)
        if isinstance(made, Err):
            return made
        checkpoint_id = made.value
        begun = self._sessions.begin_revision(
            session.session_id, plan.revision_plan_id, actor=command.actor
        )
        if isinstance(begun, Err):
            return begun
        started = self._clock.now().isoformat()
        from_version = begun.value.timeline.version

        # 3. the changes, in order
        outcomes = self._run(plan, approved, ctx)
        by_id = {o.proposal_id: o for o in outcomes}
        changes = tuple(_with_outcome(c, by_id.get(c.proposal_id)) for c in plan.proposed_changes)
        done = [o for o in outcomes if o.status in (ChangeStatus.EXECUTED, ChangeStatus.HANDED_OFF)]
        failed = [o for o in outcomes if o.status is ChangeStatus.FAILED]
        if not done:
            self._sessions.revision_failed(
                session.session_id,
                plan.revision_plan_id,
                "; ".join(o.detail for o in failed) or "nothing could be carried out",
                actor=command.actor,
            )
            failed_plan = self._record(
                plan,
                changes,
                PlanStatus.FAILED,
                checkpoint_id,
                from_version,
                None,
                started,
                (),
                tuple(o.detail for o in failed),
            )
            return self._saved(anchor.value, failed_plan, None, None)

        # 4. what the timeline is now
        bound = self._sessions.record_revision_result(
            session.session_id, plan.revision_plan_id, actor=command.actor
        )
        if isinstance(bound, Err):
            return bound
        new_version = bound.value.timeline.version
        status = (
            PlanStatus.EXECUTED if not failed and len(done) == len(approved) else PlanStatus.PARTIAL
        )
        overrides = self._overrides(session.anchor_asset_id, session.timeline.timeline_id)
        record = self._record(
            plan,
            changes,
            status,
            checkpoint_id,
            from_version,
            new_version,
            started,
            overrides,
            tuple(o.detail for o in failed),
        )
        saved = self._save(anchor.value, record)
        if isinstance(saved, Err):
            return saved
        self._events.publish(
            RevisionExecuted(
                occurred_at=self._clock.now(),
                revision_plan_id=plan.revision_plan_id,
                review_session_id=session.session_id,
                timeline_version=from_version,
                status=status.value,
                new_timeline_version=new_version,
            )
        )
        quality: QualityResult | None = None
        final = record
        if command.revalidate:
            checked = self._revalidate(bound.value.session_id, record, command, ctx)
            if isinstance(checked, Err):
                return checked
            final, quality = checked.value
            saved = self._save(anchor.value, final)
            if isinstance(saved, Err):
                return saved
        return Ok(ExecutionResult(final, saved.value, new_version, quality))

    # --- steps -------------------------------------------------------------------------------
    def _checkpoint(
        self, session_id: str, plan: RevisionPlan, command: ExecuteRevisionCommand
    ) -> Result[str, RevisionFailure]:
        session = self._reviews.get(session_id)
        if isinstance(session, Err):
            return session
        anchor, timeline_id = session.value.anchor_asset_id, session.value.timeline.timeline_id
        mix = self._mixes.latest_result(anchor, timeline_id)
        mix_asset = mix.value[1].id if isinstance(mix, Ok) and mix.value is not None else ""
        binding = self._resolve.latest_binding(anchor, timeline_id)
        binding_asset = (
            binding.value[1].id if isinstance(binding, Ok) and binding.value is not None else ""
        )
        labels = self._overrides(anchor, timeline_id)
        made = self._sessions.create_checkpoint(
            CheckpointCommand(
                session_id=session_id,
                label=f"before {plan.revision_id}",
                reason=f"before carrying out plan {plan.revision_plan_id}",
                mix_result_asset_id=mix_asset,
                mix_overrides_hash=json_codec.digest(sorted(labels)),
                resolve_binding_asset_id=binding_asset,
            ),
            actor=command.actor,
        )
        if isinstance(made, Err):
            return made
        checkpoint = made.value.checkpoints[-1]
        if not checkpoint.verified and not command.allow_unverified_checkpoint:
            return Err(
                NothingToRevise(
                    "The checkpoint taken before this revision could not be verified, so nothing "
                    "was changed. Say explicitly to go ahead without a usable way back."
                )
            )
        return Ok(checkpoint.checkpoint_id)

    def _run(
        self, plan: RevisionPlan, approved: list[ProposedChange], ctx: JobContext
    ) -> list[ChangeOutcome]:
        """The approved changes in plan order; the first failure stops the ones after it."""
        context = ExecutionContext(plan, plan.anchor_asset_id, plan.timeline_id)
        outcomes: list[ChangeOutcome] = []
        stopped = ""
        index = 0
        while index < len(approved):
            ctx.raise_if_cancelled()
            change = approved[index]
            executor = next((e for e in self._executors if e.handles(change.kind)), None)
            if stopped:
                outcomes.append(ChangeOutcome(change.proposal_id, ChangeStatus.SKIPPED, stopped))
                index += 1
                continue
            if executor is None:
                outcomes.append(
                    ChangeOutcome(
                        change.proposal_id,
                        ChangeStatus.FAILED,
                        f"no executor for {change.kind.value}",
                    )
                )
                stopped = "an earlier change failed"
                index += 1
                continue
            group = [change]
            while index + len(group) < len(approved) and executor.handles(
                approved[index + len(group)].kind
            ):
                group.append(approved[index + len(group)])
            results = executor.execute(group, context, ctx)
            outcomes.extend(results)
            if any(o.status is ChangeStatus.FAILED for o in results):
                stopped = "an earlier change failed"
            index += len(group)
        return outcomes

    def _overrides(self, anchor_id: str, timeline_id: str) -> tuple[str, ...]:
        return current_overrides(self._mixes, anchor_id, timeline_id)

    def _record(
        self,
        plan: RevisionPlan,
        changes: tuple[ProposedChange, ...],
        status: PlanStatus,
        checkpoint_id: str,
        from_version: int,
        to_version: int | None,
        started: str,
        overrides: tuple[str, ...],
        errors: tuple[str, ...],
    ) -> RevisionPlan:
        return seal(
            replace(
                plan,
                proposed_changes=changes,
                execution_status=status,
                checkpoint_reference=checkpoint_id,
                target_timeline_version=to_version,
                new_revision_reference=f"{plan.revision_id}@v{to_version}" if to_version else None,
                execution=ExecutionRecord(
                    plan_id=plan.revision_plan_id,
                    started_at=started,
                    finished_at=self._clock.now().isoformat(),
                    checkpoint_id=checkpoint_id,
                    from_version=from_version,
                    to_version=to_version,
                    mix_overrides_after=overrides,
                    errors=errors,
                ),
            )
        )

    def _save(
        self, anchor: MediaAssetDto, plan: RevisionPlan
    ) -> Result[MediaAssetDto, RevisionFailure]:
        return self._store.save(anchor, plan)

    def _saved(
        self,
        anchor: MediaAssetDto,
        plan: RevisionPlan,
        version: int | None,
        quality: QualityResult | None,
    ) -> Result[ExecutionResult, RevisionFailure]:
        saved = self._save(anchor, plan)
        if isinstance(saved, Err):
            return saved
        return Ok(ExecutionResult(plan, saved.value, version, quality))

    # --- the check ---------------------------------------------------------------------------
    def _revalidate(
        self,
        session_id: str,
        plan: RevisionPlan,
        command: ExecuteRevisionCommand,
        ctx: JobContext,
    ) -> Result[tuple[RevisionPlan, QualityResult | None], RevisionFailure]:
        started = self._sessions.advance(
            session_id,
            ReviewState.REVALIDATION_PENDING,
            actor=command.actor,
            reason="the revision is checked",
        )
        if isinstance(started, Err):
            return started
        ref: TimelineRef = started.value.timeline
        preview = self._previews.preview_for(ref, ctx)
        result = self._analyzer.execute(
            AnalyzeQualityCommand(
                anchor_asset_id=plan.anchor_asset_id,
                timeline_id=plan.timeline_id,
                preview_asset_id=preview,
                preview_timeline_hash=ref.content_hash if preview else None,
                review_session_id=session_id,
                profile=command.profile,
            ),
            ctx,
        )
        if isinstance(result, Err):
            return Err(
                NothingToRevise(f"The revised timeline could not be checked: {result.error}")
            )
        before = self._report_by_id(
            plan.anchor_asset_id, plan.timeline_id, plan.source_quality_report_id
        )
        after = result.value.report
        if before is None:
            validation = validate_revision(
                plan,
                [],
                issue_facts(after),
                before_report_id="",
                after_report_id=after.quality_report_id,
            )
        else:
            validation = validate_revision(
                plan,
                issue_facts(before),
                issue_facts(after),
                before_report_id=before.quality_report_id,
                after_report_id=after.quality_report_id,
            )
        target = (
            ReviewState.REVIEW_AGAIN_REQUIRED
            if validation.regressions
            else ReviewState.REVIEW_READY
        )
        moved = self._sessions.advance(
            session_id, target, actor=command.actor, reason=validation.summary
        )
        if isinstance(moved, Err):
            return moved
        _log.info("Revision checked", plan=plan.revision_plan_id, summary=validation.summary)
        return Ok((seal(replace(plan, validation_results=validation)), result.value))

    def _report_by_id(
        self, anchor_id: str, timeline_id: str, report_id: str
    ) -> QualityReport | None:
        for asset in self._qualities.history(anchor_id, timeline_id):
            if asset.metadata.get("quality_report_id") == report_id:
                loaded = self._qualities.load(asset.id)
                return loaded.value if isinstance(loaded, Ok) else None
        return None


def _with_outcome(change: ProposedChange, outcome: ChangeOutcome | None) -> ProposedChange:
    if outcome is None:
        return change
    return replace(
        change,
        status=outcome.status,
        outcome=outcome.detail,
        changed_elements=outcome.changed_elements,
    )
