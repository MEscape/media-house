"""The fifteen editorial scenarios: what is flagged, what is left alone, and why."""

from dataclasses import replace

import pytest

from media_house.modules.continuity_intelligence.domain.facts import AudioEdge
from media_house.modules.continuity_intelligence.domain.model import Issue
from media_house.modules.continuity_intelligence.domain.values import (
    Availability,
    IdentityVerdict,
    IssueType,
    Level,
    Owner,
    RepairKind,
    ScoreName,
    TransitionKind,
)
from tests.support.cont_fakes import (
    edge,
    issue_of,
    name,
    run,
    secs,
    side,
    subject,
    transition,
)


def jump(change: float = 0.1, **extra: object):  # type: ignore[no-untyped-def]
    """A cut inside one continuous shot where the subject moves ``change`` of the picture."""
    a = side("c1", end=10.0, edge_=edge(subject=subject(cx=0.40)))
    b = side(
        "c2", outgoing=False, start=12.0, end=20.0, edge_=edge(subject=subject(cx=0.40 + change))
    )
    return transition(a=a, b=b, kind=TransitionKind.SAME_SHOT, gap=2.0, **extra)  # type: ignore[arg-type]


class TestJumpCuts:
    """1. A talking-head jump cut that is the style, and the same cut elsewhere."""

    def test_a_talking_head_accepts_the_jump_cut_but_still_reports_it_quietly(self) -> None:
        analysis = run([jump(trimmed=True)], profile="talking_head")

        found = issue_of(analysis, IssueType.JUMP_CUT)
        assert found is not None and found.level is Level.INFO and not found.surfaced
        assert (found.intentional_style_likelihood or 0) >= 0.7
        assert "accepts this kind of break" in found.style_reason
        assert analysis.surfaced() == ()

    def test_a_cinematic_dialogue_flags_the_same_cut(self) -> None:
        found = issue_of(run([jump()], profile="cinematic_dialogue"), IssueType.JUMP_CUT)

        assert found is not None and found.level in (Level.MEDIUM, Level.HIGH) and found.surfaced
        assert (found.intentional_style_likelihood or 0) < 0.3

    def test_severity_confidence_style_and_difficulty_are_separate_answers(self) -> None:
        found = issue_of(run([jump()], profile="generic"), IssueType.JUMP_CUT)

        assert found is not None
        severity = found.score(ScoreName.JUMP_CUT_SEVERITY)
        assert severity is not None and severity.value is not None
        assert found.detectability_confidence is not None
        assert found.intentional_style_likelihood is not None
        assert (
            len(
                {severity.value, found.detectability_confidence, found.intentional_style_likelihood}
            )
            == 3
        )
        assert any(r.difficulty is not None for r in found.repairs)

    def test_a_barely_visible_move_is_not_a_jump_cut(self) -> None:
        assert issue_of(run([jump(0.01)]), IssueType.JUMP_CUT) is None

    def test_a_cutaway_over_the_cut_hides_it(self) -> None:
        assert run([jump(covered=True)]).issues == ()

    def test_a_subject_nobody_tracked_is_unresolved_not_judged(self) -> None:
        a = side("c1", edge_=edge(subject=None))
        b = side("c2", outgoing=False, start=12.0, end=20.0, edge_=edge(subject=None))
        analysis = run([transition(a=a, b=b, kind=TransitionKind.SAME_SHOT, gap=2.0)])

        assert analysis.issues == ()
        assert any("subject" in m for u in analysis.unresolved for m in u.missing)


