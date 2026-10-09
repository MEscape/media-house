"""Shot boundaries from hand-made signals: every rule is testable without media."""

from dataclasses import replace

import pytest

from media_house.modules.video_intelligence.domain.errors import InvalidProfile
from media_house.modules.video_intelligence.domain.profiles import ShotSettings
from media_house.modules.video_intelligence.domain.shot_detection import detect_transitions
from media_house.modules.video_intelligence.domain.signals import ShotSignals
from media_house.modules.video_intelligence.domain.values import BoundaryKind
from tests.support.vi_signals import QUIET, shot_signals

SETTINGS = ShotSettings()


def test_a_hard_cut_is_found_at_its_first_new_frame() -> None:
    found = detect_transitions(shot_signals(cuts=(40, 90)), SETTINGS).transitions

    assert [(t.frame, t.kind) for t in found] == [
        (40, BoundaryKind.HARD_CUT),
        (90, BoundaryKind.HARD_CUT),
    ]
    assert all(t.first == t.last == t.frame and not t.gradual for t in found)


def test_a_quiet_clip_has_no_boundary() -> None:
    assert detect_transitions(shot_signals(), SETTINGS).transitions == ()


def test_a_single_frame_flash_is_not_a_cut_and_is_reported() -> None:
    detection = detect_transitions(shot_signals(flashes=(50,)), SETTINGS)

    assert detection.transitions == ()
    assert set(detection.flashes) == {50, 51}


def test_a_flash_next_to_a_real_cut_does_not_hide_the_cut() -> None:
    detection = detect_transitions(shot_signals(cuts=(80,), flashes=(30,)), SETTINGS)

    assert [t.frame for t in detection.transitions] == [80]
    assert detection.flashes


def test_continuous_fast_motion_is_not_a_series_of_cuts() -> None:
    busy = shot_signals(steps=lambda _i: 0.14)  # every step is large, none stands out

    assert detect_transitions(busy, SETTINGS).transitions == ()


def test_one_cut_seen_twice_in_a_row_counts_once() -> None:
    signals = shot_signals(cuts=(40, 41))

    assert [t.frame for t in detect_transitions(signals, SETTINGS).transitions] == [40]


def test_the_larger_of_two_close_candidates_wins() -> None:
    signals = shot_signals(cuts=(40,))
    diff1, diff2 = list(signals.diff1), list(signals.diff2)
    diff1[41] = diff2[41] = diff2[42] = 0.5  # a bigger step right after, across the same cut
    stronger = replace(signals, diff1=tuple(diff1), diff2=tuple(diff2))

    assert [t.frame for t in detect_transitions(stronger, SETTINGS).transitions] == [41]


def test_cut_confidence_grows_with_the_size_of_the_change() -> None:
    weak = shot_signals(cuts=(40,))
    weak_diff = list(weak.diff1)
    weak_diff[40] = 0.13
    weak = replace(weak, diff1=tuple(weak_diff))
    strong = shot_signals(cuts=(40,))

    low = detect_transitions(weak, SETTINGS).transitions[0].confidence
    high = detect_transitions(strong, SETTINGS).transitions[0].confidence

    assert 0 < low < high <= 1


def test_a_cut_beside_black_frames_says_so() -> None:
    signals = shot_signals(
        cuts=(40,),
        luma=lambda i: 0.01 if i >= 40 else 0.5,
        spread=lambda i: 0.0 if i >= 40 else 0.2,
    )

    assert detect_transitions(signals, SETTINGS).transitions[0].black_adjacent


def _fade(frame: int) -> float:
    """Shot A fades to black over 8 frames (30-37), black 38-43, shot B fades in over 44-51."""
    if frame < 30 or frame > 51:
        return 0.5
    if frame <= 37:
        return 0.5 * (1 - (frame - 29) / 9)
    if frame <= 43:
        return 0.0
    return 0.5 * (frame - 43) / 9


def test_a_fade_through_black_is_one_gradual_boundary_in_the_middle_of_the_black() -> None:
    black = lambda i: 0.0 if 30 <= i <= 51 and _fade(i) < 0.03 else 0.2  # noqa: E731
    signals = shot_signals(
        frames=90, luma=_fade, spread=black, steps=lambda i: 0.05 if 30 <= i <= 51 else QUIET
    )

    found = detect_transitions(signals, SETTINGS).transitions

    assert [t.kind for t in found] == [BoundaryKind.FADE_THROUGH_BLACK]
    assert 38 <= found[0].frame <= 44
    assert found[0].gradual and found[0].black_adjacent


def test_black_at_the_start_of_the_video_is_not_a_boundary() -> None:
    signals = shot_signals(
        luma=lambda i: 0.5 * min(1.0, i / 10), spread=lambda i: 0.0 if i < 5 else 0.2
    )

    assert detect_transitions(signals, SETTINGS).transitions == ()


def _dissolve_signals(
    frames: int = 120, start: int = 40, length: int = 10, background: float = QUIET
) -> ShotSignals:
    """Steps of ``0.17 / length`` per frame during the dissolve, ``background`` elsewhere."""
    return shot_signals(
        frames=frames,
        steps=lambda i: 0.17 / length if start <= i < start + length else background,
    )


def test_a_dissolve_between_still_shots_is_found_near_its_centre() -> None:
    found = detect_transitions(_dissolve_signals(), SETTINGS).transitions

    assert [t.kind for t in found] == [BoundaryKind.DISSOLVE]
    assert abs(found[0].frame - 45) <= 3
    assert found[0].gradual and found[0].first < found[0].frame <= found[0].last


def test_steady_camera_motion_is_not_a_dissolve() -> None:
    steady = shot_signals(steps=lambda _i: 0.017)  # as much change after the 'dissolve' as in it

    assert detect_transitions(steady, SETTINGS).transitions == ()


def test_a_one_frame_blip_in_busy_motion_is_not_a_dissolve() -> None:
    bursty = shot_signals(steps=lambda i: 0.06 if i % 3 == 0 else QUIET)

    assert detect_transitions(bursty, SETTINGS).transitions == ()


def test_a_dissolve_that_lasts_too_long_is_not_reported() -> None:
    slow = _dissolve_signals(frames=300, start=40, length=140)

    assert detect_transitions(slow, SETTINGS).transitions == ()


def test_boundaries_are_strictly_increasing_and_inside_the_video() -> None:
    signals = shot_signals(cuts=(5, 6, 7, 60, 117, 119))

    frames = [t.frame for t in detect_transitions(signals, SETTINGS).transitions]

    assert frames == sorted(set(frames))
    assert all(0 < f < 120 for f in frames)


@pytest.mark.parametrize(
    "bad",
    [
        {"cut_diff": 0.0},
        {"context_radius": 0},
        {"fade_min_frames": 1},
        {"dissolve_max_frames": 2},
        {"dissolve_min_frames": 1},
        {"dissolve_ratio": -1.0},
    ],
)
def test_nonsense_settings_are_refused_with_the_reason(bad: dict[str, float]) -> None:
    with pytest.raises(InvalidProfile):
        ShotSettings(**bad)  # type: ignore[arg-type]
