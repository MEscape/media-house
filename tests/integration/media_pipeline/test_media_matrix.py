"""Hard media through the real foundation stages, plus invalidation, concurrency and interruption.

Only inspect, thumbnail, proxy and audio improvement run (no speech or vision models), which is
enough to prove stream handling, reuse and source safety with the real modules.
"""

import hashlib
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.contracts import (
    MediaPipeline,
    PipelineReport,
    RunPipelineCommand,
    StageSettings,
    TaskKind,
    TaskStatus,
)
from media_house.modules.proxy_manager.application.contracts import DEFAULT_PROFILE, profile_names
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from tests.support import proxy_media as media
from tests.support.media_factories import ffmpeg

pytestmark = pytest.mark.integration

K = TaskKind
FOUNDATION = frozenset({K.INSPECT, K.THUMBNAIL, K.PROXY, K.AUDIO_IMPROVEMENT})


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    started = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    yield started
    started.shutdown()


def run(
    app: Application,
    paths: list[Path],
    stages: frozenset[TaskKind] = FOUNDATION,
    *,
    ctx: JobContext | None = None,
    settings: StageSettings | None = None,
    observer: object = None,
) -> PipelineReport:
    result = app.container.resolve(MediaPipeline).execute(
        RunPipelineCommand(paths=paths, stages=stages, settings=settings or StageSettings()),
        ctx or JobContext.detached(),
        observer,  # type: ignore[arg-type]
    )
    assert isinstance(result, Ok)
    return result.value


def statuses(report: PipelineReport, index: int = 0) -> dict[TaskKind, TaskStatus | None]:
    return {t.kind: t.status for t in report.assets[index].tasks}


# --- stream shapes ------------------------------------------------------------------------------
Make = Callable[[Path], Path]


def variable_frame_rate_clip(path: Path) -> Path:
    """Clean variable frame rate: frames dropped at random keep their original timestamps."""
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-vf", "select='gt(random(1),0.4)'", "-fps_mode", "vfr",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path


SHAPES: dict[str, tuple[Make, dict[TaskKind, TaskStatus]]] = {
    "video_with_audio": (
        lambda p: media.cfr_with_audio(p),
        {
            K.THUMBNAIL: TaskStatus.SUCCEEDED,
            K.PROXY: TaskStatus.SUCCEEDED,
            K.AUDIO_IMPROVEMENT: TaskStatus.SUCCEEDED,
        },
    ),
    "video_only": (
        lambda p: media.video_only(p),
        {
            K.THUMBNAIL: TaskStatus.SUCCEEDED,
            K.PROXY: TaskStatus.SUCCEEDED,
            K.AUDIO_IMPROVEMENT: TaskStatus.SKIPPED_UNAVAILABLE,
        },
    ),
    "audio_only": (
        lambda p: media.audio_only(p),
        {
            K.THUMBNAIL: TaskStatus.SKIPPED_UNAVAILABLE,
            K.PROXY: TaskStatus.SKIPPED_UNAVAILABLE,
            K.AUDIO_IMPROVEMENT: TaskStatus.SUCCEEDED,
        },
    ),
    "two_audio_tracks": (
        lambda p: media.two_audio_tracks(p),
        {
            K.THUMBNAIL: TaskStatus.SUCCEEDED,
            K.PROXY: TaskStatus.SUCCEEDED,
            K.AUDIO_IMPROVEMENT: TaskStatus.SUCCEEDED,
        },
    ),
    "rotated": (
        lambda p: media.rotated(p),
        {K.THUMBNAIL: TaskStatus.SUCCEEDED, K.PROXY: TaskStatus.SUCCEEDED},
    ),
    "variable_frame_rate": (
        lambda p: variable_frame_rate_clip(p),
        {K.THUMBNAIL: TaskStatus.SUCCEEDED, K.PROXY: TaskStatus.SUCCEEDED},
    ),
    # Wobbling timestamps with duplicates: the inspection owner judges this file damaged, and the
    # pipeline must say so instead of treating it as ordinary variable-frame-rate footage.
    "irregular_timestamps": (
        lambda p: media.variable_frame_rate(p),
        {
            K.INSPECT: TaskStatus.FAILED,
            K.THUMBNAIL: TaskStatus.SUCCEEDED,
            K.PROXY: TaskStatus.SUCCEEDED,
        },
    ),
    "late_start": (
        lambda p: media.late_start(p),
        {K.THUMBNAIL: TaskStatus.SUCCEEDED, K.PROXY: TaskStatus.SUCCEEDED},
    ),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_each_stream_shape_gets_the_right_task_outcomes_and_the_source_is_untouched(
    app: Application, tmp_path: Path, shape: str
) -> None:
    make, expected = SHAPES[shape]
    clip = make(tmp_path / f"{shape}.mp4")
    before = digest(clip)

    report = run(app, [clip])
    got = statuses(report)

    assert report.ok is (K.INSPECT not in expected), report.to_dict()
    assert got[K.INSPECT] is expected.get(K.INSPECT, TaskStatus.SUCCEEDED)
    for kind, status in expected.items():
        assert got[kind] is status, (kind, got[kind], report.assets[0].task(kind))
    assert digest(clip) == before


def test_an_unfinished_download_is_reported_as_damaged_and_fails_the_run(
    app: Application, tmp_path: Path
) -> None:
    whole = tmp_path / "whole.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(whole),
    )  # fmt: skip
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(whole.read_bytes()[: int(whole.stat().st_size * 0.6)])
    good = media.cfr_with_audio(tmp_path / "good.mp4", seconds=2)
    before = digest(cut)

    report = run(app, [cut, good], frozenset({K.INSPECT, K.THUMBNAIL}))
    by_name = {a.name: a for a in report.assets}

    damaged = by_name["cut"].task(K.INSPECT)
    assert damaged is not None and damaged.status is TaskStatus.FAILED
    assert "damaged" in damaged.detail
    assert any("integrity." in w for w in damaged.warnings)  # the evidence is in the report
    assert by_name["good"].task(K.INSPECT).status is TaskStatus.SUCCEEDED  # type: ignore[union-attr]
    assert not report.ok and report.exit_code == 1
    assert digest(cut) == before