class TestSpatial:
    """2. Screen direction and eyelines."""

    def cut(self, vx_out: float, vx_in: float):  # type: ignore[no-untyped-def]
        a = side("c1", edge_=edge(subject=subject(vx=vx_out, vy=0.0)))
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(subject=subject(cx=0.5, vx=vx_in, vy=0.0)),
        )
        return transition(a=a, b=b, gap=0.2)

    def test_a_reversal_of_travel_in_one_scene_is_flagged_in_a_cinematic_dialogue(self) -> None:
        found = issue_of(
            run([self.cut(0.2, -0.2)], profile="cinematic_dialogue"), IssueType.SCREEN_DIRECTION
        )

        assert found is not None and found.level in (Level.MEDIUM, Level.HIGH)
        spatial = found.score(ScoreName.SPATIAL_CONTINUITY)
        assert spatial is not None and spatial.value is not None and spatial.value < 0.5

    def test_travel_that_continues_is_compatible_not_an_issue(self) -> None:
        analysis = run([self.cut(0.2, 0.2)])

        assert issue_of(analysis, IssueType.SCREEN_DIRECTION) is None

    def test_a_montage_does_not_get_a_direction_lecture(self) -> None:
        found = run([self.cut(0.2, -0.2)], profile="fast_montage")

        assert found.surfaced() == ()

    def test_two_people_looking_the_same_way_in_a_conversation_may_have_crossed_the_axis(
        self,
    ) -> None:
        a = side(
            "c1",
            edge_=edge(subject=subject(identity_cluster="A"), head_yaw=-35.0, visual_speaking=0.8),
        )
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(subject=subject(identity_cluster="B"), head_yaw=-30.0, visual_speaking=0.1),
        )

        found = issue_of(
            run([transition(a=a, b=b, gap=2.0)], profile="interview_podcast"), IssueType.EYELINE
        )

        assert found is not None and found.unresolved_questions

    def test_without_tracking_the_spatial_verdict_stays_open(self) -> None:
        a = side("c1", edge_=edge(subject=None))
        b = side("c2", outgoing=False, start=12.0, end=20.0, edge_=edge(subject=None))

        assert issue_of(run([transition(a=a, b=b, gap=2.0)]), IssueType.SCREEN_DIRECTION) is None


class TestActionAndState:
    def test_a_moving_subject_that_is_not_where_the_motion_would_carry_it_is_flagged(self) -> None:
        """5. An action that skips a step."""
        a = side("c1", edge_=edge(subject=subject(cx=0.2, vx=0.2, vy=0.0)))
        b = side("c2", outgoing=False, start=14.0, end=20.0, edge_=edge(subject=subject(cx=0.9)))

        found = issue_of(
            run([transition(a=a, b=b, gap=2.0)], profile="tutorial"), IssueType.ACTION_CONTINUITY
        )

        assert found is not None
        action = found.score(ScoreName.ACTION_CONTINUITY)
        assert action is not None and action.value is not None and action.value < 0.6
        keep = next(r for r in found.repairs if r.kind is RepairKind.ADJUST_CUT_POINT)
        assert keep.availability is Availability.CHECKED and keep.owner is Owner.EDITING

    def test_a_product_that_changes_state_is_flagged_for_a_review(self) -> None:
        """3. Unexpected change of the product."""
        a = side("c1", edge_=edge(objects=("box", "phone", "cable")))
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(objects=("phone", "charger", "case")),
        )

        found = issue_of(
            run([transition(a=a, b=b, gap=2.0)], profile="product_review"), IssueType.OBJECT_STATE
        )

        assert found is not None and found.detectability_confidence is not None
        assert found.detectability_confidence < 0.5  # object labels are weak evidence


class TestIdentity:
    def test_two_models_named_in_the_pictures_are_a_confirmed_mismatch(self) -> None:
        """4. Visually similar but distinct product models."""
        a = side(
            "c1", edge_=edge(names=(name("Pixel 9"),), embedding=(1.0, 0.0), embedding_model="m")
        )
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(names=(name("Pixel 8"),), embedding=(0.99, 0.1), embedding_model="m"),
        )

        found = issue_of(
            run([transition(a=a, b=b, gap=2.0)], profile="product_review"),
            IssueType.IDENTITY_MISMATCH,
        )

        assert found is not None and found.identity is IdentityVerdict.CONFIRMED_MISMATCH
        assert found.level is Level.HIGH
        assert any(e.kind == "ocr_text" for e in found.evidence)

    def test_visual_similarity_is_not_compatibility_of_identity(self) -> None:
        """15. Looks alike, contradicts the intended subject."""
        a = side(
            "c1", edge_=edge(names=(name("Pixel 9"),), embedding=(1.0, 0.0), embedding_model="m")
        )
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(names=(name("Pixel 8"),), embedding=(1.0, 0.0), embedding_model="m"),
        )

        analysis = run([transition(a=a, b=b, gap=2.0)])

        assert issue_of(analysis, IssueType.IDENTITY_MISMATCH) is not None
        assert analysis.compatible_pairs == ()

    def test_similar_looking_people_in_different_recordings_stay_uncertain(self) -> None:
        """7. Identity is not decided from looks."""
        a = side("c1", "cam1", edge_=edge(subject=subject(identity_cluster="A")))
        b = side(
            "c2",
            "cam2",
            outgoing=False,
            start=0.0,
            end=8.0,
            edge_=edge(subject=subject(identity_cluster="B")),
        )

        analysis = run([transition(a=a, b=b, kind=TransitionKind.CROSS_RECORDING)])

        assert issue_of(analysis, IssueType.IDENTITY_MISMATCH) is None
        assert any("same person" in u.question for u in analysis.unresolved)

    def test_a_different_looking_person_in_one_scene_is_a_probable_mismatch(self) -> None:
        a = side("c1", edge_=edge(subject=subject(identity_cluster="A")))
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(subject=subject(identity_cluster="B")),
        )

        found = issue_of(run([transition(a=a, b=b, gap=2.0)]), IssueType.IDENTITY_MISMATCH)

        assert found is not None and found.identity is IdentityVerdict.PROBABLE_MISMATCH
        assert found.repairs[0].kind is RepairKind.CONFIRM_IDENTITY


