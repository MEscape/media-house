# ruff: noqa: S603, S607 - builds the test file with the real FFmpeg
"""The FFmpeg ``MediaAnalyzer`` against a real file: a moving picture, one second of black and
silence in the middle, then picture and sound again."""

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from media_house.core.application.ports import TimeSpan
from media_house.core.infrastructure.ffmpeg_media_analyzer import FfmpegMediaAnalyzer
from media_house.core.infrastructure.process import SubprocessRunner
from media_house.shared.concurrency import CancellationToken

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg"),
]
TOKEN = CancellationToken()


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("analysis") / "gap.mp4"
    graph = (
        "testsrc2=s=320x180:r=25:d=2[v0];color=c=black:s=320x180:r=25:d=1[v1];"
        "testsrc2=s=320x180:r=25:d=2[v2];[v0][v1][v2]concat=n=3:v=1:a=0,format=yuv420p[v];"
        "sine=f=440:d=2:r=48000[a0];anullsrc=r=48000:cl=stereo:d=1[a1];"
        "sine=f=440:d=2:r=48000[a2];[a0]aformat=channel_layouts=stereo[b0];"
        "[a2]aformat=channel_layouts=stereo[b2];[b0][a1][b2]concat=n=3:v=0:a=1[a]"
    )
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac", str(target),
        ],
        check=True,
    )  # fmt: skip
    return target


@pytest.fixture(scope="module")
def analyzer() -> FfmpegMediaAnalyzer:
    return FfmpegMediaAnalyzer(SubprocessRunner())


def _near(span: TimeSpan, start: float, end: float, tolerance: float = 0.15) -> bool:
    return (
        abs(float(span.start) - start) < tolerance
        and span.end is not None
        and abs(float(span.end) - end) < tolerance
    )


def test_the_capabilities_name_the_tool_and_what_it_can_do(analyzer: FfmpegMediaAnalyzer) -> None:
    capabilities = analyzer.capabilities()

    assert capabilities.identity.startswith("ffmpeg ")
    assert {"black_spans", "silent_spans", "loudness", "decode"} <= capabilities.operations


def test_the_black_second_is_found(clip: Path, analyzer: FfmpegMediaAnalyzer) -> None:
    spans = analyzer.black_spans(
        clip, min_seconds=0.3, picture_threshold=0.98, pixel_threshold=0.1, cancellation=TOKEN
    )

    assert len(spans) == 1
    assert _near(spans[0], 2.0, 3.0)


def test_the_silent_second_is_found(clip: Path, analyzer: FfmpegMediaAnalyzer) -> None:
    spans = analyzer.silent_spans(clip, min_seconds=0.5, noise_db=-50.0, cancellation=TOKEN)

    assert len(spans) == 1
    assert _near(spans[0], 2.0, 3.0, tolerance=0.2)


def test_the_still_black_second_is_also_a_frozen_one(
    clip: Path, analyzer: FfmpegMediaAnalyzer
) -> None:
    spans = analyzer.frozen_spans(clip, min_seconds=0.5, noise_db=-60.0, cancellation=TOKEN)

    assert any(_near(s, 2.0, 3.0, tolerance=0.25) for s in spans)


def test_loudness_of_a_tone_with_a_gap_is_measured(
    clip: Path, analyzer: FfmpegMediaAnalyzer
) -> None:
    reading = analyzer.loudness(clip, span=None, cancellation=TOKEN)

    assert reading.has_audio and reading.sample_rate == 48000 and reading.channels == 2
    assert reading.integrated_lufs is not None and reading.integrated_lufs < 0
    assert reading.true_peak_dbtp is not None


def test_a_good_file_decodes_to_the_end_and_a_truncated_one_does_not(
    clip: Path, analyzer: FfmpegMediaAnalyzer, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(clip.read_bytes()[: clip.stat().st_size // 2])

    assert analyzer.decode(clip, cancellation=TOKEN).complete
    assert not analyzer.decode(broken, cancellation=TOKEN).complete


def test_pictures_are_sampled_and_the_black_one_is_black(
    clip: Path, analyzer: FfmpegMediaAnalyzer, tmp_path: Path
) -> None:
    samples = analyzer.sample_frames(
        clip, [Fraction(1), Fraction(5, 2)], into=tmp_path, cancellation=TOKEN
    )

    moving, black = samples
    assert black.black_fraction > 0.99 and moving.black_fraction < 0.5
    assert moving.path is not None and moving.path.is_file()
    assert moving.sharpness > black.sharpness
    same = analyzer.compare_frames(moving.path, moving.path)
    different = analyzer.compare_frames(moving.path, black.path or moving.path)
    assert same.similarity == 1.0 and different.similarity < same.similarity
