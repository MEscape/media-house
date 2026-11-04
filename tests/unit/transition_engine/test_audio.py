"""Audio transitions are planned apart from the picture: J/L kept, no fade through a word."""

from fractions import Fraction

from media_house.modules.transition_engine.domain.facts import (
    AudioBridgeMark,
    ContinuityFact,
    TransitionRequest,
)
from media_house.modules.transition_engine.domain.values import (
    AudioKind,
    Curve,
    Severity,
    TransitionKind,
)
from tests.support.trans_fakes import chain, cut, plan, r, sequence, source, words

TWO = (("c1", "A", 100.0, 110.0), ("c2", "B", 100.0, 110.0))


def _f(value: object) -> Fraction:
    return Fraction(value.numerator, value.denominator)  # type: ignore[attr-defined]


def test_a_hard_cut_gets_an_equal_power_click_guard_in_samples() -> None:
    a = cut(plan(sequence(chain(*TWO))), "c1", "c2").audio
    assert (a.kind, a.curve, a.sample_rate) == (AudioKind.CUT, Curve.EQUAL_POWER, 48_000)
    assert (a.samples_before, a.samples_after, a.duration_ms) == (240, 240, 10.0)
    assert _f(a.timeline_at) == 10


def test_j_and_l_cuts_move_the_sound_never_the_picture() -> None:
    bridge = AudioBridgeMark(
        bridge_id="b1", kind="j_cut", audio_changes_at=r("9.5"), picture_changes_at=r(10)
    )
    t = cut(plan(sequence(chain(*TWO), bridges=(bridge,))), "c1", "c2")
    assert (t.kind, t.cut_frame, _f(t.cut_at)) == (TransitionKind.HARD_CUT, 300, Fraction(10))
    assert (t.audio.kind, _f(t.audio.timeline_at)) == (AudioKind.J_CUT, Fraction("9.5"))
    assert t.audio.samples_before == t.audio.samples_after == 1920  # 80 ms split


def test_a_crossfade_is_shortened_so_no_word_is_faded_or_leaked() -> None:
    req = (TransitionRequest(request_id="fx", timeline_at=r(10)),)
    sources = {
        "A": source("A", words=words((109.0, 109.8))),
        "B": source("B", words=words((100.1, 100.6))),
    }
    t = cut(plan(sequence(chain(*TWO), requests=req), sources=sources), "c1", "c2")
    assert t.kind is TransitionKind.CROSS_DISSOLVE
    a = t.audio
    # 0.5 s with the picture: 0.25 s each side, then 20 ms from each word
    assert (_f(a.before), _f(a.after)) == (Fraction("0.18"), Fraction("0.08"))
    assert a.protected
    assert a.samples_after == 3840


def test_speech_at_the_cut_keeps_only_the_shortest_fade() -> None:
    sources = {
        "A": source("A", words=words((109.5, 109.997))),
        "B": source("B", words=words((100.001, 100.3))),
    }
    p = plan(sequence(chain(*TWO)), sources=sources)
    a = cut(p, "c1", "c2").audio
    assert (_f(a.before), _f(a.after)) == (Fraction("0.004"), Fraction("0.004"))
    assert not [i for i in p.checks if i.severity is Severity.CRITICAL]


def test_a_continuity_repair_owned_by_transitions_smooths_the_sound() -> None:
    cont = {
        ("c1", "c2"): ContinuityFact(outgoing_clip="c1", incoming_clip="c2", audio_crossfade=True)
    }
    a = cut(plan(sequence(chain(*TWO)), continuity=cont), "c1", "c2").audio
    assert (a.kind, a.duration_ms) == (AudioKind.CROSSFADE, 300.0)


def test_the_head_and_tail_fades_stay_off_the_words() -> None:
    sources = {
        "A": source("A", words=words((100.002, 100.5))),
        "B": source("B", words=words((109.6, 109.95))),
    }
    p = plan(sequence(chain(*TWO)), sources=sources)
    head, tail = p.transitions[0].audio, p.transitions[-1].audio
    assert head.kind is AudioKind.FADE_IN
    assert _f(head.after) == Fraction("0.004")
    assert tail.kind is AudioKind.FADE_OUT
    assert _f(tail.before) == Fraction("0.03")  # 0.5 s wanted, the last word ends 50 ms early
