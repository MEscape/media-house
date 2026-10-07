"""Module registration: how Video Improvement plugs into the application.

It owns no storage: improved videos are Media Library assets derived from their sources. The
renderer is one lazy instance so a GPU check (a one-frame trial encode) is made once.
"""

from media_house.core.application.ports import ProcessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.video_improvement.application.calibration import CalibrateProfile
from media_house.modules.video_improvement.application.contracts import (
    VideoCalibration,
    VideoImprover,
)
from media_house.modules.video_improvement.application.improve_video import ImproveVideo
from media_house.modules.video_improvement.application.ports import (
    ColorBaker,
    SceneAnalyzer,
    VideoProbe,
    VideoRenderer,
)
from media_house.modules.video_improvement.infrastructure.ffmpeg_renderer import FfmpegRenderer
from media_house.modules.video_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.modules.video_improvement.infrastructure.lut_baker import LutBaker
from media_house.modules.video_improvement.infrastructure.scene_analyzer import (
    NumpySceneAnalyzer,
)
from media_house.shared.filesystem import AppPaths


class VideoImprovementModule:
    name: str = "video_improvement"

    def register(self, container: Container) -> None:
        container.register_factory(FfmpegTool, lambda c: FfmpegTool(c.resolve(ProcessRunner)))
        container.register_factory(VideoProbe, lambda c: c.resolve(FfmpegTool))
        container.register_factory(
            SceneAnalyzer, lambda c: NumpySceneAnalyzer(c.resolve(FfmpegTool))
        )
        container.register_factory(ColorBaker, lambda _c: LutBaker())
        container.register_factory(VideoRenderer, lambda c: FfmpegRenderer(c.resolve(FfmpegTool)))
        container.register_factory(
            ImproveVideo,
            lambda c: ImproveVideo(
                c.resolve(MediaLibrary),
                c.resolve(VideoProbe),
                c.resolve(SceneAnalyzer),
                c.resolve(ColorBaker),
                c.resolve(VideoRenderer),
                c.resolve(AppPaths),
                c.resolve(InspectionCatalog),
            ),
        )
        container.register_factory(VideoImprover, lambda c: c.resolve(ImproveVideo))
        container.register_factory(
            CalibrateProfile,
            lambda c: CalibrateProfile(
                c.resolve(MediaLibrary),
                c.resolve(VideoProbe),
                c.resolve(SceneAnalyzer),
                c.resolve(AppPaths),
                c.resolve(InspectionCatalog),
            ),
        )
        container.register_factory(VideoCalibration, lambda c: c.resolve(CalibrateProfile))
