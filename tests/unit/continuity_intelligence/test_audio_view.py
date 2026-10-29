"""The boundary-local sound facts, read from a real audio timeline built from scripted words."""

from fractions import Fraction

from media_house.modules.continuity_intelligence.application.views import audio_edge
from tests.support.analysis_fakes import timeline

#: "hello there my friend" with a pause after "there" and a longer one at the end.
WORDS = [
    [("hello", 0.0, 0.4), ("there", 0.5, 1.0)],
    [("my", 1.8, 2.0), ("friend", 2.1, 2.7)],
]


def edge(moment: float, outgoing: bool):  # type: ignore[no-untyped-def]
    return audio_edge(
        timeline(WORDS, duration=4.0), Fraction(moment).limit_denominator(1000), outgoing
    )


def test_a_boundary_inside_a_word_names_the_word_and_how_far_it_is_from_its_edge() -> None:
    inside = edge(2.4, outgoing=True)

    assert inside.straddled_word == "friend"
    assert inside.word_edge_distance is not None and 0.25 < inside.word_edge_distance <= 0.3
    assert inside.speech_near


def test_a_boundary_in_a_silence_cuts_no_word_and_offers_the_pause() -> None:
    quiet = edge(1.4, outgoing=True)

    assert quiet.straddled_word is None and quiet.word_edge_distance is None
    assert not quiet.speech_near
    assert quiet.pause_before is not None or quiet.pause_after is not None


def test_levels_are_taken_from_the_side_of_the_boundary_they_belong_to() -> None:
    before = edge(1.4, outgoing=True)
    after = edge(1.4, outgoing=False)

    assert before.level_db is not None and after.level_db is not None
    assert before.floor_db is not None and after.floor_db is not None


def test_a_boundary_outside_the_analysed_sound_has_no_levels_rather_than_zero() -> None:
    far = edge(99.0, outgoing=True)

    assert far.level_db is None and far.floor_db is None and far.straddled_word is None
