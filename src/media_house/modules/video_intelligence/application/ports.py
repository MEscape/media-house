"""Ports the use case needs from the outside world.

Decoding and measuring are separate on purpose: one ``FrameSource`` decodes the video ONCE into
an opaque ``DecodedVideo`` and every ``SignalAnalyzer`` reads that same decode. Analyzers never
decode on their own.

Model-based analyzers are adapters: a library or model that is not installed makes ONE analyzer
``not_available`` (``SignalAnalyzer.unavailable`` says why); nothing else breaks.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.core.domain import Rational
from media_house.modules.video_intelligence.domain.profiles import MeasurementSettings
from media_house.modules.video_intelligence.domain.signals import (
    AppearanceSignals,
    BodySignals,
    DescriptionSignals,
    DetectionSignals,
    EmbeddingSignals,
    FaceSignals,
    GeometrySignals,
    MotionSignals,
    QualitySignals,
    SaliencySignals,
    ShotSignals,
    TextSignals,
)
from media_house.modules.video_intelligence.domain.source import SourceInfo
from media_house.modules.video_intelligence.domain.values import AnalyzerId, DeviceKind
from media_house.shared.concurrency import CancellationToken

type Signals = (
    ShotSignals
    | MotionSignals
    | QualitySignals
    | SaliencySignals
    | GeometrySignals
    | DetectionSignals
    | FaceSignals
    | BodySignals
    | TextSignals
    | EmbeddingSignals
    | AppearanceSignals
    | DescriptionSignals
)


@dataclass(frozen=True, slots=True)
class FrameTimeline:
    """Presentation timestamps of every decoded frame, as the decoder reported them."""

    timebase: Rational
    pts: tuple[int, ...]
    end_pts: int
    #: Frames without a decoder timestamp, filled in by interpolation.
    estimated: int


@dataclass(frozen=True, slots=True)
class DecodeRequest:
    """What to decode, from one pass over the video."""

    source: SourceInfo
    settings: MeasurementSettings
    #: Distance between the candidate grey / colour sample positions (see ``sampling``).
    stride: int
    rgb_stride: int
    dense: bool
    samples: bool
    rgb: bool


class DecodedVideo(Protocol):
    """Frames decoded once. Only the adapter that made it (and its analyzers) can read them."""

    @property
    def timeline(self) -> FrameTimeline: ...

    @property
    def warnings(self) -> tuple[str, ...]:
        """Problems met while decoding that did not stop it (damage, dropped frames)."""
        ...


@dataclass(frozen=True, slots=True)
class MeasureRequest:
    """Context of one analyzer run."""

    settings: MeasurementSettings
    stride: int
    #: Grey sample frames to analyse (adaptive plan); empty for the analyzer that reads every frame.
    plan: tuple[int, ...]
    #: Colour frames to analyse (adaptive plan) and their decode stride.
    rgb_stride: int
    rgb_plan: tuple[int, ...]
    #: Per-frame signals of the whole video, when this analyzer depends on them.
    timeline: ShotSignals | None
    source: SourceInfo
    #: Signals of the analyzers this one depends on (required and optional ones that ran).
    dependencies: Mapping[AnalyzerId, Signals]
    #: Requested device and whether a missing model may be downloaded (off unless allowed).
    device: DeviceKind
    allow_downloads: bool


class FrameSource(Protocol):
    """Decodes a video file to proxy-resolution frames, once."""

    def identity(self) -> Mapping[str, str]:
        """Tool versions that decide the decoded pixels (for example ``{"ffmpeg": "9.0"}``)."""
        ...

    def decode(
        self,
        path: Path,
        request: DecodeRequest,
        work_dir: Path,
        cancellation: CancellationToken,
    ) -> DecodedVideo:
        """Blocking. Raises ``UnreadableVideo`` when no frame could be decoded.

        A missing FFmpeg raises ``ToolNotFoundError``.
        """
        ...


class SignalAnalyzer(Protocol):
    """Measures one kind of raw signal from a ``DecodedVideo``."""

    @property
    def analyzer(self) -> AnalyzerId: ...

    def identity(self) -> Mapping[str, str]:
        """Versions of what decides the values: libraries, algorithm revision, model identity and
        weights hash. Cheap: never loads a model."""
        ...

    def unavailable(self, allow_downloads: bool) -> str | None:
        """Why this analyzer cannot run here (a library or model file is missing), else ``None``."""
        ...

    def device(self, requested: DeviceKind) -> str:
        """``gpu`` or ``cpu``: where it would run for the requested device. Cheap."""
        ...

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> Signals:
        """Blocking. Loads its model once and keeps it. Raises ``ExternalSystemError`` when the
        measurement itself fails."""
        ...
