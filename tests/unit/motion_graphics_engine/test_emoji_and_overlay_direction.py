"""Emoji punchlines (identity v2 styles), caption emphasis levels and the wipe direction."""

from datetime import UTC, datetime
from fractions import Fraction
from functools import cache
from pathlib import Path

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.application.ports import FontAsset, Scene
from media_house.modules.motion_graphics_engine.application.style import style_facts
from media_house.modules.motion_graphics_engine.domain.engine import Built, build
from media_house.modules.motion_graphics_engine.domain.model import (
    FaceMetrics,
    GraphicContent,
    StyleFacts,
    TimedEmoji,
    TimedWord,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    ElementType,
    EventKind,
    GraphicKind,
    Origin,
    OverlayDirection,
    OverlayKind,
    Prop,
)
from media_house.modules.motion_graphics_engine.infrastructure.composition import compose_html
from media_house.modules.motion_graphics_engine.infrastructure.font_metrics import read_metrics
from media_house.modules.visual_style_engine.domain.identity import MESC_IDENTITY_V2
from media_house.modules.visual_style_engine.domain.profile import StyleConfig
from media_house.modules.visual_style_engine.domain.resolve import resolve_style
from media_house.modules.visual_style_engine.infrastructure.font_files import FONT_DIR
from tests.unit.motion_graphics_engine.helpers import facts, metrics, request, settings

K = GraphicKind
R = Rational
EXPLODING = "\N{SHOCKED FACE WITH EXPLODING HEAD}"


@cache
def facts_v2(width: int = 1080, height: int = 1920) -> StyleFacts:
    style = resolve_style(
        MESC_IDENTITY_V2,
        StyleConfig(profile="shorts_haul"),
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        width=width,
        height=height,
    )
    return style_facts(style)


@cache
def metrics_v2() -> dict[str, FaceMetrics]:
    return {
        f.key: read_metrics(f.key, (FONT_DIR / f.resource).read_bytes())
        for f in MESC_IDENTITY_V2.faces
    }


def built_v2(req: object, width: int = 1080, height: int = 1920) -> Built:
    return build(req, facts_v2(width, height), metrics_v2(), {}, settings())  # type: ignore[arg-type]


WORDS = (
    TimedWord(text="Diese", local_start=R(0, 1), local_end=R(2, 5), emphasis_level=0),
    TimedWord(
        text="Batterie", local_start=R(2, 5), local_end=R(1, 1), emphasis=True, emphasis_level=3
    ),
    TimedWord(text="knallt", local_start=R(1, 1), local_end=R(3, 2), emphasis_level=1),
)


def caption(*emojis: TimedEmoji) -> object:
    return request(
        K.CAPTION,
        GraphicContent(words=WORDS, emojis=emojis, look="word_group"),
        seconds=2,
        origin=Origin.CAPTION,
    )


def test_a_caption_emoji_is_its_own_run_after_its_word_in_the_emoji_face() -> None:
    b = built_v2(caption(TimedEmoji(emoji=EXPLODING, after_word=1, local_start=R(1, 1))))

    assert b.usable, b.issues
    assert b.composition is not None
    block = next(e.text for e in b.composition.elements if e.text is not None)
    assert block is not None
    runs = [r for line in block.lines for r in line.runs]
    assert [r.id for r in runs] == ["g1.text.w0", "g1.text.w1", "g1.text.e0", "g1.text.w2"]
    assert runs[2].emoji and runs[2].text == EXPLODING and not runs[1].emoji
    assert block.emoji_face == "noto_color_emoji"
    assert runs[1].emphasis  # the word keeps its own emphasis


def test_the_emoji_widens_the_measured_line() -> None:
    plain = built_v2(caption())
    with_emoji = built_v2(caption(TimedEmoji(emoji=EXPLODING, after_word=2, local_start=R(1, 1))))
    assert plain.composition is not None and with_emoji.composition is not None

    def width(b: Built) -> int:  # all lines: the emoji may wrap onto its own line
        block = next(e.text for e in b.composition.elements if e.text)  # type: ignore[union-attr]
        return sum(line.width_px for line in block.lines)

    assert width(with_emoji) > width(plain)


def test_the_emoji_pops_in_at_its_own_time_and_is_an_event() -> None:
    b = built_v2(caption(TimedEmoji(emoji=EXPLODING, after_word=1, local_start=R(1, 1))))
    assert b.animation is not None

    tracks = {(t.target, t.prop): t for t in b.animation.tracks}
    scale = tracks[("g1.text.e0", Prop.SCALE)]
    opacity = tracks[("g1.text.e0", Prop.OPACITY)]
    assert scale.keyframes[0].value == 0.3 and scale.keyframes[-1].value == 1.0
    assert opacity.keyframes[0].value == 0.0
    assert (
        Fraction(opacity.keyframes[1].local_at.numerator, opacity.keyframes[1].local_at.denominator)
        >= 1
    )
    events = [e for e in b.animation.events if e.ref == "g1.text.e0"]
    assert [(e.kind, e.local_at) for e in events] == [(EventKind.EMPHASIS, R(1, 1))]