def test_a_file_whose_index_is_missing_is_refused_at_import_and_reported(
    app: Application, tmp_path: Path
) -> None:
    whole = media.cfr_with_audio(tmp_path / "whole.mp4", seconds=3)
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(whole.read_bytes()[: whole.stat().st_size // 3])

    report = run(app, [cut])

    assert [f.source for f in report.failures] == [str(cut)]
    assert report.assets == () and not report.ok


# --- invalidation -------------------------------------------------------------------------------
def test_changing_one_source_recomputes_only_that_source(app: Application, tmp_path: Path) -> None:
    keep = media.cfr_with_audio(tmp_path / "keep.mp4", seconds=1)
    change = media.cfr_with_audio(tmp_path / "change.mp4", seconds=1)
    run(app, [keep, change], frozenset({K.INSPECT, K.PROXY}))

    media.cfr_with_audio(change, seconds=2)  # same name, different content
    second = run(app, [keep, change], frozenset({K.INSPECT, K.PROXY}))
    by_name = {a.name: {t.kind: t.status for t in a.tasks} for a in second.assets}

    assert by_name["keep"][K.INSPECT] is TaskStatus.REUSED
    assert by_name["keep"][K.PROXY] is TaskStatus.REUSED
    assert by_name["change"][K.INSPECT] is TaskStatus.SUCCEEDED
    assert by_name["change"][K.PROXY] is TaskStatus.SUCCEEDED


def test_changing_the_proxy_setting_recomputes_the_proxy_but_not_the_inspection(
    app: Application, tmp_path: Path
) -> None:
    other = [p for p in profile_names() if p != DEFAULT_PROFILE]
    if not other:
        pytest.skip("only one proxy profile exists")
    clip = media.cfr_with_audio(tmp_path / "clip.mp4", seconds=1)
    stages = frozenset({K.INSPECT, K.PROXY})
    run(app, [clip], stages)

    again = run(app, [clip], stages, settings=StageSettings(proxy_profile=other[0]))
    got = statuses(again)

    assert got[K.INSPECT] is TaskStatus.REUSED
    assert got[K.PROXY] is TaskStatus.SUCCEEDED


# --- concurrency and interruption ---------------------------------------------------------------
def test_two_runs_on_the_same_file_at_once_compute_each_result_exactly_once(
    app: Application, tmp_path: Path
) -> None:
    clip = media.cfr_with_audio(tmp_path / "clip.mp4", seconds=2)
    stages = frozenset({K.INSPECT, K.THUMBNAIL, K.PROXY})
    reports: list[PipelineReport] = []
    errors: list[BaseException] = []
    gate = threading.Barrier(2)

    def worker() -> None:
        try:
            gate.wait(timeout=10)
            reports.append(run(app, [clip], stages))
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not errors, errors
    assert len(reports) == 2 and all(r.ok for r in reports), [r.to_dict() for r in reports]
    # Inspection and proxy are coordinated by their owners: exactly one run computes each. The
    # library cannot say who created a thumbnail when two runs race, so both may report it
    # computed; what must hold is that only one thumbnail asset exists.
    for kind in (K.INSPECT, K.PROXY):
        computed = sum(1 for r in reports if statuses(r)[kind] is TaskStatus.SUCCEEDED)
        assert computed == 1, (kind, [statuses(r)[kind] for r in reports])
    library = app.container.resolve(MediaLibrary)
    (source,) = [a for a in library.search().items if a.derivation is None]  # one original
    operations = [a.derivation.operation for a in library.list_derived(source.id) if a.derivation]
    assert operations.count("thumbnail") == 1
    assert operations.count("media_inspection") == 1


def test_cancelling_mid_encode_leaves_nothing_reusable_that_is_wrong(
    app: Application, tmp_path: Path
) -> None:
    clip = media.cfr_with_audio(tmp_path / "long.mp4", seconds=40, size="1280x720")
    before = digest(clip)
    token = CancellationToken()

    class CancelWhenProxyStarts:
        def task_started(self, task_id: object, lane: object) -> None:
            if getattr(task_id, "kind", None) is K.PROXY:
                threading.Timer(0.3, token.cancel).start()

        def task_progress(self, task_id: object, progress: object) -> None: ...

        def task_finished(self, record: object) -> None: ...

    stages = frozenset({K.INSPECT, K.PROXY})
    cancelled = run(
        app,
        [clip],
        stages,
        ctx=JobContext("test", token, JobContext.detached().progress),
        observer=CancelWhenProxyStarts(),
    )

    assert cancelled.cancelled and not cancelled.ok
    assert statuses(cancelled)[K.PROXY] in (TaskStatus.CANCELLED, TaskStatus.SUCCEEDED)
    assert digest(clip) == before

    resumed = run(app, [clip], stages)
    proxy = resumed.assets[0].task(K.PROXY)

    assert resumed.ok
    assert proxy is not None and proxy.status in (TaskStatus.SUCCEEDED, TaskStatus.REUSED)
    assert proxy.facts["verdict"] in ("valid", "valid_with_warnings", "usable")
    assert digest(clip) == before
