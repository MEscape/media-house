"""The timeline, the story's order, the opening, target length and several recordings."""

from fractions import Fraction
from itertools import pairwise

import pytest

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.facts import (
    ConstraintFact,
    OpeningFact,
    OrderChangeFact,
    RefFact,
    RelationFact,
    StoryFacts,
)
from media_house.modules.editing_intelligence.domain.profile import EditConfig, TargetDuration
from media_house.modules.editing_intelligence.domain.timing import to_fraction
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    ConstraintState,
    DurationMode,
    Executability,
    IssueKind,
    PlacementRole,
    PlanState,
    Precision,
    Severity,
    SignalName,
    StructuralKind,
    TrackKind,
)
from tests.support.edit_fakes import Recording, S, Sec, decision, edit, story, talk, with_video

TALK = [
    S("the screen is bright and sharp"),
    S("it also gets very hot", gap=2.0),
    S("so yeah okay", gap=0.4),
    S("the speaker is loud and clear", gap=0.4),
]
SECTIONS = [
    Sec("screen", (0,), "essential"),
    Sec("heat", (1,), "essential"),
    Sec("filler", (2,), "optional"),
    Sec("sound", (3,), "essential"),
]


# --- the timeline is exact and tiles ----------------------------------------------------------
def test_clips_tile_the_timeline_exactly_and_sum_to_the_estimated_length() -> None:
    rec = talk(TALK)
    plan = edit(story(rec, SECTIONS, fmt="talking_head"), rec, EditConfig(profile="talking_head"))

    for track in (TrackKind.AUDIO, TrackKind.VIDEO):
        clips = plan.track(track)
        assert clips[0].timeline_start == Rational(0, 1)
        for a, b in pairwise(clips):
            assert to_fraction(a.timeline_end) == to_fraction(b.timeline_start)
            assert a.start_frame + a.duration_frames == b.start_frame
        assert to_fraction(clips[-1].timeline_end) == to_fraction(plan.duration.estimated)
    assert (
        plan.duration.validated is False
        and "no timeline has been compiled" in plan.duration.assumptions
    )


def test_picture_and_sound_of_a_clip_are_one_source_range_at_one_place() -> None:
    rec = talk(TALK)
    plan = edit(story(rec, SECTIONS, fmt="talking_head"), rec, EditConfig(profile="talking_head"))

    for picture in plan.track(TrackKind.VIDEO):
        sound = plan.placement(picture.linked or "")
        assert sound is not None and sound.linked == picture.placement_id
        assert (sound.source, sound.timeline_start, sound.timeline_duration) == (
            picture.source,
            picture.timeline_start,
            picture.timeline_duration,
        )


def test_source_time_and_timeline_time_are_kept_apart() -> None:
    rec = talk(TALK)
    plan = edit(story(rec, SECTIONS, fmt="talking_head"), rec, EditConfig(profile="talking_head"))

    later = plan.track(TrackKind.AUDIO)[-1]
    assert later.source.start.value > later.timeline_start.value  # material was removed before it
    assert all(c.source.asset_id == "a1" for c in plan.placements)


def test_frames_are_offered_only_for_a_known_constant_rate() -> None:
    constant = talk(TALK, vfr=False)
    variable = talk(TALK, vfr=True)
    config = EditConfig(profile="talking_head")

    plan_c = edit(story(constant, SECTIONS), constant, config)
    plan_v = edit(story(variable, SECTIONS), variable, config)

    assert all(p.source.start_frame is not None for p in plan_c.placements)
    assert all(
        p.source.start_frame is None and p.source.frame_rate is None for p in plan_v.placements
    )
    assert any(
        i.kind is IssueKind.TIME_BASIS_LIMITED and "variable or unknown" in i.detail
        for i in plan_v.issues
    )
    assert not plan_c.timebase.assumed and plan_v.timebase.assumed
    assert "30 fps is assumed" in plan_v.timebase.note


def test_precision_says_where_a_time_comes_from_and_claims_frame_accuracy_only_on_a_shot() -> None:
    rec = talk(TALK)
    plan = edit(story(rec, SECTIONS), rec, EditConfig(profile="talking_head"))

    precisions = {p.source.start_precision for p in plan.placements} | {
        p.source.end_precision for p in plan.placements
    }
    assert Precision.FRAME_EXACT not in precisions
    assert Precision.WORD_ALIGNED in precisions
    assert "frame_exact" not in plan.duration.assumptions


