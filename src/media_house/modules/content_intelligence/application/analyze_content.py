"""Use case: a recording (or a supplied transcript) -> its ``ContentAnalysis``.

Resolve -> reuse -> request -> degrade, as everywhere in the pipeline:

* The transcript comes from the caller, else from Audio Intelligence through its public contract
  (which reuses a stored transcript and only transcribes when none exists).
* Acoustic cues and a visual analysis are OPTIONAL. Supplied ones are used as given; otherwise
  they are requested from their owners only when the command asks for them. An owner that cannot
  deliver degrades to an explicit ``not_available`` input, never to a failure.
* The finished analysis and every language-model reply are derived Media Library documents, found
  by fingerprints that include the transcript, the settings, the similarity method, the model and
  the prompt version. A changed transcript, setting or engine therefore can never return a stale
  result, while an unchanged passage of a changed transcript reuses its model reply.

The module analyses and recommends. It never modifies media and never edits anything.
"""

import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import StrEnum

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.contracts import (
    AnalyzeAudioCommand,
    AudioAnalyzer,
    AudioEngine,
    AudioIntelligenceTimeline,
    TranscribeAudioCommand,
    TranscribeError,
)
from media_house.modules.content_intelligence.application.inputs import (
    context_from_video,
    cues_from_timeline,
    source_from_transcript,
)
from media_house.modules.content_intelligence.application.ports import (
    LanguageModel,
    SemanticEngine,
)
from media_house.modules.content_intelligence.domain.config import (
    ContentConfig,
    Json,
    LlmScope,
    LlmSettings,
)
from media_house.modules.content_intelligence.domain.context import AudioCues, VisualContext
from media_house.modules.content_intelligence.domain.engine import (
    AnalysisMeta,
    analyze,
    derive_state,
    prepare,
)
from media_house.modules.content_intelligence.domain.errors import (
    ContentIntelligenceError,
    InvalidAnalysisDocument,
    TranscriptUnavailable,
)
from media_house.modules.content_intelligence.domain.model import (
    ContentAnalysis,
    InputRecord,
    Provenance,
    SourceRecord,
    StageReport,
)
from media_house.modules.content_intelligence.domain.refine import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    Chunk,
    Refinement,
    build_prompt,
    merge,
    parse_response,
    plan_chunks,
)
from media_house.modules.content_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.content_intelligence.domain.similarity import (
    LEXICAL,
    SimilarityProvider,
)
from media_house.modules.content_intelligence.domain.source import SourceTranscript
from media_house.modules.content_intelligence.domain.validation import validate_analysis
from media_house.modules.content_intelligence.domain.values import (
    EMBEDDINGS_OPERATION,
    LLM_CHUNK_OPERATION,
    PROCESSING_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    CacheOutcome,
    ResultState,
    Role,
    StageState,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzeVideoCommand,
    VideoAnalysis,
    VideoAnalyzer,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    Err,
    ExternalSystemError,
    Ok,
    Result,
    ValidationError,
)
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type AnalyzeContentError = MediaError | ContentIntelligenceError | TranscribeError

_STEPS = 6
_CHUNK_DOCUMENT = "content_intelligence.llm_chunk"
_VECTOR_DOCUMENT = "content_intelligence.embeddings"
_VECTOR_DECIMALS = 5
_DURATION_SLACK = 1.0


class AudioUse(StrEnum):
    #: Only the transcript (the owner reuses a stored one, or transcribes if none exists).
    TRANSCRIPT = "transcript"
    #: Also the acoustic timeline, for delivery cues (emphasis, laughter). Heavier.
    ACOUSTIC = "acoustic"


class VideoUse(StrEnum):
    OFF = "off"
    #: Ask the Video Intelligence owner for an analysis (it reuses what it stored).
    REQUEST = "request"


class CacheLayer(StrEnum):
    """What ``refresh`` makes a run recompute instead of reusing."""

    RESULT = "result"
    EMBEDDINGS = "embeddings"
    LLM = "llm"


