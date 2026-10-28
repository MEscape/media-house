"""Use case: stored analyses (and the story plan, when there is one) -> one pacing analysis.

Nothing is transcribed, analysed, aligned, probed or planned here. Every input is READ through its
owner's catalog, or used as supplied: audio timeline, content analysis, video analysis, alignment,
the stored inspection and the story plan. A missing optional input makes the analysis less specific
(an unresolved issue, a level marked unavailable), never invented. The story plan is never
requested: pacing reads the plan that exists and says narrative analysis is limited when none does.

The analysis is one derived library document of the first recording, keyed by the identity of every
input actually used, the configuration and the versions. A changed key makes a NEW analysis that
records ``analysis_version`` and ``supersedes``; earlier ones are never changed or deleted.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.contracts import (
    AudioCatalog,
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentCatalog,
    AlignmentDocument,
)
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalysis,
    ContentCatalog,
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
from media_house.modules.pacing_intelligence.application.results import PacingResults
from media_house.modules.pacing_intelligence.application.views import (
    alignment_facts,
    audio_facts,
    content_facts,
    source_facts,
    story_facts,
    video_facts,
)
from media_house.modules.pacing_intelligence.domain.engine import PlanMeta, analyse_pacing
from media_house.modules.pacing_intelligence.domain.errors import (
    InvalidPacingAnalysis,
    InvalidPacingConfig,
    MissingEvidence,
    NothingToAnalyse,
    PacingIntelligenceError,
)
from media_house.modules.pacing_intelligence.domain.facts import AssetEvidence, StoryFacts
from media_house.modules.pacing_intelligence.domain.model import (
    InputRecord,
    PacingAnalysis,
    StoryRef,
)
from media_house.modules.pacing_intelligence.domain.profile import PacingConfig
from media_house.modules.pacing_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.pacing_intelligence.domain.values import (
    ANALYZER_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    InputHow,
    Severity,
)
from media_house.modules.story_architecture.application.contracts import StoryCatalog, StoryPlan
from media_house.modules.video_intelligence.application.contracts import (
    VideoAnalysis,
    VideoCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type AnalyzePacingError = MediaError | PacingIntelligenceError | MediaHouseError


@dataclass(frozen=True, slots=True)
class AnalyzePacingCommand:
    #: The recordings, in story order. Needed unless a story plan is supplied.
    asset_ids: tuple[str, ...] = ()
    config: PacingConfig = field(default_factory=PacingConfig)
    #: Which story format's stored plan to read (``None`` = the newest for the recordings).
    story_format: str | None = None
    #: A story plan the caller holds (used as given).
    story_plan: StoryPlan | None = None
    #: Read a stored story plan when none is supplied. Never requested from its owner.
    use_story: bool = True
    project_id: str | None = None
    use_video: bool = True
    use_alignment: bool = True
    #: Results the caller already holds (used as given, nothing is looked up for them).
    content_analyses: Mapping[str, ContentAnalysis] = field(default_factory=dict)
    audio_timelines: Mapping[str, AudioIntelligenceTimeline] = field(default_factory=dict)
    video_analyses: Mapping[str, VideoAnalysis] = field(default_factory=dict)
    alignments: Mapping[str, AlignmentDocument] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PacingResult:
    analysis: PacingAnalysis
    #: The stored document (a derived asset of the first recording).
    asset: MediaAssetDto
    #: ``False`` when the identical analysis already existed and nothing was computed.
    created: bool


@dataclass(slots=True)
class _Gathered:
    evidence: dict[str, AssetEvidence] = field(default_factory=dict)
    records: list[InputRecord] = field(default_factory=list)
    identities: dict[str, JsonValue] = field(default_factory=dict)
    upstream: list[tuple[str, str]] = field(default_factory=list)


class AnalyzePacing:
    def __init__(
        self,
        library: MediaLibrary,
        stories: StoryCatalog,
        content: ContentCatalog,
        audio: AudioCatalog,
        video: VideoCatalog,
        alignments: AlignmentCatalog,
        inspections: InspectionCatalog,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._stories = stories
        self._content = content
        self._audio = audio
        self._video = video
        self._alignments = alignments
        self._inspections = inspections
        self._clock = clock
        self._documents = DerivedDocuments(library, paths)
        self._results = PacingResults(library)

    def execute(
        self, command: AnalyzePacingCommand, ctx: JobContext
    ) -> Result[PacingResult, AnalyzePacingError]:
        if command.story_plan is None and not command.asset_ids:
            return Err(
                ValidationError(
                    "asset_ids or story_plan needed",
                    field="asset_ids",
                    user_message="Give the recordings to analyse, or the story plan itself.",
                )
            )
        if len(set(command.asset_ids)) != len(command.asset_ids):
            return Err(
                ValidationError(
                    "asset_ids must be unique",
                    field="asset_ids",
                    user_message="Give each recording once.",
                )
            )
        ctx.progress.report(1, _STEPS, "Reading the story plan")
        story = self._story(command)
        order = tuple(a.asset_id for a in story[0].source_assets) if story else command.asset_ids
        try:
            profile = command.config.resolve(story[0].target_format if story else None)
        except InvalidPacingConfig as exc:
            return Err(exc)

        ctx.progress.report(2, _STEPS, "Reading the stored analyses")
        gathered = self._gather(command, order)
        if isinstance(gathered, Err):
            return gathered
        if story is not None:
            gathered.value.records.insert(0, story[2])
        facts = story_facts(story[0], story[1]) if story else None
        key = self._cache_key(command, facts, gathered.value)
        anchor = order[0]
        spec = DocumentSpec(RESULT_OPERATION, ANALYZER_VERSION, key)
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, analysis_from_json)
        if cached is not None:
            _log.info("Pacing analysis reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(PacingResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(3, _STEPS, "Analysing the pacing")
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        version, supersedes = self._history(anchor, profile.name, order)
        digest = _digest(key)
        meta = PlanMeta(
            pacing_analysis_id=f"pa_{digest[:16]}",
            analysis_version=version,
            supersedes=supersedes,
            project_id=command.project_id,
            created_at=self._clock.now(),
            configuration_fingerprint=digest,
            story=None
            if story is None
            else StoryRef(
                story_plan_id=story[0].story_plan_id,
                plan_version=story[0].plan_version,
                stored_as=story[1],
                target_format=story[0].target_format,
            ),
            inputs=tuple(gathered.value.records),
            warnings=tuple(story[0].warnings) if story else (),
            upstream=tuple(gathered.value.upstream),
        )
        try:
            result = analyse_pacing(facts, gathered.value.evidence, order, command.config, meta)
        except NothingToAnalyse as exc:
            return Err(exc)
        errors = [i.detail for i in result.issues_of(Severity.ERROR)]
        if errors:
            _log.error("Pacing analysis rejected its own result", problems=errors[:5])
            return Err(InvalidPacingAnalysis(errors))

        ctx.progress.report(_STEPS - 1, _STEPS, "Saving")
        stored = self._documents.store(
            anchor_asset.value,
            analysis_to_json(result),
            filename="pacing_analysis.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (pacing)",
            metadata={
                "profile": profile.name,
                "strategy": command.config.strategy.value,
                "assets": ",".join(order),
                "story_plan_id": story[0].story_plan_id if story else "",
                "analysis_version": result.analysis_version,
                "recommendations": len(result.recommendations),
                "state": result.state.value,
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Pacing analysis created",
            asset_id=stored.value.id,
            passages=len(result.passages),
            recommendations=len(result.recommendations),
            analysis_version=result.analysis_version,
        )
        return Ok(PacingResult(result, stored.value, created=True))

    # --- the story (optional) ----------------------------------------------------------------
    def _story(
        self, command: AnalyzePacingCommand
    ) -> tuple[StoryPlan, str | None, InputRecord] | None:
        if command.story_plan is not None:
            plan = command.story_plan
            return (
                plan,
                None,
                InputRecord(kind="story_plan", how=InputHow.SUPPLIED, identity=plan.story_plan_id),
            )
        if not command.use_story or not command.asset_ids:
            return None
        for dto in self._stories.history(command.asset_ids[0]):
            loaded = self._stories.load(dto.id)
            if isinstance(loaded, Err):
                continue
            plan = loaded.value
            if tuple(a.asset_id for a in plan.source_assets) != command.asset_ids:
                continue
            if command.story_format is not None and plan.target_format != command.story_format:
                continue
            if not plan.sections:
                continue
            return (
                plan,
                dto.id,
                InputRecord(
                    kind="story_plan",
                    how=InputHow.REUSED,
                    identity=plan.story_plan_id,
                    detail=dto.id,
                ),
            )
        return None

    # --- the evidence ------------------------------------------------------------------------
    def _gather(
        self, command: AnalyzePacingCommand, order: tuple[str, ...]
    ) -> Result[_Gathered, AnalyzePacingError]:
        out = _Gathered()
        for asset_id in order:
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
            audio = self._read(
                command.audio_timelines, asset_id, self._audio.latest, "audio_analysis", out
            )
            content = self._read(
                command.content_analyses, asset_id, self._content.latest, "content_analysis", out
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
            if audio is None and video is None:
                return Err(MissingEvidence(asset_id))
            fallback = (
                audio.duration
                if audio is not None
                else content.source.duration
                if content is not None
                else None
            )
            source = source_facts(found.value, inspection, fallback)
            correction = alignment.video_correction if alignment is not None else 0.0
            evidence = AssetEvidence(
                source=source,
                audio=audio_facts(audio, asset_id) if audio is not None else None,
                content=content_facts(content, asset_id) if content is not None else None,
                video=video_facts(video, asset_id, correction) if video is not None else None,
                alignment=alignment_facts(alignment) if alignment is not None else None,
            )
            out.evidence[asset_id] = evidence
            identities: dict[str, str | None] = {
                "source": source.identity,
                "audio": evidence.audio.identity if evidence.audio else None,
                "content": evidence.content.identity if evidence.content else None,
                "video": evidence.video.identity if evidence.video else None,
                "alignment": evidence.alignment.identity if evidence.alignment else None,
            }
            out.identities[asset_id] = dict(identities)
            out.upstream += [
                (f"{asset_id}:{kind}", ident) for kind, ident in identities.items() if ident
            ]
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

    # --- identity, history -------------------------------------------------------------------
    @staticmethod
    def _cache_key(
        command: AnalyzePacingCommand,
        story: StoryFacts | None,
        gathered: _Gathered,
    ) -> Mapping[str, JsonValue]:
        """Everything that can change the analysis and nothing that cannot (not time or job)."""
        profile = command.config.resolve(story.target_format if story else None)
        return {
            "story": None
            if story is None
            else {
                "id": story.story_plan_id,
                "version": story.plan_version,
                "facts": _digest(asdict(replace(story, stored_as=None))),
            },
            "inputs": dict(gathered.identities),
            "project": command.project_id,
            "config": json.loads(json.dumps(command.config.fingerprint(profile))),
            "versions": {
                "analyzer": ANALYZER_VERSION,
                "schema": SCHEMA_VERSION,
                "vocabulary": VOCABULARY_VERSION,
            },
        }

    def _history(
        self, anchor: str, profile: str, assets: tuple[str, ...]
    ) -> tuple[int, str | None]:
        """1 for the first analysis of these recordings and profile; later ones count up and name
        their predecessor. Earlier analyses are never changed or removed."""
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


__all__ = ["AnalyzePacing", "AnalyzePacingCommand", "AnalyzePacingError", "PacingResult"]
