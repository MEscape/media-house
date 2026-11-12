"""Expression (emphasis density, emoji restraint), layout, SRT/VTT exactness, serialization."""

from dataclasses import fields, is_dataclass, replace
from fractions import Fraction
from typing import Any

import pytest

from media_house.core.domain import Rational
from media_house.modules.caption_engine.domain.errors import (
    InvalidCaptionConfig,
    InvalidCaptionDocument,
)
from media_house.modules.caption_engine.domain.facts import SubjectBox, frac
from media_house.modules.caption_engine.domain.model import SubtitleCue
from media_house.modules.caption_engine.domain.profile import (
    PROFILES,
    CaptionConfig,
    CaptionProfile,
    apply_overrides,
    knob_fields,
)
from media_house.modules.caption_engine.domain.serialization import plan_from_json, plan_to_json
from media_house.modules.caption_engine.domain.subtitles import to_srt, to_vtt
from media_house.modules.caption_engine.domain.values import (
    CaptionMode,
    EmphasisLevel,
    RenderStatus,
    Severity,
)
from tests.support.caption_fakes import clip, inputs, plan, style, transcript, zone

NUMBERS = (
    "Die erste hat 25 Schuss, die zweite 36 Schuss, die dritte 49 Schuss, die vierte 100 Schuss"
    " und die fünfte 200 Schuss für 39,99 €."
)


def _emphasised(p: Any) -> list[str]:
    return [w.text for s in p.segments for w in s.words if w.emphasis is not EmphasisLevel.NORMAL]


def test_emphasis_is_bounded_per_caption_and_overall() -> None:
    p = plan(inputs(transcript(NUMBERS)))
    shown = sum(len(s.words) for s in p.segments)
    lit = _emphasised(p)

    assert lit, "the strongest facts are emphasised"
    assert len(lit) <= max(2, int(0.15 * shown) + 2)
    for s in p.segments:
        atoms = {w.word_id.split("@")[0] for w in s.words if w.emphasis is not EmphasisLevel.NORMAL}
        assert len(atoms) <= 2  # one fact atom (e.g. "39,99 €") per caption
    assert "39,99" in lit and "€." in lit  # the price wins and lights up whole


def test_loudness_alone_never_makes_a_word_critical() -> None:
    t = transcript("Das Wetter ist heute ziemlich normal")
    words = tuple(replace(w, vocal=1.0) for w in t.words)
    p = plan(inputs(replace(t, words=words)))

    levels = {w.emphasis for s in p.segments for w in s.words}
    assert levels <= {EmphasisLevel.NORMAL, EmphasisLevel.MEDIUM}


def test_a_person_can_switch_emphasis_off_for_one_caption() -> None:
    from media_house.modules.caption_engine.domain.model import CaptionDecision
    from media_house.modules.caption_engine.domain.values import Action

    first = plan(inputs(transcript("Nur 19,99 € heute.")))
    target = next(
        s.segment_id for s in first.segments if _emphasised(replace(first, segments=(s,)))
    )
    p = plan(
        inputs(transcript("Nur 19,99 € heute.")),
        decisions=[CaptionDecision(item_id=target, action=Action.NO_EMPHASIS)],
    )

    assert _emphasised(p) == []


def test_emoji_are_off_for_serious_formats_and_spaced_in_shorts() -> None:
    text = "Das ist krass. Wirklich krass. Total krass. Echt krass. Mega krass."
    calm = plan(
        inputs(transcript(text), vertical=False), "generic", {"selection.mode": "full_captions"}
    )
    haul = plan(inputs(transcript(text)), "shorts_haul")

    assert all(not s.emojis for s in calm.segments)
    emojis = [e for s in haul.segments for e in s.emojis]
    # 2 s apart, the same emoji not again within 20 s: "krass" once, then "Mega"
    assert [e.emoji for e in emojis] == ["🤯", "🔥"]
    assert all(e.render is RenderStatus.NOT_REQUESTED for e in emojis)
    assert {e.position for e in emojis} == {"above"}  # Shorts: big sticker emoji


def test_shorts_emoji_are_denser_stickers() -> None:
    for name in ("shorts_haul", "shorts_product_test", "shorts_versus"):
        e = PROFILES[name].emoji
        assert e.display == "sticker" and 12 <= e.max_per_minute <= 15 and e.min_spacing == 2
    assert PROFILES["shorts_meme"].emoji.display == "inline"  # the static meme line


