"""Iterative editing: only what changed is rebuilt, and a person's work is never overwritten."""

from dataclasses import replace

import pytest

from media_house.core.domain import Rational
from media_house.modules.timeline_builder.domain.engine import (
    cache_key,
    content_hash,
    human_state,
    input_hash,
)
from media_house.modules.timeline_builder.domain.errors import InvalidHumanEdit
from media_house.modules.timeline_builder.domain.facts import SpecFacts
from media_house.modules.timeline_builder.domain.human import HumanEdit, apply_edits
from media_house.modules.timeline_builder.domain.model import BuiltTimeline, SourceUse, VideoClip
from media_house.modules.timeline_builder.domain.profile import BuilderConfig, Capabilities
from media_house.modules.timeline_builder.domain.values import (
    BuildMode,
    ClipKind,
    ConflictPolicy,
    EditKind,
    FitMode,
    Provenance,
    Severity,
)
from tests.unit.timeline_builder import fixtures as f


def edit(t: BuiltTimeline, *edits: HumanEdit, version: int = 2) -> BuiltTimeline:
    return apply_edits(t, edits, meta=f.meta(version, "prev", f.LATER), config=BuilderConfig())


def modify(element: str, **changes: str) -> HumanEdit:
    return HumanEdit(EditKind.MODIFY, element, tuple(changes.items()), editor="eve", note="mix")


def moved_pb(facts: SpecFacts) -> SpecFacts:
    """The edit changes: pB plays one second later in the recording (picture and its sound)."""
    changed = f.change_video(
        facts, "v:pB", source_start=Rational(21, 1), source_end=Rational(25, 1)
    )
    return f.change_audio(changed, "a:aW", source_start=Rational(21, 1), source_end=Rational(25, 1))


class TestIncrementalBuilds:
    def test_the_first_build_is_new_and_a_repeat_carries_everything_over(self) -> None:
        first = f.built()

        again = f.built(previous=first, version=2)

        assert first.build.mode is BuildMode.NEW and first.build.elements_reused == 0
        assert again.build.mode is BuildMode.INCREMENTAL
        clips = first.counts.video_clips + first.counts.audio_clips + first.counts.transitions
        assert again.build.elements_reused == clips
        assert again.build.change.empty and again.build.change.unchanged > 0
        assert again.build.build_hash == first.build.build_hash

    def test_a_changed_clip_is_the_only_thing_rebuilt(self) -> None:
        facts = f.facts_of()
        first = f.built(facts)

        second = f.built(moved_pb(facts), previous=first, version=2)

        change = second.build.change
        assert set(change.changed) >= {"v:pB", "a:aW"}
        assert "v:pA" not in change.changed and "a:aV" not in change.changed
        assert change.sections == ("s1",) and not change.added and not change.removed
        assert second.build.elements_reused < first.build.elements_total
        assert second.video_clip("v:pB").source.source_in == Rational(21, 1)  # type: ignore[union-attr]
        assert second.build.build_hash != first.build.build_hash

    def test_added_and_removed_elements_are_found_by_their_stable_ids(self) -> None:
        facts = f.facts_of()
        first = f.built(facts)
        without_graphic = replace(
            facts, overlays=tuple(o for o in facts.overlays if o.item_id != "o:graphic:g1")
        )

        removed = f.built(without_graphic, previous=first, version=2)
        back = f.built(facts, previous=removed, version=3)

        assert removed.build.change.removed == ("o:graphic:g1",)
        assert back.build.change.added == ("o:graphic:g1",)
        assert back.video_clip("o:graphic:g1") == first.video_clip("o:graphic:g1")

    def test_a_changed_transition_does_not_rebuild_the_clips(self) -> None:
        facts = f.facts_of()
        first = f.built(facts)
        fade = f.change_transition(facts, "cut:pA>pB", preset_id="long")

        second = f.built(fade, previous=first, version=2)

        assert second.build.change.changed == ("cut:pA>pB",)
        assert second.transition("cut:pA>pB").preset_id == "long"  # type: ignore[union-attr]

    def test_a_full_rebuild_reconstructs_everything_to_the_same_result(self) -> None:
        first = f.built()

        full = f.built(previous=first, version=2, full_rebuild=True)

        assert full.build.mode is BuildMode.FULL and full.build.elements_reused == 0
        assert full.build.build_hash == first.build.build_hash
        assert "requested" in full.build.reason

    def test_other_timeline_settings_make_it_a_full_rebuild(self) -> None:
        facts = f.facts_of()
        first = f.built(facts)
        wide = replace(facts, timebase=replace(facts.timebase, width=3840, height=2160))

        second = f.built(wide, previous=first, version=2)

        assert second.build.mode is BuildMode.FULL
        assert "settings" in second.build.reason

    def test_a_changed_build_option_rebuilds_the_clips_it_touches(self) -> None:
        first = f.built()

        second = f.built(previous=first, version=2, config=BuilderConfig(fit_mode=FitMode.COVER))

        assert second.build.mode is BuildMode.FULL or second.build.elements_reused == 0

    def test_the_build_is_deterministic_and_ignores_the_clock(self) -> None:
        a = f.built(at=f.NOW)
        b = f.built(at=f.LATER)

        assert a.build.build_hash == b.build.build_hash == content_hash(a)
        assert a.build.built_at != b.build.built_at

    def test_the_cache_key_changes_with_every_input_and_nothing_else(self) -> None:
        facts = f.facts_of()
        live = f.live_for(facts)
        base = input_hash(facts, live, BuilderConfig(), Capabilities(), None, ())

        assert base == input_hash(facts, live, BuilderConfig(), Capabilities(), None, ())
        assert base != input_hash(moved_pb(facts), live, BuilderConfig(), Capabilities(), None, ())
        assert base != input_hash(
            facts, live, BuilderConfig(block_on_unsupported=True), Capabilities(), None, ()
        )
        assert base != input_hash(
            facts, live, BuilderConfig(), Capabilities(max_audio_tracks=2), None, ()
        )
        assert base != input_hash(
            facts, f.live_for(facts, gone=("gfx1",)), BuilderConfig(), Capabilities(), None, ()
        )
        assert base != input_hash(
            facts, live, BuilderConfig(), Capabilities(), None, (), full_rebuild=True
        )

    def test_a_persons_work_is_part_of_the_key(self) -> None:
        first = f.built()
        edited = edit(first, modify("a:music:seg1", gain_db="-20"))

        assert cache_key("x", first) != cache_key("x", edited)
        assert human_state(first) == human_state(f.built())
        assert human_state(None) == human_state(first)  # nobody touched it: same state


