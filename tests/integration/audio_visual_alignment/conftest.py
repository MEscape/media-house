"""A real Media Library, real ffprobe/FFmpeg and the real Video Intelligence shot analyzer.

Audio and content analyses are fakes that count how often they are asked to compute, so the tests
can prove that a stored result is never recomputed. Models are never needed.
"""

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import (
    AnalysisResult,
    AnalyzeAudioCommand,
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_visual_alignment.application.align import (
    AlignAudioVisual,
    AlignCommand,
    AlignmentResult,
    TextEmbedder,
)
from media_house.modules.audio_visual_alignment.application.resolver import UpstreamResolver
from media_house.modules.media_inspection.application.inspect_media import InspectMedia
from media_house.modules.media_inspection.infrastructure.ffprobe_prober import FfprobeProber
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.modules.video_intelligence.application.analyze_video import (
    AnalyzeVideo,
    AnalyzeVideoCommand,
)
from media_house.modules.video_intelligence.application.resolver import (
    UpstreamResolver as VideoResolver,
)
from media_house.modules.video_intelligence.application.results import VideoResults
from media_house.modules.video_intelligence.domain.profiles import get_profile
from media_house.modules.video_intelligence.domain.values import AnalyzerId
from media_house.modules.video_intelligence.infrastructure.ffmpeg_frames import FfmpegFrameSource
from media_house.modules.video_intelligence.module import classical_analyzers
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.support.analysis_fakes import timeline as make_timeline
from tests.support.fakes import FixedClock, RecordingPublisher

pytestmark = pytest.mark.integration

WORDS = [
    [("The", 0.6, 0.8), ("battery", 0.8, 1.2), ("pack", 1.2, 1.5)],
    [("is", 1.6, 1.7), ("heavy", 1.7, 2.0)],
]


def ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],  # noqa: S607
        check=True,
    )


