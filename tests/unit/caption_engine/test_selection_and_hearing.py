"""Hearing (source -> timeline through clips, unreliable words) and selection (modes)."""

from dataclasses import replace
from fractions import Fraction

from media_house.modules.caption_engine.domain.facts import BurnedInSpan, CoveredSpan, frac
from media_house.modules.caption_engine.domain.model import CaptionDecision
from media_house.modules.caption_engine.domain.profile import CaptionConfig, SectionOverride
from media_house.modules.caption_engine.domain.values import (
    Action,
    CaptionMode,
    FactKind,
    Severity,
)
from tests.support.caption_fakes import clip, inputs, plan, texts, transcript

PRICE = "Die Batterie kostet nur 19,99 € bei Lidl. Das Wetter ist heute wirklich schön."


def test_words_map_to_the_timeline_only_through_their_clip() -> None:
    t = transcript("eins zwei drei vier fünf sechs", word=1.0, start=0.0)
    # Source 2-4 s (words three and four) placed at 10 s on the timeline.
    p = plan(inputs(t, clips=[clip(source=(2.1, 4.1), at=10.0)]))

    words = [w for s in p.segments for w in s.words]
    assert [w.text for w in words] == ["drei", "vier"]
    first = words[0]
    assert frac(first.timeline_start) - frac(first.source_start) == Fraction(10) - Fraction(21, 10)
    assert frac(first.timeline_start) == Fraction(10) + (
        frac(first.source_start) - Fraction(21, 10)
    )


def test_a_word_cut_out_of_the_edit_is_not_captioned() -> None:
    t = transcript("eins zwei drei vier", word=1.0)
    p = plan(inputs(t, clips=[clip(source=(0.0, 1.0)), clip(source=(3.2, 5.0), at=1.0)]))

    assert "zwei" not in " ".join(texts(p)) and "drei" not in " ".join(texts(p))


def test_a_word_split_by_a_cut_is_kept_only_when_mostly_heard() -> None:
    t = transcript("Feuerwerk Rakete", word=1.0)  # Feuerwerk 0-1 s, Rakete 1.05-2.05 s
    mostly = plan(inputs(t, clips=[clip(source=(0.0, 1.8))]))
    barely = plan(inputs(t, clips=[clip(source=(0.0, 1.3))]))

    kept = [w for s in mostly.segments for w in s.words if w.text == "Rakete"]
    assert kept and kept[0].clipped and frac(kept[0].source_end) == Fraction(18, 10)
    assert "Rakete" not in " ".join(texts(barely))
    assert any(c.code == "word.split_by_cut" for c in barely.checks)


def test_suspect_and_uncertain_words_are_never_burned_in() -> None:
    t = transcript("Der Böller knallt richtig laut", confidences={"knallt": 0.1})
    words = tuple(replace(w, suspect=True) if w.text == "richtig" else w for w in t.words)
    p = plan(inputs(replace(t, words=words)))

    shown = " ".join(texts(p))
    assert "knallt" not in shown and "richtig" not in shown
    codes = {c.code for c in p.checks}
    assert {"word.suspect_dropped", "word.low_confidence"} <= codes
    assert p.subtitles is not None
    assert "knallt" in p.subtitles.srt and "richtig" not in p.subtitles.srt


def test_a_transcript_in_the_wrong_language_is_not_burned_in_and_asks_for_reprocessing() -> None:
    p = plan(inputs(transcript("This rocket is really loud.", language="en")))

    assert p.mode is CaptionMode.DISABLED
    assert any(c.code == "language.mismatch" for c in p.checks)
    assert [r.owner for r in p.reprocess] == ["audio_intelligence"]
    assert p.subtitles is not None and p.subtitles.language == "en"


def test_full_captions_show_all_reliable_speech() -> None:
    p = plan(inputs(transcript(PRICE)), "shorts_haul")

    assert p.mode is CaptionMode.FULL_CAPTIONS
    assert " ".join(texts(p)).split() == PRICE.split()


def test_a_picture_that_already_shows_burned_in_captions_gets_none_on_top() -> None:
    # E2E regression: a published (already captioned) Short was captioned a second time although
    # video analysis had reported its burned-in captions.
    shown = BurnedInSpan(asset_id="clip", start=Fraction(0), end=Fraction(60))
    elsewhere = BurnedInSpan(asset_id="other", start=Fraction(0), end=Fraction(60))
    given = inputs(transcript(PRICE))

    p = plan(replace(given, burned_in=(shown,)), "shorts_haul")
    unrelated = plan(replace(given, burned_in=(elsewhere,)), "shorts_haul")

    assert p.mode is CaptionMode.DISABLED and p.segments == ()
    assert "burned-in captions" in p.mode_reason
    assert p.subtitles is not None and "19,99 €" in p.subtitles.srt
    assert unrelated.mode is CaptionMode.FULL_CAPTIONS and unrelated.segments


