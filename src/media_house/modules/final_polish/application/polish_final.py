"""The final polish of one timeline: the last look at the finished presentation.

It runs after the creative work, on the exact version the review is at. It needs the quality report
of that version (and makes one if there is none, through quality control), reads what the audio mix,
the revisions and the review hold, looks at a few pictures of the final version when a preview is
given, and reports: what is still wrong, what to look at, and how ready the video is for a person's
final approval. It changes nothing: a fix goes through the revision engine, and the approval is a
person's.
"""

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from media_house.core.application.ports import Clock, MediaAnalyzer
from media_house.modules.audio_mix_engine.application.contracts import MixCatalog
from media_house.modules.final_polish.application.events import FinalPolishCompleted
from media_house.modules.final_polish.application.store import PolishStore
from media_house.modules.final_polish.application.views import (
    mix_facts,
    polish_timeline,
    quality_facts,
    review_facts,
    revision_facts,
)
from media_house.modules.final_polish.domain import facts as f
from media_house.modules.final_polish.domain.errors import FinalPolishError, NotPolishable
from media_house.modules.final_polish.domain.model import FinalPolishReport
from media_house.modules.final_polish.domain.report import assemble
from media_house.modules.final_polish.domain.values import EVIDENCE_OPERATION, Readiness
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.quality_control.application.contracts import (
    AnalyzeQualityCommand,
    QualityAnalyzer,
    QualityCatalog,
    QualityReport,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ArtifactKind,
    ReviewCatalog,
    ReviewSession,
    ReviewSessions,
    ReviewState,
    TimelineRef,
)
from media_house.modules.revision_engine.application.contracts import RevisionCatalog
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, ExternalSystemError, Ok, OperationCancelledError, Result
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_ACTOR = Actor.system("final_polish")
_MAX_FRAMES = 12
type PolishError = FinalPolishError | MediaError


@dataclass(frozen=True, slots=True)
class PolishCommand:
    #: The review to polish for; without one the latest build of ``anchor_asset_id`` is looked at.
    review_session_id: str | None = None
    anchor_asset_id: str | None = None
    timeline_id: str | None = None
    #: A rendered preview of this version; without one the pictures are not looked at.
    preview_asset_id: str | None = None
    #: The version the preview was rendered from; another version is refused.
    preview_timeline_hash: str | None = None
    reuse: bool = True


@dataclass(frozen=True, slots=True)
class PolishResult:
    report: FinalPolishReport
    asset: MediaAssetDto
    created: bool


