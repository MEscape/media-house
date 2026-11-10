"""Word edges snapped to measured speech: a pause swallowed by the aligner becomes a pause again."""

import pytest

from tests.support.analysis_fakes import speech_track, timeline

#: Where the voice really is.
VOICE = [("Das", 0.0, 0.3), ("ist", 0.35, 0.7), ("gut", 4.0, 4.4)]


def test_a_word_stretched_across_a_pause_ends_where_the_voice_stops() -> None:
    aligned = [("Das", 0.0, 0.3), ("ist", 0.35, 3.98), ("gut", 4.0, 4.4)]

    result = timeline([aligned], speech_track(VOICE, seconds=5.0))

    das, ist, gut = result.transcript.words
    assert (das.start, das.end) == (0.0, 0.3)
    assert ist.start == pytest.approx(0.35)
    assert ist.end == pytest.approx(0.7 + 0.06, abs=0.021)
    assert (gut.start, gut.end) == (4.0, 4.4)
    assert any(p.duration > 3.0 for p in result.pauses)
    assert result.transcript.segments[0].end == pytest.approx(4.4)


def test_a_leading_silence_inside_a_word_moves_its_start_to_the_voice() -> None:
    aligned = [("Das", 0.0, 0.3), ("ist", 0.35, 0.7), ("gut", 0.75, 4.4)]

    gut = timeline([aligned], speech_track(VOICE, seconds=5.0)).transcript.words[2]

    assert gut.start == pytest.approx(4.0, abs=0.021)
    assert gut.end == pytest.approx(4.4)


def test_words_matching_the_voice_and_words_without_any_voice_are_left_alone() -> None:
    aligned = [("Das", 0.0, 0.3), ("ist", 0.35, 0.7), ("leise", 1.5, 2.0), ("gut", 4.0, 4.4)]

    words = timeline([aligned], speech_track(VOICE, seconds=5.0)).transcript.words

    assert [(w.start, w.end) for w in words] == [(0.0, 0.3), (0.35, 0.7), (1.5, 2.0), (4.0, 4.4)]
