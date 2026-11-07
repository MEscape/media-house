"""Port for measuring a media FILE: what is wrong with it, by looking at picture and sound.

Quality control (a preview render), the render manager (a finished render) and export validation
(the delivered file) all need the same measurements: black frames, frozen frames, silence, loudness,
whether the file decodes to the end, and a few sampled frames with their statistics. One port, one
adapter, so no module carries its own copy.

Contract
--------
* A measurement is a fact about ONE file at the time of the call. Nothing is cached here; callers
  that cache (the Media Library) key it by the file's identity and ``capabilities().identity``.
* Times are exact fractions of a second on the file's own clock; never floats used as addresses.
* A measurement the tool cannot make is reported as such (``None`` / an empty tuple), never
  invented. A tool that is missing raises ``ToolNotFoundError``.
* Nothing here changes the file.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from media_house.shared.concurrency import CancellationToken


@dataclass(frozen=True, slots=True)
class TimeSpan:
    """A stretch of the file in exact seconds; ``end`` is ``None`` when it runs to the end."""

    start: Fraction
    end: Fraction | None

    @property
    def length(self) -> Fraction | None:
        return None if self.end is None else self.end - self.start


@dataclass(frozen=True, slots=True)
class AnalyzerCapabilities:
    #: Tool + version + adapter revision; part of any cache key built on a measurement.
    identity: str
    operations: frozenset[str]


@dataclass(frozen=True, slots=True)
class LoudnessReading:
    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbtp: float | None
    sample_peak_dbfs: float | None
    duration_samples: int | None
    sample_rate: int | None
    channels: int | None
    codec: str
    #: ``False`` when the file has no audio stream (every number above is then ``None``).
    has_audio: bool = True


@dataclass(frozen=True, slots=True)
class DecodeReport:
    #: Decoded to the last packet without a reported error.
    complete: bool
    frames_decoded: int | None
    #: Where the decoder's clock ended (seconds), if it said.
    decoded_until: Fraction | None
    #: The decoder's own error lines (bounded).
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FrameSample:
    """One decoded picture and what it measures. The PNG is evidence; the numbers are 8-bit
    analysis values (never fed back into a pipeline)."""

    requested: Fraction
    path: Path | None
    width: int
    height: int
    luma_mean: float
    #: 1st / 99th percentile of luma (0-255): crushed shadows and clipped highlights.
    luma_low: float
    luma_high: float
    #: Fraction of pixels at the floor / ceiling of the range.
    crushed_fraction: float
    clipped_fraction: float
    #: Mean of R, G, B (0-255): a neutral picture has them near each other.
    mean_rgb: tuple[float, float, float]
    saturation_mean: float
    #: Variance of the Laplacian of the luma: higher is sharper (relative, same size only).
    sharpness: float
    #: Fraction of the height / width that is a near-black border on (top, bottom, left, right).
    border_black: tuple[float, float, float, float]
    #: Fraction of the picture that is near-black / near-white overall.
    black_fraction: float


@dataclass(frozen=True, slots=True)
class FrameComparison:
    #: 1.0 = identical (mean absolute difference on a reduced luma picture).
    similarity: float
    correlation: float
    mean_abs_difference: float


class MediaAnalyzer(Protocol):
    def capabilities(self) -> AnalyzerCapabilities: ...

    def black_spans(
        self,
        path: Path,
        *,
        min_seconds: float,
        picture_threshold: float,
        pixel_threshold: float,
        cancellation: CancellationToken,
    ) -> tuple[TimeSpan, ...]:
        """Stretches of (nearly) black picture at least ``min_seconds`` long."""
        ...

    def frozen_spans(
        self,
        path: Path,
        *,
        min_seconds: float,
        noise_db: float,
        cancellation: CancellationToken,
    ) -> tuple[TimeSpan, ...]:
        """Stretches where the picture does not change at least ``min_seconds`` long."""
        ...

    def silent_spans(
        self,
        path: Path,
        *,
        min_seconds: float,
        noise_db: float,
        cancellation: CancellationToken,
    ) -> tuple[TimeSpan, ...]:
        """Stretches of silence at least ``min_seconds`` long (empty if there is no audio)."""
        ...

    def loudness(
        self,
        path: Path,
        *,
        span: TimeSpan | None,
        cancellation: CancellationToken,
    ) -> LoudnessReading:
        """EBU R128 loudness, range, true and sample peak of the file or a stretch of it."""
        ...

    def decode(self, path: Path, *, cancellation: CancellationToken) -> DecodeReport:
        """Decode every stream to the end into nowhere; the cost is the whole file."""
        ...

    def sample_frames(
        self,
        path: Path,
        times: Sequence[Fraction],
        *,
        into: Path | None,
        cancellation: CancellationToken,
    ) -> tuple[FrameSample, ...]:
        """Decode one picture at each time (``into``: where the PNGs are kept as evidence).
        A time that cannot be decoded is simply absent from the result."""
        ...

    def compare_frames(self, a: Path, b: Path) -> FrameComparison:
        """Compare two pictures (PNG files from ``sample_frames``)."""
        ...
