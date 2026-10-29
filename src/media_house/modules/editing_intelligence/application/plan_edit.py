"""Use case: story plan + stored analyses -> one validated edit plan, stored in the library.

Nothing is transcribed, analysed, aligned, probed or planned here unless the caller asks for the
STORY PLAN to be produced (``Acquire.REQUEST``, which asks the story planner: its owner). Every
other input is READ through its owner's catalog, or used as supplied: content analysis, audio
timeline, video analysis, alignment and the stored inspection. Missing optional evidence degrades
the plan honestly (an unresolved issue and a weaker decision), it never invents anything; a missing
or invalid story plan is an error, never a silent fallback.

The plan is one derived library document of the first recording, keyed by the identity of every
input actually used, the whole configuration, the manual overrides, any advisor and the versions.
A changed key makes a NEW plan that records ``plan_version`` and ``supersedes``; earlier plans are
never changed or deleted. A person's overrides in the previous plan are carried forward unless the
caller replaces them.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Protocol

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.contracts import (
    AudioCatalog,
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentCatalog,
    AlignmentDocument,
)
from media_house.modules.broll_intelligence.application.contracts import (
    BrollAnalysis,
    BrollCatalog,
)
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalysis,
    ContentCatalog,
)
from media_house.modules.editing_intelligence.application.results import EditResults
from media_house.modules.editing_intelligence.application.views import (
    alignment_facts,
    audio_facts,
    broll_facts,
    content_facts,
    pacing_facts,
    source_facts,
    story_facts,
    video_facts,
)
from media_house.modules.editing_intelligence.domain.engine import PlanMeta, plan_edit
from media_house.modules.editing_intelligence.domain.errors import (
    EditingIntelligenceError,
    InvalidEditConfig,
    InvalidEditPlan,
    InvalidStoryInput,
    MissingStoryPlan,
    NothingToEdit,
)
from media_house.modules.editing_intelligence.domain.facts import (
    AssetEvidence,
    BrollFacts,
    PacingFacts,
    ShotChoice,
)
from media_house.modules.editing_intelligence.domain.hooks import (
    ContinuityVerdict,
    CoverageQuery,
    CutQuery,
    Hooks,
)
from media_house.modules.editing_intelligence.domain.model import (
    EditPlan,
    InputRecord,
    ManualOverride,
    StoryRef,
)
from media_house.modules.editing_intelligence.domain.profile import EditConfig
from media_house.modules.editing_intelligence.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.editing_intelligence.domain.values import (
    EDITOR_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Action,
    InputHow,
    Severity,
)
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.pacing_intelligence.application.contracts import (
    PacingAnalysis,
    PacingCatalog,
)
from media_house.modules.story_architecture.application.contracts import (
    Acquire as StoryAcquire,
)
from media_house.modules.story_architecture.application.contracts import (
    PlanStoryCommand,
    StoryCatalog,
    StoryConfig,
    StoryPlan,
    StoryPlanner,
    validate_plan,
)
from media_house.modules.video_intelligence.application.contracts import (
    VideoAnalysis,
    VideoCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type PlanEditError = MediaError | EditingIntelligenceError | MediaHouseError


class Acquire(StrEnum):
    #: Use the stored story plan; fail with ``MissingStoryPlan`` if there is none.
    STORED = "stored"
    #: Ask the story planner to produce a missing story plan (it reads the stored analysis).
    REQUEST = "request"


# --- optional outside opinions (integration contracts for specialised analysis) ------------------
class CoverageAdvisor(Protocol):
    """Ranks cover footage for a place that needs it (better than the baseline association).

    Nothing implements this today. An answer is validated like any other candidate: the shots it
    names must exist in the video analysis. ``None`` means no opinion.
    """

    def identity(self) -> str: ...

    def rank(self, query: CoverageQuery) -> Sequence[ShotChoice] | None: ...


class ContinuityAdvisor(Protocol):
    """Assesses continuity at a cut (subject, eyeline, pose ...) beyond shot-level measurements."""

    def identity(self) -> str: ...

    def assess(self, query: CutQuery) -> ContinuityVerdict | None: ...


@dataclass(frozen=True, slots=True)
class Advisors:
    coverage: CoverageAdvisor | None = None
    continuity: ContinuityAdvisor | None = None

    def hooks(self) -> Hooks:
        return Hooks(
            coverage=self.coverage.rank if self.coverage else None,
            continuity=self.continuity.assess if self.continuity else None,
        )

    def identities(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (name, advisor.identity())
            for name, advisor in (
                ("coverage", self.coverage),
                ("continuity", self.continuity),
            )
            if advisor is not None
        )


@dataclass(frozen=True, slots=True)
class PlanEditCommand:
    #: The story's recordings, in story order. Needed to find the story plan unless one is supplied.
    asset_ids: tuple[str, ...] = ()
    config: EditConfig = field(default_factory=EditConfig)
    #: Which story format's stored plan to edit (``None`` = the newest for the recordings).
    story_format: str | None = None
    #: A story plan the caller holds (used as given, still validated).
    story_plan: StoryPlan | None = None
    acquire: Acquire = Acquire.STORED
    #: A label the caller keeps for the project; stored on the plan, never interpreted.
    project_id: str | None = None
    #: A person's decisions (``KEEP`` or ``CUT`` of one sentence); a replan never replaces them.
    overrides: tuple[ManualOverride, ...] = ()
    #: Carry the overrides of the previous plan of the same recordings and profile forward.
    carry_overrides: bool = True
    use_audio: bool = True
    use_video: bool = True
    use_alignment: bool = True
    #: Results the caller already holds (used as given, nothing is looked up for them).
    content_analyses: Mapping[str, ContentAnalysis] = field(default_factory=dict)
    audio_timelines: Mapping[str, AudioIntelligenceTimeline] = field(default_factory=dict)
    video_analyses: Mapping[str, VideoAnalysis] = field(default_factory=dict)
    alignments: Mapping[str, AlignmentDocument] = field(default_factory=dict)
    #: A pacing analysis the caller holds (used as given), and whether to read a stored one.
    pacing: PacingAnalysis | None = None
    use_pacing: bool = True
    #: A B-roll analysis the caller holds (used as given), and whether to read a stored one.
    broll: BrollAnalysis | None = None
    use_broll: bool = True


@dataclass(frozen=True, slots=True)
class EditResult:
    plan: EditPlan
    #: The stored document (a derived asset of the first recording).
    asset: MediaAssetDto
    #: ``False`` when the identical plan already existed and nothing was computed.
    created: bool


@dataclass(slots=True)
class _Gathered:
    evidence: dict[str, AssetEvidence] = field(default_factory=dict)
    records: list[InputRecord] = field(default_factory=list)
    identities: dict[str, JsonValue] = field(default_factory=dict)
    known: dict[str, frozenset[str]] = field(default_factory=dict)
    #: Footage of recordings outside the story that the B-roll analysis offered.
    cover: dict[str, AssetEvidence] = field(default_factory=dict)


_MANUAL = (Action.KEEP, Action.CUT)


class PlanEdit:
    def __init__(
        self,
        library: MediaLibrary,
        planner: StoryPlanner,
        stories: StoryCatalog,
        content: ContentCatalog,
        audio: AudioCatalog,
        video: VideoCatalog,
        alignments: AlignmentCatalog,
        inspections: InspectionCatalog,
        paths: AppPaths,
        clock: Clock,
        advisors: Advisors | None = None,
        pacing: PacingCatalog | None = None,
        broll: BrollCatalog | None = None,
    ) -> None:
        self._library = library
        self._planner = planner
        self._stories = stories
        self._content = content
        self._audio = audio
        self._video = video
        self._alignments = alignments
        self._inspections = inspections
        self._clock = clock
        self._advisors = advisors or Advisors()
        self._pacing = pacing
        self._broll = broll
        self._documents = DerivedDocuments(library, paths)
        self._results = EditResults(library)

    def execute(
        self, command: PlanEditCommand, ctx: JobContext
    ) -> Result[EditResult, PlanEditError]:
        bad = [o for o in command.overrides if o.action not in _MANUAL]
        if bad or len({o.decision_id for o in command.overrides}) != len(command.overrides):
            return Err(
                ValidationError(
                    "overrides must be unique and keep or cut",
                    field="overrides",
                    user_message="A manual decision must keep or cut one sentence, once.",
                )
            )
        if command.story_plan is None and not command.asset_ids:
            return Err(
                ValidationError(
                    "asset_ids or story_plan needed",
                    field="asset_ids",
                    user_message="Give the recordings of the story, or the story plan itself.",
                )
            )
        ctx.progress.report(1, _STEPS, "Reading the story plan")
        story = self._story(command, ctx)
        if isinstance(story, Err):
            return story
        plan, stored_as, story_record = story.value
        try:
            profile = command.config.resolve(plan.target_format)
        except InvalidEditConfig as exc:
            return Err(exc)

        ctx.progress.report(2, _STEPS, "Reading the stored analyses")
        gathered = self._gather(command, plan, ctx)
        if isinstance(gathered, Err):
            return gathered
        gathered.value.records.insert(0, story_record)
        pacing = self._pacing_of(command, plan, gathered.value)
        broll = self._broll_of(command, plan, gathered.value)
        if broll is not None:
            self._gather_cover(command, plan, broll, gathered.value)
        complete = len(gathered.value.known) == len(plan.source_assets)
        problems = validate_plan(plan, gathered.value.known if complete else None)
        if problems:
            _log.error("Story plan rejected", problems=problems[:5])
            return Err(InvalidStoryInput(problems))

        anchor = plan.source_assets[0].asset_id
        assets = tuple(a.asset_id for a in plan.source_assets)
        overrides = self._overrides(command, anchor, profile.name, assets)
        advisors = self._advisors.identities()
        key = self._cache_key(command, plan, gathered.value, overrides, advisors, pacing, broll)
        spec = DocumentSpec(RESULT_OPERATION, EDITOR_VERSION, key)
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, plan_from_json)
        if cached is not None:
            _log.info("Edit plan reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(EditResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(3, _STEPS, "Planning the edit")
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        version, supersedes = self._history(anchor, profile.name, assets)
        digest = _digest(key)
        meta = PlanMeta(
            edit_plan_id=f"ep_{digest[:16]}",
            plan_version=version,
            supersedes=supersedes,
            project_id=command.project_id,
            created_at=self._clock.now(),
            configuration_fingerprint=digest,
            story=StoryRef(
                story_plan_id=plan.story_plan_id,
                plan_version=plan.plan_version,
                stored_as=stored_as,
                target_format=plan.target_format,
            ),
            inputs=tuple(gathered.value.records),
            warnings=tuple(plan.warnings),
            overrides=overrides,
            advisors=advisors,
        )
        try:
            result = plan_edit(
                story_facts(plan, stored_as),
                gathered.value.evidence,
                command.config,
                meta,
                self._advisors.hooks(),
                pacing,
                broll,
                gathered.value.cover,
            )
        except NothingToEdit as exc:
            return Err(exc)
        errors = [i.detail for i in result.issues_of(Severity.ERROR)]
        if errors:
            _log.error("Edit plan rejected its own result", problems=errors[:5])
            return Err(InvalidEditPlan(errors))

        ctx.progress.report(_STEPS - 1, _STEPS, "Saving")
        stored = self._documents.store(
            anchor_asset.value,
            plan_to_json(result),
            filename="edit_plan.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (edit plan)",
            metadata={
                "profile": profile.name,
                "assets": ",".join(assets),
                "story_plan_id": plan.story_plan_id,
                "plan_version": result.plan_version,
                "placements": len(result.placements),
                "decisions": len(result.decisions),
                "state": result.state.value,
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Edit plan created",
            asset_id=stored.value.id,
            decisions=len(result.decisions),
            placements=len(result.placements),
            issues=len(result.issues),
            plan_version=result.plan_version,
        )
        return Ok(EditResult(result, stored.value, created=True))

    # --- the story -----------------------------------------------------------------------
    def _story(
        self, command: PlanEditCommand, ctx: JobContext
    ) -> Result[tuple[StoryPlan, str | None, InputRecord], PlanEditError]:
        if command.story_plan is not None:
            plan = command.story_plan
            record = InputRecord(
                kind="story_plan", how=InputHow.SUPPLIED, identity=plan.story_plan_id
            )
            return self._usable(plan, None, record)
        anchor = command.asset_ids[0]
        for dto in self._stories.history(anchor):
            loaded = self._stories.load(dto.id)
            if isinstance(loaded, Err):
                continue
            plan = loaded.value
            if tuple(a.asset_id for a in plan.source_assets) != command.asset_ids:
                continue
            if command.story_format is not None and plan.target_format != command.story_format:
                continue
            record = InputRecord(
                kind="story_plan", how=InputHow.REUSED, identity=plan.story_plan_id, detail=dto.id
            )
            return self._usable(plan, dto.id, record)
        if command.acquire is not Acquire.REQUEST:
            return Err(MissingStoryPlan(command.asset_ids))
        config = StoryConfig(command.story_format) if command.story_format else StoryConfig()
        made = self._planner.execute(
            PlanStoryCommand(command.asset_ids, config, command.project_id, StoryAcquire.REQUEST),
            ctx,
        )
        if isinstance(made, Err):
            return made
        record = InputRecord(
            kind="story_plan",
            how=InputHow.REUSED if not made.value.created else InputHow.REQUESTED,
            identity=made.value.plan.story_plan_id,
            detail=made.value.asset.id,
        )
        return self._usable(made.value.plan, made.value.asset.id, record)

    @staticmethod
    def _usable(
        plan: StoryPlan, stored_as: str | None, record: InputRecord
    ) -> Result[tuple[StoryPlan, str | None, InputRecord], PlanEditError]:
        if plan.state.value == "not_applicable" or not plan.sections:
            return Err(NothingToEdit("the story plan has no sections"))
        return Ok((plan, stored_as, record))

    # --- the evidence -----------------------------------------------------------------------
    def _gather(
        self, command: PlanEditCommand, plan: StoryPlan, ctx: JobContext
    ) -> Result[_Gathered, PlanEditError]:
        out = _Gathered()
        for record in plan.source_assets:
            asset_id = record.asset_id
            found = self._library.get(asset_id)
            if isinstance(found, Err):
                return found
            inspection = self._inspections.find(asset_id)
            out.records.append(
                InputRecord(
                    kind="inspection",
                    how=InputHow.REUSED if inspection else InputHow.NOT_AVAILABLE,
                    asset_id=asset_id,
                    detail="" if inspection else "no stored inspection (nothing was probed)",
                )
            )
            has_video = (
                None if inspection is None else inspection.inspection.primary_video is not None
            )
            content = self._read(
                command.content_analyses, asset_id, self._content.latest, "content_analysis", out
            )
            audio = (
                self._read(
                    command.audio_timelines, asset_id, self._audio.latest, "audio_analysis", out
                )
                if command.use_audio
                else None
            )
            video = (
                self._read(
                    command.video_analyses, asset_id, self._video.latest, "video_analysis", out
                )
                if command.use_video and has_video is not False
                else None
            )
            alignment = (
                self._read(command.alignments, asset_id, self._alignments.latest, "alignment", out)
                if command.use_alignment and has_video is not False
                else None
            )
            fallback = (
                content.source.duration
                if content is not None
                else audio.duration
                if audio is not None
                else record.duration
            )
            source = source_facts(found.value, inspection, fallback)
            correction = alignment.video_correction if alignment is not None else 0.0
            evidence = AssetEvidence(
                source=source,
                content=content_facts(content, asset_id) if content is not None else None,
                audio=audio_facts(audio, asset_id) if audio is not None else None,
                video=video_facts(video, asset_id, correction, alignment is not None)
                if video is not None
                else None,
                alignment=alignment_facts(alignment) if alignment is not None else None,
            )
            out.evidence[asset_id] = evidence
            if evidence.content is not None:
                out.known[asset_id] = frozenset(s.segment_id for s in evidence.content.segments)
            out.identities[asset_id] = {
                "source": source.identity,
                "content": evidence.content.identity if evidence.content else None,
                "audio": evidence.audio.identity if evidence.audio else None,
                "video": evidence.video.identity if evidence.video else None,
                "alignment": evidence.alignment.identity if evidence.alignment else None,
            }
        return Ok(out)

    @staticmethod
    def _read[T](
        supplied: Mapping[str, T],
        asset_id: str,
        latest: Callable[[str], Result[tuple[T, MediaAssetDto] | None, MediaHouseError]],
        kind: str,
        out: _Gathered,
    ) -> T | None:
        """The result the caller supplied, else the stored one, else ``None`` (recorded as such)."""
        if asset_id in supplied:
            out.records.append(InputRecord(kind=kind, how=InputHow.SUPPLIED, asset_id=asset_id))
            return supplied[asset_id]
        stored = latest(asset_id)
        if isinstance(stored, Ok) and stored.value is not None:
            out.records.append(
                InputRecord(
                    kind=kind, how=InputHow.REUSED, asset_id=asset_id, detail=stored.value[1].id
                )
            )
            return stored.value[0]
        detail = (
            f"no stored {kind.replace('_', ' ')}"
            if isinstance(stored, Ok)
            else f"the library could not be read: {stored.error.user_message}"
        )
        out.records.append(
            InputRecord(kind=kind, how=InputHow.NOT_AVAILABLE, asset_id=asset_id, detail=detail)
        )
        return None

    def _pacing_of(
        self, command: PlanEditCommand, plan: StoryPlan, gathered: _Gathered
    ) -> PacingFacts | None:
        """The pacing analysis of THIS story's recordings, supplied or stored; else ``None``.

        An analysis of other recordings, or of another story, is ignored and said so."""
        assets = tuple(a.asset_id for a in plan.source_assets)
        found: PacingAnalysis | None = None
        how = InputHow.NOT_AVAILABLE
        detail = "no pacing analysis (the editor's baseline pacing notes apply)"
        if command.pacing is not None:
            found, how = command.pacing, InputHow.SUPPLIED
        elif command.use_pacing and self._pacing is not None:
            stored = self._pacing.latest(assets[0], story_plan_id=plan.story_plan_id)
            if isinstance(stored, Ok) and stored.value is not None:
                found, how, detail = stored.value[0], InputHow.REUSED, stored.value[1].id
        if found is not None and tuple(s.asset_id for s in found.sources) != assets:
            found, how = None, InputHow.NOT_AVAILABLE
            detail = "the pacing analysis is for other recordings"
        gathered.records.append(
            InputRecord(
                kind="pacing_analysis",
                how=how,
                identity=None if found is None else found.pacing_analysis_id,
                detail=detail if found is None or how is InputHow.REUSED else "",
            )
        )
        return None if found is None else pacing_facts(found)

    def _broll_of(
        self, command: PlanEditCommand, plan: StoryPlan, gathered: _Gathered
    ) -> BrollFacts | None:
        """The B-roll analysis of THIS story's recordings, supplied or stored; else ``None``.

        An analysis of other recordings is ignored and said so."""
        assets = tuple(a.asset_id for a in plan.source_assets)
        found: BrollAnalysis | None = None
        how = InputHow.NOT_AVAILABLE
        detail = "no B-roll analysis (cover candidates come from the alignment's shots only)"
        if command.broll is not None:
            found, how = command.broll, InputHow.SUPPLIED
        elif command.use_broll and self._broll is not None:
            stored = self._broll.latest(assets[0], story_plan_id=plan.story_plan_id)
            if isinstance(stored, Ok) and stored.value is not None:
                found, how, detail = stored.value[0], InputHow.REUSED, stored.value[1].id
        if found is not None and found.sources != assets:
            found, how = None, InputHow.NOT_AVAILABLE
            detail = "the B-roll analysis is for other recordings"
        gathered.records.append(
            InputRecord(
                kind="broll_analysis",
                how=how,
                identity=None if found is None else found.broll_analysis_id,
                detail=detail if found is None or how is InputHow.REUSED else "",
            )
        )
        return None if found is None else broll_facts(found)

    def _gather_cover(
        self, command: PlanEditCommand, plan: StoryPlan, broll: BrollFacts, out: _Gathered
    ) -> None:
        """Load the footage evidence of the recordings the B-roll analysis offered (video only).

        The shots belong to the stored version that was analysed (a proxy, say) but are attributed
        to the original asset, the id the plan uses."""
        story_assets = {a.asset_id for a in plan.source_assets}
        for original, analysed in broll.analysed:
            if original in story_assets or original in out.cover:
                continue
            found = self._library.get(original)
            video = self._video.latest(analysed)
            if isinstance(found, Err) or isinstance(video, Err) or video.value is None:
                out.records.append(
                    InputRecord(
                        kind="video_analysis",
                        how=InputHow.NOT_AVAILABLE,
                        asset_id=original,
                        detail="no usable video analysis of the offered footage",
                    )
                )
                continue
            analysis, stored = video.value
            inspection = self._inspections.find(original)
            alignment = self._alignments.latest(analysed)
            aligned = alignment.value[0] if isinstance(alignment, Ok) and alignment.value else None
            correction = aligned.video_correction if aligned is not None else 0.0
            source = source_facts(found.value, inspection, analysis.source.duration_seconds)
            evidence = AssetEvidence(
                source=source,
                video=video_facts(analysis, original, correction, aligned is not None),
            )
            out.cover[original] = evidence
            out.identities[f"cover:{original}"] = {
                "source": source.identity,
                "video": evidence.video.identity if evidence.video else None,
            }
            out.records.append(
                InputRecord(
                    kind="video_analysis",
                    how=InputHow.REUSED,
                    asset_id=original,
                    detail=stored.id,
                )
            )

    # --- overrides, identity, history -------------------------------------------------
    def _overrides(
        self, command: PlanEditCommand, anchor: str, profile: str, assets: tuple[str, ...]
    ) -> tuple[ManualOverride, ...]:
        """The caller's decisions, plus the previous plan's that the caller has not replaced."""
        chosen = {o.decision_id: o for o in command.overrides}
        if command.carry_overrides:
            previous = self._results.latest(anchor, profile=profile)
            if isinstance(previous, Ok) and previous.value is not None:
                plan, asset = previous.value
                if asset.metadata.get("assets") == ",".join(assets):
                    for o in plan.overrides:
                        chosen.setdefault(o.decision_id, o)
        return tuple(sorted(chosen.values(), key=lambda o: o.decision_id))

    @staticmethod
    def _cache_key(
        command: PlanEditCommand,
        plan: StoryPlan,
        gathered: _Gathered,
        overrides: tuple[ManualOverride, ...],
        advisors: tuple[tuple[str, str], ...],
        pacing: PacingFacts | None,
        broll: BrollFacts | None,
    ) -> Mapping[str, JsonValue]:
        """Everything that can change the plan and nothing that cannot (not time, job or device)."""
        return {
            "story": {
                "id": plan.story_plan_id,
                "version": plan.plan_version,
                # Everything of the plan the editor reads: a story that changed under the same
                # id (or a hand-edited one) still misses.
                "facts": _digest(asdict(story_facts(plan, None))),
            },
            "inputs": dict(gathered.identities),
            "project": command.project_id,
            "config": json.loads(
                json.dumps(command.config.fingerprint(command.config.resolve(plan.target_format)))
            ),
            "overrides": [[o.decision_id, o.action.value, o.note] for o in overrides],
            "advisors": [list(a) for a in advisors],
            "pacing": None if pacing is None else pacing.identity,
            "broll": None if broll is None else broll.identity,
            "versions": {
                "editor": EDITOR_VERSION,
                "schema": SCHEMA_VERSION,
                "vocabulary": VOCABULARY_VERSION,
            },
        }

    def _history(self, anchor: str, profile: str, assets: Sequence[str]) -> tuple[int, str | None]:
        """1 for the first plan of these recordings and profile; later ones count up and name
        their predecessor. Earlier plans are never changed or removed."""
        wanted = ",".join(assets)
        earlier = [
            a
            for a in self._library.list_derived(anchor)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and a.metadata.get("profile") == profile
            and a.metadata.get("assets") == wanted
        ]
        if not earlier:
            return 1, None
        newest = max(earlier, key=lambda a: (a.created_at, a.id))
        return len(earlier) + 1, newest.id


_STEPS = 4


def _digest(key: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()


__all__ = [
    "Acquire",
    "Advisors",
    "ContinuityAdvisor",
    "CoverageAdvisor",
    "EditResult",
    "PlanEdit",
    "PlanEditCommand",
    "PlanEditError",
]