class TestLookAndLight:
    def shots(self, luma_a: float, luma_b: float, **extra: object):  # type: ignore[no-untyped-def]
        a = side("c1", edge_=edge(luma=luma_a))
        b = side("c2", outgoing=False, start=12.0, end=20.0, edge_=edge(luma=luma_b))
        return transition(a=a, b=b, gap=2.0, **extra)  # type: ignore[arg-type]

    def test_an_unexplained_exposure_step_between_neighbours_is_flagged_with_repairs(self) -> None:
        """9."""
        found = issue_of(run([self.shots(0.30, 0.68)]), IssueType.LIGHTING_MISMATCH)

        assert found is not None and found.level in (Level.MEDIUM, Level.HIGH)
        correction = next(r for r in found.repairs if r.kind is RepairKind.EXPOSURE_CORRECTION)
        assert correction.owner is Owner.VIDEO_IMPROVEMENT

    def test_the_same_step_across_a_story_section_is_less_alarming(self) -> None:
        """8. A deliberate change of scene."""
        plain = issue_of(
            run([self.shots(0.30, 0.68)], profile="documentary"), IssueType.LIGHTING_MISMATCH
        )
        scene = issue_of(
            run([self.shots(0.30, 0.68, section_change=True)], profile="documentary"),
            IssueType.LIGHTING_MISMATCH,
        )

        assert plain is not None and scene is not None
        assert (scene.intentional_style_likelihood or 0) > (plain.intentional_style_likelihood or 0)
        assert "section boundary" in scene.style_reason
        assert scene.level.value != Level.HIGH.value

    def test_two_pictures_that_look_unrelated_are_a_visual_discontinuity(self) -> None:
        a = side("c1", edge_=edge(embedding=(1.0, 0.0), embedding_model="m", environment="studio"))
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(
                embedding=(0.0, 1.0),
                embedding_model="m",
                environment="street",
                indoor_outdoor="outdoor",
            ),
        )

        found = issue_of(
            run([transition(a=a, b=b, gap=2.0)], profile="cinematic_dialogue"),
            IssueType.VISUAL_DISCONTINUITY,
        )

        assert found is not None and found.level in (Level.MEDIUM, Level.HIGH)

    def test_a_deliberate_montage_is_not_flagged_for_its_contrast(self) -> None:
        """12."""
        a = side(
            "c1",
            "cam1",
            edge_=edge(embedding=(1.0, 0.0), embedding_model="m", environment="studio", luma=0.2),
        )
        b = side(
            "c2",
            "cam2",
            outgoing=False,
            start=0.0,
            end=8.0,
            edge_=edge(embedding=(0.0, 1.0), embedding_model="m", environment="street", luma=0.8),
        )

        analysis = run(
            [transition(a=a, b=b, kind=TransitionKind.CROSS_RECORDING)], profile="fast_montage"
        )

        assert analysis.surfaced() == ()
        assert analysis.issues  # still reported, as information


