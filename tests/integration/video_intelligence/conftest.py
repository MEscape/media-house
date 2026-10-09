"""A real Media Library, real ffprobe/FFmpeg and the real engines (every call is recorded)."""

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.inspect_media import (
    InspectMedia,
    InspectMediaCommand,
)
from media_house.modules.media_inspection.infrastructure.ffprobe_prober import FfprobeProber
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.modules.video_intelligence.application.analyze_video import (
    AnalyzeVideo,
    AnalyzeVideoCommand,
    VideoAnalysisResult,
)
from media_house.modules.video_intelligence.application.ports import SignalAnalyzer
from media_house.modules.video_intelligence.application.resolver import UpstreamResolver
from media_house.modules.video_intelligence.domain.profiles import ProcessingProfile, get_profile
from media_house.modules.video_intelligence.domain.values import AnalyzerId
from media_house.modules.video_intelligence.infrastructure.ffmpeg_frames import FfmpegFrameSource
from media_house.modules.video_intelligence.module import classical_analyzers
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.integration.media_inspection.conftest import RecordingRunner
from tests.support.fakes import FixedClock, RecordingPublisher

pytestmark = pytest.mark.integration


@dataclass
class VideoEnv:
    library: MediaLibrary
    runner: RecordingRunner
    paths: AppPaths
    clock: Clock
    media_dir: Path
    inspector: InspectMedia

    def import_clip(self, path: Path) -> str:
        result = self.library.import_file(path)
        assert isinstance(result, Ok), result
        return result.value.asset.id

    def inspect(self, asset_id: str) -> None:
        result = self.inspector.execute(InspectMediaCommand(asset_id), JobContext.detached())
        assert isinstance(result, Ok), result

    def build(self, analyzers: Mapping[AnalyzerId, SignalAnalyzer] | None = None) -> AnalyzeVideo:
        """A fresh service over the same library (an application restart)."""
        return AnalyzeVideo(
            self.library,
            UpstreamResolver(self.inspector),
            FfmpegFrameSource(self.runner),
            dict(analyzers) if analyzers is not None else classical_analyzers(),
            self.paths,
        )

    @staticmethod
    def profile(name: str) -> ProcessingProfile:
        """A named profile restricted to the milestone-1 analyzers: these suites measure real
        footage with the classical engines only (model engines have their own suites)."""
        full = get_profile(name)
        keep = {AnalyzerId.SHOTS, AnalyzerId.QUALITY, AnalyzerId.MOTION}
        return replace(full, analyzers=tuple(a for a in full.analyzers if a in keep))

    def analyze(
        self,
        asset_id: str,
        profile: str | ProcessingProfile = "standard",
        use_case: AnalyzeVideo | None = None,
    ) -> VideoAnalysisResult:
        chosen = self.profile(profile) if isinstance(profile, str) else profile
        result = (use_case or self.build()).execute(
            AnalyzeVideoCommand(asset_id, chosen), JobContext.detached()
        )
        assert isinstance(result, Ok), result
        return result.value

    def decodes(self) -> int:
        """How many times FFmpeg was started to decode frames (not to print its version)."""
        return self.runner.count("ffmpeg", "-filter_complex")

    def probes_of(self, asset_id: str) -> int:
        """How many times ffprobe was started on this video file."""
        path = self.library.local_path(asset_id)
        assert isinstance(path, Ok)
        return self.runner.count_on("ffprobe", path.value)


def build_video_env(root: Path) -> VideoEnv:
    container = Container()
    paths = AppPaths.under_root(root / "app")
    runner = RecordingRunner(SubprocessRunner())
    clock = FixedClock()
    container.register_instance(AppPaths, paths)
    container.register_instance(Clock, clock)
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, runner)
    MediaLibraryModule().register(container)
    library = container.resolve(MediaLibrary)
    (root / "media").mkdir(parents=True, exist_ok=True)
    return VideoEnv(
        library,
        runner,
        paths,
        clock,
        root / "media",
        InspectMedia(library, FfprobeProber(runner), paths, clock),
    )


@pytest.fixture
def venv(tmp_path: Path) -> VideoEnv:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and ffprobe are required")
    return build_video_env(tmp_path)
