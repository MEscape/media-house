"""Use case: audio/video asset -> Audio Intelligence Timeline.

Three derived assets hang off the source, each found by its own processing fingerprint so that
changing one concern recomputes only what depends on it:

* ``transcription``         speech + word alignment (engine, model, language, preparation)
* ``acoustic_measurements`` raw frames/events (analyzer versions, acoustic config, preparation)
* ``audio_intelligence``    the fused timeline (all of the above + analysis + scoring settings)

A new scoring or analysis configuration therefore reuses transcript and measurements and only
re-fuses; a new analyzer version re-measures but keeps the transcript.
"""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.derived_documents import (
    DerivedDocuments,
    DocumentSpec,
    require_audio_or_video,
)
from media_house.modules.audio_intelligence.application.ports import AcousticExtractor
from media_house.modules.audio_intelligence.application.transcribe_audio import (
    TranscribeAudio,
    TranscribeAudioCommand,
    TranscribeError,
    TranscriptionResult,
)
from media_house.modules.audio_intelligence.domain.analysis.acoustic import AcousticMeasurements
from media_house.modules.audio_intelligence.domain.analysis.config import (
    ANALYSIS_OPERATION,
    ANALYSIS_VERSION,
    MEASUREMENTS_OPERATION,
    MEASUREMENTS_VERSION,
    AudioIntelligenceConfig,
    ScoringConfig,
)
from media_house.modules.audio_intelligence.domain.analysis.fusion import build_timeline
from media_house.modules.audio_intelligence.domain.analysis.scoring import (
    HeuristicScorer,
    Scorer,
)
from media_house.modules.audio_intelligence.domain.analysis.serialization import (
    measurements_from_json,
    measurements_to_json,
    timeline_from_json,
    timeline_to_json,
)
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_intelligence.domain.errors import AudioIntelligenceError
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaLibrary,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type AnalyzeError = TranscribeError
_STEPS = 5


@dataclass(frozen=True, slots=True)
class AnalyzeAudioCommand:
    source_asset_id: str
    config: AudioIntelligenceConfig = field(default_factory=AudioIntelligenceConfig)


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """The timeline plus where each building block lives in the Media Library."""

    timeline: AudioIntelligenceTimeline
    #: The stored timeline document (derived asset of the source).
    asset: MediaAssetDto
    #: The prepared audio all analyses heard.
    audio_asset: MediaAssetDto
    transcript_asset: MediaAssetDto
    measurements_asset: MediaAssetDto
    #: ``False`` when the identical timeline already existed (nothing was computed).
    created: bool


@dataclass(frozen=True, slots=True)
class _Measured:
    measurements: AcousticMeasurements
    asset: MediaAssetDto
    reused: bool