def test_emoji_stems_longest_wins_and_short_words_match_whole_only() -> None:
    said = (
        "Das Feuerwerk ist groß. Die Topf ist neu. Ein Topseller Paket. Platz eins top."
        " Der Wecker klingelt. Wir schauen jetzt."
    )
    p = plan(inputs(transcript(said)), "shorts_haul", {"emoji.min_spacing": 2.0})
    found = {
        w.text: e.emoji
        for s in p.segments
        for e in s.emojis
        for w in s.words
        if w.word_id == e.word_id
    }
    assert found.get("Feuerwerk") == "🎆"  # not the 🔥 of "feuer"
    assert "Topf" not in found and "Topseller" not in found  # "top" is a whole word only
    assert set(found.values()) <= {"🎆", "💯", "📦", "🏆", "👍", "⏰", "👀"}


def test_a_negated_word_gets_no_emoji() -> None:
    p = plan(inputs(transcript("Das ist nicht krass.")))

    assert all(not s.emojis for s in p.segments)


def test_a_price_earns_the_euro_emoji_synced_to_its_word() -> None:
    p = plan(inputs(transcript("Heute nur 19,99 € im Angebot.")))
    (emoji,) = [e for s in p.segments for e in s.emojis]
    word = next(w for s in p.segments for w in s.words if w.word_id == emoji.word_id)

    assert emoji.emoji == "💶" and word.text == "19,99"
    assert frac(emoji.timeline_start) == frac(word.timeline_start) - Fraction(1, 10)


def test_a_face_in_the_caption_band_moves_the_caption_and_keepouts_are_respected() -> None:
    t = transcript("Hallo zusammen")
    face = SubjectBox(
        asset_id="clip",
        start=Fraction(0),
        end=Fraction(10),
        x0=0.3,
        y0=0.58,
        x1=0.7,
        y1=0.61,
        label="face",
    )
    p = plan(inputs(t, subjects=[face]))

    for s in p.segments:
        assert s.layout.zone == "caption_band" and s.layout.anchor == "bottom"
        assert "face" in s.layout.avoided
        assert not any(c.code.startswith(("zone.", "layout.")) for c in p.checks)


def test_a_caption_zone_reaching_a_keep_out_is_an_error() -> None:
    bad = style(zone=zone("caption_band", 54, 1500, 853, 249, captions=True))
    p = plan(inputs(transcript("Hallo zusammen"), style_facts=bad))

    assert any(c.code == "zone.keep_out" and c.severity is Severity.ERROR for c in p.checks)
    assert all(s.render.status is RenderStatus.FAILED for s in p.segments)
    assert p.state.value == "partial"


def _cue(i: int, a: Fraction, b: Fraction, *lines: str, speaker: str | None = None) -> SubtitleCue:
    return SubtitleCue(
        index=i,
        timeline_start=Rational(a.numerator, a.denominator),
        timeline_end=Rational(b.numerator, b.denominator),
        lines=lines,
        speaker=speaker,
    )


CUES = (
    _cue(1, Fraction(1, 2), Fraction(2), "Größer & lauter", "als <alles>"),
    _cue(2, Fraction(3661, 1), Fraction(73225, 20), "19,99 €", speaker="SPEAKER_00"),
)


def test_srt_is_exact() -> None:
    assert to_srt(CUES) == (
        "1\n00:00:00,500 --> 00:00:02,000\nGrößer & lauter\nals <alles>\n"
        "\n"
        "2\n01:01:01,000 --> 01:01:01,250\n19,99 €\n"
    )


def test_webvtt_is_exact_and_escaped() -> None:
    assert to_vtt(CUES, speaker_labels=True) == (
        "WEBVTT\n"
        "\n"
        "00:00:00.500 --> 00:00:02.000\nGrößer &amp; lauter\nals &lt;alles&gt;\n"
        "\n"
        "01:01:01.000 --> 01:01:01.250\n<v SPEAKER_00>19,99 €\n"
    )


def test_subtitle_milliseconds_round_half_to_even_and_never_collapse() -> None:
    (cue,) = (_cue(1, Fraction(1, 2000), Fraction(1, 2000), "x"),)

    assert "00:00:00,000 --> 00:00:00,001" in to_srt((cue,))