@dataclass(frozen=True, slots=True)
class AnalyzeContentCommand:
    #: The library asset to analyse; optional when ``transcript`` is supplied (no storage then).
    source_asset_id: str | None = None
    #: A transcript supplied by the caller (validated; recorded as caller-provided).
    transcript: SourceTranscript | None = None
    config: ContentConfig = field(default_factory=ContentConfig)
    audio: AudioUse = AudioUse.TRANSCRIPT
    video: VideoUse = VideoUse.OFF
    video_profile: str = "fast"
    #: Already-computed upstream results handed over by an orchestrator (used as given).
    audio_timeline: AudioIntelligenceTimeline | None = None
    video_analysis: VideoAnalysis | None = None
    #: Analyse only sentences overlapping ``[start, end]`` seconds.
    time_range: tuple[float, float] | None = None
    refresh: frozenset[CacheLayer] = frozenset()


@dataclass(frozen=True, slots=True)
class ContentAnalysisResult:
    analysis: ContentAnalysis
    #: The stored document (a derived asset of the source); ``None`` when nothing was stored.
    asset: MediaAssetDto | None
    #: ``False`` when the identical result already existed and nothing was computed.
    created: bool


@dataclass(slots=True)
class _Inputs:
    source: SourceTranscript
    records: list[InputRecord]
    visual: VisualContext | None = None
    audio: AudioCues | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Reply:
    """A language-model reply: its text, or why there is none."""

    text: str | None
    cached: bool
    failure: str | None


@dataclass(slots=True)
class _Usage:
    seconds: dict[str, float] = field(default_factory=dict)
    counters: Counter[str] = field(default_factory=Counter)

    def flat(self) -> dict[str, float]:
        return {
            **{f"seconds.{k}": round(v, 3) for k, v in sorted(self.seconds.items())},
            **{k: float(v) for k, v in sorted(self.counters.items())},
        }