class TestRecordingAPersonsChanges:
    def test_a_change_marks_the_element_with_who_what_and_the_ai_version_it_came_from(self) -> None:
        first = f.built()

        edited = edit(first, modify("a:music:seg1", gain_db="-20"))

        m = edited.audio_clip("a:music:seg1")
        assert m is not None and m.gain_db == -20.0
        assert m.provenance is Provenance.HUMAN_MODIFIED and m.human is not None
        assert m.human.base_digest == first.audio_clip("a:music:seg1").source_digest  # type: ignore[union-attr]
        assert m.human.fields == ("gain_db",) and m.human.editor == "eve"
        assert edited.audio_clip("a:aV").provenance is Provenance.AI  # type: ignore[union-attr]
        assert edited.build.build_version == 2 and edited.build.supersedes == "prev"
        [record] = edited.edit_log
        assert (record.sequence, record.kind, record.element_id) == (1, EditKind.MODIFY, m.clip_id)

    def test_a_trim_keeps_exact_times_and_frames_together(self) -> None:
        first = f.built()

        edited = edit(first, modify("o:graphic:g1", end_frame="100"))

        g = edited.video_clip("o:graphic:g1")
        assert g is not None and g.end_frame == 100 and g.timeline_end == Rational(4, 1)

    def test_a_deleted_clip_leaves_a_tombstone_and_takes_its_links_with_it(self) -> None:
        first = f.built()

        edited = edit(first, HumanEdit(EditKind.DELETE, "v:pB", editor="eve"))

        assert edited.video_clip("v:pB") is None
        [stone] = edited.removed_by_human
        assert stone.element_id == "v:pB"
        assert stone.base_digest == first.video_clip("v:pB").source_digest  # type: ignore[union-attr]
        assert edited.transitions == ()  # the dissolve joined pB
        assert edited.video_clip("v:pA").transition_out is None  # type: ignore[union-attr]
        assert edited.audio_clip("a:aW").video_link is None  # type: ignore[union-attr]

    def test_a_created_clip_is_the_persons_own(self) -> None:
        first = f.built()
        mine = VideoClip(
            clip_id="human:1",
            track_id="V2",
            kind=ClipKind.CUSTOM,
            name="My insert",
            source=SourceUse(
                source_id="broll1",
                original_asset_id="broll1",
                source_in=Rational(2, 1),
                source_out=Rational(3, 1),
                source_in_frame=None,
            ),
            timeline_start=Rational(32, 5),
            timeline_end=Rational(36, 5),
            start_frame=160,
            end_frame=180,
        )

        edited = edit(first, HumanEdit(EditKind.CREATE, "human:1", clip=mine, editor="eve"))

        c = edited.video_clip("human:1")
        assert c is not None and c.provenance is Provenance.HUMAN_CREATED
        assert [x.clip_id for x in edited.track("V2").video] == ["v:pC", "human:1"]  # type: ignore[union-attr]

    def test_a_change_that_breaks_the_timeline_is_refused_whole(self) -> None:
        first = f.built()

        with pytest.raises(InvalidHumanEdit, match="overlap"):
            edit(first, modify("v:pB", start_frame="90"))

    @pytest.mark.parametrize(
        ("element", "changes", "message"),
        [
            ("nowhere", {"name": "x"}, "not a clip"),
            ("v:pB", {"colour": "red"}, "cannot be changed"),
            ("v:pB", {"opacity": "lots"}, "must be a number"),
            ("v:pB", {"track_id": "A1"}, "not a video track"),
            ("a:aV", {"start_sample": "-3"}, "at least"),
            ("a:aV", {"enabled": "perhaps"}, "true or false"),
        ],
    )
    def test_malformed_changes_are_refused_with_the_reason(
        self, element: str, changes: dict[str, str], message: str
    ) -> None:
        with pytest.raises(InvalidHumanEdit, match=message):
            edit(f.built(), modify(element, **changes))

    def test_no_change_at_all_is_refused(self) -> None:
        with pytest.raises(InvalidHumanEdit):
            edit(f.built())