class AnalyzeAudio:
    """One audio asset in, one canonical timeline out; reuse at every stage."""

    def __init__(
        self,
        library: MediaLibrary,
        transcriber: TranscribeAudio,
        extractor: AcousticExtractor,
        paths: AppPaths,
        clock: Clock,
        scorer_factory: Callable[[ScoringConfig], Scorer] = HeuristicScorer,
    ) -> None:
        self._library = library
        self._transcriber = transcriber
        self._extractor = extractor
        self._paths = paths
        self._clock = clock
        self._scorer_factory = scorer_factory
        self._documents = DerivedDocuments(library, paths)

    def execute(
        self,
        command: AnalyzeAudioCommand,
        ctx: JobContext,
    ) -> Result[AnalysisResult, AnalyzeError]:
        started = time.perf_counter()
        source_result = self._library.get(command.source_asset_id)
        if isinstance(source_result, Err):
            return source_result
        source = source_result.value
        if (rejected := require_audio_or_video(source, "analysed")) is not None:
            return rejected
        config = command.config
        identity = self._extractor.identity(config.acoustic)
        engine_version = self._transcriber.engine_version(config.transcription)
        scorer = self._scorer_factory(config.scoring)
        spec = DocumentSpec(
            ANALYSIS_OPERATION,
            ANALYSIS_VERSION,
            config.timeline_fingerprint(engine_version, identity, scorer.identity),
        )
        fingerprint = self._documents.fingerprint(source.id, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(source.id, fingerprint.value, timeline_from_json)
        if cached is not None:
            reused = self._with_related(*cached)
            if reused is not None:
                _log.info("Audio intelligence reused", asset_id=reused.asset.id, cache_hit=True)
                return Ok(reused)

        ctx.raise_if_cancelled()
        ctx.progress.report(1, _STEPS, "Preparing audio")
        prepared = self._transcriber.prepare_audio(source, config.transcription.preparation, ctx)
        if isinstance(prepared, Err):
            return prepared
        audio_asset, audio_info, audio_path = prepared.value

        ctx.progress.report(2, _STEPS, "Transcribing and measuring")
        speech, measured = self._run_stages(command, source, audio_path, identity, ctx)
        if isinstance(speech, Err):
            return speech
        if isinstance(measured, Err):
            return measured
        transcription = speech.value

        ctx.raise_if_cancelled()
        ctx.progress.report(4, _STEPS, "Fusing timeline")
        try:
            timeline = build_timeline(
                transcript=transcription.transcript,
                measurements=measured.value.measurements,
                config=config,
                scorer=scorer,
                audio_offset=audio_info.audio_offset,
                created_at=self._clock.now(),
            )
        except AudioIntelligenceError as exc:
            _log.error("Fusion rejected its own result", reason=exc.code, asset_id=source.id)
            return Err(exc)
        ctx.progress.report(5, _STEPS, "Saving timeline")
        stored = self._store(
            source,
            timeline,
            spec,
            audio_asset,
            transcription.asset,
            measured.value.asset,
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Audio intelligence created",
            asset_id=stored.value.id,
            cache_hit=False,
            transcript_reused=not transcription.created,
            measurements_reused=measured.value.reused,
            audio_seconds=round(timeline.duration, 1),
            processing_seconds=round(time.perf_counter() - started, 1),
            words=len(timeline.words),
            events=len(timeline.events),
            warnings=len(timeline.metadata.warnings),
        )
        return Ok(
            AnalysisResult(
                timeline,
                stored.value,
                audio_asset,
                transcription.asset,
                measured.value.asset,
                created=True,
            ),
        )

    # --- parallel stages ---------------------------------------------------------------------
    def _run_stages(
        self,
        command: AnalyzeAudioCommand,
        source: MediaAssetDto,
        audio_path: Path,
        identity: dict[str, str],
        ctx: JobContext,
    ) -> tuple[Result[TranscriptionResult, AnalyzeError], Result[_Measured, AnalyzeError]]:
        """Speech (model bound) and acoustic measurement (CPU bound) run concurrently.

        Exactly two workers: the heavy models are shared singletons, so more threads would only
        compete for the same resources. Both always finish before an error is reported.
        """
        config = command.config
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio-intelligence") as pool:
            speech = pool.submit(
                self._transcriber.execute,
                TranscribeAudioCommand(source.id, config.transcription),
                ctx,
            )
            measuring = pool.submit(self._measure, source, audio_path, config, identity, ctx)
            return speech.result(), measuring.result()

    def _measure(
        self,
        source: MediaAssetDto,
        audio_path: Path,
        config: AudioIntelligenceConfig,
        identity: dict[str, str],
        ctx: JobContext,
    ) -> Result[_Measured, AnalyzeError]:
        spec = DocumentSpec(
            MEASUREMENTS_OPERATION,
            MEASUREMENTS_VERSION,
            config.measurements_fingerprint(identity),
        )
        fingerprint = self._documents.fingerprint(source.id, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(source.id, fingerprint.value, measurements_from_json)
        if cached is not None:
            loaded, asset = cached
            _log.info("Acoustic measurements reused", asset_id=asset.id)
            return Ok(_Measured(loaded, asset, reused=True))

        try:
            measurements = self._extractor.measure(audio_path, config.acoustic, ctx.cancellation)
        except AudioIntelligenceError as exc:
            return Err(exc)
        stored = self._documents.store(
            source,
            measurements_to_json(measurements),
            filename="measurements.json",
            spec=spec,
            display_name=f"{source.display_name} (acoustic measurements)",
            metadata={
                "frame_count": len(measurements.track),
                "hop": measurements.track.hop,
                "event_count": len(measurements.events),
                "analyzers": dict(measurements.identity),
            },
        )
        if isinstance(stored, Err):
            return stored
        return Ok(_Measured(measurements, stored.value, reused=False))

    # --- persistence -------------------------------------------------------------------------
    def _store(
        self,
        source: MediaAssetDto,
        timeline: AudioIntelligenceTimeline,
        spec: DocumentSpec,
        audio_asset: MediaAssetDto,
        transcript_asset: MediaAssetDto,
        measurements_asset: MediaAssetDto,
    ) -> Result[MediaAssetDto, AnalyzeError]:
        return self._documents.store(
            source,
            timeline_to_json(timeline),
            filename="timeline.json",
            spec=spec,
            display_name=f"{source.display_name} (audio intelligence)",
            metadata={
                "audio_asset_id": audio_asset.id,
                "transcript_asset_id": transcript_asset.id,
                "measurements_asset_id": measurements_asset.id,
                "language": timeline.language,
                "scoring": timeline.metadata.scoring,
                "word_count": len(timeline.words),
                "segment_count": len(timeline.segments),
                "event_count": len(timeline.events),
                "editing_signal_count": len(timeline.editing_signals),
                "duration": timeline.duration,
                "warning_count": len(timeline.metadata.warnings),
            },
        )

    def _with_related(
        self,
        timeline: AudioIntelligenceTimeline,
        asset: MediaAssetDto,
    ) -> AnalysisResult | None:
        """The stored result, or ``None`` if a building block it names is gone."""
        related: list[MediaAssetDto] = []
        for key in ("audio_asset_id", "transcript_asset_id", "measurements_asset_id"):
            ref = asset.metadata.get(key)
            found = self._library.get(ref) if isinstance(ref, str) else None
            if not isinstance(found, Ok):
                _log.warning("Stored timeline's inputs are gone, recomputing", asset_id=asset.id)
                return None
            related.append(found.value)
        audio, transcript, measurements = related
        return AnalysisResult(timeline, asset, audio, transcript, measurements, created=False)