class AnalyzeContent:
    def __init__(
        self,
        library: MediaLibrary,
        paths: AppPaths,
        clock: Clock,
        audio: AudioEngine | None = None,
        acoustics: AudioAnalyzer | None = None,
        video: VideoAnalyzer | None = None,
        semantic: SemanticEngine | None = None,
        language_model: LanguageModel | None = None,
    ) -> None:
        self._library = library
        self._clock = clock
        self._audio = audio
        self._acoustics = acoustics
        self._video = video
        self._semantic = semantic
        self._llm = language_model
        self._documents = DerivedDocuments(library, paths)

    # --- entry point -------------------------------------------------------------------------
    def execute(
        self, command: AnalyzeContentCommand, ctx: JobContext
    ) -> Result[ContentAnalysisResult, AnalyzeContentError]:
        started = time.perf_counter()
        usage = _Usage()
        try:
            command.config.validate()
        except ContentIntelligenceError as exc:
            return Err(exc)
        asset: MediaAssetDto | None = None
        if command.source_asset_id is not None:
            found = self._library.get(command.source_asset_id)
            if isinstance(found, Err):
                return found
            asset = found.value
            if asset.media_type not in {MediaType.AUDIO, MediaType.VIDEO}:
                return Err(
                    ValidationError(
                        f"Asset {asset.id} is {asset.media_type.value}, not audio or video",
                        field="source_asset_id",
                        user_message="Only audio and video files can be analysed for content.",
                    )
                )
        elif command.transcript is None:
            return Err(
                ValidationError(
                    "Neither an asset nor a transcript was given",
                    field="source_asset_id",
                    user_message="Choose a recording or provide a transcript to analyse.",
                )
            )

        ctx.raise_if_cancelled()
        ctx.progress.report(1, _STEPS, "Collecting transcript and observations")
        gathered = self._gather(command, asset, ctx)
        if isinstance(gathered, Err):
            return gathered
        inputs = gathered.value
        config = command.config

        try:
            plan = self._similarity_plan(config)
            llm_plan = self._llm_plan(config)
            spec_config = self._spec_config(command, inputs, plan, llm_plan)
        except ContentIntelligenceError as exc:
            return Err(exc)
        fingerprint = _digest(spec_config)
        spec = DocumentSpec(RESULT_OPERATION, PROCESSING_VERSION, spec_config)

        library_key: str | None = None
        if asset is not None:
            computed = self._documents.fingerprint(asset.id, spec)
            if isinstance(computed, Err):
                return computed
            library_key = computed.value
            if CacheLayer.RESULT not in command.refresh:
                cached = self._documents.load(asset.id, library_key, analysis_from_json)
                if cached is not None:
                    _log.info("Content analysis reused", asset_id=asset.id, cache_hit=True)
                    return Ok(ContentAnalysisResult(cached[0], cached[1], created=False))

        try:
            analysis = self._compute(
                command, inputs, plan, llm_plan, fingerprint, asset, ctx, usage
            )
        except ContentIntelligenceError as exc:
            _log.warning("Content analysis refused", reason=exc.code)
            return Err(exc)

        stored: MediaAssetDto | None = None
        if (
            asset is not None
            and library_key is not None
            and analysis.state is not ResultState.PARTIAL
        ):
            ctx.progress.report(6, _STEPS, "Saving analysis")
            saved = self._documents.store(
                asset,
                analysis_to_json(analysis),
                filename="content_analysis.json",
                spec=spec,
                display_name=f"{asset.display_name} (content analysis)",
                metadata={
                    "language": analysis.source.language,
                    "state": analysis.state.value,
                    "profile": config.profile.value,
                    "similarity": analysis.provenance.similarity,
                    "segment_count": len(analysis.segments),
                    "topic_count": len(analysis.topics),
                    "relationship_count": len(analysis.relationships),
                    "key_item_count": len(analysis.key_items),
                    "transcript_fingerprint": analysis.source.transcript_fingerprint,
                    "configuration_fingerprint": fingerprint,
                    "warning_count": len(analysis.warnings),
                },
            )
            if isinstance(saved, Err):
                return saved
            stored = saved.value
        _log.info(
            "Content analysis created",
            asset_id=asset.id if asset else None,
            cache_hit=False,
            state=analysis.state.value,
            segments=len(analysis.segments),
            topics=len(analysis.topics),
            relationships=len(analysis.relationships),
            similarity=analysis.provenance.similarity,
            processing_seconds=round(time.perf_counter() - started, 2),
            **{k: v for k, v in analysis.provenance.usage.items() if not k.startswith("seconds.")},
        )
        return Ok(ContentAnalysisResult(analysis, stored, created=True))

    # --- inputs ------------------------------------------------------------------------------
    def _gather(
        self, command: AnalyzeContentCommand, asset: MediaAssetDto | None, ctx: JobContext
    ) -> Result[_Inputs, AnalyzeContentError]:
        records: list[InputRecord] = []
        warnings: list[str] = []
        if command.transcript is not None:
            source = command.transcript
            records.append(
                InputRecord(
                    kind="transcript",
                    how="supplied",
                    asset_id=source.asset_id or (asset.id if asset else None),
                    fingerprint=source.fingerprint(),
                    detail="caller-provided transcript",
                )
            )
        else:
            if asset is None or self._audio is None:
                return Err(TranscriptUnavailable("no asset or no speech-transcript provider"))
            transcribed = self._audio.execute(TranscribeAudioCommand(asset.id), ctx)
            if isinstance(transcribed, Err):
                return transcribed
            result = transcribed.value
            source = source_from_transcript(result.transcript, transcript_asset_id=result.asset.id)
            records.append(
                InputRecord(
                    kind="transcript",
                    how="reused" if not result.created else "requested",
                    asset_id=result.asset.id,
                    fingerprint=source.fingerprint(),
                    detail="from Audio Intelligence",
                )
            )
        if command.time_range is not None:
            start, end = command.time_range
            if end <= start or start < 0:
                return Err(
                    ValidationError(
                        "The time range is empty or negative",
                        field="time_range",
                        user_message="Choose a time range that ends after it starts.",
                    )
                )
            source = _restrict(source, start, end)
            records.append(
                InputRecord(kind="range", how="supplied", detail=f"{start:.3f}-{end:.3f} seconds")
            )

        audio = self._acoustic_cues(command, asset, records, warnings, ctx)
        visual = self._visual(command, asset, records, warnings, ctx)
        if (
            visual
            and visual.duration
            and source.duration
            and abs(visual.duration - source.duration) > _DURATION_SLACK
        ):
            warnings.append(
                "the visual analysis and the transcript differ in duration by more than a "
                "second: temporal matching between them may be unreliable"
            )
        return Ok(_Inputs(source, records, visual, audio, warnings))

    def _acoustic_cues(
        self,
        command: AnalyzeContentCommand,
        asset: MediaAssetDto | None,
        records: list[InputRecord],
        warnings: list[str],
        ctx: JobContext,
    ) -> AudioCues | None:
        owner = command.source_asset_id or ""
        if command.audio_timeline is not None:
            cues = cues_from_timeline(command.audio_timeline, owner)
            records.append(
                InputRecord(
                    kind="audio_timeline",
                    how="supplied",
                    asset_id=owner or None,
                    fingerprint=cues.fingerprint,
                )
            )
            return cues
        if command.audio is not AudioUse.ACOUSTIC:
            records.append(
                InputRecord(kind="audio_timeline", how="not_used", detail="not requested")
            )
            return None
        if asset is None or self._acoustics is None:
            records.append(
                InputRecord(
                    kind="audio_timeline",
                    how="not_available",
                    detail="no asset or no acoustic provider",
                )
            )
            warnings.append("acoustic cues were requested but are not available")
            return None
        analysed = self._acoustics.execute(AnalyzeAudioCommand(asset.id), ctx)
        if isinstance(analysed, Err):
            records.append(
                InputRecord(
                    kind="audio_timeline",
                    how="not_available",
                    asset_id=asset.id,
                    detail=analysed.error.code,
                )
            )
            warnings.append("acoustic cues could not be obtained; delivery evidence is absent")
            return None
        cues = cues_from_timeline(analysed.value.timeline, asset.id)
        records.append(
            InputRecord(
                kind="audio_timeline",
                how="reused" if not analysed.value.created else "requested",
                asset_id=analysed.value.asset.id,
                fingerprint=cues.fingerprint,
            )
        )
        return cues

    def _visual(
        self,
        command: AnalyzeContentCommand,
        asset: MediaAssetDto | None,
        records: list[InputRecord],
        warnings: list[str],
        ctx: JobContext,
    ) -> VisualContext | None:
        if command.video_analysis is not None:
            context = context_from_video(command.video_analysis)
            records.append(
                InputRecord(
                    kind="video_analysis",
                    how="supplied",
                    asset_id=context.asset_id,
                    fingerprint=context.fingerprint,
                )
            )
            return context
        if command.video is not VideoUse.REQUEST:
            records.append(
                InputRecord(kind="video_analysis", how="not_used", detail="not requested")
            )
            return None
        if asset is None or asset.media_type is not MediaType.VIDEO or self._video is None:
            records.append(
                InputRecord(
                    kind="video_analysis",
                    how="not_available",
                    detail="not a video asset or no video provider",
                )
            )
            warnings.append("a visual analysis was requested but is not available")
            return None
        analysed = self._video.execute(
            AnalyzeVideoCommand(asset.id, profile=command.video_profile), ctx
        )
        if isinstance(analysed, Err):
            records.append(
                InputRecord(
                    kind="video_analysis",
                    how="not_available",
                    asset_id=asset.id,
                    detail=analysed.error.code,
                )
            )
            warnings.append("the visual analysis could not be obtained; visual evidence is absent")
            return None
        context = context_from_video(analysed.value.analysis)
        records.append(
            InputRecord(
                kind="video_analysis",
                how="reused" if not analysed.value.created else "requested",
                asset_id=analysed.value.asset.id,
                fingerprint=context.fingerprint,
            )
        )
        return context

    # --- plans -------------------------------------------------------------------------------
    def _similarity_plan(self, config: ContentConfig) -> tuple[str, str]:
        """``(identity, kind)`` of the similarity method this run will use."""
        wanted = config.settings.prefer_embeddings and config.embedding.model is not None
        if (
            wanted
            and self._semantic is not None
            and self._semantic.unavailable(config.embedding) is None
        ):
            ident = self._semantic.identity(config.embedding)
            return f"embedding:{ident.name}:{ident.version}:{ident.model}", "embedding"
        return "lexical-tfidf-v1", LEXICAL

    def _llm_plan(self, config: ContentConfig) -> dict[str, Json] | None:
        """The language model's identity if it will run, else ``None``."""
        if config.llm is None or config.settings.llm_scope is LlmScope.NONE:
            return None
        if self._llm is None or self._llm.unavailable(config.llm) is not None:
            return None
        ident = self._llm.identity(config.llm)
        return {
            "engine": ident.name,
            "version": ident.version,
            "model": ident.model,
            "prompt_version": PROMPT_VERSION,
            "temperature": config.llm.temperature,
            "seed": config.llm.seed,
            "max_output_tokens": config.llm.max_output_tokens,
            "max_chunk_chars": config.max_chunk_chars,
            "scope": config.settings.llm_scope.value,
        }

    def _spec_config(
        self,
        command: AnalyzeContentCommand,
        inputs: _Inputs,
        plan: tuple[str, str],
        llm_plan: dict[str, Json] | None,
    ) -> dict[str, Json]:
        language = command.config.language or inputs.source.language
        return {
            "transcript": inputs.source.fingerprint(),
            "language": language,
            "analysis": command.config.analysis_fingerprint(),
            "similarity": plan[0],
            "llm": llm_plan,
            "visual": inputs.visual.fingerprint if inputs.visual else None,
            "audio": inputs.audio.fingerprint if inputs.audio else None,
            "schema_version": SCHEMA_VERSION,
        }

    # --- computation -------------------------------------------------------------------------
    def _compute(
        self,
        command: AnalyzeContentCommand,
        inputs: _Inputs,
        plan: tuple[str, str],
        llm_plan: dict[str, Json] | None,
        fingerprint: str,
        asset: MediaAssetDto | None,
        ctx: JobContext,
        usage: _Usage,
    ) -> ContentAnalysis:
        config = command.config
        stages: list[StageReport] = []

        ctx.raise_if_cancelled()
        ctx.progress.report(2, _STEPS, "Reading sentences")
        t0 = time.perf_counter()
        prepared = prepare(inputs.source, config)
        usage.seconds["prepare"] = time.perf_counter() - t0
        stages.append(
            StageReport(
                stage="transcript",
                state=StageState.OK,
                seconds=round(usage.seconds["prepare"], 3),
                detail=f"{len(prepared.units)} sentences",
            )
        )

        ctx.raise_if_cancelled()
        ctx.progress.report(3, _STEPS, "Measuring similarity")
        similarity, device, similarity_stage = self._similarity(
            prepared.texts,
            prepared.lexical_similarity(),
            plan,
            config,
            asset,
            command.refresh,
            ctx,
            usage,
        )
        stages.append(similarity_stage)
        # The engine may have fallen back to word overlap: record what was really used.
        used = plan if similarity.kind == plan[1] else (similarity.identity, similarity.kind)

        ctx.raise_if_cancelled()
        ctx.progress.report(4, _STEPS, "Understanding the content")
        meta = AnalysisMeta(
            analysis_id=f"ci-{fingerprint[:16]}",
            asset_id=asset.id if asset else inputs.source.asset_id,
            timestamp=self._clock.now(),
            source=SourceRecord(
                origin=inputs.source.origin,
                transcript_asset_id=inputs.source.transcript_asset_id,
                transcript_fingerprint=inputs.source.fingerprint(),
                language=prepared.language,
                duration=inputs.source.duration,
                upstream_segments=len(inputs.source.segments),
            ),
            inputs=tuple(inputs.records),
            provenance=self._provenance(config, used, llm_plan, fingerprint, device, usage),
            stages=tuple(stages),
            warnings=tuple(inputs.warnings),
        )
        t0 = time.perf_counter()
        analysis = analyze(prepared, similarity, config, meta, inputs.visual, inputs.audio)
        usage.seconds["analyze"] = time.perf_counter() - t0
        validate_analysis(analysis)

        ctx.raise_if_cancelled()
        ctx.progress.report(5, _STEPS, "Refining with the language model")
        analysis = self._refine(analysis, command, llm_plan, asset, ctx, usage)
        provenance = self._provenance(config, used, llm_plan, fingerprint, device, usage)
        warnings = tuple(dict.fromkeys(analysis.warnings))
        return replace(
            analysis,
            provenance=provenance,
            warnings=warnings,
            state=derive_state(warnings, analysis.stages, empty=not analysis.units),
        )

    def _similarity(
        self,
        texts: list[str],
        lexical: SimilarityProvider,
        plan: tuple[str, str],
        config: ContentConfig,
        asset: MediaAssetDto | None,
        refresh: frozenset[CacheLayer],
        ctx: JobContext,
        usage: _Usage,
    ) -> tuple[SimilarityProvider, str, StageReport]:
        identity, kind = plan
        if kind == LEXICAL:
            detail = "word overlap (no embedding model configured)"
            if config.embedding.model is not None and config.settings.prefer_embeddings:
                reason = (
                    self._semantic.unavailable(config.embedding)
                    if self._semantic
                    else "no embedding engine installed"
                )
                state = StageState.NOT_AVAILABLE
                return (
                    lexical,
                    "cpu",
                    StageReport(
                        stage="similarity",
                        state=state,
                        detail=f"embedding model unavailable ({reason}); used word overlap",
                    ),
                )
            state = StageState.SKIPPED if not config.settings.prefer_embeddings else StageState.OK
            return lexical, "cpu", StageReport(stage="similarity", state=state, detail=detail)
        semantic = self._semantic
        if semantic is None:
            return (
                lexical,
                "cpu",
                StageReport(
                    stage="similarity",
                    state=StageState.NOT_AVAILABLE,
                    detail="no embedding engine installed; used word overlap",
                ),
            )
        t0 = time.perf_counter()
        try:
            vectors, cached = self._vectors(semantic, texts, config, asset, refresh, ctx)
            provider = semantic.index(vectors, config.embedding)
        except ExternalSystemError as exc:
            _log.warning("Embedding engine failed, using word overlap", reason=exc.code)
            return (
                lexical,
                "cpu",
                StageReport(
                    stage="similarity",
                    state=StageState.FAILED,
                    detail=f"embedding engine failed ({exc.code}); used word overlap",
                ),
            )
        elapsed = time.perf_counter() - t0
        usage.seconds["embedding"] = elapsed
        usage.counters["embedding_cache_hits"] += int(cached)
        return (
            provider,
            semantic.device,
            StageReport(
                stage="similarity",
                state=StageState.OK,
                cache=CacheOutcome.HIT if cached else CacheOutcome.MISS,
                seconds=round(elapsed, 3),
                detail=identity,
            ),
        )

    def _vectors(
        self,
        semantic: SemanticEngine,
        texts: list[str],
        config: ContentConfig,
        asset: MediaAssetDto | None,
        refresh: frozenset[CacheLayer],
        ctx: JobContext,
    ) -> tuple[list[list[float]], bool]:
        """The sentence vectors and whether they came from the library (else just computed).

        Vectors are rounded before use so a stored and a fresh run are bit-identical.
        """
        ident = semantic.identity(config.embedding)
        spec = DocumentSpec(
            EMBEDDINGS_OPERATION,
            1,
            {
                "texts": hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest(),
                "count": len(texts),
                "engine": ident.name,
                "version": ident.version,
                "model": ident.model,
            },
        )
        key: str | None = None
        if asset is not None:
            fingerprint = self._documents.fingerprint(asset.id, spec)
            if isinstance(fingerprint, Ok):
                key = fingerprint.value
                if CacheLayer.EMBEDDINGS not in refresh:
                    stored = self._documents.load(
                        asset.id, key, lambda text: _parse_vectors(text, len(texts))
                    )
                    if stored is not None:
                        return stored[0], True
        vectors = [
            [round(x, _VECTOR_DECIMALS) for x in row]
            for row in semantic.embed(texts, config.embedding, ctx.cancellation)
        ]
        if asset is not None and key is not None:
            self._documents.store(
                asset,
                json.dumps(
                    {"document_type": _VECTOR_DOCUMENT, "model": ident.model, "vectors": vectors},
                    separators=(",", ":"),
                ),
                filename="embeddings.json",
                spec=spec,
                display_name=f"{asset.display_name} (sentence embeddings)",
                metadata={"model": ident.model, "sentence_count": len(texts)},
            )
        return vectors, False

    def _provenance(
        self,
        config: ContentConfig,
        plan: tuple[str, str],
        llm_plan: dict[str, Json] | None,
        fingerprint: str,
        device: str,
        usage: _Usage,
    ) -> Provenance:
        engines: dict[str, str] = {}
        if plan[1] != LEXICAL:
            engines["similarity"] = plan[0]
        if llm_plan is not None:
            engines["language_model"] = f"{llm_plan['engine']}:{llm_plan['model']}"
        return Provenance(
            schema_version=SCHEMA_VERSION,
            processing_version=PROCESSING_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile=config.profile.value,
            similarity=plan[0],
            similarity_kind=plan[1],
            engines=engines,
            prompt_version=PROMPT_VERSION if llm_plan is not None else None,
            configuration_fingerprint=fingerprint,
            device=device,
            usage=usage.flat(),
        )

    # --- language model ----------------------------------------------------------------------
    def _targets(self, analysis: ContentAnalysis, scope: LlmScope) -> list[int]:
        if scope is LlmScope.ALL:
            return list(range(len(analysis.segments)))
        out: list[int] = []
        for k, segment in enumerate(analysis.segments):
            unclassified = all(r.role is Role.UNCLASSIFIED for r in segment.roles)
            if unclassified or segment.summary is None or not segment.key_item_ids:
                out.append(k)
        return out

    def _refine(
        self,
        analysis: ContentAnalysis,
        command: AnalyzeContentCommand,
        llm_plan: dict[str, Json] | None,
        asset: MediaAssetDto | None,
        ctx: JobContext,
        usage: _Usage,
    ) -> ContentAnalysis:
        config = command.config
        scope = config.settings.llm_scope
        if config.llm is None or scope is LlmScope.NONE:
            detail = (
                "not configured"
                if config.llm is None
                else "the profile does not use a language model"
            )
            return _with_stage(
                analysis,
                StageReport(stage="language_model", state=StageState.SKIPPED, detail=detail),
            )
        if llm_plan is None or self._llm is None:
            reason = (
                "no language model adapter"
                if self._llm is None
                else (self._llm.unavailable(config.llm) or "unavailable")
            )
            return _with_stage(
                analysis,
                StageReport(
                    stage="language_model",
                    state=StageState.NOT_AVAILABLE,
                    detail=f"language model unavailable ({reason})",
                ),
                warning=(
                    "the language model was configured but is unavailable: "
                    "summaries and roles are the deterministic ones"
                ),
            )
        targets = self._targets(analysis, scope)
        chunks = plan_chunks(
            analysis, targets, config.max_chunk_chars, config.settings.context_segments
        )
        if not chunks:
            return _with_stage(
                analysis,
                StageReport(
                    stage="language_model",
                    state=StageState.OK,
                    detail="no passage needed refinement",
                ),
            )
        refinements: list[Refinement] = []
        rejected = hits = 0
        failure: str | None = None
        t0 = time.perf_counter()
        for chunk in chunks:
            ctx.raise_if_cancelled()
            reply = self._reply(
                chunk, analysis, command, (self._llm, config.llm), asset, llm_plan, ctx, usage
            )
            if reply.text is None:
                failure = reply.failure
                break
            text = reply.text
            hits += int(reply.cached)
            first_item = len(analysis.key_items) + sum(len(r.items) for r in refinements)
            first_rel = len(analysis.relationships) + sum(len(r.relationships) for r in refinements)
            refinement = parse_response(analysis, chunk, text, first_item, first_rel)
            rejected += len(refinement.rejected)
            refinements.append(refinement)
        usage.seconds["language_model"] = time.perf_counter() - t0
        usage.counters["llm_chunks"] += len(chunks)
        usage.counters["llm_cache_hits"] += hits
        usage.counters["llm_rejections"] += rejected

        merged = merge(analysis, refinements)
        try:
            validate_analysis(merged)
        except ContentIntelligenceError as exc:
            _log.error("Language-model output broke an invariant, discarded", reason=exc.code)
            return _with_stage(
                analysis,
                StageReport(
                    stage="language_model",
                    state=StageState.FAILED,
                    detail="model output failed validation and was discarded",
                ),
                warning="the language model's output was discarded because it failed validation",
            )
        detail = (
            f"{len(chunks)} request(s), {hits} from cache, "
            f"{rejected} suggestion(s) rejected by checks"
        )
        if failure is not None:
            return _with_stage(
                merged,
                StageReport(
                    stage="language_model", state=StageState.FAILED, detail=f"{failure}; {detail}"
                ),
                warning="the language model failed part-way: only some passages were refined",
            )
        cache = CacheOutcome.HIT if hits == len(chunks) else CacheOutcome.MISS
        return _with_stage(
            merged,
            StageReport(
                stage="language_model",
                state=StageState.OK,
                cache=cache,
                seconds=round(usage.seconds["language_model"], 3),
                detail=detail,
            ),
        )

    def _reply(
        self,
        chunk: Chunk,
        analysis: ContentAnalysis,
        command: AnalyzeContentCommand,
        engine: tuple[LanguageModel, LlmSettings],
        asset: MediaAssetDto | None,
        llm_plan: dict[str, Json],
        ctx: JobContext,
        usage: _Usage,
    ) -> _Reply:
        """The model's reply to a chunk (from the cache when the identical request was stored)."""
        llm, settings = engine
        prompt = build_prompt(analysis, chunk)
        chunk_config: dict[str, Json] = {
            "prompt_sha": hashlib.sha256((SYSTEM_PROMPT + "\n" + prompt).encode()).hexdigest(),
            **{
                k: llm_plan[k]
                for k in ("engine", "version", "model", "temperature", "seed", "max_output_tokens")
            },
        }
        spec = DocumentSpec(LLM_CHUNK_OPERATION, 1, chunk_config)
        key: str | None = None
        if asset is not None:
            fingerprint = self._documents.fingerprint(asset.id, spec)
            if isinstance(fingerprint, Err):
                return _Reply(None, False, fingerprint.error.code)
            key = fingerprint.value
            if CacheLayer.LLM not in command.refresh:
                cached = self._documents.load(asset.id, key, _parse_chunk_document)
                if cached is not None:
                    return _Reply(cached[0], True, None)
        last = "no attempt"
        for attempt in range(2):  # one retry: transport failures are the only recoverable ones
            ctx.raise_if_cancelled()
            try:
                reply = llm.complete(SYSTEM_PROMPT, prompt, settings, ctx.cancellation)
            except ExternalSystemError as exc:
                last = exc.code
                _log.warning("Language model request failed", attempt=attempt + 1, reason=exc.code)
                continue
            usage.counters["llm_calls"] += 1
            usage.counters["llm_prompt_tokens"] += reply.prompt_tokens or 0
            usage.counters["llm_completion_tokens"] += reply.completion_tokens or 0
            if asset is not None and key is not None:
                self._documents.store(
                    asset,
                    json.dumps(
                        {"document_type": _CHUNK_DOCUMENT, "response": reply.text},
                        ensure_ascii=False,
                    ),
                    filename="llm_chunk.json",
                    spec=spec,
                    display_name=f"{asset.display_name} (content model reply)",
                    metadata={"model": str(llm_plan["model"]), "prompt_version": PROMPT_VERSION},
                )
            return _Reply(reply.text, False, None)
        return _Reply(None, False, f"language model unreachable ({last})")