def test_the_plan_is_deterministic() -> None:
    rec = talk(TALK)
    config = EditConfig(profile="talking_head")

    assert edit(story(rec, SECTIONS), rec, config) == edit(story(rec, SECTIONS), rec, config)


# --- the story's order ------------------------------------------------------------------------
def test_sections_play_in_the_story_order_and_a_move_is_recorded_with_its_reason() -> None:
    rec = talk([S("this is the result first"), S("and this explains the setup", gap=0.4)])
    change = OrderChangeFact(
        section_id="sec1",
        from_index=1,
        to_index=0,
        reason="the result needs its setup",
        constraint_ids=("c1",),
    )
    st = story(
        rec,
        [Sec("setup", (0,), "essential"), Sec("result", (1,), "essential")],
        order=(1, 0),
        order_changes=(change,),
        constraints=(
            ConstraintFact(
                constraint_id="c1",
                kind="must_precede",
                first="sec1",
                second="sec0",
                strength="hard",
                reason="x",
            ),
        ),
    )

    plan = edit(st, rec)

    first = plan.track(TrackKind.AUDIO)[0]
    assert first.section_id == "sec1"
    (moved,) = [d for d in plan.decisions if d.action is Action.REORDER]
    assert moved.value(SignalName.REORDER) == 1.0 and "needs its setup" in moved.signals[0].reason
    assert not plan.issues_of(Severity.ERROR)


def test_a_story_that_contradicts_its_own_hard_constraint_is_an_error_in_the_plan() -> None:
    rec = talk([S("this is the result first"), S("and this explains the setup", gap=0.4)])
    st = story(
        rec,
        [Sec("setup", (0,), "essential"), Sec("result", (1,), "essential")],
        constraints=(
            ConstraintFact(
                constraint_id="c1",
                kind="must_precede",
                first="sec1",
                second="sec0",
                strength="hard",
                reason="x",
            ),
        ),
    )

    plan = edit(st, rec)

    errors = plan.issues_of(Severity.ERROR)
    assert errors and errors[0].kind is IssueKind.ORDER_VIOLATED
    order = plan.check("section_order")
    assert order is not None and order.state.value == "failed"


# --- the recommended opening ------------------------------------------------------------------
def opening(
    rec: Recording, sentence: int, strategy: str = "premise_first", risks: tuple[str, ...] = ()
) -> OpeningFact:
    start, end = rec.spans[sentence]
    return OpeningFact(
        candidate_id="open1",
        strategy=strategy,
        source=RefFact(
            asset_id="a1",
            segment_ids=(f"seg{sentence}",),
            unit_ids=(f"u{sentence:03d}",),
            start=start,
            end=end,
            exact_time=True,
        ),
        excerpt="the verdict",
        section_id="sec1",
        risks=risks,
        needs_context=False,
    )


def test_the_story_s_recommended_opening_plays_first_and_points_at_its_origin() -> None:
    rec = talk([S("so here we go with the screen"), S("overall it is an excellent phone", gap=0.4)])
    st = story(
        rec,
        [Sec("body", (0,), "essential"), Sec("end", (1,), "essential")],
        openings=(opening(rec, 1),),
        recommended="open1",
    )

    plan = edit(st, rec)

    first = plan.track(TrackKind.AUDIO)[0]
    assert first.role is PlacementRole.COLD_OPEN
    origin = plan.placement(first.reuse_of or "")
    assert (
        origin is not None
        and origin.role is PlacementRole.MAIN
        and origin.source.start.value <= first.source.start.value
    )
    (proposal,) = [p for p in plan.structural_proposals if p.kind is StructuralKind.COLD_OPEN]
    assert proposal.applied
    assert decision(plan, "open:open1").executability is Executability.EXECUTABLE
    assert not plan.issues_of(Severity.ERROR)


def test_a_risky_opening_is_a_suggestion_and_can_be_switched_off() -> None:
    rec = talk([S("so here we go with the screen"), S("overall it is an excellent phone", gap=0.4)])
    st = story(
        rec,
        [Sec("body", (0,), "essential"), Sec("end", (1,), "essential")],
        openings=(opening(rec, 1, risks=("the claim is qualified later",)),),
        recommended="open1",
    )

    risky = edit(st, rec)
    off = edit(st, rec, EditConfig(use_recommended_opening=False))

    assert decision(risky, "open:open1").executability is Executability.NEEDS_REVIEW
    assert all(p.role is PlacementRole.MAIN for p in off.placements)