def test_other_on_screen_text_counts_only_where_it_is_the_spoken_words() -> None:
    # The owner's fast-cut Shorts: one caption per shot reads as a "lower third" to video OCR.
    def text(start: float, words: str) -> BurnedInSpan:
        at = Fraction(start).limit_denominator(1000)
        return BurnedInSpan(asset_id="clip", start=at, end=at, text=words, caption=False)

    given = inputs(transcript(PRICE))  # 0.3 s per word from 0 s
    same = (text(0.3, "DIE BATTERIE KOSTET"), text(1.5, "19,99 € BEI LIDL"))
    other = (text(0.3, "WERBUNG"), text(1.5, "SALE"))

    assert plan(replace(given, burned_in=same), "shorts_haul").mode is CaptionMode.DISABLED
    assert plan(replace(given, burned_in=other), "shorts_haul").mode is CaptionMode.FULL_CAPTIONS


def test_key_information_shows_the_fact_with_one_context_word() -> None:
    p = plan(inputs(transcript(PRICE), vertical=False), "generic")

    assert p.mode is CaptionMode.KEY_INFORMATION
    assert texts(p) == ["nur 19,99 € bei Lidl."]
    assert "price" in p.segments[0].reasons


def test_key_information_does_not_repeat_a_fact_a_graphic_already_shows() -> None:
    covered = CoveredSpan(
        timeline_start=Fraction(0), timeline_end=Fraction(5), kind=FactKind.PRICE, text="19,99 €"
    )
    p = plan(inputs(transcript(PRICE), vertical=False, covered=[covered]), "generic")

    assert texts(p) == []
    assert any(c.code == "selection.covered_by_graphic" for c in p.checks)


def test_emphasis_only_shows_strong_phrases_alone() -> None:
    p = plan(
        inputs(transcript(PRICE), vertical=False), "generic", {"selection.mode": "emphasis_only"}
    )

    assert texts(p) == ["19,99 €"]
    assert p.mode_reason == "project override"


def test_selective_captions_pick_the_important_sentence() -> None:
    p = plan(
        inputs(transcript(PRICE), vertical=False),
        "generic",
        {"selection.mode": "selective_captions"},
    )

    assert " ".join(texts(p)) == "Die Batterie kostet nur 19,99 € bei Lidl."


def test_long_form_formats_burn_in_nothing_but_still_export_subtitles() -> None:
    for name in ("ranking", "brochure_review", "shopping_tour", "unboxing", "nye_uncut"):
        p = plan(inputs(transcript(PRICE), vertical=False), name)

        assert p.mode is CaptionMode.DISABLED and p.segments == ()
        assert p.subtitles is not None and "19,99 €" in p.subtitles.srt
        assert "no burned-in" in p.mode_reason or "no speech" in p.mode_reason


def test_the_no_speech_path_is_disabled_and_never_fails() -> None:
    silent = replace(transcript("x"), words=(), sentences=(), facts=())
    p = plan(inputs(silent, edit=None), "shorts_meme")

    assert p.mode is CaptionMode.DISABLED and p.edit is None
    assert "no-speech" in p.mode_reason
    assert p.subtitles is None and p.segments == ()


def test_a_missing_transcript_is_requested_not_invented() -> None:
    p = plan(inputs(clips=[clip("other")]), "shorts_haul")

    assert p.segments == ()
    assert [(r.owner, r.asset_id) for r in p.reprocess] == [("audio_intelligence", "other")]


def test_a_section_override_changes_the_mode_of_that_section_only() -> None:
    t = transcript(PRICE, word=0.3)
    clips = [
        clip(source=(0.0, 3.0), section_id="intro"),
        clip(source=(3.0, 30.0), at=3.0, section_id="rest"),
    ]
    config = CaptionConfig(
        profile="generic",
        section_overrides=(
            SectionOverride(section_id="rest", overrides={"selection.mode": "full_captions"}),
        ),
    )
    p = plan(inputs(t, clips=clips, vertical=False), config=config)

    assert {s.mode for s in p.segments} == {CaptionMode.KEY_INFORMATION, CaptionMode.FULL_CAPTIONS}
    assert "Wetter" in " ".join(texts(p))


def test_a_rejected_caption_and_a_word_correction_survive() -> None:
    first = plan(inputs(transcript(PRICE)), "shorts_haul")
    rejected = first.segments[0].segment_id
    word = first.segments[-1].words[-1].word_id
    p = plan(
        inputs(transcript(PRICE)),
        "shorts_haul",
        decisions=[
            CaptionDecision(item_id=rejected, action=Action.REJECT),
            CaptionDecision(item_id=word, action=Action.CORRECT, text="herrlich."),
        ],
    )

    assert rejected not in {s.segment_id for s in p.segments}
    last = p.segments[-1].words[-1]
    assert last.text == "herrlich." and last.corrected
    assert any(c.code == "decision.rejected" and c.severity is Severity.INFO for c in p.checks)
