"""The pipeline on real files through the real composition root (FFmpeg, library, owners).

Only cheap stages run (no speech or vision models): enough to prove that wiring, reuse and
graph execution hold with the real modules.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.media_pipeline.application.contracts import (
    MediaPipeline,
    PipelineReport,
    RunPipelineCommand,
    TaskKind,
    TaskStatus,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from tests.support.proxy_media import cfr_with_audio, video_only

pytestmark = pytest.mark.integration

K = TaskKind
CHEAP = frozenset({K.INSPECT, K.THUMBNAIL, K.PROXY, K.AUDIO_IMPROVEMENT})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def application(tmp_path: Path) -> Iterator[Application]:
    app = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    yield app
    app.shutdown()


def run(app: Application, paths: list[Path], stages: frozenset[TaskKind]) -> PipelineReport:
    result = app.container.resolve(MediaPipeline).execute(
        RunPipelineCommand(paths=paths, stages=stages), JobContext.detached()
    )
    assert isinstance(result, Ok)
    return result.value


def test_second_run_reuses_everything_and_sources_stay_untouched(
    application: Application, tmp_path: Path
) -> None:
    clip = cfr_with_audio(tmp_path / "clip.mp4", seconds=2)
    before = sha256(clip)

    first = run(application, [clip], CHEAP - {K.AUDIO_IMPROVEMENT})
    second = run(application, [clip], CHEAP - {K.AUDIO_IMPROVEMENT})

    statuses = {t.kind: t.status for t in first.assets[0].tasks if t.lane}
    assert statuses == {
        K.INSPECT: TaskStatus.SUCCEEDED,
        K.THUMBNAIL: TaskStatus.SUCCEEDED,
        K.PROXY: TaskStatus.SUCCEEDED,
    }
    again = {t.kind: t.status for t in second.assets[0].tasks if t.lane}
    assert set(again.values()) == {TaskStatus.REUSED}
    assert first.ok and second.ok
    assert sha256(clip) == before


def test_audio_stages_are_not_applicable_when_the_inspection_shows_no_audio(
    application: Application, tmp_path: Path
) -> None:
    clip = video_only(tmp_path / "silent.mp4", seconds=2)

    report = run(application, [clip], frozenset({K.INSPECT, K.AUDIO_IMPROVEMENT, K.AUDIO_ANALYSIS}))
    tasks = {t.kind: t for t in report.assets[0].tasks}

    assert tasks[K.AUDIO_IMPROVEMENT].status is TaskStatus.SKIPPED_UNAVAILABLE
    assert tasks[K.AUDIO_ANALYSIS].status is TaskStatus.SKIPPED_UNAVAILABLE
    assert tasks[K.INSPECT].status is TaskStatus.SUCCEEDED
    assert report.ok


def test_independent_assets_overlap_and_a_bad_file_does_not_stop_the_others(
    application: Application, tmp_path: Path
) -> None:
    good = cfr_with_audio(tmp_path / "good.mp4", seconds=2)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video at all")

    report = run(application, [broken, good], frozenset({K.INSPECT, K.THUMBNAIL}))

    assert [f.source for f in report.failures] == [str(broken)]
    assert report.assets[0].task(K.THUMBNAIL).status is TaskStatus.SUCCEEDED  # type: ignore[union-attr]
    assert not report.ok  # an input failed, and the report says so
