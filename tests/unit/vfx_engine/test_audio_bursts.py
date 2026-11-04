"""Burst peaks from stored audio bangs (``impulse``) when the picture analysis found no flash."""

from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace

from media_house.modules.audio_intelligence.application.contracts import IMPULSE, AudioEvent
from media_house.modules.audio_visual_alignment.application.contracts import (
    ClockCorrection,
    CorrectionSource,
)
from media_house.modules.vfx_engine.application.views import with_bangs
from media_house.modules.vfx_engine.domain.facts import BangFact, SourceFacts, fr
from media_house.modules.vfx_engine.domain.planner import plan_treatments
from media_house.modules.vfx_engine.domain.profile import PROFILES
from media_house.modules.vfx_engine.domain.values import EffectType, RequestKind
from tests.support.vfx_fakes import clip, facts, inputs, q, request, shots


def with_bang_facts(
    bangs: list[tuple[float, float]], luma: tuple[tuple[float, float], ...] = (), **changes: object
) -> SourceFacts:
    return replace(
        facts(),
        bangs=tuple(
            BangFact(event_id=f"b{i}", source_time=q(t), strength=s)
            for i, (t, s) in enumerate(bangs)
        ),
        luma=tuple((q(t), v) for t, v in luma),
        bang_clock="alignment",
        **changes,  # type: ignore[arg-type]
    )


def freeze_at(source: SourceFacts, *, requests: tuple[object, ...] = ()) -> Fraction | None:
    the_shots = shots(clip(end=8), requests=list(requests))  # type: ignore[arg-type]
    result = plan_treatments(inputs(the_shots, source), PROFILES["fireworks_showcase"])
    t = result.treatments[0]
    if t.time_map is None:
        return None
    return fr(next(s for s in t.time_map.segments if s.kind.value == "freeze").source_start)


def reason(source: SourceFacts) -> str:
    result = plan_treatments(inputs(shots(clip(end=8)), source), PROFILES["fireworks_showcase"])
    slow = next(e for e in result.treatments[0].effects if e.effect_type is EffectType.SLOW_MOTION)
    return " ".join(slow.reasons)


def test_the_strongest_bang_marks_the_burst_and_the_brightest_frame_is_its_peak() -> None:
    # light arrives before the sound: the brightest frame is 0.6 s before the bang is heard
    luma = ((4.0, 0.01), (4.4, 0.08), (4.8, 0.03), (5.0, 0.02), (5.4, 0.02))
    source = with_bang_facts([(2.0, 0.6), (5.0, 0.95), (6.5, 0.3)], luma)

    assert freeze_at(source) == Fraction(22, 5)
    assert "brightest sampled frame at 4.40 s" in reason(source)


def test_a_flat_brightness_signal_keeps_the_bang_time() -> None:
    flat = with_bang_facts([(5.0, 0.95)], ((4.2, 0.0), (4.6, 0.0), (5.2, 0.004)))
    unknown = with_bang_facts([(5.0, 0.95)])

    assert freeze_at(flat) == 5 and "flat" in reason(flat)
    assert freeze_at(unknown) == 5 and "no stored brightness" in reason(unknown)


def test_the_brightest_frame_is_searched_only_inside_the_bounded_window() -> None:
    far = with_bang_facts([(5.0, 0.95)], ((2.0, 0.9), (5.0, 0.01), (7.5, 0.9)))

    assert freeze_at(far) == 5  # 2.0 s and 7.5 s are outside [4.0, 5.5]


def test_picture_flashes_win_and_weak_bangs_do_nothing() -> None:
    both = replace(with_bang_facts([(5.0, 0.95)]), flashes=facts(flashes=[(3.0, 0.9)]).flashes)
    weak = with_bang_facts([(5.0, 0.2)])

    assert freeze_at(both) == 3
    assert freeze_at(weak) is None


def test_a_reveal_request_snaps_to_the_nearest_bangs_peak() -> None:
    source = with_bang_facts([(5.0, 0.3)], ((4.5, 0.01), (4.7, 0.2), (5.2, 0.01)))
    reveal = request(at=5.2, kind=RequestKind.REVEAL)

    assert freeze_at(source, requests=(reveal,)) == Fraction(47, 10)


def test_unknown_clocks_are_named_in_the_reason() -> None:
    assumed = replace(with_bang_facts([(5.0, 0.95)]), bang_clock="assumed")

    assert "clocks assumed equal" in reason(assumed)


def test_audio_impulses_reach_the_picture_clock_through_the_stored_correction() -> None:
    events = (
        AudioEvent(kind=IMPULSE, start=10.25, end=10.3, strength=0.9, confidence=0.8),
        AudioEvent(kind="energy_spike", start=3.0, end=3.2, strength=0.9),
    )
    timeline = SimpleNamespace(events=events)  # the two fields ``with_bangs`` reads
    video_late = ClockCorrection(stream="video", seconds=0.5, source=CorrectionSource.INSPECTION)
    alignment = SimpleNamespace(sync=SimpleNamespace(corrections=(video_late,)))

    aligned = with_bangs(facts(), timeline, alignment)  # type: ignore[arg-type]
    assumed = with_bangs(facts(), timeline, None)  # type: ignore[arg-type]

    # common clock = video + 0.5 = audio + 0  ->  video = audio - 0.5
    assert [(fr(b.source_time), b.strength) for b in aligned.bangs] == [(Fraction(39, 4), 0.9)]
    assert aligned.bang_clock == "alignment"
    assert [fr(b.source_time) for b in assumed.bangs] == [Fraction(41, 4)]
    assert assumed.bang_clock == "assumed"
