"""WhisperX adapter: ASR (faster-whisper) followed by forced alignment (wav2vec2).

Everything WhisperX/torch specific stays in this file. Models are loaded lazily, cached per
configuration and reused across calls; inference is serialised with a lock because the shared
model objects are not thread-safe and parallel instances would exhaust GPU memory.
"""

import importlib.metadata
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from media_house.modules.audio_intelligence.application.ports import EngineIdentity
from media_house.modules.audio_intelligence.domain.errors import (
    AlignmentUnavailable,
    UnreadableAudio,
)
from media_house.modules.audio_intelligence.domain.raw import (
    ALIGNMENT_FORCED,
    ALIGNMENT_NONE,
    ALIGNMENT_SEGMENT,
    RawSegment,
    RawTranscription,
    RawWord,
)
from media_house.modules.audio_intelligence.domain.values import (
    ENGINE_WHISPERX,
    AlignmentFailurePolicy,
    TranscriptionConfig,
)
from media_house.modules.audio_intelligence.infrastructure.wav_io import read_mono_pcm16
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ConfigurationError, ExternalSystemError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_SAMPLE_RATE = 16_000
_GPU_COMPUTE_PREFERENCE = ("float16", "int8_float16", "int8", "float32")
_CPU_COMPUTE_PREFERENCE = ("int8", "float32")


@dataclass(frozen=True, slots=True)
class _Runtime:
    device: str
    compute_type: str


