"""Plans: identity, level, validation of what a revision did, storage form, and the restoration
of a checkpoint's clips."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from media_house.modules.revision_engine.application.executors import (
    TimelineEdits,
    overrides_of,
    parse_overrides,
)
from media_house.modules.revision_engine.application.rollback import restore_edits
from media_house.modules.revision_engine.application.views import EDITOR_PREFIX, timeline_view
from media_house.modules.revision_engine.domain.errors import InvalidRevisionDocument
from media_house.modules.revision_engine.domain.facts import IssueFacts
from media_house.modules.revision_engine.domain.invalidation import invalidations
from media_house.modules.revision_engine.domain.model import RevisionPlan
from media_house.modules.revision_engine.domain.plan import assemble_plan, validate_revision
from media_house.modules.revision_engine.domain.planner import plan as plan_changes
from media_house.modules.revision_engine.domain.serialization import plan_from_json, plan_to_json
from media_house.modules.revision_engine.domain.values import (
    ChangeKind,
    ChangeStatus,
    Level,
    Origin,
)
from media_house.modules.timeline_builder.application.contracts import (
    EditKind,
    EditRecord,
    Provenance,
)
from tests.unit.revision_engine.fixtures import PA, PB, issue, planning, review
from tests.unit.timeline_builder import fixtures as bf


def made(*issues: IssueFacts, **kw: Any) -> RevisionPlan:
    data = planning(*issues, **kw)
    planned = plan_changes(data)
    return assemble_plan(
        timeline=data.timeline, review=data.review, report_id="qr1", anchor_asset_id="anchor",
        changes=planned.changes, instructions=[], skipped=planned.skipped, created_at="t0",
        previous=None, protected_clips=(), warnings=(), provenance=(),
    )  # fmt: skip


def gap_issue() -> IssueFacts:
    return issue(
        "structure.gap", severity="high", clips=(PA, PB),
        measurements=(("gap_frames", "10"), ("previous_clip", PA), ("next_clip", PB)),
    )  # fmt: skip


def test_a_plan_has_one_identity_for_the_same_inputs_and_names_the_strictest_level() -> None:
    a = made(gap_issue(), issue("caption.overlap"))
    b = made(gap_issue(), issue("caption.overlap"))

    assert a.revision_plan_id == b.revision_plan_id and a.content_hash == b.content_hash
    assert a.approval_requirement is Level.SUGGESTED and a.approval_status == "pending"
    assert a.responsible_modules == ("timeline_builder", "caption_engine")
    assert a.source_timeline_version == 1 and a.target_timeline_version is None


def test_a_plan_with_a_conflict_cannot_be_approved_as_it_stands() -> None:
    plan = made(gap_issue(), review=review(locked_sections=("s1",)))

    assert (
        plan.approval_status == "blocked_by_conflicts"
        and plan.approval_requirement is Level.MANUAL_ONLY
    )


def test_a_plan_of_nothing_says_there_is_nothing_to_approve() -> None:
    assert made().approval_status == "nothing_to_approve"


def test_a_change_is_resolved_only_when_the_finding_it_answered_is_gone() -> None:
    plan = made(gap_issue())
    executed = replace(
        plan,
        proposed_changes=tuple(
            replace(c, status=ChangeStatus.EXECUTED) for c in plan.proposed_changes
        ),
    )
    before = [gap_issue()]
    fixed = validate_revision(executed, before, [], before_report_id="r1", after_report_id="r2")
    stuck = validate_revision(
        executed, before, [gap_issue()], before_report_id="r1", after_report_id="r2"
    )

    assert fixed.checks[0].result == "resolved" and fixed.resolved == 1 and fixed.regressions == ()
    assert stuck.checks[0].result == "still_there" and stuck.persisting == 1


def test_a_new_or_worse_finding_is_a_regression_and_a_handoff_is_not_checked() -> None:
    plan = made(issue("caption.overlap"))
    handed = replace(
        plan,
        proposed_changes=tuple(
            replace(c, status=ChangeStatus.HANDED_OFF) for c in plan.proposed_changes
        ),
    )
    new = issue("structure.overlap", severity="high")

    result = validate_revision(
        handed,
        [issue("caption.overlap")],
        [issue("caption.overlap"), new],
        before_report_id="r1",
        after_report_id="r2",
    )

    assert result.regressions == (new.issue_id,) and result.introduced == 1
    assert (
        result.checks[0].result == "not_checked"
        and "handed to caption_engine" in result.checks[0].detail
    )


def test_a_plan_round_trips_and_a_foreign_document_is_refused() -> None:
    plan = made(gap_issue())

    assert plan_from_json(plan_to_json(plan)) == plan
    with pytest.raises(InvalidRevisionDocument):
        plan_from_json('{"document_type": "other"}')


def test_a_change_makes_stale_what_depends_on_it_and_nothing_else() -> None:
    caption = invalidations(
        ChangeKind.HANDOFF, owner="caption_engine", clip_ids=(), section_ids=("s1",)
    )
    music = invalidations(
        ChangeKind.MIX_BUS_GAIN, owner="audio_mix_engine", clip_ids=(), section_ids=(), bus="music"
    )
    trim = invalidations(
        ChangeKind.TRIM_CLIP, owner="timeline_builder", clip_ids=(PA,), section_ids=("s1",)
    )

    assert "plan:captions" in caption and not any("vfx" in d or "audio" in d for d in caption)
    assert (
        "audio:stem:music" in music
        and "audio:stem:dialogue" not in music
        and "timeline:section:s1" not in music
    )
    assert (
        "timeline:element:v:pA" in trim
        and "timeline:section:s1" in trim
        and not any(d.startswith("audio") for d in trim)
    )


# --- overrides, executors' translation, restoring a checkpoint -------------------------------


def test_the_mix_engines_override_labels_are_read_back_and_added_to() -> None:
    bus, clip = parse_overrides(["bus:music=-3", "clip:a:aW=2.5", "bus:dialogue=1", "nonsense"])

    assert bus == {"music": -3.0, "dialogue": 1.0} and clip == {"a:aW": 2.5}
    assert overrides_of(bus, clip).labels() == ("bus:dialogue=1", "bus:music=-3", "clip:a:aW=2.5")


def test_a_trim_becomes_a_recorded_edit_marked_as_made_on_a_persons_approval() -> None:
    plan = made(gap_issue())
    change = plan.proposed_changes[0]

    edit = TimelineEdits._edit(change, EDITOR_PREFIX + plan.revision_id)

    assert edit is not None and edit.kind is EditKind.MODIFY and edit.element_id == PA
    assert edit.changes == (("end_frame", "110"),) and edit.editor.startswith(EDITOR_PREFIX)


def test_restoring_a_checkpoint_edits_only_what_differs() -> None:
    target = bf.built()
    clip = next(c for c in target.video_clips() if c.kind.value == "primary")
    changed_tracks = tuple(
        replace(
            t,
            video=tuple(
                replace(c, end_frame=c.end_frame - 5, opacity=0.5)
                if c.clip_id == clip.clip_id
                else c
                for c in t.video
            ),
        )
        for t in target.tracks
    )
    current = replace(target, tracks=changed_tracks)

    edits = restore_edits(current, target, editor="revision_engine:x", note="restore")

    assert (
        len(edits) == 1 and edits[0].kind is EditKind.MODIFY and edits[0].element_id == clip.clip_id
    )
    assert dict(edits[0].changes) == {
        "end_frame": str(clip.end_frame),
        "opacity": str(clip.opacity),
    }
    assert restore_edits(target, target, editor="e", note="n") == ()


def test_a_clip_made_after_the_checkpoint_is_removed_and_one_removed_is_made_again() -> None:
    target = bf.built()
    extra = next(c for c in target.video_clips() if c.kind.value == "graphic")
    without = replace(
        target,
        tracks=tuple(
            replace(t, video=tuple(c for c in t.video if c.clip_id != extra.clip_id))
            for t in target.tracks
        ),
    )

    back = restore_edits(without, target, editor="e", note="n")
    away = restore_edits(target, without, editor="e", note="n")

    assert [(e.kind, e.element_id) for e in back] == [(EditKind.CREATE, extra.clip_id)]
    assert [(e.kind, e.element_id) for e in away] == [(EditKind.DELETE, extra.clip_id)]


def test_what_a_person_edited_is_told_from_what_a_revision_edited() -> None:
    built = bf.built()
    clip = next(c for c in built.video_clips() if c.kind.value == "primary")
    human = replace(clip, provenance=Provenance.HUMAN_MODIFIED)
    swapped = replace(
        built,
        tracks=tuple(
            replace(t, video=tuple(human if c.clip_id == clip.clip_id else c for c in t.video))
            for t in built.tracks
        ),
    )

    manual = timeline_view(swapped)
    by_revision = timeline_view(
        replace(swapped, edit_log=(_record(clip.clip_id, EDITOR_PREFIX + "rev_1"),))
    )
    by_person = timeline_view(replace(swapped, edit_log=(_record(clip.clip_id, "Ada"),)))

    assert manual.clip(clip.clip_id).origin is Origin.HUMAN_MANUAL  # type: ignore[union-attr]
    assert by_revision.clip(clip.clip_id).origin is Origin.AUTOMATED_REVISION  # type: ignore[union-attr]
    assert by_person.clip(clip.clip_id).origin is Origin.HUMAN_MANUAL  # type: ignore[union-attr]
    assert timeline_view(built).clip(clip.clip_id).origin is Origin.MEDIA_HOUSE  # type: ignore[union-attr]


def _record(element_id: str, editor: str) -> EditRecord:
    return EditRecord(
        sequence=1, kind=EditKind.MODIFY, element_id=element_id, fields=("opacity",),
        editor=editor, note="", at=datetime(2026, 1, 1, tzinfo=UTC),
    )  # fmt: skip
