"""Configuration of audio preparation and transcription."""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from media_house.shared.errors import InvariantViolation

#: Same shape as the media library's ``JsonValue`` (aliases are structural).
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

ENGINE_WHISPERX = "whisperx"

#: Bump when the transcript pipeline changes in a way that alters results.
TRANSCRIPTION_VERSION = 1
TRANSCRIPTION_OPERATION = "transcription"
#: Bump when audio preparation changes the produced samples.
EXTRACTION_VERSION = 1
EXTRACTION_OPERATION = "audio_extraction"

DEVICES = frozenset({"auto", "cuda", "cpu"})
_LANGUAGE = re.compile(r"^[a-z]{2,3}$")


class AlignmentFailurePolicy(StrEnum):
    """What to do when word-level forced alignment is impossible (e.g. unsupported language)."""

    ERROR = "error"  # default: fail loudly, never downgrade silently
    SEGMENT_TIMESTAMPS = "segment_timestamps"  # estimate words inside segments, mark the result


@dataclass(frozen=True, slots=True)
class PreparationConfig:
    """How source audio becomes the speech-model input (mono PCM, resampled).

    Conservative on purpose: no denoising. Loudness normalisation is off unless requested.
    """

    sample_rate: int = 16_000
    channels: int = 1
    loudness_normalization: bool = False
    #: Which audio stream of the source to use (0 = first audio stream).
    audio_stream: int = 0

    def __post_init__(self) -> None:
        if not 8_000 <= self.sample_rate <= 48_000:
            raise InvariantViolation("Sample rate must be 8000-48000 Hz")
        if self.channels not in {1, 2}:
            raise InvariantViolation("Channels must be 1 or 2")
        if self.audio_stream < 0:
            raise InvariantViolation("Audio stream index must not be negative")

    def to_config(self) -> dict[str, JsonValue]:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "loudness_normalization": self.loudness_normalization,
            "audio_stream": self.audio_stream,
            "codec": "pcm_s16le",
        }


@dataclass(frozen=True, slots=True)
class TranscriptionConfig:
    """Quality-first defaults: WhisperX large-v3 with forced alignment, language auto-detected.

    ``device``, ``compute_type`` and ``batch_size`` only tune speed and memory, so they are not
    part of a result's identity (see ``fingerprint_config``).
    """

    engine: str = ENGINE_WHISPERX
    model: str = "large-v3"
    #: ISO 639-1/3 code such as ``"de"``; ``None`` detects the language.
    language: str | None = None
    align: bool = True
    #: Override the alignment model (wav2vec2 name); ``None`` uses the engine's default.
    alignment_model: str | None = None
    on_alignment_failure: AlignmentFailurePolicy = AlignmentFailurePolicy.ERROR
    device: str = "auto"
    #: ``None`` picks the best type the hardware supports (e.g. ``float16``, else ``int8``).
    compute_type: str | None = None
    batch_size: int = 16
    #: Seconds of speech per recognition window (Whisper's maximum is 30).
    chunk_size: int = 30
    preparation: PreparationConfig = field(default_factory=PreparationConfig)

    def __post_init__(self) -> None:
        if not self.engine.strip() or not self.model.strip():
            raise InvariantViolation("Engine and model must not be empty")
        if self.language is not None and not _LANGUAGE.fullmatch(self.language):
            raise InvariantViolation(
                f"Invalid language code {self.language!r}",
                user_message="Use a language code such as 'de' or 'en', or leave it empty.",
            )
        if self.device not in DEVICES:
            raise InvariantViolation(f"Device must be one of {sorted(DEVICES)}")
        if self.batch_size < 1:
            raise InvariantViolation("Batch size must be at least 1")
        if not 5 <= self.chunk_size <= 30:
            raise InvariantViolation("Chunk size must be 5-30 seconds")
        if (
            not self.align
            and self.on_alignment_failure is AlignmentFailurePolicy.SEGMENT_TIMESTAMPS
        ):
            raise InvariantViolation("Disabling alignment already means segment-level timing")

    def fingerprint_config(self, engine_version: str) -> dict[str, JsonValue]:
        """Everything that materially changes the transcript, plus the engine version."""
        return {
            "engine": self.engine,
            "engine_version": engine_version,
            "model": self.model,
            "language": self.language or "auto",
            "align": self.align,
            "alignment_model": self.alignment_model,
            "chunk_size": self.chunk_size,
            "on_alignment_failure": self.on_alignment_failure.value,
            "preparation": self.preparation.to_config(),
        }