class WhisperXEngine:
    """Structurally implements ``TranscriptionEngine``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._asr_models: dict[tuple[str, str, str], Any] = {}
        self._aligners: dict[tuple[str, str, str], tuple[Any, dict[str, Any], str]] = {}

    def identity(self, config: TranscriptionConfig) -> EngineIdentity:
        _ = config
        try:
            version = importlib.metadata.version("whisperx")
        except importlib.metadata.PackageNotFoundError as exc:
            raise ExternalSystemError(
                "WhisperX is not installed",
                user_message="The speech engine (WhisperX) is not installed.",
            ) from exc
        return EngineIdentity(ENGINE_WHISPERX, version)

    def close(self) -> None:
        """Drop cached models (frees GPU memory)."""
        with self._lock:
            self._asr_models.clear()
            self._aligners.clear()

    # ------------------------------------------------------------------------------------------
    def transcribe(
        self,
        audio: Path,
        config: TranscriptionConfig,
        cancellation: CancellationToken,
        on_stage: Callable[[str], None],
    ) -> RawTranscription:
        samples = _read_wav(audio)
        runtime = _resolve_runtime(config)
        version = self.identity(config).version
        with self._lock:
            cancellation.raise_if_cancelled()
            on_stage("Loading speech model")
            model = self._asr_model(config, runtime)
            on_stage("Recognising speech")
            result = _guarded(
                "Speech recognition",
                lambda: model.transcribe(
                    samples,
                    batch_size=config.batch_size,
                    language=config.language,
                    chunk_size=config.chunk_size,
                ),
            )
            language = str(result["language"])
            segments: list[dict[str, Any]] = list(result["segments"])
            cancellation.raise_if_cancelled()

            raw_segments, method, align_name = self._align(
                segments,
                language,
                samples,
                config,
                runtime,
                on_stage,
            )
        return RawTranscription(
            segments=raw_segments,
            language=language,
            language_detected=config.language is None,
            engine=ENGINE_WHISPERX,
            engine_version=version,
            model=config.model,
            model_source=_model_source(config.model),
            alignment_engine=ENGINE_WHISPERX if method == ALIGNMENT_FORCED and segments else None,
            alignment_model=align_name,
            alignment_method=method,
            device=runtime.device,
            compute_type=runtime.compute_type,
        )

    # ------------------------------------------------------------------------------------------
    def _align(
        self,
        segments: list[dict[str, Any]],
        language: str,
        samples: NDArray[np.float32],
        config: TranscriptionConfig,
        runtime: _Runtime,
        on_stage: Callable[[str], None],
    ) -> tuple[tuple[RawSegment, ...], str, str | None]:
        if not config.align:
            return _plain_segments(segments), ALIGNMENT_NONE, None
        if not segments:
            return (), ALIGNMENT_FORCED, None
        on_stage("Aligning words")
        try:
            aligner, metadata, name = self._aligner(language, runtime, config.alignment_model)
        except (ValueError, OSError) as exc:
            return self._alignment_failed(segments, language, str(exc), config, exc)
        import whisperx

        aligned = _guarded(
            "Word alignment",
            lambda: whisperx.align(
                segments,
                aligner,
                metadata,
                samples,
                runtime.device,
                return_char_alignments=False,
            ),
        )
        return _aligned_segments(aligned["segments"]), ALIGNMENT_FORCED, name

    @staticmethod
    def _alignment_failed(
        segments: list[dict[str, Any]],
        language: str,
        reason: str,
        config: TranscriptionConfig,
        cause: Exception,
    ) -> tuple[tuple[RawSegment, ...], str, str | None]:
        if config.on_alignment_failure is AlignmentFailurePolicy.SEGMENT_TIMESTAMPS:
            _log.warning(
                "Alignment unavailable, using segment timing as configured",
                language=language,
                reason=reason,
            )
            return _plain_segments(segments), ALIGNMENT_SEGMENT, None
        raise AlignmentUnavailable(language, reason) from cause

    def _asr_model(self, config: TranscriptionConfig, runtime: _Runtime) -> Any:
        key = (config.model, runtime.device, runtime.compute_type)
        if key not in self._asr_models:
            whisperx = _import_whisperx()
            _log.info(
                "Loading speech model",
                model=config.model,
                device=runtime.device,
                compute_type=runtime.compute_type,
            )
            self._asr_models[key] = _guarded(
                f"Loading model {config.model!r}",
                lambda: whisperx.load_model(
                    config.model,
                    runtime.device,
                    compute_type=runtime.compute_type,
                ),
            )
        return self._asr_models[key]

    def _aligner(
        self,
        language: str,
        runtime: _Runtime,
        override: str | None,
    ) -> tuple[Any, dict[str, Any], str]:
        import whisperx.alignment as alignment

        name = (
            override
            or alignment.DEFAULT_ALIGN_MODELS_TORCH.get(language)
            or alignment.DEFAULT_ALIGN_MODELS_HF.get(language)
        )
        if name is None:
            raise ValueError(f"no default alignment model for language {language!r}")
        key = (name, runtime.device, language)
        if key not in self._aligners:
            _log.info("Loading alignment model", model=name, device=runtime.device)
            model, metadata = alignment.load_align_model(
                language_code=language,
                device=runtime.device,
                model_name=name,
            )
            self._aligners[key] = (model, metadata, name)
        return self._aligners[key]


# --- helpers ---------------------------------------------------------------------------------
def _import_whisperx() -> Any:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # torchcodec/pyannote import-time noise
            import torch  # noqa: F401  # must load first: ctranslate2 reuses its CUDA DLLs
            import whisperx
    except ImportError as exc:
        raise ExternalSystemError(
            f"WhisperX could not be imported: {exc}",
            user_message="The speech engine (WhisperX) is not installed correctly.",
        ) from exc
    return whisperx


def _resolve_runtime(config: TranscriptionConfig) -> _Runtime:
    _import_whisperx()
    import ctranslate2
    import torch

    cuda_ready = torch.cuda.is_available() and ctranslate2.get_cuda_device_count() > 0
    if config.device == "cuda" and not cuda_ready:
        raise ConfigurationError(
            "Device 'cuda' was requested but no usable NVIDIA GPU was found",
            user_message="CUDA was requested but no NVIDIA GPU is available. Use 'auto' or 'cpu'.",
        )
    device = (
        "cuda" if config.device == "cuda" or (config.device == "auto" and cuda_ready) else "cpu"
    )
    supported = set(ctranslate2.get_supported_compute_types(device))
    if config.compute_type is not None:
        if config.compute_type not in supported:
            raise ConfigurationError(
                f"Compute type {config.compute_type!r} is not supported on {device}",
                user_message=f"Compute type '{config.compute_type}' is not supported on {device}.",
                details={"supported": sorted(supported)},
            )
        return _Runtime(device, config.compute_type)
    preference = _GPU_COMPUTE_PREFERENCE if device == "cuda" else _CPU_COMPUTE_PREFERENCE
    for candidate in preference:
        if candidate in supported:
            return _Runtime(device, candidate)
    raise ConfigurationError(f"No usable compute type on {device} (supported: {sorted(supported)})")


def _guarded[T](what: str, call: Callable[[], T]) -> T:
    """Run an engine call; turn library failures into one typed, user-readable error."""
    try:
        return call()
    except Exception as exc:
        text = str(exc)
        if "out of memory" in text.lower():
            raise ExternalSystemError(
                f"{what} ran out of memory: {text}",
                user_message=(
                    "The GPU/CPU ran out of memory. Use a smaller model, a lower batch size "
                    "or a lighter compute type such as int8."
                ),
            ) from exc
        raise ExternalSystemError(
            f"{what} failed: {text}",
            user_message=f"{what} failed. See the log for details.",
        ) from exc


def _read_wav(path: Path) -> NDArray[np.float32]:
    """Our own prepared 16 kHz mono PCM16 file as the float32 array WhisperX expects."""
    samples, rate = read_mono_pcm16(path)
    if rate != _SAMPLE_RATE:
        raise UnreadableAudio("prepared audio is not 16 kHz")
    return samples


def _model_source(model: str) -> str | None:
    try:
        from faster_whisper.utils import _MODELS
    except ImportError:
        return None
    return str(_MODELS.get(model)) if model in _MODELS else None


def _plain_segments(segments: list[dict[str, Any]]) -> tuple[RawSegment, ...]:
    return tuple(
        RawSegment(float(s["start"]), float(s["end"]), str(s["text"]), ()) for s in segments
    )


def _aligned_segments(segments: list[dict[str, Any]]) -> tuple[RawSegment, ...]:
    return tuple(
        RawSegment(
            float(s["start"]),
            float(s["end"]),
            str(s["text"]),
            tuple(
                RawWord(
                    str(w["word"]),
                    None if w.get("start") is None else float(w["start"]),
                    None if w.get("end") is None else float(w["end"]),
                    None if w.get("score") is None else float(w["score"]),
                )
                for w in s.get("words", [])
            ),
        )
        for s in segments
    )
