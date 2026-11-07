"""From findings to proposals: the change, its owner, who may approve it, what it touches, what is
left out and what is a conflict."""

from dataclasses import replace

from media_house.modules.revision_engine.domain.facts import IssueFacts
from media_house.modules.revision_engine.domain.planner import plan
from media_house.modules.revision_engine.domain.values import (
    ChangeKind,
    ChangeStatus,
    Level,
    Origin,
)
from tests.unit.revision_engine.fixtures import PA, PB, clip, issue, planning, review, timeline


def gap() -> IssueFacts:
    return issue(
        "structure.gap", severity="high", clips=(PA, PB), sections=("s1",),
        measurements=(("gap_frames", "10"), ("previous_clip", PA), ("next_clip", PB)),
    )  # fmt: skip


def test_a_gap_becomes_a_concrete_trim_a_person_approves() -> None:
    result = plan(planning(gap()))

    change = result.changes[0]
    assert change.kind is ChangeKind.TRIM_CLIP and change.owner_module == "timeline_builder"
    assert change.param("clip") == PA and change.param("end_frame") == "110"
    assert change.level is Level.SUGGESTED and change.status is ChangeStatus.PROPOSED
    assert "Hook" in change.description and "10 frames" in change.description
    assert change.expected_improvement and change.alternatives
    assert change.issue_ids == ("q_structure.gap",)


def test_a_missing_file_is_a_rebuild_and_automatic_only_when_the_project_said_so() -> None:
    found = issue("structure.media_missing", severity="critical", clips=(PA,))

    default = plan(planning(found)).changes[0]
    granted = plan(
        planning(
            found,
            review=review(authority=(("asset_reconnection", "automatic"),)),
        )
    ).changes[0]

    assert default.kind is ChangeKind.REBUILD_TIMELINE and default.level is Level.SUGGESTED
    assert granted.level is Level.AUTOMATIC


def test_a_change_never_becomes_automatic_because_the_finding_is_sure() -> None:
    measured = (("gap_frames", "5"), ("previous_clip", PA), ("next_clip", PB))
    sure = issue("structure.gap", severity="critical", confidence=1.0, measurements=measured)

    assert plan(planning(sure)).changes[0].level is not Level.AUTOMATIC


def test_a_caption_problem_goes_to_the_caption_engine_and_touches_only_captions() -> None:
    change = plan(planning(issue("caption.overlap"))).changes[0]

    assert change.kind is ChangeKind.HANDOFF and change.owner_module == "caption_engine"
    assert "plan:captions" in change.invalidates
    assert not any(d.startswith(("plan:vfx", "audio:", "render:vfx")) for d in change.invalidates)


def test_a_dialogue_level_problem_changes_the_dialogue_stem_and_not_the_music() -> None:
    found = issue(
        "audio.dialogue_inconsistent",
        measurements=(
            ("quietest_clip", "a:aW"), ("quietest_lufs", "-30.00"), ("loudest_clip", "a:aV"),
            ("loudest_lufs", "-15.00"), ("median_lufs", "-16.00"),
        ),
    )  # fmt: skip

    changes = plan(planning(found)).changes

    quiet = next(c for c in changes if c.param("clip") == "a:aW")
    assert quiet.kind is ChangeKind.MIX_CLIP_GAIN and quiet.param("delta_db") == "6.0"  # clamped
    assert (
        "audio:stem:dialogue" in quiet.invalidates and "audio:stem:music" not in quiet.invalidates
    )
    assert all(c.param("clip") != "a:aV" for c in changes)  # 1 dB is not worth a change


def test_something_a_person_rejected_is_not_proposed_again_unless_asked() -> None:
    first = plan(planning(issue("caption.overlap"))).changes[0]
    again = planning(
        issue("caption.overlap"), review=review(rejected_keys=frozenset({first.proposal_key}))
    )

    assert plan(again).changes == ()
    assert "rejected earlier" in plan(again).skipped[0].reason
    assert len(plan(replace(again, reconsider=True)).changes) == 1


def test_a_finding_that_got_worse_is_not_covered_by_the_old_rejection() -> None:
    mild = plan(planning(issue("caption.overlap", severity="medium"))).changes[0]
    worse = planning(
        issue("caption.overlap", severity="high"),
        review=review(rejected_keys=frozenset({mild.proposal_key})),
    )

    assert len(plan(worse).changes) == 1


def test_a_locked_section_makes_the_change_a_conflict_a_person_must_decide() -> None:
    result = plan(planning(gap(), review=review(locked_sections=("s1",))))

    change = result.changes[0]
    assert change.status is ChangeStatus.CONFLICT and change.level is Level.MANUAL_ONLY
    assert "locked" in change.conflicts[0]


def test_a_persons_own_edit_is_never_overwritten_silently() -> None:
    edited = timeline(
        clips=(clip(PA, 0, 100, origin=Origin.HUMAN_MANUAL), clip(PB, 100, 200, section="s2"))
    )

    change = plan(planning(gap(), timeline=edited)).changes[0]

    assert change.status is ChangeStatus.CONFLICT
    assert "changed by a person" in change.conflicts[0]


def test_a_clip_the_person_asked_to_keep_is_not_touched() -> None:
    change = plan(planning(gap(), protected_clips=frozenset({PA}))).changes[0]

    assert change.status is ChangeStatus.CONFLICT and "keep exactly as it is" in change.conflicts[0]


def test_replacing_an_approved_section_needs_a_persons_instruction() -> None:
    change = plan(planning(gap(), review=review(approved_sections=("s1",)))).changes[0]

    assert change.level is Level.MANUAL_ONLY


def test_an_optional_suggestion_is_left_out_with_the_reason_unless_asked_for() -> None:
    found = issue("editing.static_stretch", severity="optional")

    assert plan(planning(found)).changes == ()
    assert plan(planning(found)).skipped[0].reason.startswith("an optional suggestion")
    assert len(plan(planning(found, include_optional=True)).changes) == 1


def test_the_same_change_asked_for_by_two_findings_is_one_change() -> None:
    one = issue("video.black_frames", severity="critical", start_frame=10)
    two = issue("video.frozen_frames", severity="high", start_frame=90, sections=("s2",))

    changes = plan(planning(one, two)).changes

    assert [c.kind for c in changes] == [ChangeKind.REBUILD_TIMELINE, ChangeKind.REBUILD_TIMELINE]
    same = plan(
        planning(
            issue("structure.media_missing", clips=(PA,)),
            issue("structure.media_missing", issue_id="q2", fingerprint="fp2", clips=(PA,)),
        )
    ).changes
    assert len(same) == 1
    assert set(same[0].issue_ids) == {"q_structure.media_missing", "q2"}


def test_changes_run_in_a_sensible_order() -> None:
    found = (
        issue("resolve.stale_version"),
        issue("caption.overlap"),
        issue("audio.loudness_off"),
        issue("structure.media_missing", clips=(PA,)),
    )

    kinds = [c.kind for c in plan(planning(*found)).changes]

    assert kinds == [
        ChangeKind.REBUILD_TIMELINE,
        ChangeKind.REMIX,
        ChangeKind.HANDOFF,
        ChangeKind.RESYNC_RESOLVE,
    ]


def test_a_finding_the_planner_does_not_know_is_still_handed_to_an_owner() -> None:
    change = plan(planning(issue("brand.new_check", owner_hint="x"))).changes[0]

    assert change.kind is ChangeKind.HANDOFF and change.status is ChangeStatus.PROPOSED


def test_the_same_findings_give_the_same_plan() -> None:
    a = plan(planning(gap()))
    b = plan(planning(gap()))

    assert a == b