class PolishFinal:
    def __init__(
        self,
        library: MediaLibrary,
        builds: BuiltTimelineCatalog,
        qualities: QualityCatalog,
        quality: QualityAnalyzer,
        mixes: MixCatalog,
        revisions: RevisionCatalog,
        sessions: ReviewSessions,
        reviews: ReviewCatalog,
        analyzer: MediaAnalyzer,
        store: PolishStore,
        events: EventPublisher,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._builds = builds
        self._qualities = qualities
        self._quality = quality
        self._mixes = mixes
        self._revisions = revisions
        self._sessions = sessions
        self._reviews = reviews
        self._analyzer = analyzer
        self._store = store
        self._events = events
        self._paths = paths
        self._clock = clock

    def execute(self, command: PolishCommand, ctx: JobContext) -> Result[PolishResult, PolishError]:
        session: ReviewSession | None = None
        anchor_id, timeline_id = command.anchor_asset_id, command.timeline_id
        if command.review_session_id is not None:
            synced = self._sessions.sync_timeline(command.review_session_id)
            if isinstance(synced, Err):
                return Err(NotPolishable(str(synced.error.user_message)))
            session = synced.value
            anchor_id, timeline_id = session.anchor_asset_id, session.timeline.timeline_id
        if anchor_id is None:
            return Err(NotPolishable("Say which review or which recording to polish."))
        anchor = self._library.get(anchor_id)
        if isinstance(anchor, Err):
            return anchor
        latest = self._builds.latest(anchor_id, timeline_id=timeline_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(NotPolishable("There is no built timeline to polish."))
        timeline, _build_asset = latest.value
        if session is not None and session.timeline.content_hash != timeline.build.build_hash:
            return Err(NotPolishable("The review is not at the current version of the timeline."))
        if (
            command.preview_timeline_hash is not None
            and command.preview_timeline_hash != timeline.build.build_hash
        ):
            return Err(
                NotPolishable(
                    "The preview was rendered from another version of the timeline than the one "
                    "to be polished; render the current version first."
                )
            )
        if session is not None and command.reuse and session.polish_report_id:
            held = self._store.latest(
                anchor_id,
                timeline_id=timeline.timeline_id,
                timeline_content_hash=timeline.build.build_hash,
            )
            if isinstance(held, Ok) and held.value is not None:
                report, asset = held.value
                if report.final_polish_report_id == session.polish_report_id:
                    return Ok(PolishResult(report, asset, created=False))
        begun = self._begin(session)
        if isinstance(begun, Err):
            return begun
        quality = self._quality_report(anchor_id, timeline, command, ctx)
        if isinstance(quality, Err):
            return quality
        return self._polish(command, anchor.value, timeline, quality.value, session, ctx)

    # --- steps -------------------------------------------------------------------------------
    def _begin(self, session: ReviewSession | None) -> Result[None, PolishError]:
        if session is None or session.state is ReviewState.FINAL_POLISH_PENDING:
            return Ok(None)
        moved = self._sessions.advance(
            session.session_id,
            ReviewState.FINAL_POLISH_PENDING,
            actor=_ACTOR,
            reason="final polish",
        )
        if isinstance(moved, Err):
            return Err(NotPolishable(str(moved.error.user_message)))
        return Ok(None)

    def _quality_report(
        self, anchor_id: str, timeline: BuiltTimeline, command: PolishCommand, ctx: JobContext
    ) -> Result[QualityReport, PolishError]:
        found = self._qualities.latest(
            anchor_id,
            timeline_id=timeline.timeline_id,
            timeline_content_hash=timeline.build.build_hash,
        )
        if isinstance(found, Err):
            return found
        if found.value is not None and (
            command.preview_asset_id is None
            or found.value[0].preview_asset_id == command.preview_asset_id
        ):
            return Ok(found.value[0])
        made = self._quality.execute(
            AnalyzeQualityCommand(
                anchor_asset_id=anchor_id,
                timeline_id=timeline.timeline_id,
                preview_asset_id=command.preview_asset_id,
                preview_timeline_hash=command.preview_timeline_hash,
            ),
            ctx,
        )
        if isinstance(made, Err):
            return Err(NotPolishable(f"Quality control could not check this version: {made.error}"))
        return Ok(made.value.report)

    def _polish(
        self,
        command: PolishCommand,
        anchor: MediaAssetDto,
        timeline: BuiltTimeline,
        quality: QualityReport,
        session: ReviewSession | None,
        ctx: JobContext,
    ) -> Result[PolishResult, PolishError]:
        view = polish_timeline(timeline)
        warnings: list[str] = []
        mixed = self._mixes.latest_result(anchor.id, timeline.timeline_id)
        mix = (
            mix_facts(mixed.value[0]) if isinstance(mixed, Ok) and mixed.value is not None else None
        )
        plans = self._revisions.plans(anchor.id, session_id=session.session_id if session else None)
        revisions = revision_facts([p for p, _ in plans.value] if isinstance(plans, Ok) else [])
        review = review_facts(session) if session is not None else None
        inputs = (
            ("timeline", f"{view.timeline_id}@{view.version}:{view.content_hash}"),
            ("quality_report", quality.quality_report_id),
            ("preview", command.preview_asset_id or ""),
            (
                "mix",
                f"{mixed.value[0].mix_job_id}" if isinstance(mixed, Ok) and mixed.value else "",
            ),
            ("revisions", ",".join(revisions.executed_plans)),
            ("locks", ",".join(review.locked_sections) if review else ""),
            ("drift", ",".join(review.drifted_locks) if review else ""),
            ("polish", "1"),
        )
        frames: tuple[f.FrameFacts, ...] = ()
        if command.preview_asset_id:
            taken = self._frames(command.preview_asset_id, view, warnings, ctx)
            frames = taken
        report = assemble(
            timeline=view,
            quality=quality_facts(quality),
            mix=mix,
            frames=frames,
            revisions=revisions,
            review=review,
            preview_asset_id=command.preview_asset_id or "",
            created_at=self._clock.now().isoformat(),
            inputs=inputs,
            warnings=tuple(warnings),
        )
        if command.reuse:
            stored = self._store.find(anchor.id, report.final_polish_report_id)
            if isinstance(stored, Err):
                return stored
            if stored.value is not None:
                self._report_to_review(session, stored.value[0], quality)
                return Ok(PolishResult(stored.value[0], stored.value[1], created=False))
        saved = self._store.save(anchor, report)
        if isinstance(saved, Err):
            return saved
        self._report_to_review(session, report, quality)
        self._events.publish(
            FinalPolishCompleted(
                occurred_at=self._clock.now(),
                final_polish_report_id=report.final_polish_report_id,
                timeline_id=report.timeline_id,
                timeline_version=report.timeline_version,
                review_session_id=report.review_session_id,
                readiness=report.readiness_status.value,
                remaining=len(report.remaining_issues),
                created=True,
            )
        )
        return Ok(PolishResult(report, saved.value, created=True))

    def _report_to_review(
        self, session: ReviewSession | None, report: FinalPolishReport, quality: QualityReport
    ) -> None:
        """The reports of this version go to the review; the review then holds a final review
        version, or - when the video is not ready - asks for another round."""
        if session is None:
            return
        ref = TimelineRef(
            timeline_id=report.timeline_id,
            version=report.timeline_version,
            content_hash=report.timeline_content_hash,
            anchor_asset_id=report.anchor_asset_id,
        )
        for kind, artifact in (
            (ArtifactKind.QUALITY_REPORT, quality.quality_report_id),
            (ArtifactKind.FINAL_POLISH_REPORT, report.final_polish_report_id),
        ):
            attached = self._sessions.attach(
                session.session_id, kind, artifact, timeline=ref, actor=_ACTOR
            )
            if isinstance(attached, Err):
                _log.warning("Report not recorded in the review", reason=str(attached.error))
                return
        if report.readiness_status is Readiness.NOT_READY:
            self._sessions.advance(
                session.session_id,
                ReviewState.REVIEW_AGAIN_REQUIRED,
                actor=_ACTOR,
                reason="the final polish found what must be fixed first",
            )

    # --- pictures ----------------------------------------------------------------------------
    def _frames(
        self,
        preview_id: str,
        view: f.PolishTimeline,
        warnings: list[str],
        ctx: JobContext,
    ) -> tuple[f.FrameFacts, ...]:
        located = self._library.local_path(preview_id)
        if isinstance(located, Err):
            warnings.append("The preview file could not be found; no picture was looked at.")
            return ()
        wanted = _moments(view)
        with self._paths.temporary_directory(prefix="polish-") as work:
            try:
                samples = self._analyzer.sample_frames(
                    located.value,
                    [m for _, _, m in wanted],
                    into=work,
                    cancellation=ctx.cancellation,
                )
            except OperationCancelledError:
                raise
            except ExternalSystemError as exc:
                warnings.append(f"Pictures could not be taken from the preview: {exc.user_message}")
                return ()
            by_time = {s.requested: s for s in samples}
            facts: list[f.FrameFacts] = []
            for label, frame, moment in wanted:
                sample = by_time.get(moment)
                if sample is None:
                    continue
                asset_id = self._keep(preview_id, sample.path, view, label, frame)
                facts.append(
                    f.FrameFacts(label, frame, sample.black_fraction, sample.luma_mean, asset_id)
                )
            return tuple(facts)

    def _keep(
        self, preview_id: str, path: Path | None, view: f.PolishTimeline, label: str, frame: int
    ) -> str:
        if path is None:
            return ""
        registered = self._library.register_derived(
            preview_id,
            path,
            operation=EVIDENCE_OPERATION,
            config={
                "timeline_id": view.timeline_id,
                "timeline_content_hash": view.content_hash,
                "label": label,
                "frame": frame,
            },
            display_name=f"Final review frame '{label}' @ {frame}",
            metadata={"timeline_version": view.version, "label": label},
            role=AssetRole.PREVIEW,
        )
        return registered.value.asset.id if isinstance(registered, Ok) else ""


def _moments(view: f.PolishTimeline) -> list[tuple[str, int, Fraction]]:
    """The first and last picture, and the middle of each section (bounded)."""
    end = max(view.duration_frames - 2, 0)
    wanted = [("first", 2, Fraction(2) / view.fps), ("last", end, Fraction(end) / view.fps)]
    for section in view.sections[: _MAX_FRAMES - 2]:
        middle = (section.start_frame + section.end_frame) // 2
        wanted.append((f"section:{section.name}", middle, Fraction(middle) / view.fps))
    seen: dict[Fraction, tuple[str, int, Fraction]] = {}
    for item in wanted:
        seen.setdefault(item[2], item)
    return list(seen.values())