def test_the_natural_start_adds_no_cold_open() -> None:
    rec = talk([S("so here we go with the screen"), S("overall it is an excellent phone", gap=0.4)])
    st = story(
        rec,
        [Sec("body", (0,), "essential")],
        openings=(opening(rec, 0, "natural_start"),),
        recommended="open1",
    )

    assert all(p.role is PlacementRole.MAIN for p in edit(st, rec).placements)


# --- target length ----------------------------------------------------------------------------
LONG = [
    S("the screen is bright and sharp and very colourful"),
    S("it also gets quite hot when charging", gap=0.4),
]


def long_story() -> tuple[Recording, StoryFacts]:
    sentences = [
        S("the screen is bright and sharp and very colourful"),
        S("the fingerprint sensor is quick to unlock", gap=0.4, removal=0.2),
        S("the case is available in four different colours", gap=0.4, removal=0.2),
        S("the battery lasts two full days of use", gap=0.4),
    ]
    rec = talk(sentences)
    secs = [
        Sec("screen", (0,), "essential"),
        Sec("sensor", (1,), "optional"),
        Sec("colours", (2,), "optional"),
        Sec("battery", (3,), "essential"),
    ]
    return rec, story(rec, secs)


def test_a_soft_target_removes_optional_passages_as_suggestions_and_keeps_essential_ones() -> None:
    rec, st = long_story()
    full = edit(st, rec).duration.estimated.value

    plan = edit(st, rec, EditConfig(target=TargetDuration(high=full - 3.0, mode=DurationMode.SOFT)))

    cuts = plan.decisions_for(Action.CUT)
    assert cuts and all(c.executability is Executability.NEEDS_REVIEW for c in cuts)
    assert all("target length" in c.rationale[-1] for c in cuts)
    assert decision(plan, "dec:0:u000").action is Action.KEEP
    assert decision(plan, "dec:0:u003").action is Action.KEEP
    assert plan.duration.estimated.value <= full - 3.0 + 0.05
    assert plan.duration.met is True


def test_a_target_that_essential_material_cannot_meet_is_reported_not_forced() -> None:
    rec, st = long_story()

    plan = edit(st, rec, EditConfig(target=TargetDuration(high=2.0, mode=DurationMode.HARD)))

    assert decision(plan, "dec:0:u000").action is Action.KEEP
    assert decision(plan, "dec:0:u003").action is Action.KEEP
    assert plan.state is PlanState.UNMET_CONSTRAINTS
    hard = [c for c in plan.constraints if c.name == "target duration"]
    assert hard and hard[0].state is ConstraintState.VIOLATED
    miss = [i for i in plan.issues if i.kind is IssueKind.TARGET_NOT_MET]
    assert miss and "Essential material alone is" in miss[0].detail
    assert plan.duration.met is False
    assert not plan.issues_of(Severity.ERROR)  # best effort, not invalid


def test_the_same_miss_is_only_a_preference_when_the_target_is_soft() -> None:
    rec, st = long_story()

    plan = edit(st, rec, EditConfig(target=TargetDuration(high=2.0, mode=DurationMode.SOFT)))

    assert plan.state is PlanState.WARNINGS
    assert next(c for c in plan.constraints if c.name == "target duration").kind.value == "soft"


def test_the_story_s_own_target_is_a_soft_preference_unless_the_caller_says_otherwise() -> None:
    rec, st = long_story()
    st = story(
        rec,
        [
            Sec("screen", (0,), "essential"),
            Sec("sensor", (1,), "optional"),
            Sec("colours", (2,), "optional"),
            Sec("battery", (3,), "essential"),
        ],
        target=(None, 5.0),
    )

    plan = edit(st, rec)

    assert plan.duration.target is not None and plan.duration.target.origin == "story"
    assert plan.duration.target.mode is DurationMode.SOFT


def test_a_supporting_section_is_proposed_for_omission_and_applied_only_when_allowed() -> None:
    sentences = [
        S("the screen is bright and sharp and very colourful"),
        S("the fingerprint sensor is quick to unlock", gap=0.4),
        S("the battery lasts two full days of use", gap=0.4),
    ]
    rec = talk(sentences)
    st = story(
        rec,
        [
            Sec("screen", (0,), "essential"),
            Sec("sensor", (1,), "supporting"),
            Sec("battery", (2,), "essential"),
        ],
    )
    target = TargetDuration(high=7.0, mode=DurationMode.SOFT)

    proposed = edit(st, rec, EditConfig(target=target))
    applied = edit(st, rec, EditConfig(target=target, allow_structural=True))

    (p,) = [x for x in proposed.structural_proposals if x.kind is StructuralKind.OMIT_SECTION]
    assert not p.applied and "not allowed" in p.status and p.section_ids == ("sec1",)
    assert decision(proposed, "dec:0:u001").action is Action.KEEP
    (q,) = [x for x in applied.structural_proposals if x.kind is StructuralKind.OMIT_SECTION]
    assert q.applied
    assert decision(applied, "dec:0:u001").action is Action.CUT
    assert applied.duration.estimated.value < proposed.duration.estimated.value


