"""Building, querying and validating transcripts (pure domain, no I/O)."""

import dataclasses
from typing import Any

import pytest

from media_house.modules.audio_intelligence.domain.errors import AlignmentUnavailable
from media_house.modules.audio_intelligence.domain.frames import FrameRounding
from media_house.modules.audio_intelligence.domain.raw import (
    ALIGNMENT_SEGMENT,
    RawSegment,
    RawTranscription,
)
from media_house.modules.audio_intelligence.domain.text import NormalizationOptions, normalize_text
from media_house.modules.audio_intelligence.domain.transcript import (
    EMPHASIS_UPPERCASE,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
    WordTiming,
)
from media_house.modules.audio_intelligence.domain.validation import (
    Severity,
    validate_transcript,
)
from tests.support.audio_fakes import build, raw_transcription


def codes(transcript: Transcript) -> set[str]:
    return {i.code for i in validate_transcript(transcript)}


# --- building ----------------------------------------------------------------------------------
def test_words_keep_raw_text_punctuation_and_precise_times() -> None:
    t = build()

    first = t.words[0]
    assert (first.raw_word, first.normalized_word) == ("Hello,", "Hello")
    assert (first.start, first.end) == (0.42, 0.81)
    assert first.duration == pytest.approx(0.39)
    assert t.words[3].raw_word == "Feuerwerk,"
    assert t.words[3].normalized_word == "Feuerwerk"
    assert [w.index for w in t.words] == [0, 1, 2, 3]
    assert [w.segment_id for w in t.words] == [0, 0, 1, 1]
    assert first.confidence == 0.9


def test_words_are_chronological_and_segments_contain_their_words() -> None:
    t = build()

    assert [w.start for w in t.words] == sorted(w.start for w in t.words)
    for segment in t.segments:
        assert all(segment.start <= w.start and w.end <= segment.end for w in segment.words)
    assert codes(t) == set()


def test_preparation_offset_maps_times_onto_the_source_timeline() -> None:
    plain, shifted = build(), build(offset=0.5)

    for a, b in zip(plain.words, shifted.words, strict=True):
        assert b.start == pytest.approx(a.start + 0.5)
        assert b.end == pytest.approx(a.end + 0.5)
    assert shifted.segments[0].start == pytest.approx(plain.segments[0].start + 0.5)
    assert shifted.metadata.audio_offset == 0.5


def test_duration_is_the_media_duration_not_the_last_word() -> None:
    assert build(duration=60.0).duration == 60.0


def test_uppercase_words_get_an_emphasis_hint_only() -> None:
    t = build()

    assert t.words[2].emphasis_hints[0] == EMPHASIS_UPPERCASE
    assert t.words[2].emphasis_hint
    assert not t.words[0].emphasis_hint
    assert not t.words[3].emphasis_hint


def test_unaligned_tokens_are_interpolated_between_timed_neighbours_and_flagged() -> None:
    raw = raw_transcription(
        words=(("one", 1.0, 1.4), ("2024", None, None), ("three", 2.0, 2.4)),
        split_after=3,
    )

    t = build(raw)

    middle = t.words[1]
    assert middle.timing is WordTiming.INTERPOLATED
    assert 1.4 <= middle.start < middle.end <= 2.0
    assert t.words[0].timing is WordTiming.ALIGNED
    assert "interpolated_timing" in codes(t)
    assert t.metadata.warnings


def test_missing_word_timing_falls_back_to_marked_segment_estimates() -> None:
    raw = dataclasses.replace(
        raw_transcription(),
        segments=(RawSegment(2.0, 4.0, "alpha beta gamma", ()),),
        alignment_method=ALIGNMENT_SEGMENT,
        alignment_engine=None,
        alignment_model=None,
    )

    t = build(raw)

    assert [w.raw_word for w in t.words] == ["alpha", "beta", "gamma"]
    assert all(w.timing is WordTiming.SEGMENT_ESTIMATE for w in t.words)
    assert t.words[0].start == 2.0
    assert t.words[-1].end == pytest.approx(4.0)
    assert t.metadata.alignment_method == ALIGNMENT_SEGMENT
    assert "estimated_timing" in codes(t)


def test_empty_speech_yields_an_empty_but_valid_timeline() -> None:
    raw = dataclasses.replace(raw_transcription(), segments=())

    t = build(raw)

    assert t.words == ()
    assert t.first_word is None
    assert t.word_at(1.0) is None
    assert codes(t) == {"no_words"}