class TestSound:
    def audio(self, **kw: object) -> AudioEdge:
        return AudioEdge(**kw)  # type: ignore[arg-type]

    def test_a_cut_through_a_word_is_clipped_speech_with_a_cleaner_cut_point(self) -> None:
        """10."""
        a = side(
            "c1",
            audio=self.audio(
                straddled_word="battery",
                word_edge_distance=0.03,
                speech_near=True,
                pause_before=secs(9.4),
            ),
        )
        b = side("c2", outgoing=False, start=12.0, end=20.0, audio=self.audio(speech_near=True))

        found = issue_of(
            run([transition(a=a, b=b, gap=2.0)], profile="interview_podcast"),
            IssueType.CLIPPED_SPEECH,
        )

        assert found is not None and found.level in (Level.MEDIUM, Level.HIGH)
        move = next(r for r in found.repairs if r.kind is RepairKind.ADJUST_CUT_POINT)
        assert move.availability is Availability.CHECKED and move.at_source_seconds is not None

    def test_a_step_in_level_and_floor_is_an_audio_discontinuity_a_bridge_softens_it(self) -> None:
        a = side("c1", audio=self.audio(level_db=-20.0, floor_db=-60.0, speech_near=True))
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            audio=self.audio(level_db=-32.0, floor_db=-45.0, speech_near=True),
        )

        plain = issue_of(run([transition(a=a, b=b, gap=2.0)]), IssueType.AUDIO_DISCONTINUITY)
        bridged = issue_of(
            run([transition(a=a, b=b, gap=2.0, bridged=True)]), IssueType.AUDIO_DISCONTINUITY
        )

        assert plain is not None and bridged is not None
        assert (plain.score(ScoreName.AUDIO_DISCONTINUITY).value or 0) > (  # type: ignore[union-attr]
            bridged.score(ScoreName.AUDIO_DISCONTINUITY).value or 0  # type: ignore[union-attr]
        )
        kinds = {r.kind for r in plain.repairs}
        assert {RepairKind.AUDIO_CROSSFADE, RepairKind.ROOM_TONE} <= kinds
        assert all(
            r.owner is not Owner.EDITING or r.kind is not RepairKind.AUDIO_CROSSFADE
            for r in plain.repairs
        )

    def test_uncertain_sync_is_named_not_assumed(self) -> None:
        """11."""
        a = side("c1", sync="ambiguous")
        b = side("c2", outgoing=False, start=12.0, end=20.0)

        found = issue_of(run([transition(a=a, b=b, gap=2.0)]), IssueType.SYNC_UNCERTAIN)

        assert found is not None and found.scores == ()
        assert found.repairs[0].kind is RepairKind.VERIFY_SYNC

    def test_no_alignment_is_a_missing_input_not_a_sync_problem(self) -> None:
        a = side("c1", sync="unknown")
        b = side("c2", outgoing=False, start=12.0, end=20.0, sync="unknown")

        analysis = run([transition(a=a, b=b, gap=2.0)])

        assert issue_of(analysis, IssueType.SYNC_UNCERTAIN) is None
        assert "alignment" in analysis.transitions[0].evidence_missing


class TestOrderAndStory:
    def back(self, **extra: object):  # type: ignore[no-untyped-def]
        a = side("c1", end=60.0)
        b = side("c2", outgoing=False, start=10.0, end=20.0)
        return transition(a=a, b=b, reordered=True, **extra)  # type: ignore[arg-type]

    def test_a_cut_that_runs_backwards_through_a_recording_is_flagged(self) -> None:
        found = issue_of(run([self.back()], profile="tutorial"), IssueType.TEMPORAL_INCONSISTENCY)

        assert found is not None and found.surfaced
        assert found.repairs[0].kind is RepairKind.REVIEW_ORDER

    def test_a_declared_flashback_or_cold_open_is_not_mistaken_for_an_error(self) -> None:
        """13."""
        plain = issue_of(run([self.back()]), IssueType.TEMPORAL_INCONSISTENCY)
        declared = issue_of(
            run([self.back(section_change=True, cold_open=True)]), IssueType.TEMPORAL_INCONSISTENCY
        )

        assert plain is not None and declared is not None
        assert (declared.intentional_style_likelihood or 0) > 0.7
        assert not declared.surfaced and plain.surfaced
        assert any(r.conflicts for r in declared.repairs if r.kind is RepairKind.REVIEW_ORDER)

    def test_a_hard_visual_break_at_a_section_boundary_with_no_bridge_confuses(self) -> None:
        a = side("c1", edge_=edge(embedding=(1.0, 0.0), embedding_model="m", environment="studio"))
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(
                embedding=(0.0, 1.0),
                embedding_model="m",
                environment="street",
                indoor_outdoor="outdoor",
            ),
        )

        found = issue_of(
            run([transition(a=a, b=b, gap=2.0, section_change=True)], profile="documentary"),
            IssueType.NARRATIVE_CONFUSION,
        )

        assert found is not None


