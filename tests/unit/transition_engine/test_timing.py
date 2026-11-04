"""Frame-exact timing and handle math (odd frame counts, VFR, other rates, unknown lengths)."""

from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.transition_engine.domain.facts import TransitionRequest
from media_house.modules.transition_engine.domain.timing import (
    available_after,
    available_before,
    fit,
    split,
)
from media_house.modules.transition_engine.domain.values import (
    Alignment,
    DecisionStatus,
    TransitionKind,
)
from tests.support.trans_fakes import chain, clip, codes, cut, plan, r, sequence, source

K = TransitionKind
REQ = (TransitionRequest(request_id="fx", timeline_at=r(10)),)


def test_an_odd_transition_puts_the_extra_frame_after_the_cut() -> None:
    assert split(5, Alignment.CENTER) == (2, 3)
    assert split(4, Alignment.CENTER) == (2, 2)
    assert split(5, Alignment.START) == (0, 5)
    assert split(5, Alignment.END) == (5, 0)


def test_available_handles_are_whole_frames_and_vfr_keeps_a_safety_frame() -> None:
    cfr = source("A", 10.1)
    assert available_after(cfr, Fraction(10), Fraction(30), 1) == 3
    assert available_before(cfr, Fraction("0.1"), Fraction(30), 1) == 3
    vfr = source("A", 10.1, variable_frame_rate=True)
    assert available_after(vfr, Fraction(10), Fraction(30), 1) == 2
    assert available_after(source("A", None), Fraction(10), Fraction(30), 1) is None


def test_fit_shortens_and_realigns() -> None:
    found = fit(
        wanted=15, minimum=2, alignment=Alignment.CENTER, overlap=True,
        handle_out=3, handle_in=3, room_out=300, room_in=150,
    )  # fmt: skip
    assert found is not None
    assert (found.frames, found.before, found.after, found.shortened) == (6, 3, 3, True)
    start = fit(
        wanted=15, minimum=2, alignment=Alignment.CENTER, overlap=True,
        handle_out=100, handle_in=0, room_out=300, room_in=150,
    )  # fmt: skip
    assert start is not None
    assert (start.alignment, start.before, start.after, start.realigned) == (
        Alignment.START,
        0,
        15,
        True,
    )
    assert (
        fit(
            wanted=15, minimum=2, alignment=Alignment.CENTER, overlap=True,
            handle_out=0, handle_in=0, room_out=300, room_in=150,
        )
        is None
    )  # fmt: skip


def test_a_dissolve_without_an_incoming_handle_starts_at_the_cut() -> None:
    clips = chain(("c1", "A", 100.0, 110.0), ("c2", "B", 0.0, 10.0))
    t = cut(plan(sequence(clips, requests=REQ)), "c1", "c2")
    assert (t.kind, t.alignment, t.start_frame, t.end_frame) == (
        K.CROSS_DISSOLVE,
        Alignment.START,
        300,
        315,
    )
    assert (t.handles.required_out, t.handles.required_in) == (15, 0)
    assert any("aligned to start" in reason for reason in t.reasons)


def test_short_handles_shorten_the_transition() -> None:
    clips = chain(("c1", "A", 100.0, 110.0), ("c2", "B", 0.1, 10.1))
    sources = {"A": source("A", 110.1), "B": source("B")}
    t = cut(plan(sequence(clips, requests=REQ), sources=sources), "c1", "c2")
    assert (t.kind, t.duration_frames, t.handles.available_out, t.handles.available_in) == (
        K.CROSS_DISSOLVE,
        6,
        3,
        3,
    )
    vfr = {"A": source("A", 110.1, variable_frame_rate=True), "B": source("B")}
    v = plan(sequence(clips, requests=REQ), sources=vfr)
    assert cut(v, "c1", "c2").duration_frames == 4
    assert "source.vfr" in codes(v)


def test_without_handles_or_known_lengths_the_cut_stays_hard() -> None:
    clips = chain(("c1", "A", 100.0, 110.0), ("c2", "B", 0.0, 10.0))
    none = cut(
        plan(sequence(clips, requests=REQ), sources={"A": source("A", 110.0), "B": source("B")}),
        "c1",
        "c2",
    )
    assert (none.kind, none.status) == (K.HARD_CUT, DecisionStatus.FALLBACK)
    assert "insufficient handles" in none.rejected[0].reason
    unknown = cut(
        plan(
            sequence(clips, requests=REQ), sources={"A": source("A", None), "B": source("B", None)}
        ),
        "c1",
        "c2",
    )
    assert unknown.kind is K.HARD_CUT
    assert "unknown" in unknown.rejected[0].reason


def test_ntsc_rates_stay_frame_exact() -> None:
    ntsc = Rational(30000, 1001)
    ten = Fraction(300 * 1001, 30000)  # exactly 300 frames
    clips = (
        clip("c1", "A", 100, Fraction(100) + ten, 0),
        clip("c2", "B", 100, Fraction(100) + ten, ten),
    )
    seq = sequence(
        clips, frame_rate=ntsc, requests=(TransitionRequest(request_id="x", timeline_at=r(ten)),)
    )
    t = cut(plan(seq), "c1", "c2")
    assert (t.cut_frame, t.start_frame, t.end_frame, t.duration_frames) == (300, 293, 308, 15)
    assert Fraction(t.timeline_start.numerator, t.timeline_start.denominator) == Fraction(
        293 * 1001, 30000
    )
    assert Fraction(t.duration.numerator, t.duration.denominator) == Fraction(15 * 1001, 30000)


def test_cuts_between_frames_are_snapped_and_reported() -> None:
    clips = (clip("c1", "A", 100, "110.005", 0), clip("c2", "B", 100, 110, "10.005"))
    p = plan(sequence(clips))
    assert cut(p, "c1", "c2").cut_frame == 300
    assert "frames.off_grid" in codes(p)


def test_another_source_rate_is_counted_in_edit_frames() -> None:
    sources = {"A": source("A", frame_rate=Fraction(25)), "B": source("B")}
    p = plan(
        sequence(chain(("c1", "A", 100.0, 110.0), ("c2", "B", 100.0, 110.0)), requests=REQ),
        sources=sources,
    )
    assert cut(p, "c1", "c2").kind is K.CROSS_DISSOLVE
    assert "source.frame_rate" in codes(p)