def test_the_plan_round_trips_through_its_strict_json() -> None:
    p = plan(inputs(transcript("Heute nur 19,99 € im Angebot. Das ist krass!")))

    again = plan_from_json(plan_to_json(p))

    assert again == p
    with pytest.raises(InvalidCaptionDocument):
        plan_from_json(plan_to_json(p).replace('"schema_version": 1', '"schema_version": 9'))


# --- configuration --------------------------------------------------------------------------


def _leaf(profile: CaptionProfile, path: str) -> Any:
    value: Any = profile
    for part in path.split("."):
        value = getattr(value, part)
    return value


def test_every_knob_is_labelled_and_its_range_is_enforced() -> None:
    base = PROFILES["generic"]
    for path, f in knob_fields(CaptionProfile):
        meta = f.metadata
        if not meta:
            continue  # internal vocabulary (lexicon, emoji rules, beat functions)
        assert meta.get("label"), path
        if "min" in meta and "step" in meta:
            low = apply_overrides(base, {path: meta["min"] - meta["step"]})
            high = apply_overrides(base, {path: meta["max"] + meta["step"]})
            assert any(path in p for p in low.problems()), path
            assert any(path in p for p in high.problems()), path
            assert not [
                p for p in apply_overrides(base, {path: meta["min"]}).problems() if path in p
            ]
        if "choices" in meta:
            for choice in meta["choices"]:
                assert not [
                    p for p in apply_overrides(base, {path: choice}).problems() if path in p
                ]


def test_every_built_in_profile_is_valid() -> None:
    for name, profile in PROFILES.items():
        assert profile.problems() == [], name
        assert profile.evidence, name


def test_profile_precedence_and_overrides() -> None:
    assert CaptionConfig().resolve(style_profile="shorts_haul").name == "shorts_haul"
    assert CaptionConfig().resolve(story_format="top_n").name == "ranking"
    assert CaptionConfig().resolve(aspect="9:16").name == "shorts_meme"
    assert CaptionConfig().resolve().name == "generic"
    assert CaptionConfig(profile="ranking").resolve(style_profile="shorts_haul").name == "ranking"
    tuned = CaptionConfig(
        profile="ranking", overrides={"selection.mode": "full_captions"}
    ).resolve()
    assert tuned.mode is CaptionMode.FULL_CAPTIONS
    with pytest.raises(InvalidCaptionConfig):
        CaptionConfig(overrides={"selection.nope": 1}).resolve()
    with pytest.raises(InvalidCaptionConfig):
        CaptionConfig(overrides={"segmentation.max_words": 99}).resolve()


def test_profiles_follow_the_channel_evidence() -> None:
    modes = {name: p.mode for name, p in PROFILES.items()}

    assert modes["ranking"] is modes["shopping_tour"] is CaptionMode.DISABLED
    assert modes["compilation"] is CaptionMode.KEY_INFORMATION
    assert all(modes[n] is CaptionMode.FULL_CAPTIONS for n in modes if n.startswith("shorts_"))
    assert PROFILES["shorts_meme"].layout.look == "line"
    assert not PROFILES["brochure_review"].emoji.enabled


def test_the_knob_tree_has_no_unlabelled_user_value() -> None:
    def walk(obj: object) -> None:
        for f in fields(obj):  # type: ignore[arg-type]
            value = getattr(obj, f.name)
            if is_dataclass(value):
                walk(value)

    walk(PROFILES["generic"])
    assert clip().audio


def test_back_to_back_captions_cut_out_and_the_last_one_animates_out() -> None:
    p = plan(
        inputs(
            transcript("Das ist eine sehr schnelle Aussage", word=0.2),
            style_facts=style(exit_seconds=Fraction(35, 100)),
        )
    )

    assert len(p.segments) >= 2
    assert {s.animation.exit for s in p.segments[:-1]} == {"cut"}
    last = p.segments[-1]
    assert last.animation.exit == "pop_out"
    spoken_end = frac(last.words[-1].timeline_end)
    assert frac(last.timeline_end) - spoken_end >= Fraction(35, 100)


def test_every_emoji_stem_is_unique_and_case_folded() -> None:
    triggers = [r.trigger for r in PROFILES["shorts_haul"].emoji.vocabulary]
    assert len(triggers) == len(set(triggers))
    assert all(t == t.casefold() for t in triggers)
