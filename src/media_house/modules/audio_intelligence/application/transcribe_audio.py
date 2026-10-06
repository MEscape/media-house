"""Use case: audio/video asset -> word-level transcript, reusing anything already computed.

Two derived assets hang off the source in the Media Library, each found by its processing
fingerprint, so nothing is extracted or recognised twice:

* ``audio_extraction``: the prepared mono PCM audio (an ordinary, independently usable asset)
* ``transcription``: the transcript JSON document
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.ports import (
    AudioPreparer,
    PreparedAudio,
    TranscriptionEngine,
)
from media_house.modules.audio_intelligence.domain.builder import BuildContext, build_transcript
from media_house.modules.audio_intelligence.domain.errors import AudioIntelligenceError
from media_house.modules.audio_intelligence.domain.serialization import from_json, to_json
from media_house.modules.audio_intelligence.domain.transcript import Transcript
from media_house.modules.audio_intelligence.domain.values import (
    EXTRACTION_OPERATION,
    EXTRACTION_VERSION,
    TRANSCRIPTION_OPERATION,
    TRANSCRIPTION_VERSION,
    PreparationConfig,
    TranscriptionConfig,
)
from media_house.modules.media_library.application.contracts import (
    JsonValue as LibraryJson,
)
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type TranscribeError = MediaError | AudioIntelligenceError
_STEPS = 5


@dataclass(frozen=True, slots=True)
class TranscribeAudioCommand:
    source_asset_id: str
    config: TranscriptionConfig = field(default_factory=TranscriptionConfig)


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """The timeline plus where everything lives in the Media Library."""

    transcript: Transcript
    #: The stored transcript document (a derived asset of the source).
    asset: MediaAssetDto
    #: The prepared audio the engine heard (a derived asset of the source).
    audio_asset: MediaAssetDto
    #: ``False`` when the identical transcript already existed and no engine ran.
    created: bool


class TranscribeAudio:
    """Process once, persist once, reuse everywhere."""

    def __init__(
        self,
        library: MediaLibrary,
        preparer: AudioPreparer,
        engine: TranscriptionEngine,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._preparer = preparer
        self._engine = engine
        self._paths = paths
        self._clock = clock

    def execute(
        self,
        command: TranscribeAudioCommand,
        ctx: JobContext,
    ) -> Result[TranscriptionResult, TranscribeError]:
        started = time.perf_counter()
        source_result = self._library.get(command.source_asset_id)
        if isinstance(source_result, Err):
            return source_result
        source = source_result.value
        if source.media_type not in {MediaType.AUDIO, MediaType.VIDEO}:
            return Err(
                ValidationError(
                    f"Asset {source.id} is {source.media_type.value}, not audio or video",
                    field="source_asset_id",
                    user_message="Only audio and video files can be transcribed.",
                ),
            )
        config = command.config
        identity = self._engine.identity(config)
        transcript_config = config.fingerprint_config(identity.version)
        fingerprint = self._library.fingerprint(
            source.id,
            TRANSCRIPTION_OPERATION,
            transcript_config,
            TRANSCRIPTION_VERSION,
        )
        if isinstance(fingerprint, Err):
            return fingerprint

        existing = self._library.find_derived_asset(source.id, fingerprint.value)
        if existing is not None:
            reused = self._load_existing(existing)
            if isinstance(reused, Ok):
                _log.info("Transcript reused", asset_id=existing.id, cache_hit=True)
                return reused
            _log.warning("Stored transcript unusable, regenerating", asset_id=existing.id)

        ctx.raise_if_cancelled()
        ctx.progress.report(1, _STEPS, "Preparing audio")
        prepared = self.prepare_audio(source, config.preparation, ctx)
        if isinstance(prepared, Err):
            return prepared
        audio_asset, audio_info, audio_path = prepared.value

        ctx.raise_if_cancelled()
        ctx.progress.report(2, _STEPS, "Transcribing")
        try:
            raw = self._engine.transcribe(
                audio_path,
                config,
                ctx.cancellation,
                lambda stage: ctx.progress.report(3, _STEPS, stage),
            )
        except AudioIntelligenceError as exc:
            _log.warning("Transcription rejected", reason=exc.code, asset_id=source.id)
            return Err(exc)

        ctx.raise_if_cancelled()
        ctx.progress.report(4, _STEPS, "Validating timeline")
        transcript = build_transcript(
            raw,
            BuildContext(
                source_asset_id=source.id,
                audio_asset_id=audio_asset.id,
                duration=audio_info.source_duration,
                audio_offset=audio_info.audio_offset,
                sample_rate=audio_info.sample_rate,
                channels=audio_info.channels,
                processing_version=TRANSCRIPTION_VERSION,
                created_at=self._clock.now(),
            ),
        )

        ctx.progress.report(5, _STEPS, "Saving transcript")
        stored = self._store(source, transcript, transcript_config, audio_asset)
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Transcript created",
            asset_id=stored.value.id,
            cache_hit=False,
            model=raw.model,
            device=raw.device,
            alignment=raw.alignment_method,
            audio_seconds=round(audio_info.source_duration, 1),
            processing_seconds=round(time.perf_counter() - started, 1),
            words=len(transcript.words),
            warnings=len(transcript.metadata.warnings),
        )
        return Ok(TranscriptionResult(transcript, stored.value, audio_asset, created=True))

    # --- reuse -------------------------------------------------------------------------------
    def _load_existing(
        self,
        asset: MediaAssetDto,
    ) -> Result[TranscriptionResult, TranscribeError]:
        path = self._library.local_path(asset.id)
        if isinstance(path, Err):
            return path
        try:
            transcript = from_json(path.value.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            return Err(ValidationError(f"Stored transcript unreadable: {exc}"))
        except AudioIntelligenceError as exc:
            return Err(exc)
        audio = self._library.get(transcript.metadata.audio_asset_id)
        if isinstance(audio, Err):
            return audio
        return Ok(TranscriptionResult(transcript, asset, audio.value, created=False))

    # --- audio preparation -------------------------------------------------------------------
    def engine_version(self, config: TranscriptionConfig) -> str:
        """Version of the speech engine (part of every result's processing identity)."""
        return self._engine.identity(config).version

    def prepare_audio(
        self,
        source: MediaAssetDto,
        preparation: PreparationConfig,
        ctx: JobContext,
    ) -> Result[tuple[MediaAssetDto, PreparedAudio, Path], TranscribeError]:
        """The prepared-audio asset, its facts and a readable local path (extracting if needed).

        Public so that other analyses of the same audio (acoustic measurement) share this
        exact asset instead of decoding the source again.
        """
        extraction_config = preparation.to_config()
        fingerprint = self._library.fingerprint(
            source.id,
            EXTRACTION_OPERATION,
            extraction_config,
            EXTRACTION_VERSION,
        )
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self._library.find_derived_asset(source.id, fingerprint.value)
        if existing is not None:
            facts = _facts_from_metadata(existing)
            path = self._library.local_path(existing.id)
            if facts is not None and isinstance(path, Ok):
                _log.info("Prepared audio reused", asset_id=existing.id)
                return Ok((existing, facts, path.value))

        source_path = self._library.local_path(source.id)
        if isinstance(source_path, Err):
            return source_path
        with self._paths.temporary_directory(prefix="audio-") as tmp:
            wav = tmp / "audio.wav"
            try:
                info = self._preparer.prepare(source_path.value, wav, preparation, ctx.cancellation)
            except AudioIntelligenceError as exc:
                return Err(exc)
            registered = self._library.register_derived(
                source.id,
                wav,
                operation=EXTRACTION_OPERATION,
                config=extraction_config,
                version=EXTRACTION_VERSION,
                display_name=f"{source.display_name} (audio)"[:255],
                metadata=_metadata_from_facts(source.id, info),
            )
        if isinstance(registered, Err):
            return registered
        path = self._library.local_path(registered.value.asset.id)
        if isinstance(path, Err):
            return path
        return Ok((registered.value.asset, info, path.value))

    # --- persistence -------------------------------------------------------------------------
    def _store(
        self,
        source: MediaAssetDto,
        transcript: Transcript,
        config: dict[str, LibraryJson],
        audio_asset: MediaAssetDto,
    ) -> Result[MediaAssetDto, TranscribeError]:
        meta = transcript.metadata
        with self._paths.temporary_directory(prefix="transcript-") as tmp:
            document = tmp / "transcript.json"
            document.write_text(to_json(transcript), encoding="utf-8")
            registered = self._library.register_derived(
                source.id,
                document,
                operation=TRANSCRIPTION_OPERATION,
                config=config,
                version=TRANSCRIPTION_VERSION,
                display_name=f"{source.display_name} (transcript, {meta.language})"[:255],
                metadata={
                    "processing_type": TRANSCRIPTION_OPERATION,
                    "source_asset_id": source.id,
                    "audio_asset_id": audio_asset.id,
                    "language": meta.language,
                    "model": meta.model,
                    "alignment_method": meta.alignment_method,
                    "alignment_model": meta.alignment_model,
                    "word_count": len(transcript.words),
                    "segment_count": len(transcript.segments),
                    "duration": meta.duration,
                    "warning_count": len(meta.warnings),
                },
            )
        if isinstance(registered, Err):
            return registered
        return Ok(registered.value.asset)


def _metadata_from_facts(source_id: str, info: PreparedAudio) -> dict[str, LibraryJson]:
    return {
        "processing_type": EXTRACTION_OPERATION,
        "source_asset_id": source_id,
        "source_offset_seconds": info.audio_offset,
        "source_duration_seconds": info.source_duration,
        "sample_rate": info.sample_rate,
        "channels": info.channels,
        "duration_seconds": info.duration,
    }


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"not a number: {value!r}")
    return float(value)


def _facts_from_metadata(asset: MediaAssetDto) -> PreparedAudio | None:
    m = asset.metadata
    try:
        return PreparedAudio(
            source_duration=_number(m["source_duration_seconds"]),
            audio_offset=_number(m["source_offset_seconds"]),
            sample_rate=int(_number(m["sample_rate"])),
            channels=int(_number(m["channels"])),
            duration=_number(m["duration_seconds"]),
        )
    except (KeyError, TypeError):
        return None