def test_a_fade_in_emoji_only_fades() -> None:
    b = built_v2(
        caption(TimedEmoji(emoji=EXPLODING, after_word=0, local_start=R(1, 2), entry="fade_in"))
    )
    assert b.animation is not None
    props = {t.prop for t in b.animation.tracks if t.target == "g1.text.e0"}
    assert props == {Prop.OPACITY}


def test_emphasis_levels_set_the_word_event_strength() -> None:
    b = built_v2(caption())
    assert b.animation is not None
    strength = {e.ref: e.strength for e in b.animation.events if e.ref.startswith("g1.text.w")}
    assert strength == {"g1.text.w0": 0.25, "g1.text.w1": 1.0, "g1.text.w2": 0.5}


def test_a_v1_style_draws_the_words_and_warns_about_the_emoji() -> None:
    req = caption(TimedEmoji(emoji=EXPLODING, after_word=1, local_start=R(1, 1)))
    b = build(req, facts(1080, 1920), metrics(), {}, settings())  # type: ignore[arg-type]

    assert b.usable
    assert any(i.code == "emoji.not_available" for i in b.issues)
    assert b.composition is not None
    block = next(e.text for e in b.composition.elements if e.text is not None)
    assert not block.emoji_face
    assert all(not r.id.endswith("e0") for line in block.lines for r in line.runs)


def test_invalid_emoji_and_levels_are_errors() -> None:
    wrong = (
        TimedEmoji(emoji=EXPLODING, after_word=9, local_start=R(1, 1)),
        TimedEmoji(emoji=EXPLODING, after_word=0, local_start=R(5, 1)),
        TimedEmoji(emoji=EXPLODING, after_word=0, local_start=R(1, 1), entry="spin"),
    )
    for emoji in wrong:
        b = built_v2(caption(emoji))
        assert not b.usable and any(i.code == "caption.emoji" for i in b.issues)
    loud = TimedWord(text="laut", local_start=R(0, 1), local_end=R(1, 1), emphasis_level=7)
    b = built_v2(
        request(K.CAPTION, GraphicContent(words=(loud,)), seconds=2, origin=Origin.CAPTION)
    )
    assert any(i.code == "caption.emphasis_level" for i in b.issues)


def test_an_emoji_in_a_callout_headline_uses_the_emoji_face() -> None:
    b = built_v2(
        request(K.CALLOUT, GraphicContent(headline=f"Mega Effekt {EXPLODING}")), 1920, 1080
    )
    assert b.usable, b.issues
    assert b.composition is not None
    block = next(e.text for e in b.composition.elements if e.type is ElementType.TEXT and e.text)
    assert block.emoji_face == "noto_color_emoji"
    runs = [r for line in block.lines for r in line.runs]
    assert [r.emoji for r in runs] == [False, False, True]


def test_the_html_draws_emoji_runs_in_the_emoji_face_without_outline() -> None:
    b = built_v2(caption(TimedEmoji(emoji=EXPLODING, after_word=1, local_start=R(1, 1))))
    comp, anim, frames = b.parts()
    fonts = tuple(
        FontAsset(key=k, family=k, weight=400, path=Path(f"{k}.ttf"), sha256="0" * 64)
        for k in sorted(metrics_v2())
    )
    html = compose_html(
        Scene(
            composition=comp,
            animation=anim,
            fps=frames.fps,
            frames=frames.frames,
            easings=facts_v2().easings,
            fonts=fonts,
            images=(),
        )
    )
    assert "font-family:'mh-anton_400','mh-noto_color_emoji'" in html
    assert "font-family:'mh-noto_color_emoji';-webkit-text-stroke:0" in html
    assert f">{EXPLODING}<" in html


def overlay(direction: OverlayDirection | None) -> Built:
    return build(
        request(
            K.TRANSITION_OVERLAY,
            GraphicContent(overlay=OverlayKind.WIPE, direction=direction),
            origin=Origin.TRANSITION,
        ),
        facts(),
        metrics(),
        {},
        settings(),
    )


def test_the_wipe_travels_in_its_direction_and_defaults_to_the_original_wipe() -> None:
    def moves(b: Built) -> set[tuple[Prop, float, float]]:
        assert b.animation is not None
        return {
            (t.prop, t.keyframes[0].value, t.keyframes[-1].value)
            for t in b.animation.tracks
            if t.target == "g1.a"
        }

    assert moves(overlay(None)) == moves(overlay(OverlayDirection.RIGHT)) == {(Prop.X, -1920, 1920)}
    assert moves(overlay(OverlayDirection.LEFT)) == {(Prop.X, 1920, -1920)}
    assert moves(overlay(OverlayDirection.UP)) == {(Prop.Y, 1080, -1080)}
    assert moves(overlay(OverlayDirection.DOWN)) == {(Prop.Y, -1080, 1080)}
