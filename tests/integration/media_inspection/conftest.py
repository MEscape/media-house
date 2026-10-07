"""A real Media Library, real ffprobe/FFmpeg, and a runner that records every external call."""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.core.application.ports import (
    Clock,
    OutputLine,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.modules import Container
from media_house.modules.media_inspection.application.inspect_media import (
    InspectionResult,
    InspectMedia,
    InspectMediaCommand,
)
from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.model import MediaInspection
from media_house.modules.media_inspection.infrastructure.ffprobe_prober import FfprobeProber
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Ok
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.support.fakes import FixedClock, RecordingPublisher
from tests.support.inspection_media import Samples

pytestmark = pytest.mark.integration


class RecordingRunner:
    """The real subprocess runner that remembers which tool was started with which arguments."""

    def __init__(self, inner: ProcessRunner) -> None:
        self._inner = inner
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(
        self,
        spec: ProcessSpec,
        *,
        cancellation: CancellationToken | None = None,
        on_output: Callable[[OutputLine], None] | None = None,
    ) -> ProcessResult:
        self.calls.append((spec.executable, tuple(spec.args)))
        return self._inner.run(spec, cancellation=cancellation, on_output=on_output)

    def count_on(self, executable: str, path: Path) -> int:
        """How often ``executable`` was started on exactly this file."""
        return sum(1 for tool, args in self.calls if tool == executable and str(path) in args)

    def count(self, executable: str, *flags: str) -> int:
        return sum(
            1
            for tool, args in self.calls
            if tool == executable and all(flag in args for flag in flags)
        )

    def reset(self) -> None:
        self.calls.clear()


@dataclass
class Env:
    library: MediaLibrary
    runner: RecordingRunner
    paths: AppPaths
    clock: Clock
    use_case: InspectMedia
    samples: Samples
    media_dir: Path

    def import_media(self, path: Path) -> str:
        result = self.library.import_file(path)
        assert isinstance(result, Ok), result
        return result.value.asset.id

    def build(self) -> InspectMedia:
        """A fresh service over the same library (an application restart)."""
        return InspectMedia(self.library, FfprobeProber(self.runner), self.paths, self.clock)

    def inspect(
        self,
        asset_id: str,
        config: InspectionConfig | None = None,
        use_case: InspectMedia | None = None,
    ) -> InspectionResult:
        command = InspectMediaCommand(asset_id, config or InspectionConfig())
        result = (use_case or self.use_case).execute(command, JobContext.detached())
        assert isinstance(result, Ok), result
        return result.value

    def inspection_of(self, path: Path, config: InspectionConfig | None = None) -> MediaInspection:
        return self.inspect(self.import_media(path), config).inspection


@pytest.fixture(scope="session")
def samples(tmp_path_factory: pytest.TempPathFactory) -> Samples:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and ffprobe are required")
    return Samples(tmp_path_factory.mktemp("inspection-samples"))


def build_env(root: Path, samples: Samples) -> Env:
    """A complete, independent environment (own library, own storage) under ``root``."""
    container = Container()
    paths = AppPaths.under_root(root / "app")
    runner = RecordingRunner(SubprocessRunner())
    clock = FixedClock()
    container.register_instance(AppPaths, paths)
    container.register_instance(Clock, clock)
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, runner)
    MediaLibraryModule().register(container)
    holder = Env(
        container.resolve(MediaLibrary),
        runner,
        paths,
        clock,
        None,  # type: ignore[arg-type]
        samples,
        root / "media",
    )
    (root / "media").mkdir(parents=True, exist_ok=True)
    holder.use_case = holder.build()
    return holder


@pytest.fixture
def env(tmp_path: Path, samples: Samples) -> Env:
    return build_env(tmp_path, samples)
