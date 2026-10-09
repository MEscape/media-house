"""Accuracy regression on a golden set of programmatically generated clips (ground truth known).

Nothing is committed: every clip is rendered here from seeded textures. The thresholds are the
regression bar: shot detection F1 with a frame tolerance, a camera-movement confusion matrix and
quality metrics ordered by (and close to) the synthetic truth.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from media_house.modules.video_intelligence.application.contracts import (
    BoundaryKind,
    CameraMovement,
    VideoAnalysis,
)
from tests.integration.video_intelligence.conftest import VideoEnv, build_video_env
from tests.support import vi_footage as vf

pytestmark = pytest.mark.integration

type Programme = tuple[vf.FrameFn, int, list[int]]


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> VideoEnv:
    import shutil

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and ffprobe are required")
    return build_video_env(tmp_path_factory.mktemp("accuracy"))


class Bench:
    """Renders and analyses clips once each (the golden set shares a library)."""

    def __init__(self, env: VideoEnv, root: Path) -> None:
        self.env, self.root = env, root
        self.results: dict[str, VideoAnalysis] = {}

    def run(
        self, name: str, frame: vf.FrameFn, frames: int, profile: str = "standard"
    ) -> VideoAnalysis:
        key = f"{name}/{profile}"
        if key not in self.results:
            clip = vf.write(self.root / f"{name}.mp4", frame, frames)
            self.results[key] = self.env.analyze(self.env.import_clip(clip), profile).analysis
        return self.results[key]


@pytest.fixture(scope="module")
def bench(lab: VideoEnv, tmp_path_factory: pytest.TempPathFactory) -> Bench:
    return Bench(lab, tmp_path_factory.mktemp("clips"))


# --- shot boundaries ------------------------------------------------------------------------
def _cuts() -> Programme:
    return vf.edit(
        [vf.Segment(vf.still(1), 40), vf.Segment(vf.still(2), 40), vf.Segment(vf.pan(3, 2.0), 40)]
    )


def _moving_cut() -> Programme:
    return vf.edit([vf.Segment(vf.pan(1, 1.5), 50), vf.Segment(vf.pan(2, -1.5), 50)])


def _flash_and_cut() -> Programme:
    return vf.edit([vf.Segment(vf.still(1), 60, flash_at=(30,)), vf.Segment(vf.still(2), 50)])


def _dissolve() -> Programme:
    return vf.edit([vf.Segment(vf.still(1), 50), vf.Segment(vf.still(2), 50, dissolve=12)])


def _through_black() -> Programme:
    frame, total, boundary = vf.through_black(vf.still(1), vf.still(2), 8, 6, 30)
    return frame, total, [boundary]


def _many_short() -> Programme:
    return vf.edit([vf.Segment(vf.still(10 + k), 20) for k in range(8)])


GOLDEN: dict[str, tuple[Callable[[], Programme], int]] = {
    # name: (programme, tolerance in frames)
    "cuts": (_cuts, 1),
    "moving_cut": (_moving_cut, 1),
    "flash_and_cut": (_flash_and_cut, 1),
    "dissolve": (_dissolve, 3),
    "through_black": (_through_black, 3),
    "many_short": (_many_short, 1),
}


@dataclass(frozen=True, slots=True)
class Score:
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def f1(self) -> float:
        denominator = 2 * self.true_positive + self.false_positive + self.false_negative
        return 2 * self.true_positive / denominator if denominator else 1.0


def _score(found: list[int], truth: list[int], tolerance: int) -> Score:
    unmatched = list(truth)
    hits = 0
    for frame in found:
        match = next((t for t in unmatched if abs(t - frame) <= tolerance), None)
        if match is not None:
            unmatched.remove(match)
            hits += 1
    return Score(hits, len(found) - hits, len(unmatched))


def _boundaries(analysis: VideoAnalysis) -> list[int]:
    return [s.range.start.frame for s in analysis.shots[1:]]


@pytest.mark.parametrize("name", list(GOLDEN))
def test_each_golden_programme_finds_exactly_its_boundaries(bench: Bench, name: str) -> None:
    programme, tolerance = GOLDEN[name]
    frame, total, truth = programme()

    found = _boundaries(bench.run(name, frame, total))

    score = _score(found, truth, tolerance)
    assert (score.false_positive, score.false_negative) == (0, 0), (found, truth)


def test_shot_detection_f1_over_the_golden_set_meets_the_bar(bench: Bench) -> None:
    totals = [0, 0, 0]
    for name, (programme, tolerance) in GOLDEN.items():
        frame, total, truth = programme()
        score = _score(_boundaries(bench.run(name, frame, total)), truth, tolerance)
        totals = [
            a + b
            for a, b in zip(
                totals,
                (score.true_positive, score.false_positive, score.false_negative),
                strict=True,
            )
        ]

    assert Score(*totals).f1 >= 0.95


def test_cuts_are_exact_to_the_frame_and_carry_exact_times(bench: Bench) -> None:
    frame, total, truth = _cuts()

    analysis = bench.run("cuts", frame, total)

    assert _boundaries(analysis) == truth
    first, second = analysis.shots[0], analysis.shots[1]
    assert second.range.start.frame == 40
    assert second.range.start.pts == 40 * 512  # 30 fps in a 1/15360 time base
    assert first.range.end == second.range.start
    assert analysis.shots[-1].range.end.frame == total
    assert analysis.shots[-1].range.end.timebase.denominator == 15360


def test_a_flash_is_reported_and_not_counted_as_a_cut(bench: Bench) -> None:
    frame, total, _ = _flash_and_cut()

    analysis = bench.run("flash_and_cut", frame, total)

    assert len(analysis.shots) == 2
    assert any("flash" in w for w in analysis.warnings)


def test_boundary_kinds_are_told_apart(bench: Bench) -> None:
    kinds = {}
    for name in ("cuts", "dissolve", "through_black"):
        programme, _ = GOLDEN[name]
        frame, total, _truth = programme()
        kinds[name] = bench.run(name, frame, total).shots[1].boundary_in

    assert kinds["cuts"].kind is BoundaryKind.HARD_CUT and kinds["cuts"].transition is None
    assert kinds["dissolve"].kind is BoundaryKind.DISSOLVE
    assert kinds["dissolve"].transition is not None
    assert kinds["through_black"].kind is BoundaryKind.FADE_THROUGH_BLACK
    assert kinds["through_black"].black_adjacent


# --- camera movement ------------------------------------------------------------------------
MOVEMENTS: dict[str, tuple[Callable[[], vf.FrameFn], CameraMovement]] = {
    "static": (lambda: vf.still(1), CameraMovement.STATIC),
    "pan_right": (lambda: vf.pan(2, 2.0), CameraMovement.PAN),
    "pan_left": (lambda: vf.pan(2, -2.0), CameraMovement.PAN),
    "pan_slow": (lambda: vf.pan(2, 0.6), CameraMovement.PAN),
    "tilt_down": (lambda: vf.pan(2, 1.5, vertical=True), CameraMovement.TILT),
    "tilt_up": (lambda: vf.pan(2, -1.5, vertical=True), CameraMovement.TILT),
    "push_in": (lambda: vf.push(3, 0.01), CameraMovement.PUSH_IN),
    "pull_out": (lambda: vf.push(3, -0.008), CameraMovement.PULL_OUT),
    "handheld": (lambda: vf.handheld(4, 4), CameraMovement.HANDHELD),
}


def test_camera_movement_confusion_matrix_is_diagonal(bench: Bench) -> None:
    matrix: Counter[tuple[CameraMovement, CameraMovement | None]] = Counter()
    for name, (source, truth) in MOVEMENTS.items():
        analysis = bench.run(name, source(), 60)
        assert len(analysis.shots) == 1, name
        matrix[(truth, analysis.shots[0].camera.movement)] += 1

    wrong = {pair: n for pair, n in matrix.items() if pair[0] != pair[1]}
    assert wrong == {}, matrix


@pytest.mark.parametrize(
    ("name", "degrees"),
    [("pan_right", 0.0), ("pan_left", 180.0), ("tilt_down", 270.0), ("tilt_up", 90.0)],
)
def test_the_direction_is_the_cameras_not_the_pictures(
    bench: Bench, name: str, degrees: float
) -> None:
    source, _ = MOVEMENTS[name]
    camera = bench.run(name, source(), 60).shots[0].camera

    assert camera.direction_degrees is not None
    difference = abs((camera.direction_degrees - degrees + 180) % 360 - 180)
    assert difference < 10, camera.direction_degrees


def test_pan_speed_matches_the_known_speed(bench: Bench) -> None:
    camera = bench.run("pan_right", vf.pan(2, 2.0), 60).shots[0].camera

    # 2 px per frame at 30 fps on a 320 px wide picture
    assert camera.speed == pytest.approx(2 * 30 / 320, rel=0.05)
    assert camera.intensity is not None and 0.2 < camera.intensity < 0.6


def test_a_static_camera_has_no_shake_and_handheld_has(bench: Bench) -> None:
    still = bench.run("static", vf.still(1), 60).shots[0].camera
    shaky = bench.run("handheld", vf.handheld(4, 4), 60).shots[0].camera

    assert still.shake_residual is not None and shaky.shake_residual is not None
    assert still.shake_residual < 0.001 < 0.004 < shaky.shake_residual


def test_a_textureless_picture_leaves_the_movement_unknown(bench: Bench) -> None:
    flat = bench.run("flat", lambda _i: np.full((vf.HEIGHT, vf.WIDTH), 0.5), 60).shots[0]

    assert flat.camera.movement is None or flat.camera.confidence is not None
    if flat.camera.movement is None:
        assert flat.camera.reasons == ("too_few_reliable_motion_estimates",)


def test_a_zoom_is_not_reported_as_a_pan(bench: Bench) -> None:
    camera = bench.run("push_in", vf.push(3, 0.01), 60).shots[0].camera

    assert camera.zoom_rate == pytest.approx(0.30, rel=0.15)
    assert camera.speed is not None and camera.speed < 0.02


# --- quality metrics ------------------------------------------------------------------------
def test_sharpness_falls_as_the_picture_is_blurred(bench: Bench) -> None:
    sharpness = [
        bench.run(f"blur_{s}", vf.blurred(vf.still(1), s) if s else vf.still(1), 40)
        .shots[0]
        .quality.metrics.sharpness
        for s in (0.0, 1.5, 3.0, 5.0)
    ]

    assert all(v is not None for v in sharpness)
    assert sharpness == sorted(sharpness, reverse=True)  # type: ignore[type-var]
    assert sharpness[0] > 2 * sharpness[-1]  # type: ignore[operator]


def test_soft_focus_is_reported_for_a_blurred_picture_only(bench: Bench) -> None:
    sharp = bench.run("blur_0.0", vf.still(1), 40).shots[0].quality
    soft = bench.run("blur_5.0", vf.blurred(vf.still(1), 5.0), 40).shots[0].quality

    assert "soft_focus" not in sharp.reasons
    assert "soft_focus" in soft.reasons


def test_the_noise_estimate_follows_the_added_noise(bench: Bench) -> None:
    levels = (0.0, 0.02, 0.05, 0.1)
    estimates = [
        bench.run(f"noise_{n}", vf.noisy(vf.still(1), n) if n else vf.still(1), 40)
        .shots[0]
        .quality.metrics.noise_sigma
        for n in levels
    ]

    assert estimates == sorted(estimates)  # type: ignore[type-var]
    assert estimates[2] == pytest.approx(0.05, rel=0.4)
    assert estimates[3] == pytest.approx(0.1, rel=0.4)


def test_exposure_follows_the_gain_and_underexposure_is_described(bench: Bench) -> None:
    medians = []
    for gain in (1.0, 0.5, 0.25):
        quality = bench.run(f"gain_{gain}", vf.dimmed(vf.still(1), gain), 40).shots[0].quality
        medians.append(quality.metrics.luma_p50)
        if gain == 0.25:
            assert "dark_exposure" in quality.reasons

    assert medians == sorted(medians, reverse=True)  # type: ignore[type-var]


def test_flat_footage_is_not_reported_as_underexposed(bench: Bench) -> None:
    def flat(i: int) -> np.ndarray:
        return np.asarray(0.30 + 0.12 * vf.still(1)(i))  # lifted blacks, narrow range

    quality = bench.run("flat_log", flat, 40).shots[0].quality

    assert "flat_or_log_footage" in quality.reasons
    assert "dark_exposure" not in quality.reasons


def test_flicker_is_measured_on_every_frame(bench: Bench) -> None:
    def flicker(i: int) -> np.ndarray:
        return np.asarray(vf.still(1)(i) * (0.85 if i % 2 else 1.0))

    steady = bench.run("steady_light", vf.still(1), 60).shots[0].quality.metrics.flicker
    flickering = bench.run("flicker", flicker, 60).shots[0].quality.metrics.flicker

    assert steady is not None and flickering is not None
    assert flickering > 10 * steady


def test_a_shot_exposes_stable_handles_and_keyframes(bench: Bench) -> None:
    analysis = bench.run("cuts", *_cuts()[:2])
    still_a, still_b, panning = analysis.shots

    for shot in (still_a, still_b):  # nothing moves: the margins are as long as the shot allows
        assert shot.handles.ok
        assert shot.handles.head_frames == shot.handles.tail_frames == shot.frames // 2
    assert panning.handles.head_frames == 0  # the picture never settles during a pan
    for shot in analysis.shots:
        assert shot.keyframes.ok and shot.keyframes.representative is not None
        assert shot.range.contains_frame(shot.keyframes.representative.frame)
        assert shot.range.contains_frame(shot.keyframes.sharpest.frame)  # type: ignore[union-attr]


def test_blur_helper_really_blurs() -> None:
    # guards the golden set itself: a ground truth that did nothing would make the tests vacuous
    picture = vf.texture(1)
    assert np.abs(gaussian_filter(picture, 3.0) - picture).mean() > 0.01
