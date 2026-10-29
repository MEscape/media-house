"""Planning stays cheap: no model, no decoding, bounded work per observation."""

import time

from tests.support.refr_fakes import T916, analyse, shot, walk

#: Generous: the real figure is a few milliseconds per shot; this only catches a quadratic scan.
BUDGET_SECONDS = 30.0


def test_a_ten_minute_shot_with_six_thousand_observations_is_planned_cheaply() -> None:
    track = walk("p", (0.1, 0.2, 0.25, 0.9), (0.7, 0.2, 0.85, 0.9), end=600, step=0.1)
    start = time.perf_counter()

    analysis = analyse(shot("s0", 0, 600, (track,)), target=T916)

    assert time.perf_counter() - start < BUDGET_SECONDS
    assert len(analysis.recommendations[0].keyframes) < 40  # simplified, not one per observation
    assert analysis.metrics.samples == 6001 and analysis.metrics.model_calls == 0


def test_many_shots_are_planned_and_an_unchanged_library_is_not_planned_again() -> None:
    shots = tuple(
        shot(
            f"s{i}",
            i * 10,
            i * 10 + 10,
            (
                walk(
                    "p", (0.1, 0.2, 0.25, 0.9), (0.4, 0.2, 0.55, 0.9),
                    start=i * 10, end=i * 10 + 10, step=0.5,
                ),
            ),
        )
        for i in range(300)
    )  # fmt: skip
    start = time.perf_counter()
    first = analyse(*shots, target=T916)
    planned = time.perf_counter() - start
    again = analyse(*shots, target=T916, previous=first)

    assert planned < BUDGET_SECONDS
    assert first.metrics.evaluated == 300 and again.metrics.reused == 300
    assert again.metrics.samples == 0  # nothing was read again
    assert again.recommendations == first.recommendations
