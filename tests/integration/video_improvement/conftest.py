"""A real Media Library, real FFmpeg and the real video engines (recording every external call)."""

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.modules.video_improvement.application.improve_video import (
    ImproveVideo,
    ImproveVideoCommand,
    VideoImprovementResult,
)
from media_house.modules.video_improvement.domain.values import JsonValue
from media_house.modules.video_improvement.infrastructure.ffmpeg_renderer import FfmpegRenderer
from media_house.modules.video_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.modules.video_improvement.infrastructure.lut_baker import LutBaker
from media_house.modules.video_improvement.infrastructure.scene_analyzer import NumpySceneAnalyzer
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.integration.media_inspection.conftest import RecordingRunner
from tests.support.fakes import FixedClock, RecordingPublisher
from tests.support.video_media import Image, scene, write_clip

pytestmark = pytest.mark.integration

#: Fewer analysed frames keep the suite fast; the pipeline is the same.
FAST_ANALYSIS: dict[str, JsonValue] = {"execution.sample_count": 8}


@dataclass
class Env:
    library: MediaLibrary
    runner: RecordingRunner
    paths: AppPaths
    clock: Clock
    tmp: Path
    use_case: ImproveVideo

    def clip(self, name: str, frame: Callable[[int], Image], **options: object) -> str:
        """Encode a clip and import it; the new asset's id."""
        path = write_clip(self.tmp / f"{name}.mp4", frame, **options)  # type: ignore[arg-type]
        result = self.library.import_file(path)
        assert isinstance(result, Ok), result
        return result.value.asset.id

    def build(self, catalog: InspectionCatalog | None = None) -> ImproveVideo:
        """A fresh service over the same library (an application restart)."""
        tool = FfmpegTool(self.runner)
        return ImproveVideo(
            self.library,
            tool,
            NumpySceneAnalyzer(tool),
            LutBaker(),
            FfmpegRenderer(tool),
            self.paths,
            catalog,
        )

    def improve(
        self,
        asset_id: str,
        *,
        source_profile: str | None = None,
        processing_profile: str | None = None,
        overrides: Mapping[str, JsonValue] | None = None,
        use_case: ImproveVideo | None = None,
    ) -> VideoImprovementResult:
        result = (use_case or self.use_case).execute(
            ImproveVideoCommand(
                asset_id, source_profile, processing_profile, FAST_ANALYSIS | dict(overrides or {})
            ),
            JobContext.detached(),
        )
        assert isinstance(result, Ok), result
        return result.value


def build_env(root: Path) -> Env:
    container = Container()
    paths = AppPaths.under_root(root / "app")
    runner = RecordingRunner(SubprocessRunner())
    clock = FixedClock()
    container.register_instance(AppPaths, paths)
    container.register_instance(Clock, clock)
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, runner)
    MediaLibraryModule().register(container)
    (root / "media").mkdir(parents=True, exist_ok=True)
    holder = Env(
        container.resolve(MediaLibrary),
        runner,
        paths,
        clock,
        root / "media",
        None,  # type: ignore[arg-type]
    )
    holder.use_case = holder.build()
    return holder


@pytest.fixture
def env(tmp_path: Path) -> Env:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and ffprobe are required")
    return build_env(tmp_path)


@pytest.fixture(scope="session")
def base_scene() -> Image:
    return scene()
