"""Sentences keep their source text and say how reliable their times are."""

import pytest

from media_house.modules.content_intelligence.domain.config import Thresholds
from media_house.modules.content_intelligence.domain.errors import InvalidTranscriptInput
from media_house.modules.content_intelligence.domain.source import (
    SourceSegment,
    SourceTranscript,
    SourceWord,
    validate_source,
)
from media_house.modules.content_intelligence.domain.units import build_units, split_sentences
from media_house.modules.content_intelligence.domain.values import SpeechTiming, TimeBasis
from tests.support.ci_texts import lines_to_transcript

T = Thresholds()


def texts(source: SourceTranscript, language: str = "en") -> list[str]:
    return [u.text for u in build_units(source, T, language)]


class TestSentenceSplitting:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("One. Two! Three?", ["One.", "Two!", "Three?"]),
            ("It costs 3.5 euros. Cheap.", ["It costs 3.5 euros.", "Cheap."]),
            ("Dr. Smith arrived. He sat.", ["Dr. Smith arrived.", "He sat."]),
            ("See e.g. the manual. Then act.", ["See e.g. the manual.", "Then act."]),
            ("J. Smith spoke. We listened.", ["J. Smith spoke.", "We listened."]),
            ("He said “stop.” Then left.", ["He said “stop.”", "Then left."]),
        ],
    )
    def test_ordinary_text(self, text: str, expected: list[str]) -> None:
        got = [text[a:b] for a, b in split_sentences(text, "en")]

        assert got == expected

    def test_a_german_ordinal_is_not_a_sentence_end(self) -> None:
        text = "Am 3. Mai kam er an. Er blieb."

        got = [text[a:b] for a, b in split_sentences(text, "de")]

        assert got == ["Am 3. Mai kam er an.", "Er blieb."]

    def test_cjk_punctuation_ends_a_sentence(self) -> None:
        text = "今日は晴れです。明日は雨です。"

        assert len(split_sentences(text, "ja")) == 2

    def test_text_without_punctuation_stays_one_sentence_per_segment(self) -> None:
        source = lines_to_transcript(["so this is one thought", "and this is another"])

        assert texts(source) == ["so this is one thought", "and this is another"]


class TestUnitsAreVerbatim:
    def test_every_unit_is_a_slice_of_its_source_segment(self) -> None:
        source = lines_to_transcript(
            ["Hello there. This is a test!  Really?", "Second   segment, spaced."]
        )
        by_id = {s.id: s.text for s in source.segments}

        units = build_units(source, T, "en")

        assert units
        for unit in units:
            start, end = unit.char_range
            assert by_id[unit.segment_id][start:end] == unit.text

    def test_the_source_transcript_is_never_modified(self) -> None:
        source = lines_to_transcript(["Hello  there.  Bye."])
        before = source.fingerprint()

        build_units(source, T, "en")

        assert source.fingerprint() == before

    def test_one_word_fragments_join_the_next_sentence(self) -> None:
        source = lines_to_transcript(["Yes. We should go now."])

        assert texts(source) == ["Yes. We should go now."]

    def test_a_very_long_sentence_is_split_at_a_pause_rather_than_mid_clause(self) -> None:
        words = [f"word{i}" for i in range(80)]
        tokens = []
        clock = 0.0
        for i, w in enumerate(words):
            clock += 1.0 if i == 40 else 0.0  # a one second pause in the middle
            tokens.append(SourceWord(w, clock, clock + 0.3))
            clock += 0.3
        segment = SourceSegment(0, " ".join(words), 0.0, clock, words=tuple(tokens))
        source = SourceTranscript((segment,), language="en", duration=clock)

        got = texts(source)

        assert len(got) == 2
        assert got[0].split()[-1] == "word39"


