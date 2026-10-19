"""Sound against movement, speech across cuts, and the intervals handed to downstream stages."""

from typing import Any

import pytest

from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.inputs import (
    AudioEventFact,
    ContentOpportunityFact,
    MouthSample,
    ShotFacts,
    VideoEventFact,
)
from media_house.modules.audio_visual_alignment.domain.model import Opportunity, SoundAction
from media_house.modules.audio_visual_alignment.domain.values import (
    BoundaryKind,
    FlagKind,
    OpportunityKind,
    Visibility,
)
from tests.support.avl_facts import (
    audio,
    content,
    ft,
    inputs,
    segment,
    shot,
    still,
    talking,
    video,
)
from tests.unit.audio_visual_alignment.helpers import run


def spike(i: int, at: float, strength: float = 0.8) -> AudioEventFact:
    return AudioEventFact(f"a{i}", "energy_spike", at, at + 0.1, strength)


def motion(i: int, at: float, kind: str = "motion_peak") -> VideoEventFact:
    return VideoEventFact(f"v{i}", kind, "s", ft(at))


CLOCK_OK = None  # no inspection: the tolerance gains the unverified-origin slack


def pairs(
    heard: list[AudioEventFact],
    seen: list[VideoEventFact],
    *,
    duration: float = 60.0,
    profile: AlignmentProfile | None = None,
) -> tuple[SoundAction, ...]:
    from media_house.modules.audio_visual_alignment.domain.inputs import ClockFacts

    clock = ClockFacts(container_start=0.0, video_first_pts=0.0)
    doc = run(
        inputs(
            audio([], events=heard, duration=duration),
            video([shot("s", 0, duration)], events=seen, duration=duration, first_frame=0.0),
            clock=clock,
        ),
        profile,
    )
    return doc.sound_actions


class TestSoundAction:
    def test_a_movement_at_the_sound_onset_is_a_candidate_with_its_offset(self) -> None:
        found = pairs([spike(1, 10.0)], [motion(1, 10.1)])

        assert len(found) == 1
        assert found[0].delta_seconds == pytest.approx(0.1)
        assert found[0].scores.strength is not None and found[0].scores.strength > 0

    def test_a_movement_outside_the_tolerance_is_not_paired(self) -> None:
        assert pairs([spike(1, 10.0)], [motion(1, 11.0)]) == ()

    def test_a_sound_with_no_visible_movement_is_simply_unpaired(self) -> None:
        assert pairs([spike(1, 10.0)], []) == ()

    def test_weak_sounds_and_other_kinds_are_not_considered(self) -> None:
        weak = pairs([spike(1, 10.0, strength=0.2)], [motion(1, 10.0)])
        other = pairs([spike(1, 10.0)], [motion(1, 10.0, kind="flash")])

        assert weak == () and other == ()

    def test_the_nearest_movements_are_kept_in_order_and_bounded(self) -> None:
        seen = [motion(i, 10.0 + d) for i, d in enumerate([0.3, -0.05, 0.1, -0.2])]

        found = pairs([spike(1, 10.0)], seen, profile=AlignmentProfile(max_candidates=2))

        assert [round(p.delta_seconds, 2) for p in found] == [-0.05, 0.1]
        assert found[0].candidates_in_window == 4

    def test_a_busy_picture_makes_a_match_weaker_than_a_rare_movement(self) -> None:
        rare = pairs([spike(1, 10.0)], [motion(1, 10.05)])
        busy = pairs(
            [spike(1, 10.0)], [motion(i, 10.05 + i * 0.5) for i in range(100)], duration=60.0
        )

        assert busy[0].chance_hits > rare[0].chance_hits
        assert (busy[0].scores.strength or 0.0) < (rare[0].scores.strength or 0.0)

    def test_without_an_inspection_the_window_is_widened_for_the_unverified_origin(self) -> None:
        doc = run(
            inputs(
                audio([], events=[spike(1, 10.0)]),
                video([shot("s", 0, 60)], events=[motion(1, 10.6)], first_frame=0.0),
            )
        )

        assert len(doc.sound_actions) == 1
        assert doc.sound_actions[0].tolerance_seconds == pytest.approx(0.85)

    def test_a_video_correction_moves_the_picture_side_onto_the_audio_clock(self) -> None:
        from media_house.modules.audio_visual_alignment.domain.inputs import ClockFacts

        clock = ClockFacts(container_start=0.0, video_first_pts=0.5)  # frame 0 is at 0.5 s
        doc = run(
            inputs(
                audio([], events=[spike(1, 10.5)]),
                video([shot("s", 0, 60)], events=[motion(1, 10.0)], first_frame=0.0),
                clock=clock,
            )
        )

        assert doc.sound_actions[0].delta_seconds == pytest.approx(0.0)


class TestBoundaries:
    def test_speech_across_a_cut_reports_both_sides(self) -> None:
        mouth = talking(0, 15) + still(15, 60)
        doc = run(
            inputs(
                audio([segment("1", 10.0, 22.0)]),
                video([shot("a", 0, 15), shot("b", 15, 60)], mouth),
            )
        )

        relation = doc.boundaries[0]
        assert relation.kind is BoundaryKind.SPEECH_CROSSES_CUT
        assert relation.boundary_at == 15.0
        assert relation.speech_before_seconds == pytest.approx(5.0)
        assert relation.speech_after_seconds == pytest.approx(7.0)
        assert (relation.visibility_before, relation.visibility_after) == (
            Visibility.SPEAKING_FACE,
            Visibility.QUIET_FACE,
        )

    def test_speech_that_stays_in_one_shot_has_no_boundary(self) -> None:
        doc = run(inputs(audio([segment("1", 2, 8)]), video([shot("a", 0, 60)], talking(0, 60))))

        assert doc.boundaries == ()


