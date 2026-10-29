"""Use case: stored picture analyses (+ inspection, story, continuity) -> one reframing analysis.

Nothing is detected, tracked, probed, transcribed or rendered here: the boxes, the sizes and the
story are READ through their owners' catalogs, or used as supplied. A missing optional input makes
a recommendation less certain (a record, an unresolved issue), never invented; only the picture
analysis of a recording is required, because without tracks there is nothing to place a window on.

The analysis is one derived library document of the first recording. The cache key covers every
shot's evidence, the target, the configuration and the versions, so a changed target, safe area,
profile, track or story misses the cache, while a shot whose own fingerprint is unchanged is
carried over from the previous analysis without planning again. A person's accept/reject follows
the segment.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import cast

from media_house.core.application.ports import Clock
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentCatalog,
    AlignmentDocument,
)
from media_house.modules.continuity_intelligence.application.contracts import (
    ContinuityAnalysis,
    ContinuityCatalog,
)
from media_house.modules.media_inspection.application.contracts import (
    InspectionCatalog,
    InspectionResult,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.reframing_intelligence.application.results import ReframingResults
from media_house.modules.reframing_intelligence.application.views import (
    continuity_facts,
    source_facts,
    story_facts,
)
from media_house.modules.reframing_intelligence.domain.engine import (
    AnalysisMeta,
    cache_key,
    plan_reframing,
)
from media_house.modules.reframing_intelligence.domain.errors import (
    InvalidReframingAnalysis,
    InvalidReframingConfig,
    MissingPrerequisite,
    NothingToFrame,
    ReframingIntelligenceError,
)
from media_house.modules.reframing_intelligence.domain.facts import (
    ContinuityFacts,
    Inputs,
    SourceFacts,
    StoryFacts,
)
from media_house.modules.reframing_intelligence.domain.geometry import TargetFormat
from media_house.modules.reframing_intelligence.domain.model import (
    InputRecord,
    ReframingAnalysis,
    StageReport,
)
from media_house.modules.reframing_intelligence.domain.profile import ReframingConfig
from media_house.modules.reframing_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.reframing_intelligence.domain.validation import validate_analysis
from media_house.modules.reframing_intelligence.domain.values import (
    ANALYZER_VERSION,
    RESULT_OPERATION,
    Decision,
    InputHow,
    Severity,
    StageState,
)
from media_house.modules.story_architecture.application.contracts import (
    StoryCatalog,
    StoryPlan,
)
from media_house.modules.video_intelligence.application.contracts import (
    VideoAnalysis,
    VideoCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type AnalyzeReframingError = MediaError | ReframingIntelligenceError | MediaHouseError

_STEPS = 5


@dataclass(frozen=True, slots=True)
class SegmentDecision:
    """A person's verdict on the recommendation for one segment (``asset_id:shot_id``)."""

    segment_key: str
    decision: Decision
    note: str = ""


@dataclass(frozen=True, slots=True)
class AnalyzeReframingCommand:
    asset_ids: tuple[str, ...]
    target: TargetFormat
    config: ReframingConfig = field(default_factory=ReframingConfig)
    #: Names the profile when neither a story plan nor the configuration does.
    story_format: str | None = None
    project_id: str | None = None
    #: A person's verdicts; those of the previous analysis are carried unless replaced.
    decisions: tuple[SegmentDecision, ...] = ()
    carry_decisions: bool = True
    use_story: bool = True
    use_inspection: bool = True
    use_alignment: bool = True
    use_continuity: bool = True
    #: Results the caller already holds (used as given, nothing is looked up for them).
    video_analyses: Mapping[str, VideoAnalysis] = field(default_factory=dict)
    alignments: Mapping[str, AlignmentDocument] = field(default_factory=dict)
    story_plan: StoryPlan | None = None
    continuity: ContinuityAnalysis | None = None


@dataclass(frozen=True, slots=True)
class ReframingResult:
    analysis: ReframingAnalysis
    #: The stored document (a derived asset of the first recording).
    asset: MediaAssetDto
    #: ``False`` when the identical analysis already existed and nothing was planned.
    created: bool


