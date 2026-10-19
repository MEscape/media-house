"""What the picture says about speech, and when a speaker may be linked to a person."""

import pytest

from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.inputs import MouthSample, ShotFacts
from media_house.modules.audio_visual_alignment.domain.model import AlignmentDocument
from media_house.modules.audio_visual_alignment.domain.values import (
    FlagKind,
    LinkState,
    Visibility,
)
from tests.support.avl_facts import audio, inputs, segment, shot, still, talking, video
from tests.unit.audio_visual_alignment.helpers import run


def visibility(
    shots: list[ShotFacts],
    mouth: list[MouthSample],
    start: float = 10.0,
    end: float = 20.0,
    speaker: str | None = None,
) -> Visibility:
    doc = run(inputs(audio([segment("1", start, end, speaker)]), video(shots, mouth)))
    return doc.speech_sources[0].visibility


class TestVisibility:
    def test_a_moving_mouth_during_speech_is_a_speaking_face(self) -> None:
        assert visibility([shot("a", 0, 60)], talking(0, 60)) is Visibility.SPEAKING_FACE

    def test_a_still_mouth_is_a_quiet_face_not_an_off_screen_voice(self) -> None:
        result = visibility([shot("a", 0, 60)], still(0, 60))

        assert result is Visibility.QUIET_FACE

    def test_a_face_that_is_only_present_proves_nothing(self) -> None:
        # Samples exist but the mouth moves a little: neither speaking nor still.
        middling = [type(m)(m.seconds, 0.2 + 0.04 * (i % 2)) for i, m in enumerate(talking(0, 60))]

        assert visibility([shot("a", 0, 60)], middling) is Visibility.INSUFFICIENT

    def test_too_few_mouth_samples_are_insufficient_not_quiet(self) -> None:
        assert visibility([shot("a", 0, 60)], talking(12, 13)) is Visibility.INSUFFICIENT

    def test_no_face_found_by_an_analyser_that_ran_is_stated_as_such(self) -> None:
        assert visibility([shot("a", 0, 60, faces=0)], []) is Visibility.NO_FACE_DETECTED

    def test_an_analyser_that_did_not_run_is_unavailable_not_absent(self) -> None:
        assert visibility([shot("a", 0, 60, measured=False)], []) is Visibility.VISUAL_UNAVAILABLE

    def test_several_people_make_even_a_moving_mouth_ambiguous(self) -> None:
        assert visibility([shot("a", 0, 60, people=3)], talking(0, 60)) is Visibility.AMBIGUOUS

    def test_speech_outside_every_shot_has_no_visual_evidence(self) -> None:
        doc = run(
            inputs(audio([segment("1", 70.0, 75.0)], duration=80.0), video([shot("a", 0, 60)]))
        )

        source = doc.speech_sources[0]
        assert source.visibility is Visibility.VISUAL_UNAVAILABLE and source.shots == ()

    def test_a_cut_inside_speech_keeps_each_shot_separate(self) -> None:
        mouth = talking(0, 15) + still(15, 60)
        doc = run(
            inputs(
                audio([segment("1", 10.0, 22.0)]),
                video([shot("a", 0, 15), shot("b", 15, 60)], mouth),
            )
        )

        source = doc.speech_sources[0]
        assert [(p.shot_id, p.visibility) for p in source.shots] == [
            ("a", Visibility.SPEAKING_FACE),
            ("b", Visibility.QUIET_FACE),
        ]
        # 7 s of quiet face against 5 s of speaking face
        assert source.visibility is Visibility.QUIET_FACE

    def test_a_sliver_of_overlap_is_not_judged(self) -> None:
        doc = run(
            inputs(
                audio([segment("1", 10.0, 20.0)]),
                video([shot("a", 0, 10.3), shot("b", 10.3, 60)], talking(0, 60)),
            )
        )

        assert [p.shot_id for p in doc.speech_sources[0].shots] == ["b"]

    def test_strength_exists_only_where_there_is_mouth_evidence(self) -> None:
        loud = run(inputs(audio([segment("1", 10, 20)]), video([shot("a", 0, 60)], talking(0, 60))))
        none = run(inputs(audio([segment("1", 10, 20)]), video([shot("a", 0, 60, faces=0)])))

        assert loud.speech_sources[0].scores.strength is not None
        assert none.speech_sources[0].scores.strength is None


