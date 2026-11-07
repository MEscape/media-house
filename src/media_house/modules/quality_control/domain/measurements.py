"""What was measured on the preview render, as plain facts. A measurement that was not made is
``None`` (never zero, never "fine"): the report then says the check did not run."""

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class Span:
    start: Fraction
    #: ``None``: to the end of the file.
    end: Fraction | None


@dataclass(frozen=True, slots=True)
class StreamFacts:
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    fps: Fraction | None
    duration: Fraction | None
    audio_duration: Fraction | None
    video_codec: str
    audio_sample_rate: int | None
    #: Seconds the audio starts after the picture (negative: before), from the inspection.
    av_offset: float | None


@dataclass(frozen=True, slots=True)
class DecodeFacts:
    complete: bool
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LoudnessFacts:
    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbtp: float | None
    sample_peak_dbfs: float | None


@dataclass(frozen=True, slots=True)
class ClipLoudness:
    clip_id: str
    integrated_lufs: float | None


@dataclass(frozen=True, slots=True)
class FrameStats:
    at: Fraction
    #: What the frame was taken for: ``clip:<id>``, ``cut:<frame>:before`` or ``cut:<frame>:after``.
    tag: str
    clip_id: str | None
    luma_mean: float
    crushed_fraction: float
    clipped_fraction: float
    mean_rgb: tuple[float, float, float]
    sharpness: float
    border_black: tuple[float, float, float, float]
    black_fraction: float
    #: The key the application stored the picture under (maps to a library asset id).
    evidence_key: str = ""


@dataclass(frozen=True, slots=True)
class RenderMeasurements:
    asset_id: str
    #: Identity of the measuring tool (part of the report's inputs).
    analyzer: str
    streams: StreamFacts | None
    decode: DecodeFacts | None
    black: tuple[Span, ...] | None
    frozen: tuple[Span, ...] | None
    silent: tuple[Span, ...] | None
    loudness: LoudnessFacts | None
    #: Sample peak of the last quarter second (an abrupt stop is loud right up to the end).
    end_peak_dbfs: float | None
    dialogue_levels: tuple[ClipLoudness, ...]
    frames: tuple[FrameStats, ...]
    #: Why a measurement was not made.
    skipped: tuple[tuple[str, str], ...] = ()