def make_offset_clip(path: Path, *, video_start: float = 1.0, audio_start: float = 0.5) -> Path:
    """A 2 s test pattern whose video and audio streams start at different, non-zero times."""
    ffmpeg(
        *[
            "-itsoffset",
            str(video_start),
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=25:duration=2",
        ],
        *["-itsoffset", str(audio_start), "-f", "lavfi", "-i", "sine=frequency=440:duration=2"],
        *["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
        str(path),
    )
    return path


def probe_first_frame_seconds(path: Path) -> tuple[float, float]:
    """(first presented video frame, container start), both on the file's own origin (ffprobe)."""

    def run(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["ffprobe", "-v", "error", *args, str(path)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    frame = run(
        "-select_streams",
        "v:0",
        "-read_intervals",
        "%+10",
        "-show_entries",
        "frame=pts_time",
        "-of",
        "csv=p=0",
    )
    start = run("-show_entries", "format=start_time", "-of", "csv=p=0")
    return float(frame.splitlines()[0].strip(",")), float(start)


class FakeAudioCatalog:
    def __init__(self) -> None:
        self.stored: dict[str, tuple[AudioIntelligenceTimeline, MediaAssetDto]] = {}

    def latest(
        self, asset_id: str
    ) -> Result[tuple[AudioIntelligenceTimeline, MediaAssetDto] | None, object]:
        return Ok(self.stored.get(asset_id))


class FakeAudioAnalyzer:
    def __init__(self, catalog: FakeAudioCatalog, env: "AlignEnv") -> None:
        self.calls = 0
        self._catalog = catalog
        self._env = env

    def execute(
        self, command: AnalyzeAudioCommand, ctx: JobContext
    ) -> Result[AnalysisResult, object]:
        self.calls += 1
        asset = self._env.asset(command.source_asset_id)
        tl = self._env.audio_timeline(command.source_asset_id)
        self._catalog.stored[command.source_asset_id] = (tl, asset)
        return Ok(AnalysisResult(tl, asset, asset, asset, asset, created=True))


class FakeContentCatalog:
    def latest(self, asset_id: str) -> Result[None, object]:
        return Ok(None)


class FakeContentAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command: object, ctx: JobContext) -> Result[object, object]:
        self.calls += 1
        raise AssertionError("content analysis must not be requested in these tests")


class CountingVideoAnalyzer:
    """The real analyzer, restricted to shots, that remembers how often it computed."""

    def __init__(self, env: "AlignEnv") -> None:
        self.calls = 0
        self._env = env

    def execute(self, command: AnalyzeVideoCommand, ctx: JobContext):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self._env.video.execute(replace(command, profile=self._env.shots_profile), ctx)


@dataclass
class AlignEnv:
    library: MediaLibrary
    paths: AppPaths
    clock: Clock
    root: Path
    inspector: InspectMedia
    video: AnalyzeVideo
    audio_catalog: FakeAudioCatalog = field(default_factory=FakeAudioCatalog)
    content_analyzer: FakeContentAnalyzer = field(default_factory=FakeContentAnalyzer)
    embedder: TextEmbedder | None = None
    audio_analyzer: FakeAudioAnalyzer = field(init=False)
    video_analyzer: CountingVideoAnalyzer = field(init=False)

    def __post_init__(self) -> None:
        self.audio_analyzer = FakeAudioAnalyzer(self.audio_catalog, self)
        self.video_analyzer = CountingVideoAnalyzer(self)

    @property
    def shots_profile(self):  # type: ignore[no-untyped-def]
        full = get_profile("triage")
        return replace(full, analyzers=tuple(a for a in full.analyzers if a is AnalyzerId.SHOTS))

    def import_clip(self, path: Path) -> str:
        result = self.library.import_file(path)
        assert isinstance(result, Ok), result
        return result.value.asset.id

    def asset(self, asset_id: str) -> MediaAssetDto:
        found = self.library.get(asset_id)
        assert isinstance(found, Ok)
        return found.value

    def audio_timeline(self, asset_id: str, offset: float = 0.0) -> AudioIntelligenceTimeline:
        tl = make_timeline(WORDS, duration=2.5, offset=offset)
        return replace(tl, metadata=replace(tl.metadata, source_asset_id=asset_id))

    def analyze_video(self, asset_id: str) -> None:
        result = self.video.execute(
            AnalyzeVideoCommand(asset_id, self.shots_profile), JobContext.detached()
        )
        assert isinstance(result, Ok), result

    def aligner(self) -> AlignAudioVisual:
        resolver = UpstreamResolver(
            self.audio_catalog,  # type: ignore[arg-type]
            self.audio_analyzer,  # type: ignore[arg-type]
            VideoResults(self.library),
            self.video_analyzer,
            FakeContentCatalog(),  # type: ignore[arg-type]
            self.content_analyzer,  # type: ignore[arg-type]
            self.inspector,
            self.inspector,
        )
        return AlignAudioVisual(self.library, resolver, self.paths, self.clock, self.embedder)

    def align(
        self, command: AlignCommand, use_case: AlignAudioVisual | None = None
    ) -> AlignmentResult:
        result = (use_case or self.aligner()).execute(command, JobContext.detached())
        assert isinstance(result, Ok), result
        return result.value

    def refused(self, command: AlignCommand) -> Err:  # type: ignore[type-arg]
        result = self.aligner().execute(command, JobContext.detached())
        assert isinstance(result, Err), result
        return result


def build_env(root: Path) -> AlignEnv:
    container = Container()
    paths = AppPaths.under_root(root / "app")
    runner = SubprocessRunner()
    clock = FixedClock()
    container.register_instance(AppPaths, paths)
    container.register_instance(Clock, clock)
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, runner)
    MediaLibraryModule().register(container)
    library = container.resolve(MediaLibrary)
    inspector = InspectMedia(library, FfprobeProber(runner), paths, clock)
    video = AnalyzeVideo(
        library,
        VideoResolver(inspector),
        FfmpegFrameSource(runner),
        classical_analyzers(),
        paths,
    )
    (root / "media").mkdir(parents=True, exist_ok=True)
    return AlignEnv(library, paths, clock, root / "media", inspector, video)


@pytest.fixture
def aenv(tmp_path: Path) -> AlignEnv:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and ffprobe are required")
    return build_env(tmp_path)


Factory = Callable[[Path], Path]
