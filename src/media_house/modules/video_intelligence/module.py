"""Module registration: how Video Intelligence plugs into the application.

It owns no storage: signals and results are JSON documents derived from their source asset in the
Media Library. Technical facts come from ``MediaInspector`` (never probed here) and prior
processing from the video improver's public provenance. No UI yet; pipelines and the GUI call the
``VideoAnalyzer`` contract from a job.

Model files live in the application's cache directory (``video_intelligence/models``); torchvision
weights are also looked up in the torch hub cache and Hugging Face models in the standard Hugging
Face cache, so a model already downloaded for another purpose is reused. Nothing is downloaded
unless a run allows it.
"""

from pathlib import Path

from media_house.core.application.ports import ProcessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.contracts import MediaInspector
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.video_intelligence.application.analyze_video import AnalyzeVideo
from media_house.modules.video_intelligence.application.contracts import VideoAnalyzer
from media_house.modules.video_intelligence.application.ports import FrameSource, SignalAnalyzer
from media_house.modules.video_intelligence.application.resolver import UpstreamResolver
from media_house.modules.video_intelligence.domain.values import AnalyzerId
from media_house.modules.video_intelligence.infrastructure.clip_engines import (
    ClipAppearanceAnalyzer,
    ClipEmbedder,
    ClipRuntime,
)
from media_house.modules.video_intelligence.infrastructure.ffmpeg_frames import FfmpegFrameSource
from media_house.modules.video_intelligence.infrastructure.mediapipe_engines import (
    MediaPipeBodyAnalyzer,
    MediaPipeFaceAnalyzer,
)
from media_house.modules.video_intelligence.infrastructure.model_files import ModelStore
from media_house.modules.video_intelligence.infrastructure.numpy_motion import NumpyMotionAnalyzer
from media_house.modules.video_intelligence.infrastructure.numpy_picture import (
    NumpyGeometryAnalyzer,
    NumpySaliencyAnalyzer,
)
from media_house.modules.video_intelligence.infrastructure.numpy_signals import (
    NumpyQualityAnalyzer,
    NumpyShotAnalyzer,
)
from media_house.modules.video_intelligence.infrastructure.rapidocr_text import RapidOcrAnalyzer
from media_house.modules.video_intelligence.infrastructure.torchvision_detector import (
    TorchvisionDetector,
)
from media_house.modules.video_intelligence.infrastructure.vlm_descriptions import TransformersVlm
from media_house.shared.filesystem import AppPaths


def classical_analyzers() -> dict[AnalyzerId, SignalAnalyzer]:
    """The engines that need no model and no optional library (NumPy and SciPy only)."""
    engines: tuple[SignalAnalyzer, ...] = (
        NumpyShotAnalyzer(),
        NumpyMotionAnalyzer(),
        NumpyQualityAnalyzer(),
        NumpySaliencyAnalyzer(),
        NumpyGeometryAnalyzer(),
    )
    return {engine.analyzer: engine for engine in engines}


def signal_analyzers(model_dir: Path | None = None) -> dict[AnalyzerId, SignalAnalyzer]:
    """The engines of every analyzer, keyed by analyzer id.

    The model-based ones are always registered: one whose library or model file is missing
    reports ``not_available`` for itself (and what depends on it) when a run asks for it.
    """
    torch_hub = Path.home() / ".cache" / "torch" / "hub" / "checkpoints"
    store = ModelStore(model_dir or Path("models"), torch_hub)
    clip = ClipRuntime()
    models: tuple[SignalAnalyzer, ...] = (
        TorchvisionDetector(store),
        MediaPipeFaceAnalyzer(store),
        MediaPipeBodyAnalyzer(store),
        RapidOcrAnalyzer(),
        ClipEmbedder(clip),
        ClipAppearanceAnalyzer(clip),
        TransformersVlm(),
    )
    return {**classical_analyzers(), **{engine.analyzer: engine for engine in models}}


class VideoIntelligenceModule:
    name: str = "video_intelligence"

    def register(self, container: Container) -> None:
        container.register_factory(
            FrameSource, lambda c: FfmpegFrameSource(c.resolve(ProcessRunner))
        )
        container.register_factory(
            AnalyzeVideo,
            lambda c: AnalyzeVideo(
                c.resolve(MediaLibrary),
                UpstreamResolver(c.resolve(MediaInspector)),
                c.resolve(FrameSource),
                signal_analyzers(c.resolve(AppPaths).cache_dir / "video_intelligence" / "models"),
                c.resolve(AppPaths),
            ),
        )
        container.register_factory(VideoAnalyzer, lambda c: c.resolve(AnalyzeVideo))
