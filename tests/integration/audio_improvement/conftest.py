"""A real Media Library, real FFmpeg and the real engines; engines wrapped to count their runs."""

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.modules import Container
from media_house.modules.audio_improvement.application.improve_audio import (
    ImproveAudio,
    ImproveAudioCommand,
    ImprovementResult,
)
from media_house.modules.audio_improvement.application.ports import EngineIdentity, StageProcessor
from media_house.modules.audio_improvement.domain.values import (
    JsonValue,
    Parameter,
    ProcessingStage,
)
from media_house.modules.audio_improvement.infrastructure.ffmpeg_stages import (
    FfmpegCompressor,
    FfmpegDeclipper,
    FfmpegDeEsser,
    FfmpegEqualizer,
    FfmpegMastering,
    FfmpegNoiseReducer,
)
from media_house.modules.audio_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.modules.audio_improvement.infrastructure.ffmpeg_transcoder import FfmpegTranscoder
from media_house.modules.audio_improvement.infrastructure.quality_analyzer import (
    SignalQualityAnalyzer,
)
from media_house.modules.audio_improvement.infrastructure.spectral_dereverb import SpectralDereverb
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Ok
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.support.fakes import FixedClock, RecordingPublisher

pytestmark = pytest.mark.integration


class CountingEngine:
    """Delegates to a real engine and records every run (to prove nothing runs twice)."""

    def __init__(self, inner: StageProcessor) -> None:
        self._inner = inner
        self.calls = 0

    @property
    def stage(self) -> ProcessingStage:
        return self._inner.stage

    @property
    def identity(self) -> EngineIdentity:
        return self._inner.identity

    def process(
        self,
        source: Path,
        destination: Path,
        params: Mapping[str, Parameter],
        cancellation: CancellationToken,
    ) -> None:
        self.calls += 1
        self._inner.process(source, destination, params, cancellation)


@dataclass
class Env:
    library: MediaLibrary
    runner: ProcessRunner
    paths: AppPaths
    clock: Clock
    tool: FfmpegTool
    tmp: Path
    engines: list[CountingEngine]
    use_case: ImproveAudio

    @property
    def total_runs(self) -> int:
        return sum(e.calls for e in self.engines)

    def import_media(self, path: Path) -> str:
        result = self.library.import_file(path)
        assert isinstance(result, Ok), result
        return result.value.asset.id

    def build(self, replacements: Sequence[StageProcessor] = ()) -> ImproveAudio:
        """A fresh service over the same library (an application restart), engines counted."""
        defaults: list[StageProcessor] = [
            FfmpegDeclipper(self.tool),
            FfmpegNoiseReducer(self.tool),
            SpectralDereverb(),
            FfmpegEqualizer(self.tool),
            FfmpegCompressor(self.tool),
            FfmpegDeEsser(self.tool),
            FfmpegMastering(self.tool),
        ]
        replaced = {e.stage: e for e in replacements}
        self.engines = [CountingEngine(replaced.get(e.stage, e)) for e in defaults]
        return ImproveAudio(
            self.library,
            FfmpegTranscoder(self.tool),
            SignalQualityAnalyzer(self.tool),
            self.engines,
            self.paths,
        )

    def improve(
        self,
        asset_id: str,
        profile: str = "youtube",
        overrides: Mapping[str, JsonValue] | None = None,
        use_case: ImproveAudio | None = None,
    ) -> ImprovementResult:
        result = (use_case or self.use_case).execute(
            ImproveAudioCommand(asset_id, profile, overrides or {}), JobContext.detached()
        )
        assert isinstance(result, Ok), result
        return result.value


@pytest.fixture
def env(tmp_path: Path) -> Env:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and ffprobe are required")
    container = Container()
    paths = AppPaths.under_root(tmp_path / "app")
    runner = SubprocessRunner()
    clock = FixedClock()
    container.register_instance(AppPaths, paths)
    container.register_instance(Clock, clock)
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, runner)
    MediaLibraryModule().register(container)
    media = tmp_path / "media"
    media.mkdir()
    holder = Env(
        container.resolve(MediaLibrary),
        runner,
        paths,
        clock,
        FfmpegTool(runner),
        media,
        [],
        None,  # type: ignore[arg-type]
    )
    holder.use_case = holder.build()
    return holder