class TestInsufficientEvidence:
    def test_a_cut_with_no_picture_evidence_is_unresolved_and_the_result_partial(self) -> None:
        """14."""
        a = side("c1")
        b = side("c2", outgoing=False, start=12.0, end=20.0)
        analysis = run([transition(a=replace(a, edge=None), b=replace(b, edge=None), gap=2.0)])

        assert analysis.issues == ()
        assert analysis.unresolved and "picture" in analysis.transitions[0].evidence_missing
        assert analysis.state.value == "partial"
        assert analysis.metrics.picture_coverage == 0.0


class TestAlternateTakes:
    def test_a_related_take_is_offered_as_a_direction_not_as_a_checked_swap(self) -> None:
        """6. Compatible alternate takes."""
        a = side("c1", edge_=edge(luma=0.3))
        b = side("c2", outgoing=False, start=12.0, end=20.0, edge_=edge(luma=0.7))

        found = issue_of(
            run([transition(a=a, b=b, gap=2.0, related=("cam2",))]), IssueType.LIGHTING_MISMATCH
        )

        assert found is not None
        take = next(r for r in found.repairs if r.kind is RepairKind.ALTERNATE_TAKE)
        assert take.availability is Availability.UNCHECKED and "not compared" in take.tradeoffs[0]
        assert found.related_assets == ("cam2",)

    def test_a_clean_cut_is_recorded_as_a_compatible_pair_with_what_was_compared(self) -> None:
        a = side("c1", edge_=edge(embedding=(1.0, 0.0), embedding_model="m"))
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(embedding=(1.0, 0.05), embedding_model="m"),
        )

        analysis = run([transition(a=a, b=b, gap=2.0)])

        (pair,) = analysis.compatible_pairs
        assert pair.compatibility > 0.7 and "looks" in pair.checked

    def test_the_unavailable_modules_are_listed_as_such(self) -> None:
        found = issue_of(run([jump()], profile="cinematic_dialogue"), IssueType.JUMP_CUT)

        assert found is not None
        reframe = next(r for r in found.repairs if r.kind is RepairKind.REFRAME)
        assert reframe.availability is Availability.UNAVAILABLE and reframe.rank == len(
            found.repairs
        )

    def test_broll_cover_depends_on_what_the_broll_analysis_found(self) -> None:
        with_cover = issue_of(
            run([jump(cover_available=True)], profile="cinematic_dialogue"), IssueType.JUMP_CUT
        )
        none_found = issue_of(
            run([jump(cover_available=False)], profile="cinematic_dialogue"), IssueType.JUMP_CUT
        )
        unknown = issue_of(run([jump()], profile="cinematic_dialogue"), IssueType.JUMP_CUT)

        def cover(issue: Issue | None) -> Availability:
            assert issue is not None
            return next(r for r in issue.repairs if r.kind is RepairKind.BROLL_COVER).availability

        assert cover(with_cover) is Availability.CHECKED
        assert cover(none_found) is Availability.UNAVAILABLE
        assert cover(unknown) is Availability.UNCHECKED


@pytest.mark.parametrize("strictness", [0.5, 1.0, 1.5])
def test_strictness_moves_the_thresholds_never_the_evidence(strictness: float) -> None:
    def severity(level: float) -> float | None:
        found = issue_of(run([jump(0.1)], strictness=level), IssueType.JUMP_CUT)
        assert found is not None
        score = found.score(ScoreName.JUMP_CUT_SEVERITY)
        return None if score is None else score.value

    assert severity(strictness) == severity(1.0)