def _parse_vectors(text: str, count: int) -> list[list[float]]:
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise InvalidAnalysisDocument("stored embeddings are not JSON") from exc
    vectors = document.get("vectors") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("document_type") != _VECTOR_DOCUMENT
        or not isinstance(vectors, list)
        or len(vectors) != count
        or not all(isinstance(row, list) and row for row in vectors)
    ):
        raise InvalidAnalysisDocument("stored embeddings do not match the sentences")
    return [[float(x) for x in row] for row in vectors]


def _parse_chunk_document(text: str) -> str:
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise InvalidAnalysisDocument("stored model reply is not JSON") from exc
    if not isinstance(document, dict) or document.get("document_type") != _CHUNK_DOCUMENT:
        raise InvalidAnalysisDocument("not a stored model reply")
    response = document.get("response")
    if not isinstance(response, str):
        raise InvalidAnalysisDocument("stored model reply has no text")
    return response


def _with_stage(
    analysis: ContentAnalysis, stage: StageReport, warning: str | None = None
) -> ContentAnalysis:
    warnings = (*analysis.warnings, warning) if warning else analysis.warnings
    return replace(analysis, stages=(*analysis.stages, stage), warnings=warnings)


def _restrict(source: SourceTranscript, start: float, end: float) -> SourceTranscript:
    """Segments that overlap the range, whole (text is never cut)."""
    kept = tuple(
        s
        for s in source.segments
        if s.start is not None and s.end is not None and s.end > start and s.start < end
    )
    return replace(source, segments=kept)


def _digest(config: dict[str, Json]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