# --- several recordings -----------------------------------------------------------------------
def test_a_story_of_several_recordings_keeps_each_recordings_analysis_apart() -> None:
    one = talk([S("the first clip talks about the screen")], asset_id="a1")
    two = talk([S("the second clip talks about the battery")], asset_id="a2")
    st = story(
        {"a1": one, "a2": two},
        [Sec("one", (0,), "essential", asset="a1"), Sec("two", (0,), "essential", asset="a2")],
    )

    plan = edit(st, {"a1": one, "a2": two})

    assert {s.asset_id for s in plan.sources} == {"a1", "a2"}
    assets = [p.source.asset_id for p in plan.track(TrackKind.AUDIO)]
    assert assets == ["a1", "a2"]
    assert (
        decision(plan, "dec:0:u000").asset_id == "a1"
        and decision(plan, "dec:1:u000").asset_id == "a2"
    )
    first, second = plan.track(TrackKind.AUDIO)
    assert to_fraction(first.timeline_end) == to_fraction(second.timeline_start)
    assert isinstance(to_fraction(first.timeline_end), Fraction)


def test_video_evidence_makes_a_shot_boundary_a_frame_exact_cut() -> None:
    rec = with_video(
        talk([S("first sentence here", gap=0.3), S("second sentence here", gap=2.0)]),
        [(0.0, 2.15), (2.15, 8.0)],
    )
    plan = edit(story(rec, [Sec("a", (0,), "essential"), Sec("b", (1,), "essential")]), rec)

    first = plan.track(TrackKind.AUDIO)[0]
    assert first.source.end_precision is Precision.FRAME_EXACT
    assert first.source.end.value == pytest.approx(2.15)
    assert first.source.start_precision is not Precision.FRAME_EXACT


# --- realistic situations -----------------------------------------------------------------------
def test_a_tutorial_keeps_a_demonstration_after_its_explanation_though_it_looks_removable() -> None:
    rec = talk(
        [
            S("first we explain how the tool is configured"),
            S("now watch the screen as I run the command", gap=0.5, removal=0.95, highlight=0.1),
            S("and that is how you check it worked", gap=0.5),
        ]
    )
    st = story(
        rec,
        [
            Sec("explain", (0,), "essential", "explanation"),
            Sec("demonstrate", (1,), "optional", "demonstration"),
            Sec("verify", (2,), "essential", "evidence"),
        ],
        fmt="tutorial",
        constraints=(
            ConstraintFact(
                constraint_id="c1",
                kind="must_precede",
                first="sec0",
                second="sec1",
                strength="hard",
                reason="the command is explained before it is run",
            ),
        ),
    )

    plan = edit(st, rec, EditConfig(profile="tutorial"))

    demonstration = decision(plan, "dec:0:u001")
    assert demonstration.action is Action.KEEP
    assert any("tutorial keeps demonstration" in c for c in demonstration.constraints)
    assert [c.section_id for c in plan.track(TrackKind.AUDIO)] == ["sec0", "sec1", "sec2"]
    assert not plan.issues_of(Severity.ERROR)


def test_two_confirmed_takes_of_one_beat_are_both_kept_and_flagged_for_a_choice() -> None:
    take_one = talk([S("this is the battery take one")], asset_id="a1")
    take_two = talk([S("this is the battery take two")], asset_id="a2")
    relation = RelationFact(
        certainty="confirmed",
        treatment="alternate_takes",
        affected_sections=("sec0", "sec1"),
    )
    st = story(
        {"a1": take_one, "a2": take_two},
        [Sec("one", (0,), "essential", asset="a1"), Sec("two", (0,), "supporting", asset="a2")],
        relations=(relation,),
    )

    plan = edit(st, {"a1": take_one, "a2": take_two})

    assert {c.source.asset_id for c in plan.track(TrackKind.AUDIO)} == {
        "a1",
        "a2",
    }  # nothing dropped
    note = next(i for i in plan.issues if i.kind is IssueKind.RELATED_RECORDINGS)
    assert note.severity is Severity.RECOMMENDATION and "alternate takes" in note.detail