def scenario(*parts: tuple[str, float, float, str | None, str, str, str]) -> AlignmentDocument:
    """Segments of two speakers over shots: (segment_id, start, end, speaker, shot, mouth_kind)."""
    segments, shots, mouth = [], {}, []
    for seg_id, start, end, speaker, shot_id, identity, kind in parts:
        segments.append(segment(seg_id, start, end, speaker))
        shots[shot_id] = shot(shot_id, start - 0.5, end + 0.5, identity=identity)
        mouth += (talking if kind == "talk" else still)(start, end)
    return run(
        inputs(
            audio(segments, duration=400.0),
            video(
                sorted(shots.values(), key=lambda s: s.range.start.seconds), mouth, duration=400.0
            ),
        )
    )


class TestSpeakerLinks:
    def test_a_speaker_seen_speaking_as_one_person_is_linked(self) -> None:
        doc = scenario(
            ("1", 10, 20, "S1", "a", "person_A", "talk"),
            ("2", 30, 42, "S1", "b", "person_A", "talk"),
        )

        link = doc.speaker_links[0]
        assert (link.speaker, link.identity_id, link.state) == (
            "S1",
            "person_A",
            LinkState.SUPPORTED,
        )
        assert link.supporting_seconds == pytest.approx(22.0, abs=1.0)
        assert link.scores.identity_support == 1.0

    def test_two_short_observations_are_only_a_candidate(self) -> None:
        doc = scenario(
            ("1", 10, 13.5, "S1", "a", "person_A", "talk"),
            ("2", 30, 33.5, "S1", "b", "person_A", "talk"),
        )

        assert doc.speaker_links[0].state is LinkState.CANDIDATE

    def test_one_long_shot_is_enough_alone(self) -> None:
        doc = scenario(("1", 10, 50, "S1", "a", "person_A", "talk"))

        assert doc.speaker_links[0].state is LinkState.SUPPORTED

    def test_one_moderate_shot_is_not_enough_alone(self) -> None:
        doc = scenario(("1", 10, 25, "S1", "a", "person_A", "talk"))

        assert doc.speaker_links[0].state is LinkState.CANDIDATE

    def test_a_speaker_seen_as_two_people_is_a_conflict_not_a_pick(self) -> None:
        doc = scenario(
            ("1", 10, 24, "S1", "a", "person_A", "talk"),
            ("2", 30, 44, "S1", "b", "person_A", "talk"),
            ("3", 50, 64, "S1", "c", "person_B", "talk"),
            ("4", 70, 84, "S1", "d", "person_B", "talk"),
        )

        states = {link.identity_id: link.state for link in doc.speaker_links}
        assert states == {"person_A": LinkState.CONFLICT, "person_B": LinkState.CONFLICT}
        assert FlagKind.SPEAKER_LINK_CONFLICT in {f.kind for f in doc.flags}

    def test_two_voices_on_one_face_cluster_are_a_conflict(self) -> None:
        doc = scenario(
            ("1", 10, 24, "S1", "a", "person_A", "talk"),
            ("2", 30, 44, "S1", "b", "person_A", "talk"),
            ("3", 50, 64, "S2", "c", "person_A", "talk"),
            ("4", 70, 84, "S2", "d", "person_A", "talk"),
        )

        assert {link.state for link in doc.speaker_links} == {LinkState.CONFLICT}

    def test_a_quiet_face_while_the_speaker_talks_counts_against_the_link(self) -> None:
        doc = scenario(
            ("1", 10, 24, "S1", "a", "person_A", "talk"),
            ("2", 30, 44, "S1", "b", "person_A", "talk"),
            ("3", 50, 80, "S1", "c", "person_A", "quiet"),
        )

        link = doc.speaker_links[0]
        assert link.contradicting_seconds > link.supporting_seconds
        assert link.state is LinkState.CANDIDATE
        assert FlagKind.SPEAKER_CONTRADICTED in {f.kind for f in doc.flags}

    def test_ambiguous_and_unmeasured_shots_count_for_nothing(self) -> None:
        busy = shot("a", 9, 61, people=3, identity="person_A")
        doc = run(
            inputs(
                audio([segment("1", 10, 60, "S1")]),
                video([busy], talking(0, 60)),
            )
        )

        assert doc.speaker_links == ()

    def test_without_speaker_labels_no_link_is_made_and_the_flag_says_why(self) -> None:
        doc = scenario(("1", 10, 50, None, "a", "person_A", "talk"))

        assert doc.speaker_links == ()
        assert FlagKind.NO_SPEAKER_LABELS in {f.kind for f in doc.flags}

    def test_the_support_thresholds_come_from_the_profile(self) -> None:
        doc_strict = run(
            inputs(
                audio([segment("1", 10, 50, "S1")]),
                video([shot("a", 9, 51)], talking(0, 60)),
            ),
            AlignmentProfile(link_solo_support_seconds=100.0, link_min_shots=2),
        )

        assert doc_strict.speaker_links[0].state is LinkState.CANDIDATE