class TestNoVisibleSpeakerWindow:
    def window(
        self,
        shots: list[ShotFacts],
        mouth: list[MouthSample],
        start: float = 10.0,
        end: float = 30.0,
        profile: AlignmentProfile | None = None,
    ) -> list[Opportunity]:
        doc = run(inputs(audio([segment("1", start, end)]), video(shots, mouth)), profile)
        return [o for o in doc.opportunities if o.kind is OpportunityKind.NO_VISIBLE_SPEAKER_WINDOW]

    def test_a_still_face_during_speech_yields_a_window_inside_the_margins(self) -> None:
        found = self.window([shot("a", 0, 60)], still(0, 60))

        assert (found[0].start, found[0].end) == (10.25, 29.75)
        assert "mouth is still" in found[0].reasons[0]

    def test_no_face_found_also_yields_one(self) -> None:
        assert len(self.window([shot("a", 0, 60, faces=0)], [])) == 1

    def test_a_speaking_face_yields_none(self) -> None:
        assert self.window([shot("a", 0, 60)], talking(0, 60)) == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"measured": False},  # never measured
            {"people": 3},  # several people
        ],
    )
    def test_unknown_unmeasured_and_ambiguous_never_create_one(
        self, kwargs: dict[str, Any]
    ) -> None:
        assert self.window([shot("a", 0, 60, **kwargs)], still(0, 60)) == []

    def test_too_few_samples_never_create_one(self) -> None:
        assert self.window([shot("a", 0, 60)], still(40, 60)) == []

    def test_a_window_too_short_after_the_margins_is_dropped(self) -> None:
        assert self.window([shot("a", 0, 60)], still(0, 60), start=10.0, end=11.5) == []

    def test_the_window_stops_short_of_the_cut(self) -> None:
        mouth = talking(0, 20) + still(20, 60)
        found = self.window([shot("a", 0, 20), shot("b", 20, 60)], mouth, start=10.0, end=40.0)

        assert [(o.shot_ids, o.start) for o in found] == [(("b",), 20.25)]

    def test_a_window_does_not_say_a_cutaway_is_wanted(self) -> None:
        found = self.window([shot("a", 0, 60)], still(0, 60))

        assert any("voice-over" in x for x in found[0].limitations)


class TestPassageContext:
    def fact(self, covered: bool = False) -> ContentOpportunityFact:
        return ContentOpportunityFact(
            "o1", "broll", "battery pack", "p1", 10.0, 20.0, 0.7, covered, ("s1",)
        )

    def context(
        self, shots: list[ShotFacts], mouth: list[MouthSample], covered: bool = False
    ) -> Opportunity:
        doc = run(
            inputs(
                audio([segment("1", 8.0, 22.0)]),
                video(shots, mouth),
                content_facts=content([("p1", 8.0, 22.0, "battery pack")], [self.fact(covered)]),
            )
        )
        return next(
            o for o in doc.opportunities if o.kind is OpportunityKind.CONTENT_PASSAGE_CONTEXT
        )

    def test_content_intelligences_coverage_verdict_is_passed_through_unchanged(self) -> None:
        assert (
            self.context([shot("a", 0, 60)], talking(0, 60), covered=True).already_covered is True
        )
        assert (
            self.context([shot("a", 0, 60)], talking(0, 60), covered=False).already_covered is False
        )

    def test_it_states_how_long_a_speaking_face_is_on_screen(self) -> None:
        found = self.context([shot("a", 0, 60)], talking(0, 60))

        assert found.visible_speaking_seconds == pytest.approx(10.0)
        assert "speaking face is on screen for 10.0s" in found.reasons[1]

    def test_it_names_the_shots_overlapping_the_passage(self) -> None:
        found = self.context([shot("a", 0, 15), shot("b", 15, 60)], still(0, 60))

        assert found.shot_ids == ("a", "b") and found.visible_speaking_seconds == 0.0

    def test_it_never_claims_a_visual_is_wanted(self) -> None:
        found = self.context([shot("a", 0, 60)], still(0, 60))

        assert any("not decided here" in x for x in found.limitations)


def test_a_fast_profile_skips_everything_but_speech() -> None:
    from media_house.modules.audio_visual_alignment.domain.config import PROFILES

    doc = run(
        inputs(
            audio([segment("1", 10, 20)], events=[spike(1, 10.0)]),
            video([shot("a", 0, 60)], talking(0, 60), events=[motion(1, 10.0)]),
            content_facts=content([("p1", 8.0, 22.0, "battery pack")]),
        ),
        PROFILES["fast"],
    )

    assert doc.speech_sources and not (doc.text_matches or doc.sound_actions or doc.opportunities)
    assert FlagKind.MOUTH_SIGNAL_COARSE in {f.kind for f in doc.flags}
