"""Animation compiler, events, frames, collisions and fingerprints."""

from dataclasses import replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.engine import (
    Built,
    build,
    frame_span,
    item_fingerprint,
    resolve_collisions,
)
from media_house.modules.motion_graphics_engine.domain.model import (
    AnimationRequest,
    GraphicContent,
    TimedWord,
    Timing,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    EventKind,
    GraphicKind,
    Origin,
    Prop,
    Severity,
)
from tests.unit.motion_graphics_engine.helpers import FPS, facts, metrics, request, settings

K = GraphicKind


def built(req: object, width: int = 1920, height: int = 1080) -> Built:
    return build(req, facts(width, height), metrics(), {}, settings())  # type: ignore[arg-type]


def f(r: Rational) -> Fraction:
    return Fraction(r.numerator, r.denominator)


def test_frames_are_computed_once_with_half_even_rounding() -> None:
    span = frame_span(
        Timing(local_duration=Rational(3, 1), timeline_start=Rational(1001, 1000)),
        Rational(30000, 1001),
    )
    assert span.start_frame == round(Fraction(1001, 1000) * Fraction(30000, 1001))
    assert span.frames == span.end_frame - span.start_frame
    assert f(span.duration) == Fraction(span.frames) * Fraction(1001, 30000)
    local = frame_span(Timing(local_duration=Rational(1, 50)), Rational(25, 1))
    assert (local.start_frame, local.frames, local.timeline_start) == (0, 0, None)  # 0.5 -> 0


def test_events_are_ordered_on_the_frame_grid_and_carry_timeline_times() -> None:
    b = built(request(K.RANK_CARD, GraphicContent(rank=2), start=Rational(10, 1)))
    assert b.usable, b.issues
    assert b.animation is not None
    kinds = [e.kind for e in b.animation.events]
    assert kinds[0] is EventKind.ENTER_START and kinds[-1] is EventKind.EXIT_END
    for e in b.animation.events:
        assert (f(e.local_at) * 25).denominator == 1
        assert e.timeline_at is not None and f(e.timeline_at) == 10 + f(e.local_at)
    peak = next(e for e in b.animation.events if e.kind is EventKind.ENTER_PEAK)
    m = facts().motion
    assert f(m.entry_min) <= f(peak.local_at)


def test_keyframes_stay_on_frames_inside_the_duration() -> None:
    b = built(request(K.LOWER_THIRD, GraphicContent(headline="Marvin", body=("MEsc",))))
    assert b.animation is not None
    for track in b.animation.tracks:
        times = [f(k.local_at) for k in track.keyframes]
        assert times == sorted(times)
        assert all(0 <= t <= 4 and (t * 25).denominator == 1 for t in times)
    assert any(t.prop is Prop.REVEAL for t in b.animation.tracks)  # the panel wipes open


def test_intensity_is_bounded_by_the_style_ceiling() -> None:
    b = built(
        request(
            K.CALLOUT, GraphicContent(headline="Achtung"), animation=AnimationRequest(intensity=4)
        )
    )
    assert b.animation is not None
    assert b.animation.motion.intensity == facts().motion.intensity_ceiling
    assert any(i.code == "motion.intensity" and i.severity is Severity.WARNING for i in b.issues)


def test_cut_has_no_tracks_and_intensity_zero_cuts() -> None:
    b = built(
        request(
            K.SPONSOR_LABEL,
            GraphicContent(headline="Werbung"),
            origin=Origin.BRAND,
            animation=AnimationRequest(entry="cut", exit="cut"),
        )
    )
    assert b.animation is not None
    assert b.animation.tracks == ()
    zero = built(
        request(
            K.CALLOUT, GraphicContent(headline="Hallo"), animation=AnimationRequest(intensity=0)
        )
    )
    assert zero.animation is not None and zero.animation.motion.entry == "cut"


def test_emphasis_is_a_pulse_and_an_event() -> None:
    b = built(
        request(
            K.CALLOUT,
            GraphicContent(headline="Achtung"),
            animation=AnimationRequest(emphasis_at=(Rational(2, 1),)),
        )
    )
    assert b.animation is not None
    assert any(e.kind is EventKind.EMPHASIS and f(e.local_at) == 2 for e in b.animation.events)


def test_caption_words_light_up_when_spoken() -> None:
    words = (
        TimedWord(text="Das", local_start=Rational(0, 1), local_end=Rational(1, 2)),
        TimedWord(
            text="knallt", local_start=Rational(1, 2), local_end=Rational(1, 1), emphasis=True
        ),
    )
    b = built(
        request(
            K.CAPTION,
            GraphicContent(words=words, look="word_group"),
            seconds=2,
            origin=Origin.CAPTION,
        ),
        1080,
        1920,
    )
    assert b.usable, b.issues
    assert b.animation is not None
    highlight = {t.target for t in b.animation.tracks if t.prop is Prop.HIGHLIGHT}
    assert highlight == {"g1.text.w0"}  # the emphasised word stays highlighted (no track)
    reveal = {t.target for t in b.animation.tracks if t.prop is Prop.OPACITY}
    assert {"g1.text.w0", "g1.text.w1"} <= reveal


def test_caption_word_outside_the_group_is_an_error() -> None:
    words = (TimedWord(text="spät", local_start=Rational(5, 1), local_end=Rational(6, 1)),)
    b = built(request(K.CAPTION, GraphicContent(words=words), seconds=2, origin=Origin.CAPTION))
    assert not b.usable
    assert any(i.code == "caption.word_timing" for i in b.issues)


def test_simultaneous_callouts_move_to_the_free_zone() -> None:
    a = request(K.CALLOUT, GraphicContent(headline="Erst"), request_id="a", priority=0.9)
    b = request(K.CALLOUT, GraphicContent(headline="Zweit"), request_id="b", priority=0.5)
    out = resolve_collisions([built(a), built(b)], facts(), metrics(), {}, settings())
    assert out[0].composition is not None and out[0].composition.zone == "callout_left"
    assert out[1].usable and out[1].composition is not None
    assert out[1].composition.zone == "callout_right"
    assert any(i.code == "layout.moved" for i in out[1].issues)


def test_the_density_limit_drops_the_lowest_priority_overlay() -> None:
    limit = facts().graphics.max_simultaneous
    reqs = [
        request(K.CALLOUT, GraphicContent(headline="Eins"), request_id="a", priority=0.9),
        request(K.CALLOUT, GraphicContent(headline="Zwei"), request_id="b", priority=0.8),
        request(K.RANK_CARD, GraphicContent(rank=1), request_id="c", priority=0.1),
    ]
    out = resolve_collisions([built(r) for r in reqs], facts(), metrics(), {}, settings())
    dropped = [b for b in out if any(i.code == "density.max_simultaneous" for i in b.issues)]
    assert limit == 2 and [b.request.request_id for b in dropped] == ["c"]


def test_the_item_fingerprint_changes_with_content_style_and_fps_only() -> None:
    req = request(K.CALLOUT, GraphicContent(headline="Hallo"))
    same = item_fingerprint(req, facts(), settings())
    assert same == item_fingerprint(req, facts(), settings())
    assert same != item_fingerprint(
        replace(req, content=GraphicContent(headline="Tschüss")), facts(), settings()
    )
    assert same != item_fingerprint(req, facts(1080, 1920), settings())
    assert same != item_fingerprint(req, facts(), settings(Rational(30, 1)))
    assert Rational(25, 1) == FPS