class AnalyzeReframing:
    def __init__(
        self,
        library: MediaLibrary,
        video: VideoCatalog,
        inspections: InspectionCatalog,
        alignments: AlignmentCatalog,
        stories: StoryCatalog,
        continuity: ContinuityCatalog,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._video = video
        self._inspections = inspections
        self._alignments = alignments
        self._stories = stories
        self._continuity = continuity
        self._clock = clock
        self._documents = DerivedDocuments(library, paths)
        self._results = ReframingResults(library)

    def execute(
        self, command: AnalyzeReframingCommand, ctx: JobContext
    ) -> Result[ReframingResult, AnalyzeReframingError]:
        if not command.asset_ids:
            return Err(NothingToFrame())
        anchor = command.asset_ids[0]
        records: list[InputRecord] = []
        ctx.progress.report(1, _STEPS, "Reading the stored analyses")
        sources = self._sources(command, records)
        if isinstance(sources, Err):
            return sources
        story, story_plan = self._story(command, anchor, records)
        continuity = self._continuity_of(command, anchor, records)
        try:
            profile = command.config.resolve(story.story_format if story else command.story_format)
        except InvalidReframingConfig as exc:
            return Err(exc)

        ctx.progress.report(2, _STEPS, "Fingerprinting the shots")
        previous = self._previous(anchor, command.target, profile.name)
        decisions = _decisions(command, previous)
        inputs = Inputs(tuple(sources.value), story, continuity, decisions)
        key = cache_key(inputs, command.target, command.config, profile)
        spec = DocumentSpec(RESULT_OPERATION, ANALYZER_VERSION, _json(key))
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, analysis_from_json)
        if cached is not None:
            _log.info("Reframing analysis reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(ReframingResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(3, _STEPS, "Planning the framing")
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        version, supersedes = self._history(anchor, command.target.name, profile.name)
        meta = AnalysisMeta(
            analysis_version=version,
            supersedes=supersedes,
            project_id=command.project_id,
            inputs=tuple(records),
            stages=_stages(sources.value, story, continuity, command),
        )
        result = plan_reframing(
            inputs,
            command.target,
            command.config,
            now=self._clock.now(),
            previous=previous,
            meta=meta,
        )

        ctx.progress.report(4, _STEPS, "Validating the crops")
        errors = [i.detail for i in validate_analysis(result) if i.severity is Severity.ERROR]
        if errors:
            _log.error("Reframing analysis rejected its own result", problems=errors[:5])
            return Err(InvalidReframingAnalysis(errors))

        ctx.progress.report(_STEPS, _STEPS, "Saving")
        stored = self._documents.store(
            anchor_asset.value,
            analysis_to_json(result),
            filename="reframing_analysis.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (reframing {command.target.name})",
            metadata={
                "target": command.target.name,
                "profile": profile.name,
                "analysis_version": result.analysis_version,
                "recommendations": len(result.recommendations),
                "state": result.state.value,
                "story_plan_id": None if story_plan is None else story_plan.story_plan_id,
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Reframing analysis created",
            asset_id=stored.value.id,
            target=command.target.name,
            shots=result.metrics.shots,
            evaluated=result.metrics.evaluated,
            reused=result.metrics.reused,
            unreframeable=result.metrics.unreframeable,
            model_calls=0,
        )
        return Ok(ReframingResult(result, stored.value, created=True))

    # --- evidence ----------------------------------------------------------------------------
    def _sources(
        self, command: AnalyzeReframingCommand, records: list[InputRecord]
    ) -> Result[list[SourceFacts], AnalyzeReframingError]:
        out: list[SourceFacts] = []
        for asset_id in command.asset_ids:
            found = self._library.get(asset_id)
            if isinstance(found, Err):
                return found
            video = _read(
                command.video_analyses, asset_id, self._video.latest, "video_analysis", records
            )
            if video is None:
                return Err(MissingPrerequisite(asset_id, "video analysis"))
            inspection = self._inspection(command, asset_id, records)
            alignment = (
                _read(command.alignments, asset_id, self._alignments.latest, "alignment", records)
                if command.use_alignment
                else None
            )
            out.append(
                source_facts(
                    video,
                    asset_id,
                    inspection,
                    alignment.video_correction if alignment is not None else 0.0,
                    alignment is not None,
                )
            )
        return Ok(out)

    def _inspection(
        self, command: AnalyzeReframingCommand, asset_id: str, records: list[InputRecord]
    ) -> InspectionResult | None:
        found = self._inspections.find(asset_id) if command.use_inspection else None
        records.append(
            InputRecord(
                kind="inspection",
                how=InputHow.REUSED if found is not None else InputHow.NOT_AVAILABLE,
                asset_id=asset_id,
                identity=None if found is None else found.asset.id,
                detail=""
                if found is not None
                else "no stored inspection: the stored size and pixel shape are assumed",
            )
        )
        return found

    def _story(
        self, command: AnalyzeReframingCommand, anchor: str, records: list[InputRecord]
    ) -> tuple[StoryFacts | None, StoryPlan | None]:
        plan = command.story_plan
        how = InputHow.SUPPLIED if plan is not None else InputHow.NOT_AVAILABLE
        detail = "no story plan: no beat or visual need informed the crops"
        if plan is None and command.use_story:
            stored = self._stories.latest(anchor)
            if isinstance(stored, Ok) and stored.value is not None:
                plan, how, detail = stored.value[0], InputHow.REUSED, stored.value[1].id
        if plan is not None and not {a.asset_id for a in plan.source_assets} & set(
            command.asset_ids
        ):
            plan, how, detail = (
                None,
                InputHow.NOT_AVAILABLE,
                "the story plan is for other recordings",
            )
        records.append(
            InputRecord(
                kind="story_plan",
                how=how,
                identity=None if plan is None else plan.story_plan_id,
                detail=detail if plan is None or how is InputHow.REUSED else "",
            )
        )
        return (None, None) if plan is None else (story_facts(plan), plan)

    def _continuity_of(
        self, command: AnalyzeReframingCommand, anchor: str, records: list[InputRecord]
    ) -> ContinuityFacts | None:
        found = command.continuity
        how = InputHow.SUPPLIED if found is not None else InputHow.NOT_AVAILABLE
        detail = "no continuity analysis: neighbouring shots are paired by recording order"
        if found is None and command.use_continuity:
            stored = self._continuity.latest(anchor)
            if isinstance(stored, Ok) and stored.value is not None:
                found, how, detail = stored.value[0], InputHow.REUSED, stored.value[1].id
        if found is not None and not set(found.sources) & set(command.asset_ids):
            found, how, detail = (
                None,
                InputHow.NOT_AVAILABLE,
                "the continuity analysis is for other recordings",
            )
        records.append(
            InputRecord(
                kind="continuity_analysis",
                how=how,
                identity=None if found is None else found.continuity_analysis_id,
                detail=detail if found is None or how is InputHow.REUSED else "",
            )
        )
        return None if found is None else continuity_facts(found)

    # --- history -----------------------------------------------------------------------------
    def _previous(
        self, anchor: str, target: TargetFormat, profile: str
    ) -> ReframingAnalysis | None:
        found = self._results.latest(anchor, target=target.name, profile=profile)
        return found.value[0] if isinstance(found, Ok) and found.value is not None else None

    def _history(self, anchor: str, target: str, profile: str) -> tuple[int, str | None]:
        """1 for the first analysis of this recording, target and profile; later ones count up and
        name their predecessor. Earlier analyses are never changed or removed."""
        earlier = [
            a
            for a in self._library.list_derived(anchor)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and a.metadata.get("target") == target
            and a.metadata.get("profile") == profile
        ]
        if not earlier:
            return 1, None
        newest = max(earlier, key=lambda a: (a.created_at, a.id))
        return len(earlier) + 1, newest.id


# --- helpers ---------------------------------------------------------------------------------
def _json(key: Mapping[str, object]) -> Mapping[str, JsonValue]:
    return cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str)))