# --- text normalisation ------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Feuerwerk,", "Feuerwerk"),
        ("«Hallo»", "Hallo"),
        ("don't", "don't"),
        ("E-Mail.", "E-Mail"),
        ("...", ""),
        ("  spaced   out ", "spaced out"),
    ],
)
def test_default_normalisation(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_normalisation_is_configurable_and_leaves_the_canonical_word_alone() -> None:
    word = build().words[3]

    assert word.normalized(NormalizationOptions(lowercase=True)) == "feuerwerk"
    assert word.normalized(NormalizationOptions(strip_punctuation=False)) == "Feuerwerk,"
    assert word.raw_word == "Feuerwerk,"


# --- queries -----------------------------------------------------------------------------------
def test_word_at_uses_half_open_intervals() -> None:
    t = build()

    word = t.word_at(0.5)
    assert word is not None
    assert word.raw_word == "Hello,"
    assert t.word_at(0.42) is word
    assert t.word_at(0.81) is None  # end is exclusive; 0.81 falls in the gap before "world."
    assert t.word_at(0.835) is t.words[1]
    assert t.word_at(2.0) is None
    assert t.word_at(100.0) is None


def test_words_between_returns_every_overlapping_word() -> None:
    t = build()

    assert [w.raw_word for w in t.words_between(0.0, 1.0)] == ["Hello,", "world."]
    assert [w.raw_word for w in t.words_between(0.7, 0.9)] == ["Hello,", "world."]
    assert [w.raw_word for w in t.words_between(1.3, 3.4)] == []
    assert [w.raw_word for w in t.words_between(4.0, 4.6)] == ["AMAZING", "Feuerwerk,"]
    assert t.words_between(5.0, 5.0) == ()
    assert t.words_between(6.0, 5.0) == ()


def test_segment_queries_and_structure() -> None:
    t = build()

    first = t.segment_at(0.5)
    assert isinstance(first, TranscriptSegment)
    assert first.id == 0
    assert t.segment_at(2.0) is None
    assert [s.id for s in t.segments_between(0.0, 10.0)] == [0, 1]
    assert [s.id for s in t.segments_between(3.0, 4.0)] == [1]
    assert [w.raw_word for w in t.words_for_segment(1)] == ["AMAZING", "Feuerwerk,"]
    assert t.words_for_segment(99) == ()


def test_neighbours_and_ends() -> None:
    t = build()

    assert t.first_word is t.words[0]
    assert t.last_word is t.words[-1]
    assert t.next_word(t.words[0]) is t.words[1]
    assert t.previous_word(t.words[1]) is t.words[0]
    assert t.previous_word(t.words[0]) is None
    assert t.next_word(t.words[-1]) is None


def test_gaps_are_preserved_and_queryable() -> None:
    t = build(duration=10.0)

    assert t.gap_before(t.words[0]) == pytest.approx(0.42)  # silence from the start
    assert t.gap_before(t.words[1]) == pytest.approx(0.025)
    assert t.gap_before(t.words[2]) == pytest.approx(2.26)  # 1.24 -> 3.5
    assert t.gap_after(t.words[3]) == pytest.approx(10.0 - 5.1)  # to the media end
    long_pauses = t.pauses(min_duration=1.0)
    assert len(long_pauses) == 1
    assert long_pauses[0][0] == pytest.approx(1.24)
    assert long_pauses[0][1] == pytest.approx(3.5)
    assert len(t.pauses()) == 3


def test_frame_conversion_is_deterministic_and_fps_agnostic() -> None:
    t = build()
    word = t.words[0]

    assert t.to_frame(word.start, 30) == 12  # 0.42 * 30 = 12.6
    assert t.to_frame(word.start, 30, FrameRounding.ROUND) == 13
    assert t.to_frame(word.start, 25) == 10
    assert t.to_frame(word.start, 30) == t.to_frame(word.start, 30)
    assert t.from_frame(30, 30) == 1.0
    assert word.start == 0.42  # seconds stay canonical


# --- validation --------------------------------------------------------------------------------
def with_words(t: Transcript, **changes: Any) -> Transcript:
    """Replace the first word's fields, keeping everything else (to inject bad data)."""
    first = dataclasses.replace(t.words[0], **changes)
    segment = dataclasses.replace(t.segments[0], words=(first, *t.segments[0].words[1:]))
    return Transcript(t.metadata, (segment, *t.segments[1:]))


@pytest.mark.parametrize(
    ("changes", "code", "severity"),
    [
        ({"start": -1.0}, "negative_start", Severity.ERROR),
        ({"start": 0.9, "end": 0.5}, "end_before_start", Severity.ERROR),
        ({"end": 99.0}, "past_duration", Severity.WARNING),
        ({"start": float("nan")}, "non_finite_time", Severity.ERROR),
    ],
)
def test_validation_reports_impossible_values(
    changes: dict[str, float],
    code: str,
    severity: Severity,
) -> None:
    findings = {i.code: i for i in validate_transcript(with_words(build(), **changes))}

    assert code in findings
    assert findings[code].severity is severity


def test_validation_reports_overlap_disorder_gaps_and_segment_mismatch() -> None:
    t = build()
    assert "words_out_of_order" in codes(with_words(t, start=0.9, end=0.95))
    assert "words_overlap" in codes(with_words(t, end=0.9))  # runs into "world."
    assert codes(with_words(t, start=0.45, end=0.6)) == set()  # inside its segment: fine
    assert "word_outside_segment" in codes(with_words(t, end=1.5))

    far = build(raw_transcription(words=(("a", 1.0, 1.5), ("b", 30.0, 30.5)), split_after=2))
    assert "long_gap" in codes(far)


def test_validation_never_alters_the_data() -> None:
    bad = with_words(build(), start=-1.0)

    validate_transcript(bad)

    assert bad.words[0].start == -1.0


def test_alignment_errors_are_user_readable() -> None:
    error = AlignmentUnavailable("xx", "no model")

    assert "xx" in error.user_message


def test_word_is_immutable() -> None:
    word: TranscriptWord = build().words[0]

    with pytest.raises(dataclasses.FrozenInstanceError):
        word.start = 5.0  # type: ignore[misc]


def test_raw_transcription_type_is_engine_neutral() -> None:
    assert isinstance(raw_transcription(), RawTranscription)