class TestTiming:
    def test_word_times_make_unit_times_exact(self) -> None:
        source = lines_to_transcript(["First sentence here. Second one follows."])

        first, second = build_units(source, T, "en")

        assert first.span.start_basis is TimeBasis.WORDS and first.span.end_basis is TimeBasis.WORDS
        assert first.span.end is not None and second.span.start is not None
        assert first.span.end <= second.span.start
        assert first.span.exact and second.span.exact

    def test_upstream_estimates_are_not_called_exact(self) -> None:
        source = lines_to_transcript(["A short one.", "Another."], timing=SpeechTiming.INTERPOLATED)

        units = build_units(source, T, "en")

        assert all(u.span.start_basis is TimeBasis.UPSTREAM_ESTIMATE for u in units)
        assert not any(u.span.exact for u in units)

    def test_without_words_a_whole_segment_keeps_the_source_times(self) -> None:
        source = lines_to_transcript(["Only one sentence here."], with_words=False)

        (unit,) = build_units(source, T, "en")

        assert unit.span.start_basis is TimeBasis.SOURCE
        assert unit.span.end_basis is TimeBasis.SOURCE
        assert (unit.span.start, unit.span.end) == (
            source.segments[0].start,
            source.segments[0].end,
        )

    def test_without_words_an_inner_boundary_is_marked_interpolated(self) -> None:
        source = lines_to_transcript(
            ["First sentence here. Second sentence here."], with_words=False
        )

        first, second = build_units(source, T, "en")

        assert first.span.start_basis is TimeBasis.SOURCE
        assert first.span.end_basis is TimeBasis.INTERPOLATED
        assert second.span.start_basis is TimeBasis.INTERPOLATED
        assert second.span.end_basis is TimeBasis.SOURCE
        segment = source.segments[0]
        assert segment.start is not None and segment.end is not None
        assert first.span.end is not None and segment.start < first.span.end < segment.end

    def test_a_transcript_without_times_has_missing_spans_not_zero(self) -> None:
        source = lines_to_transcript(["Nothing is timed here."], timed=False)

        (unit,) = build_units(source, T, "en")

        assert unit.span.start is None and unit.span.end is None
        assert unit.span.start_basis is TimeBasis.MISSING

    def test_silence_before_a_unit_is_measured_not_guessed(self) -> None:
        source = lines_to_transcript(["First thing said.", "Then a long wait."], pauses={1: 2.5})

        first, second = build_units(source, T, "en")

        assert first.gap_before is None
        assert second.gap_before == pytest.approx(2.5)


class TestValidation:
    def test_duplicate_ids_are_refused(self) -> None:
        source = SourceTranscript((SourceSegment(1, "a b c."), SourceSegment(1, "d e f.")))

        with pytest.raises(InvalidTranscriptInput):
            validate_source(source)

    def test_a_segment_ending_before_it_starts_is_refused(self) -> None:
        with pytest.raises(InvalidTranscriptInput):
            validate_source(SourceTranscript((SourceSegment(0, "x y z.", 5.0, 1.0),)))

    def test_half_a_time_range_is_refused(self) -> None:
        with pytest.raises(InvalidTranscriptInput):
            validate_source(SourceTranscript((SourceSegment(0, "x y z.", 1.0, None),)))

    def test_odd_but_usable_input_only_warns(self) -> None:
        source = SourceTranscript(
            (SourceSegment(0, "late words here.", 5.0, 99.0), SourceSegment(1, "early.", 1.0, 2.0)),
            language=None,
            duration=10.0,
        )

        warnings = validate_source(source)

        assert any("before the previous" in w for w in warnings)
        assert any("after the media duration" in w for w in warnings)
        assert any("language is unknown" in w for w in warnings)

    def test_the_fingerprint_follows_text_time_and_language(self) -> None:
        base = lines_to_transcript(["One sentence here."])

        assert base.fingerprint() == lines_to_transcript(["One sentence here."]).fingerprint()
        assert base.fingerprint() != lines_to_transcript(["One sentence there."]).fingerprint()
        assert (
            base.fingerprint()
            != lines_to_transcript(
                ["One sentence here."], gap=0.9, seconds_per_word=0.5
            ).fingerprint()
        )
        assert (
            base.fingerprint()
            != lines_to_transcript(["One sentence here."], language="de").fingerprint()
        )

    def test_plain_text_becomes_untimed_segments(self) -> None:
        source = SourceTranscript.from_text("First line.\n\nSecond line.\n", language="en")

        assert [s.text for s in source.segments] == ["First line.", "Second line."]
        assert all(s.start is None for s in source.segments)
