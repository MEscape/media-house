"""Use case: the stored analyses of the passages' recordings + the whole library's footage -> one
B-roll analysis.

Nothing is transcribed, analysed, aligned, probed or planned here. The passages come from the story
plan, the content analysis, the pacing analysis and the recording's own picture (all READ through
their owners' catalogs, or used as supplied); the footage comes from the library's shot indexes
(``video_intelligence``), searched in stages (words, picture embeddings, descriptions) before any
shot is judged in detail. A missing optional input makes the analysis less specific (a stage marked
unavailable, a narrower pool), never invented.

The analysis is one derived library document of the first recording, keyed by the identity of every
input that decides it: the passages, the searched footage, the encoders, the configuration and
the versions. A changed key makes a NEW analysis with ``analysis_version`` and ``supersedes``;
earlier ones are never changed or deleted.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.contracts import (
    AudioCatalog,
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentCatalog,
    AlignmentDocument,
)
from media_house.modules.broll_intelligence.application.pool import (
    LibraryPool,
    LibraryScope,
    PoolBuild,
)
from media_house.modules.broll_intelligence.application.results import BrollResults
from media_house.modules.broll_intelligence.application.retrieval import Retrieved, Retriever
from media_house.modules.broll_intelligence.application.views import (
    TargetBuilder,
    alignment_parts,
    audio_fact,
)
from media_house.modules.broll_intelligence.domain.engine import AnalysisMeta, analyse_broll
from media_house.modules.broll_intelligence.domain.errors import (
    BrollIntelligenceError,
    InvalidBrollAnalysis,
    InvalidBrollConfig,
    MissingTargetEvidence,
    NothingToCover,
)
from media_house.modules.broll_intelligence.domain.facts import (
    ShotFact,
    SoundActionFact,
    TargetFact,
)
from media_house.modules.broll_intelligence.domain.model import (
    BrollAnalysis,
    InputRecord,
    StageReport,
    StoryRef,
)
from media_house.modules.broll_intelligence.domain.profile import BrollConfig
from media_house.modules.broll_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.broll_intelligence.domain.values import (
    ANALYZER_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    ClockBasis,
    InputHow,
    Severity,
    StageState,
)
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalysis,
    ContentCatalog,
)
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
from media_house.modules.story_architecture.application.contracts import StoryCatalog, StoryPlan
from media_house.modules.video_intelligence.application.contracts import (
    IndexedAsset,
    VideoAnalysis,
    VideoCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type AnalyzeBrollError = MediaError | BrollIntelligenceError | MediaHouseError

#: Candidate recordings whose own sound and alignment are read for the shortlisted shots.
MAX_ENRICHED_ASSETS = 60
_STEPS = 6


@dataclass(frozen=True, slots=True)
class AnalyzeBrollCommand:
    #: The recordings whose passages need footage, in story order (or give a story plan).
    asset_ids: tuple[str, ...] = ()
    config: BrollConfig = field(default_factory=BrollConfig)
    #: Which footage of the library may be searched.
    scope: LibraryScope = field(default_factory=LibraryScope)
    #: Which story format's stored plan to read (``None`` = the newest for the recordings).
    story_format: str | None = None
    #: A story plan the caller holds (used as given).
    story_plan: StoryPlan | None = None
    #: Read a stored story plan when none is supplied. Never requested from its owner.
    use_story: bool = True
    project_id: str | None = None
    use_video: bool = True
    use_alignment: bool = True
    use_pacing: bool = True
    #: Results the caller already holds (used as given, nothing is looked up for them).
    content_analyses: Mapping[str, ContentAnalysis] = field(default_factory=dict)
    audio_timelines: Mapping[str, AudioIntelligenceTimeline] = field(default_factory=dict)
    video_analyses: Mapping[str, VideoAnalysis] = field(default_factory=dict)
    alignments: Mapping[str, AlignmentDocument] = field(default_factory=dict)
    pacing: PacingAnalysis | None = None
    #: Shot indexes the caller already holds, by library asset id (searched instead of the stored).
    shot_indexes: Mapping[str, IndexedAsset] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BrollResult:
    analysis: BrollAnalysis
    #: The stored document (a derived asset of the first recording).
    asset: MediaAssetDto
    #: ``False`` when the identical analysis already existed and nothing was computed.
    created: bool


@dataclass(slots=True)
class _Gathered:
    targets: list[TargetFact] = field(default_factory=list)
    records: list[InputRecord] = field(default_factory=list)
    identities: dict[str, JsonValue] = field(default_factory=dict)


class AnalyzeBroll:
    def __init__(
        self,
        library: MediaLibrary,
        stories: StoryCatalog,
        content: ContentCatalog,
        audio: AudioCatalog,
        video: VideoCatalog,
        alignments: AlignmentCatalog,
        pacing: PacingCatalog,
        pool: LibraryPool,
        retriever: Retriever,
        encoder_identity: Callable[[], str | None],
        text_identity: Callable[[], str | None],
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._stories = stories
        self._content = content
        self._audio = audio
        self._video = video
        self._alignments = alignments
        self._pacing = pacing
        self._pool = pool
        self._retriever = retriever
        self._encoder_identity = encoder_identity
        self._text_identity = text_identity
        self._clock = clock
        self._documents = DerivedDocuments(library, paths)
        self._results = BrollResults(library)

    def execute(
        self, command: AnalyzeBrollCommand, ctx: JobContext
    ) -> Result[BrollResult, AnalyzeBrollError]:
        if command.story_plan is None and not command.asset_ids:
            return Err(
                ValidationError(
                    "asset_ids or story_plan needed",
                    field="asset_ids",
                    user_message="Give the recordings that need footage, or the story plan itself.",
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
        except InvalidBrollConfig as exc:
            return Err(exc)

        ctx.progress.report(2, _STEPS, "Finding the passages that need footage")
        gathered = self._gather(command, order, story[0] if story else None)
        if isinstance(gathered, Err):
            return gathered
        if story is not None:
            gathered.value.records.insert(0, story[2])
        targets = gathered.value.targets
        if not targets:
            return Err(NothingToCover("no passage asks for footage"))

        ctx.progress.report(3, _STEPS, "Listing the footage of the library")
        encoder = self._encoder_identity()
        pool = self._pool.build(command.scope, frozenset(order), command.shot_indexes, encoder)
        key = self._cache_key(command, story, gathered.value, pool, encoder)
        anchor = order[0]
        spec = DocumentSpec(RESULT_OPERATION, ANALYZER_VERSION, key)
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, analysis_from_json)
        if cached is not None:
            _log.info("B-roll analysis reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(BrollResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(4, _STEPS, "Searching the footage")
        retrieved = self._retriever.run(targets, pool, profile, ctx.cancellation)
        shortlist = self._enrich(retrieved.shortlist, command)

        ctx.raise_if_cancelled()
        ctx.progress.report(5, _STEPS, "Ranking the candidates")
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        version, supersedes = self._history(anchor, profile.name, order)
        digest = _digest(key)
        meta = AnalysisMeta(
            broll_analysis_id=f"ba_{digest[:16]}",
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
            story_format=story[0].target_format if story else None,
            sources=order,
            inputs=(*gathered.value.records, *pool.records),
            stages=self._stages(pool, retrieved),
            warnings=tuple(story[0].warnings) if story else (),
            pool_identity=pool.identity,
        )
        result = analyse_broll(
            targets,
            shortlist,
            retrieved.facts,
            retrieved.lexical,
            pool.fact,
            pool.identity,
            command.config,
            meta,
        )
        errors = [i.detail for i in result.issues_of(Severity.ERROR)]
        if errors:
            _log.error("B-roll analysis rejected its own result", problems=errors[:5])
            return Err(InvalidBrollAnalysis(errors))

        ctx.progress.report(_STEPS, _STEPS, "Saving")
        stored = self._documents.store(
            anchor_asset.value,
            analysis_to_json(result),
            filename="broll_analysis.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (b-roll)",
            metadata={
                "profile": profile.name,
                "assets": ",".join(order),
                "story_plan_id": story[0].story_plan_id if story else "",
                "analysis_version": result.analysis_version,
                "recommendations": len(result.recommendations),
                "gaps": len(result.gaps),
                "state": result.state.value,
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "B-roll analysis created",
            asset_id=stored.value.id,
            passages=len(targets),
            recommendations=len(result.recommendations),
            gaps=len(result.gaps),
            pool_assets=pool.fact.assets,
            pool_shots=pool.fact.shots,
            analysis_version=result.analysis_version,
        )
        return Ok(BrollResult(result, stored.value, created=True))

    # --- the story (optional) ----------------------------------------------------------------
    def _story(
        self, command: AnalyzeBrollCommand
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

    # --- the passages ------------------------------------------------------------------------
    def _gather(
        self, command: AnalyzeBrollCommand, order: tuple[str, ...], story: StoryPlan | None
    ) -> Result[_Gathered, AnalyzeBrollError]:
        out = _Gathered()
        pacing = self._pacing_of(command, order, story, out)
        for asset_id in order:
            found = self._library.get(asset_id)
            if isinstance(found, Err):
                return found
            content = self._read(
                command.content_analyses, asset_id, self._content.latest, "content_analysis", out
            )
            if content is None and story is None:
                return Err(MissingTargetEvidence(asset_id))
            audio = self._read(
                command.audio_timelines, asset_id, self._audio.latest, "audio_analysis", out
            )
            video = (
                self._read(
                    command.video_analyses, asset_id, self._video.latest, "video_analysis", out
                )
                if command.use_video
                else None
            )
            alignment = (
                self._read(command.alignments, asset_id, self._alignments.latest, "alignment", out)
                if command.use_alignment
                else None
            )
            correction = alignment.video_correction if alignment is not None else 0.0
            out.targets += TargetBuilder(
                asset_id, content, story, video, audio, alignment, pacing, correction
            ).build()
            out.identities[asset_id] = {
                "content": content.analysis_id if content else None,
                "audio": f"{audio.duration}/{len(audio.words)}" if audio else None,
                "video": f"{video.asset_checksum}/{video.provenance.profile_name}"
                if video
                else None,
                "alignment": alignment.alignment_id if alignment else None,
            }
        return Ok(out)

    def _pacing_of(
        self,
        command: AnalyzeBrollCommand,
        order: tuple[str, ...],
        story: StoryPlan | None,
        out: _Gathered,
    ) -> PacingAnalysis | None:
        found: PacingAnalysis | None = None
        how, detail = InputHow.NOT_AVAILABLE, "no pacing analysis (no visual-change places)"
        if command.pacing is not None:
            found, how, detail = command.pacing, InputHow.SUPPLIED, ""
        elif command.use_pacing:
            stored = self._pacing.latest(
                order[0], story_plan_id=story.story_plan_id if story else None
            )
            if isinstance(stored, Ok) and stored.value is not None:
                found, how, detail = stored.value[0], InputHow.REUSED, stored.value[1].id
        out.records.append(
            InputRecord(
                kind="pacing_analysis",
                how=how,
                identity=None if found is None else found.pacing_analysis_id,
                detail=detail,
            )
        )
        out.identities["pacing"] = None if found is None else found.pacing_analysis_id
        return found

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

    # --- the footage's own sound and clock ----------------------------------------------------
    def _enrich(self, shortlist: list[ShotFact], command: AnalyzeBrollCommand) -> list[ShotFact]:
        """Add what only the shortlisted recordings need: their clock correction, their sound."""
        assets = list(dict.fromkeys(s.asset_id for s in shortlist))[:MAX_ENRICHED_ASSETS]
        analysed = {s.asset_id: s.analyzed_asset_id for s in shortlist}
        parts: dict[str, tuple[Fraction, tuple[SoundActionFact, ...], bool | None] | None] = {}
        audio: dict[str, AudioIntelligenceTimeline | None] = {}
        for asset_id in assets:
            alignment = self._first(
                [analysed[asset_id], asset_id], command.alignments, self._alignments.latest
            )
            parts[asset_id] = alignment_parts(alignment) if alignment is not None else None
            audio[asset_id] = self._first([asset_id], command.audio_timelines, self._audio.latest)
        enriched = []
        for shot in shortlist:
            if shot.asset_id not in parts:
                enriched.append(shot)
                continue
            aligned = parts[shot.asset_id]
            timeline = audio[shot.asset_id]
            correction = aligned[0] if aligned else Fraction(0)
            start, end = shot.start + correction, shot.end + correction
            enriched.append(
                replace(
                    shot,
                    start=start,
                    end=end,
                    clock=ClockBasis.CONTAINER if aligned else ClockBasis.VIDEO_ANALYSIS,
                    events=tuple(
                        replace(
                            e,
                            start=e.start + correction,
                            end=None if e.end is None else e.end + correction,
                        )
                        for e in shot.events
                    ),
                    audio=audio_fact(timeline) if timeline is not None else None,
                    sound_actions=tuple(
                        a for a in (aligned[1] if aligned else ()) if start <= a.video_time < end
                    ),
                    sync_established=aligned[2] if aligned else None,
                )
            )
        return enriched

    @staticmethod
    def _first[T](
        ids: list[str],
        supplied: Mapping[str, T],
        latest: Callable[[str], Result[tuple[T, MediaAssetDto] | None, MediaHouseError]],
    ) -> T | None:
        for asset_id in dict.fromkeys(ids):
            if asset_id in supplied:
                return supplied[asset_id]
            stored = latest(asset_id)
            if isinstance(stored, Ok) and stored.value is not None:
                return stored.value[0]
        return None

    # --- stages, identity, history -----------------------------------------------------------
    @staticmethod
    def _stages(pool: PoolBuild, retrieved: Retrieved) -> tuple[StageReport, ...]:
        skipped = len(pool.fact.skipped)
        limits = "; ".join(pool.fact.limits)
        out = [
            StageReport(stage="targets", state=StageState.OK),
            StageReport(
                stage="pool",
                state=StageState.PARTIAL if skipped or limits else StageState.OK,
                reason=f"{skipped} recording(s) could not be searched" if skipped else limits,
            ),
            StageReport(stage="words", state=StageState.OK),
        ]
        for stage, prefix in (
            ("pictures", "visual_unavailable"),
            ("descriptions", "text_unavailable"),
        ):
            note = next((n for n in retrieved.notes if n.startswith(prefix)), None)
            out.append(
                StageReport(
                    stage=stage,
                    state=StageState.NOT_AVAILABLE if note else StageState.OK,
                    reason=note.split(": ", 1)[1] if note else "",
                )
            )
        return tuple(out)

    def _cache_key(
        self,
        command: AnalyzeBrollCommand,
        story: tuple[StoryPlan, str | None, InputRecord] | None,
        gathered: _Gathered,
        pool: PoolBuild,
        encoder: str | None,
    ) -> Mapping[str, JsonValue]:
        """Everything that can change the analysis and nothing that cannot (not time or job)."""
        profile = command.config.resolve(story[0].target_format if story else None)
        scope = command.scope
        return {
            "story": None
            if story is None
            else {"id": story[0].story_plan_id, "version": story[0].plan_version},
            "targets": _digest({"t": [asdict(t) for t in gathered.targets]}),
            "inputs": dict(gathered.identities),
            "pool": pool.identity,
            "encoders": {
                "pictures": encoder if pool.embedding_model else None,
                "descriptions": self._text_identity(),
            },
            "scope": {
                "assets": list(scope.asset_ids),
                "groups": sorted(scope.group_ids),
                "tags": sorted(scope.tags),
                "exclude": sorted(scope.exclude),
                "max_assets": scope.max_assets,
                "targets": scope.include_targets,
            },
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


def _digest(key: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()


__all__ = [
    "AnalyzeBroll",
    "AnalyzeBrollCommand",
    "AnalyzeBrollError",
    "BrollResult",
    "LibraryScope",
]
