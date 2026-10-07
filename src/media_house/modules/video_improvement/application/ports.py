"""What video improvement needs from the outside world."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.modules.video_improvement.domain.color import ColorSpec
from media_house.modules.video_improvement.domain.measurements import SceneMeasurements
from media_house.modules.video_improvement.domain.planning import ColorPlan, ProcessingPlan
from media_house.modules.video_improvement.domain.settings import ProcessingProfile
from media_house.modules.video_improvement.domain.source import VideoFacts
from media_house.shared.concurrency import CancellationToken


class VideoProbe(Protocol):
    """Reads the minimal facts of a video file. Used only when Media Inspection has none."""

    def probe_video(self, source: Path, cancellation: CancellationToken) -> VideoFacts:
        """Blocking. Raises ``UnreadableSource``; a missing FFmpeg raises ``ToolNotFoundError``."""
        ...


class Samples(Protocol):
    """Decoded sample frames, opaque to the application."""


class SceneAnalyzer(Protocol):
    """Takes sample frames of a video, measures them and predicts a colour plan's effect."""

    @property
    def identity(self) -> str: ...

    def sample(
        self,
        path: Path,
        facts: VideoFacts,
        count: int,
        work_dir: Path,
        cancellation: CancellationToken,
    ) -> Samples: ...

    def measure(self, samples: Samples, color: ColorSpec) -> SceneMeasurements:
        """Facts about the frames, whose RGB is encoded as ``color``."""
        ...

    def predict(self, samples: Samples, plan: ColorPlan) -> SceneMeasurements:
        """What the measurements would be after ``plan`` (as Rec.709 delivery video)."""
        ...


class ColorBaker(Protocol):
    """Turns a colour plan into a 3D LUT file the renderer applies to every frame."""

    @property
    def identity(self) -> str: ...

    def look_identity(self, lut_path: str) -> str:
        """Content hash of a look LUT. Raises ``InvalidLut`` if it cannot be used."""
        ...

    def bake(self, plan: ColorPlan, size: int, destination: Path) -> None: ...


@dataclass(frozen=True, slots=True)
class RenderRequest:
    source: Path
    destination: Path
    facts: VideoFacts
    plan: ProcessingPlan
    profile: ProcessingProfile
    #: The baked colour LUT; present exactly when the plan applies the colour stage.
    lut_file: Path | None


class VideoRenderer(Protocol):
    """Streams the source through the plan into a new file; audio and metadata are copied."""

    @property
    def identity(self) -> str: ...

    def encoder_for(
        self, profile: ProcessingProfile, facts: VideoFacts, cancellation: CancellationToken
    ) -> str:
        """The encoder that WILL be used (a GPU one only if verified to work). Part of the
        processing fingerprint, because different encoders produce different files."""
        ...

    def render(
        self,
        request: RenderRequest,
        cancellation: CancellationToken,
        on_progress: Callable[[float], None] | None = None,
    ) -> None:
        """Blocking. ``on_progress`` receives the finished fraction (0-1) while it runs.

        Falls back from a GPU decoder/encoder to the CPU if the GPU path fails.
        """
        ...