class TestAPersonsWorkSurvivesTheNextBuild:
    def test_a_change_the_specification_did_not_touch_stays(self) -> None:
        first = f.built()
        edited = edit(first, modify("a:music:seg1", gain_db="-20"))

        after = f.built(previous=edited, version=3)

        m = after.audio_clip("a:music:seg1")
        assert m is not None and m.gain_db == -20.0 and m.provenance is Provenance.HUMAN_MODIFIED
        assert after.build.change.conflicted == ()
        assert not [i for i in after.issues if i.code.startswith("human_")]

    def test_a_deletion_the_specification_did_not_touch_stays(self) -> None:
        edited = edit(f.built(), HumanEdit(EditKind.DELETE, "o:graphic:g1"))

        after = f.built(previous=edited, version=3)

        assert after.video_clip("o:graphic:g1") is None
        assert [t.element_id for t in after.removed_by_human] == ["o:graphic:g1"]
        assert after.build.change.conflicted == ()

    def test_a_created_clip_is_carried_over(self) -> None:
        first = f.built()
        cover = first.video_clip("v:pC")
        assert cover is not None
        mine = replace(
            cover,
            clip_id="human:1",
            start_frame=160,
            end_frame=180,
            timeline_start=Rational(32, 5),
            timeline_end=Rational(36, 5),
            kind=ClipKind.CUSTOM,
        )
        edited = edit(first, HumanEdit(EditKind.CREATE, "human:1", clip=mine))

        after = f.built(previous=edited, version=3)

        assert after.video_clip("human:1") is not None
        assert after.video_clip("human:1").provenance is Provenance.HUMAN_CREATED  # type: ignore[union-attr]

    def conflict(self, policy: ConflictPolicy) -> tuple[BuiltTimeline, BuiltTimeline]:
        """A person changed the music gain; then the specification changes it too."""
        facts = f.facts_of()
        first = f.built(facts)
        edited = edit(first, modify("a:music:seg1", gain_db="-20"))
        newer = f.change_audio(facts, "a:music:seg1", gain_db=-12.0)
        after = f.built(
            newer, previous=edited, version=3, config=BuilderConfig(conflict_policy=policy)
        )
        return edited, after

    def test_review_keeps_the_persons_version_and_holds_the_new_one(self) -> None:
        _, after = self.conflict(ConflictPolicy.REVIEW)

        m = after.audio_clip("a:music:seg1")
        assert m is not None and m.gain_db == -20.0 and m.human is not None
        assert m.human.pending_digest is not None
        issue = next(i for i in after.issues if i.code == "human_change_review")
        assert issue.severity is Severity.REVIEW and issue.element_id == "a:music:seg1"
        assert after.build.change.conflicted == ("a:music:seg1",)
        assert after.status.value == "partial"

    def test_preserve_keeps_the_persons_version_and_says_so(self) -> None:
        _, after = self.conflict(ConflictPolicy.PRESERVE)

        m = after.audio_clip("a:music:seg1")
        assert m is not None and m.gain_db == -20.0 and m.human is not None
        assert m.human.pending_digest is None
        assert next(i for i in after.issues if i.code == "human_change_kept").severity is (
            Severity.INFO
        )

    def test_replace_uses_the_specification_and_says_what_was_replaced(self) -> None:
        _, after = self.conflict(ConflictPolicy.REPLACE)

        m = after.audio_clip("a:music:seg1")
        assert m is not None and m.gain_db == -12.0 and m.provenance is Provenance.AI
        issue = next(i for i in after.issues if i.code == "human_change_replaced")
        assert issue.severity is Severity.WARNING and "gain_db" in issue.message
        assert after.edit_log  # the history of what the person did is kept

    def test_a_review_that_the_specification_withdraws_resolves_itself(self) -> None:
        facts = f.facts_of()
        first = f.built(facts)
        edited = edit(first, modify("a:music:seg1", gain_db="-20"))
        held = f.built(
            f.change_audio(facts, "a:music:seg1", gain_db=-12.0), previous=edited, version=3
        )

        back = f.built(facts, previous=held, version=4)

        m = back.audio_clip("a:music:seg1")
        assert m is not None and m.human is not None and m.human.pending_digest is None
        assert not [i for i in back.issues if i.code == "human_change_review"]

    def test_a_deleted_element_the_specification_changed_follows_the_policy(self) -> None:
        facts = f.facts_of()
        edited = edit(f.built(facts), HumanEdit(EditKind.DELETE, "o:graphic:g1"))
        newer = f.change_overlay(facts, "o:graphic:g1", end_frame=80, timeline_end=Rational(16, 5))

        review = f.built(newer, previous=edited, version=3)
        replace_ = f.built(
            newer,
            previous=edited,
            version=3,
            config=BuilderConfig(conflict_policy=ConflictPolicy.REPLACE),
        )

        assert review.video_clip("o:graphic:g1") is None
        assert any(i.code == "human_deletion_review" for i in review.issues)
        assert replace_.video_clip("o:graphic:g1") is not None
        assert any(i.code == "human_deletion_replaced" for i in replace_.issues)

    def test_a_changed_element_the_specification_dropped_stays_for_a_person_to_decide(self) -> None:
        facts = f.facts_of()
        edited = edit(f.built(facts), modify("o:graphic:g1", end_frame="60"))
        dropped = replace(
            facts, overlays=tuple(o for o in facts.overlays if o.item_id != "o:graphic:g1")
        )

        after = f.built(dropped, previous=edited, version=3)

        assert after.video_clip("o:graphic:g1") is not None
        assert any(i.code == "human_change_orphaned" for i in after.issues)

    def test_an_unchanged_ai_element_the_specification_dropped_is_simply_removed(self) -> None:
        facts = f.facts_of()
        first = f.built(facts)
        dropped = replace(
            facts, overlays=tuple(o for o in facts.overlays if o.item_id != "o:graphic:g1")
        )

        after = f.built(dropped, previous=first, version=2)

        assert after.video_clip("o:graphic:g1") is None
        assert not [i for i in after.issues if i.code.startswith("human_")]