def _decisions(
    command: AnalyzeReframingCommand, previous: ReframingAnalysis | None
) -> dict[str, tuple[str, str]]:
    chosen = {d.segment_key: (d.decision.value, d.note) for d in command.decisions}
    if command.carry_decisions and previous is not None:
        for r in previous.recommendations:
            if r.decision is not None:
                chosen.setdefault(r.segment_key, (r.decision.value, r.decision_note))
    return chosen


def _stages(
    sources: list[SourceFacts],
    story: StoryFacts | None,
    continuity: ContinuityFacts | None,
    command: AnalyzeReframingCommand,
) -> tuple[StageReport, ...]:
    def stage(name: str, present: bool, reason: str) -> StageReport:
        return StageReport(
            stage=name,
            state=StageState.OK if present else StageState.NOT_AVAILABLE,
            detail="" if present else reason,
        )

    return (
        stage("picture", bool(sources), "no picture analysis"),
        stage(
            "inspection",
            all(s.geometry.exact for s in sources),
            "no stored inspection: the stored size and pixel shape are assumed",
        ),
        stage(
            "alignment",
            all(s.corrected for s in sources),
            "no alignment: the picture clock is taken as the container clock",
        ),
        stage(
            "story", story is not None, "no story plan: no beat or visual need informed the crops"
        ),
        stage(
            "continuity_analysis",
            continuity is not None,
            "no continuity analysis: neighbouring shots are paired by recording order",
        ),
        StageReport(
            stage="safe_areas",
            state=StageState.OK if command.config.safe_areas else StageState.SKIPPED,
            detail="" if command.config.safe_areas else "no safe area configured: none was assumed",
        ),
    )


def _read[T](
    supplied: Mapping[str, T],
    asset_id: str,
    latest: Callable[[str], Result[tuple[T, MediaAssetDto] | None, MediaHouseError]],
    kind: str,
    records: list[InputRecord],
) -> T | None:
    """The result the caller supplied, else the stored one, else ``None`` (recorded as such)."""
    if asset_id in supplied:
        records.append(InputRecord(kind=kind, how=InputHow.SUPPLIED, asset_id=asset_id))
        return supplied[asset_id]
    stored = latest(asset_id)
    if isinstance(stored, Ok) and stored.value is not None:
        records.append(
            InputRecord(
                kind=kind, how=InputHow.REUSED, asset_id=asset_id, identity=stored.value[1].id
            )
        )
        return stored.value[0]
    detail = (
        f"no stored {kind.replace('_', ' ')}"
        if isinstance(stored, Ok)
        else f"the library could not be read: {stored.error.user_message}"
    )
    records.append(
        InputRecord(kind=kind, how=InputHow.NOT_AVAILABLE, asset_id=asset_id, detail=detail)
    )
    return None


__all__ = [
    "AnalyzeReframing",
    "AnalyzeReframingCommand",
    "AnalyzeReframingError",
    "ReframingResult",
    "SegmentDecision",
]
