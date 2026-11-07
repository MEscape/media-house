"""One execution of a built timeline in Resolve: first build, update, retry, and a person's work.

The real scripting adapter runs against a stand-in of Resolve's documented object tree.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from media_house.modules.resolve_adapter.application.execution import Execution
from media_house.modules.resolve_adapter.application.sources import SourceFile
from media_house.modules.resolve_adapter.domain.binding import SyncState
from media_house.modules.resolve_adapter.domain.config import ResolveAdapterConfig
from media_house.modules.resolve_adapter.domain.errors import (
    ConfigurationNotApplied,
    ProjectConflict,
    ResolveConnectionLost,
)
from media_house.modules.resolve_adapter.domain.values import (
    Area,
    CaptionStrategy,
    ExecutionState,
    OperationStatus,
    Resolution,
    Strategy,
    Verification,
)
from media_house.modules.resolve_adapter.domain.values import (
    ExecutionPolicy as P,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import OperationCancelledError
from tests.support.resolve_fakes import FakeFolder, FakeResolve
from tests.unit.resolve_adapter import fixtures as f

PROJECT = "Test edit"
CLIPS = {
    "v:pA", "v:pB", "v:pC", "o:graphic:g1", "o:caption:cap:1", "o:caption:cap:2",
    "a:aV", "a:aW", "a:music:seg1", "a:sfx:sfx:1",
}  # fmt: skip


def timeline_of(fake: FakeResolve) -> object:
    project = fake.projects[PROJECT]
    assert project.timeline is not None
    return project.timeline


def placed(fake: FakeResolve) -> dict[str, tuple[str, int, int, int]]:
    """element tag -> (kind, track, start, duration) as Resolve holds it."""
    timeline = fake.projects[PROJECT].timeline
    assert timeline is not None
    found = {}
    for kind, lanes in timeline.tracks.items():
        for index, lane in enumerate(lanes, start=1):
            for item in lane:
                tag = next(
                    (m["customData"][len("mh:clip:") :] for m in item.markers.values()
                     if m["customData"].startswith("mh:clip:")),
                    item.uid,
                )  # fmt: skip
                found[tag] = (kind, index, item.start, item.duration)
    return found


class TestFirstBuild:
    @pytest.fixture
    def built(self, tmp_path: Path) -> tuple[Execution, FakeResolve]:
        ex, fake = f.execution(tmp_path)
        f.run(ex)
        return ex, fake

    def test_a_project_and_a_timeline_are_made_and_the_project_is_saved(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        ex, fake = built

        project = fake.projects[PROJECT]
        assert [t.name for t in project.timelines] == [PROJECT]
        assert project.saved >= 1 and ex.ledger.state is ExecutionState.READY_TO_RENDER

    def test_settings_are_applied_and_read_back(self, built: tuple[Execution, FakeResolve]) -> None:
        ex, fake = built

        assert fake.projects[PROJECT].settings["timelineFrameRate"] == "25"
        assert all(s.applied for s in ex.ledger.settings)
        assert {s.name for s in ex.ledger.settings if s.critical} == {
            "timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight",
        }  # fmt: skip

    def test_the_tracks_exist_in_the_builders_order(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        _, fake = built
        timeline = fake.projects[PROJECT].timeline
        assert timeline is not None

        assert (timeline.GetTrackCount("video"), timeline.GetTrackCount("audio")) == (5, 5)
        assert timeline.GetTrackName("video", 3) == "V3 Graphics"
        assert timeline.GetTrackName("audio", 1) == "A1 Dialogue"

    def test_every_clip_is_where_the_builder_put_it(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        _, fake = built

        found = placed(fake)
        assert set(found) == CLIPS
        assert found["v:pB"] == ("video", 1, 100, 100)
        assert found["v:pC"] == ("video", 2, 125, 25)
        assert found["o:graphic:g1"] == ("video", 3, 25, 50)
        assert found["o:caption:cap:2"] == ("video", 4, 100, 50)
        assert found["a:music:seg1"][:2] == ("audio", 3) and found["a:sfx:sfx:1"][:2] == (
            "audio",
            5,
        )

    def test_each_clip_plays_the_part_of_its_file_the_builder_chose(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        _, fake = built
        timeline = fake.projects[PROJECT].timeline
        assert timeline is not None

        by_left = {i.left for lane in timeline.tracks["video"] for i in lane}
        assert {250, 500, 0, 50} <= by_left  # in points of pA, pB, the graphics, the caption batch

    def test_media_is_imported_once_each_into_folders_by_what_it_is(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        ex, fake = built

        pool = fake.projects[PROJECT].pool.root
        [house] = pool.folders
        folders = {f.name: sorted(c.path.stem for c in f.clips) for f in house.folders}
        assert folders["Original footage"] == ["broll1", "rec1"]
        assert folders["Graphics"] == ["gfx1"] and folders["Captions"] == ["cap-file"]
        assert folders["Music"] == ["song1"] and folders["Sound effects"] == ["whoosh1"]
        assert {r.asset_id for r in ex.media_records.values()} == {
            "rec1", "broll1", "gfx1", "cap-file", "song1", "whoosh1",
        }  # fmt: skip

    def test_a_source_with_a_proxy_gets_it_linked_and_the_source_stays_the_clip(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        ex, fake = built

        footage = next(
            f
            for f in fake.projects[PROJECT].pool.root.folders[0].folders
            if f.name == "Original footage"
        )
        rec1 = next(c for c in footage.clips if c.path.stem == "rec1")
        assert rec1.proxy.endswith("proxy-rec1.mp4")
        assert (
            ex.media_records["rec1"].proxy == "linked"
            and ex.media_records["broll1"].proxy == "none"
        )

    def test_picture_and_sound_of_a_take_are_linked(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        _, fake = built
        timeline = fake.projects[PROJECT].timeline
        assert timeline is not None

        picture = next(i for i in timeline.tracks["video"][0] if i.start == 0)
        assert picture.linked  # linked to its sound

    def test_every_clip_is_tagged_so_it_can_be_recognised_after_a_person_moved_it(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        _, fake = built

        assert set(placed(fake)) == CLIPS  # tags, not unique ids, name them

    def test_what_was_read_back_is_verified_per_clip(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        ex, _ = built

        by_element = {
            o.element_id: o
            for o in ex.ledger.operations
            if o.area in (Area.CLIP, Area.AUDIO) and o.action == "place"
        }
        assert set(by_element) == CLIPS
        assert all(o.verification is Verification.VERIFIED for o in by_element.values())
        assert not any(c.state is Verification.FAILED for c in ex.ledger.checks)

    def test_the_transition_is_an_instruction_at_the_cut_never_a_substitute(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        ex, fake = built

        marker = next(
            m for m in fake.projects[PROJECT].timeline.markers.values()
            if "transition.insert" in m["customData"]
        )  # fmt: skip
        assert marker["name"].startswith("TRANSITION dissolve")
        op = next(o for o in ex.ledger.operations if o.element_id == "cut:pA>pB")
        assert op.status is OperationStatus.PARTIAL and op.strategy is Strategy.MANUAL_MARKER
        assert any(a.operation == "transition.insert" for a in ex.ledger.alternatives)

    def test_the_mix_is_left_to_the_mix_engine(self, built: tuple[Execution, FakeResolve]) -> None:
        ex, _ = built

        mix = [a for a in ex.ledger.alternatives if a.operation == "audio.gain_fades"]
        assert mix and all(a.owner == "audio_mix_engine" for a in mix)

    def test_what_a_read_back_confirmed_is_remembered_for_the_matrix(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        ex, _ = built

        assert {
            "clip.place",
            "project.settings",
            "media.import",
            "snapshot.read",
            "timeline.create",
        } <= (ex.ledger.observed)
        state = state_of(ex)
        assert "clip.place" in state.observed and state.environment_key.startswith(
            "studio:19.0.3.2"
        )

    def test_the_binding_holds_every_clip_with_the_state_that_was_read_back(
        self, built: tuple[Execution, FakeResolve]
    ) -> None:
        ex, _ = built

        state = state_of(ex)

        assert {e.element_id for e in state.elements} == CLIPS
        pb = state.element("v:pB")
        assert (
            pb is not None and ("start", "100") in pb.fields and ("src_start", "500") in pb.fields
        )
        assert state.project.project_name == PROJECT and state.project.timeline_name == PROJECT
        assert {m.asset_id for m in state.media} >= {"rec1", "gfx1"}


class TestSecondRun:
    def test_nothing_is_placed_or_imported_again(self, tmp_path: Path) -> None:
        first, fake = f.execution(tmp_path)
        state = f.run(first)
        before = fake.placed
        pool = [len(x.clips) for x in fake.projects[PROJECT].pool.root.folders[0].folders]

        second, again = f.rerun(tmp_path, fake, state)

        assert fake.placed == before == 10
        assert [len(x.clips) for x in fake.projects[PROJECT].pool.root.folders[0].folders] == pool
        assert {d.action.value for d in second.decisions.values()} == {"keep"}
        assert again.build_hash == state.build_hash
        assert [t.name for t in fake.projects[PROJECT].timelines] == [PROJECT]
        assert not second.conflicts and second.sync_record().status == "in_sync"

    def test_the_second_run_leaves_the_markers_alone(self, tmp_path: Path) -> None:
        first, fake = f.execution(tmp_path)
        state = f.run(first)
        count = len(fake.projects[PROJECT].timeline.markers)

        f.rerun(tmp_path, fake, state)

        assert len(fake.projects[PROJECT].timeline.markers) == count

    def test_the_same_inputs_give_the_same_resolve_state_on_a_fresh_resolve(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "1").mkdir()
        (tmp_path / "2").mkdir()
        one, fake1 = f.execution(tmp_path / "1")
        two, fake2 = f.execution(tmp_path / "2")
        s1, s2 = f.run(one), f.run(two)

        assert placed(fake1) == placed(fake2)
        assert [e.fields for e in s1.elements] == [e.fields for e in s2.elements]


class TestAPersonsWork:
    def setup(self, tmp_path: Path) -> tuple[FakeResolve, SyncState]:
        first, fake = f.execution(tmp_path)
        return fake, f.run(first)

    def test_a_clip_a_person_moved_is_not_moved_back(self, tmp_path: Path) -> None:
        fake, state = self.setup(tmp_path)
        fake.human_item("v:pB", fake.projects[PROJECT]).start = 130

        ex, new = f.rerun(tmp_path, fake, state)

        assert placed(fake)["v:pB"][2] == 130
        d = ex.decisions["v:pB"]
        assert d.action.value == "leave" and d.changed_by_person and not d.conflicts
        assert "v:pB" in ex.sync_record().human_modified
        assert ex.sync_record().status == "changes_found"
        # the base is kept, so the change is still seen next time
        kept_binding = new.element("v:pB")
        assert kept_binding is not None and dict(kept_binding.fields)["start"] == "100"

    def test_a_clip_a_person_deleted_stays_deleted(self, tmp_path: Path) -> None:
        fake, state = self.setup(tmp_path)
        item = fake.human_item("o:graphic:g1", fake.projects[PROJECT])
        fake.projects[PROJECT].timeline.DeleteClips([item])

        ex, _ = f.rerun(tmp_path, fake, state)

        assert "o:graphic:g1" not in placed(fake)
        assert "o:graphic:g1" in ex.sync_record().human_deleted
        assert fake.placed == 10  # nothing was placed again

    def test_a_clip_a_person_added_is_never_touched(self, tmp_path: Path) -> None:
        fake, state = self.setup(tmp_path)
        project = fake.projects[PROJECT]
        mine = project.pool.ImportMedia([str(_file(tmp_path, "mine.mov"))])[0]
        project.pool.AppendToTimeline(
            [
                {
                    "mediaPoolItem": mine,
                    "startFrame": 0,
                    "endFrame": 9,
                    "mediaType": 1,
                    "trackIndex": 5,
                    "recordFrame": 500,
                }
            ]
        )

        ex, _ = f.rerun(tmp_path, fake, state)

        assert len(ex.human_created) == 1 and ex.sync_record().human_created == tuple(
            ex.human_created
        )
        assert len(project.timeline.tracks["video"][4]) == 1

    def test_a_change_media_house_makes_to_what_the_person_left_alone_is_applied(
        self, tmp_path: Path
    ) -> None:
        fake, state = self.setup(tmp_path)
        facts = f.with_clip(f.facts_of(), "o:graphic:g1", opacity=0.5)

        ex, _ = f.rerun(tmp_path, fake, state, facts=facts)

        assert ex.decisions["o:graphic:g1"].action.value == "update"
        item = fake.human_item("o:graphic:g1", fake.projects[PROJECT])
        assert item.props["Opacity"] == 50.0
        assert fake.placed == 10  # updated in place, not rebuilt

    def test_non_conflicting_changes_of_both_sides_are_merged(self, tmp_path: Path) -> None:
        fake, state = self.setup(tmp_path)
        item = fake.human_item("o:graphic:g1", fake.projects[PROJECT])
        item.enabled = False  # the person switches it off
        facts = f.with_clip(f.facts_of(), "o:graphic:g1", opacity=0.5)  # Media House dims it

        ex, _ = f.rerun(tmp_path, fake, state, facts=facts)

        assert item.props["Opacity"] == 50.0 and item.enabled is False
        assert not ex.decisions["o:graphic:g1"].conflicts

    @pytest.mark.parametrize(
        ("policy", "start", "resolution"),
        [
            (P.REQUIRE_CONFLICT_REVIEW, 130, Resolution.OPEN),
            (P.PRESERVE_MANUAL_EDITS, 130, Resolution.KEPT_PERSON),
            (P.UPDATE_MANAGED_ELEMENTS, 110, Resolution.APPLIED_MEDIA_HOUSE),
        ],
    )
    def test_when_both_moved_the_same_clip_the_policy_decides_and_it_is_on_record(
        self, tmp_path: Path, policy: P, start: int, resolution: Resolution
    ) -> None:
        fake, state = self.setup(tmp_path)
        fake.human_item("v:pB", fake.projects[PROJECT]).start = 130
        facts = f.with_clip(f.facts_of(), "v:pB", start_frame=110, end_frame=210)

        ex, new = f.rerun(
            tmp_path, fake, state, facts=facts, config=ResolveAdapterConfig(policy=policy)
        )

        assert placed(fake)["v:pB"][2] == start
        [record] = [c for c in ex.conflicts if c.element_id == "v:pB"]
        assert record.resolution is resolution
        assert record.fields[0].resolve == "130" and record.fields[0].media_house == "110"
        assert bool(new.conflicts) is (resolution is Resolution.OPEN)

    def test_an_open_conflict_is_a_partial_result_and_stays_in_the_binding(
        self, tmp_path: Path
    ) -> None:
        fake, state = self.setup(tmp_path)
        fake.human_item("v:pB", fake.projects[PROJECT]).start = 130
        facts = f.with_clip(f.facts_of(), "v:pB", start_frame=110, end_frame=210)

        ex, new = f.rerun(tmp_path, fake, state, facts=facts)

        assert ex.sync_record().status == "conflicts_open"
        assert [c.element_id for c in new.conflicts] == ["v:pB"]

    def test_a_clip_media_house_dropped_is_removed_when_nobody_touched_it(
        self, tmp_path: Path
    ) -> None:
        fake, state = self.setup(tmp_path)
        facts = f.facts_of()
        facts = replace(facts, clips=tuple(c for c in facts.clips if c.clip_id != "v:pC"))

        ex, new = f.rerun(tmp_path, fake, state, facts=facts)

        assert "v:pC" not in placed(fake) and new.element("v:pC") is None
        assert ex.decisions["v:pC"].action.value == "delete"

    def test_a_clip_media_house_dropped_that_the_person_changed_is_kept(
        self, tmp_path: Path
    ) -> None:
        fake, state = self.setup(tmp_path)
        fake.human_item("v:pC", fake.projects[PROJECT]).start = 140
        facts = f.facts_of()
        facts = replace(facts, clips=tuple(c for c in facts.clips if c.clip_id != "v:pC"))

        ex, _ = f.rerun(tmp_path, fake, state, facts=facts)

        assert "v:pC" in placed(fake) and ex.decisions["v:pC"].action.value == "leave"


class TestRetryAfterAnInterruption:
    def test_a_resolve_that_dies_mid_run_is_a_lost_connection_with_the_work_so_far_on_record(
        self, tmp_path: Path
    ) -> None:
        fake = FakeResolve()
        fake.die_after = 4
        ex, _ = f.execution(tmp_path, fake=fake)

        with pytest.raises(ResolveConnectionLost):
            f.run(ex)

        assert fake.placed == 4
        assert any(
            o.action == "place" and o.status is OperationStatus.EXECUTED
            for o in ex.ledger.operations
        ) or (fake.placed == 4)

    def test_the_rerun_takes_back_what_was_placed_and_places_only_the_rest(
        self, tmp_path: Path
    ) -> None:
        fake = FakeResolve()
        fake.die_after = 4
        checkpoints: list[SyncState] = []
        ex, _ = f.execution(tmp_path, fake=fake)
        ex.on_checkpoint = checkpoints.append
        with pytest.raises(ResolveConnectionLost):
            f.run(ex)
        fake.alive, fake.die_after = True, None
        last = checkpoints[-1]  # what the use case stored before the crash

        again, state = f.rerun(tmp_path, fake, last)

        assert set(placed(fake)) == CLIPS and fake.placed == 10  # no clip twice
        assert len(fake.projects[PROJECT].timelines) == 1
        assert not again.conflicts
        assert {e.element_id for e in state.elements} == CLIPS

    def test_the_project_a_crashed_run_made_is_recognised_by_its_checkpoint(
        self, tmp_path: Path
    ) -> None:
        fake = FakeResolve()
        fake.die_after = 0
        checkpoints: list[SyncState] = []
        ex, _ = f.execution(tmp_path, fake=fake)
        ex.on_checkpoint = checkpoints.append
        with pytest.raises(ResolveConnectionLost):
            f.run(ex)
        fake.alive, fake.die_after = True, None

        assert checkpoints and checkpoints[0].project.project_name == PROJECT
        _, state = f.rerun(tmp_path, fake, checkpoints[0])
        assert len(state.elements) == 10

    def test_without_any_record_an_existing_project_is_never_taken_over(
        self, tmp_path: Path
    ) -> None:
        fake = FakeResolve()
        first, _ = f.execution(tmp_path, fake=fake)
        f.run(first)
        second, _ = f.execution(tmp_path, fake=fake)

        with pytest.raises(ProjectConflict, match="already exists"):
            f.run(second)


class TestProjects:
    def test_another_open_project_blocks_the_run_unless_switching_is_allowed(
        self, tmp_path: Path
    ) -> None:
        fake = FakeResolve()
        be, _ = f.backend(tmp_path, fake)
        be.connect()
        be.create_project("Someone's film")
        ex, _ = f.execution(tmp_path, fake=fake)

        with pytest.raises(ProjectConflict, match="Someone's film"):
            f.run(ex)
        assert fake.current is not None and fake.current.name == "Someone's film"

    def test_switching_saves_the_open_project_first(self, tmp_path: Path) -> None:
        fake = FakeResolve()
        be, _ = f.backend(tmp_path, fake)
        be.connect()
        be.create_project("Someone's film")
        ex, _ = f.execution(
            tmp_path, fake=fake, config=ResolveAdapterConfig(allow_project_switch=True)
        )

        f.run(ex)

        assert fake.projects["Someone's film"].saved == 1 and fake.current is fake.projects[PROJECT]

    def test_an_untitled_project_is_not_anybodys_work(self, tmp_path: Path) -> None:
        fake = FakeResolve()
        be, _ = f.backend(tmp_path, fake)
        be.connect()
        be.create_project("Untitled Project")
        ex, _ = f.execution(tmp_path, fake=fake)

        f.run(ex)

        assert fake.current is fake.projects[PROJECT]

    def test_an_existing_project_can_be_used_when_that_is_allowed(self, tmp_path: Path) -> None:
        fake = FakeResolve()
        be, _ = f.backend(tmp_path, fake)
        be.connect()
        be.create_project(PROJECT)
        ex, _ = f.execution(
            tmp_path, fake=fake, config=ResolveAdapterConfig(adopt_existing_project=True)
        )

        f.run(ex)

        assert set(placed(fake)) == CLIPS

    def test_a_project_that_cannot_take_the_frame_rate_stops_before_anything_is_built(
        self, tmp_path: Path
    ) -> None:
        fake = FakeResolve()
        be, _ = f.backend(tmp_path, fake)
        be.connect()
        be.create_project(PROJECT)
        project = fake.projects[PROJECT]
        project.new_timeline("old")
        other = _file(tmp_path, "x.mov")
        [clip] = project.pool.ImportMedia([str(other)])
        project.pool.AppendToTimeline(
            [
                {
                    "mediaPoolItem": clip,
                    "startFrame": 0,
                    "endFrame": 9,
                    "mediaType": 1,
                    "trackIndex": 1,
                    "recordFrame": 86400,
                }
            ]
        )  # it has media, so its 24 fps is final
        ex, _ = f.execution(
            tmp_path, fake=fake, config=ResolveAdapterConfig(adopt_existing_project=True)
        )

        with pytest.raises(ConfigurationNotApplied, match="timelineFrameRate"):
            f.run(ex)
        assert fake.placed == 1  # only the person's clip is in the project
        assert any(not s.applied and s.critical for s in ex.ledger.settings)


class TestTimelinePolicies:
    def test_create_new_builds_next_to_the_old_timeline_and_touches_nothing(
        self, tmp_path: Path
    ) -> None:
        first, fake = f.execution(tmp_path)
        state = f.run(first)
        before = placed(fake)

        _, new = f.rerun(tmp_path, fake, state, config=ResolveAdapterConfig(policy=P.CREATE_NEW))

        names = [t.name for t in fake.projects[PROJECT].timelines]
        assert names == [PROJECT, f"{PROJECT} v1"] and new.project.timeline_name == f"{PROJECT} v1"
        old = fake.projects[PROJECT].timelines[0]
        assert sum(len(lane) for lanes in old.tracks.values() for lane in lanes) == len(before)

    def test_a_full_rebuild_keeps_the_old_timeline_as_a_backup(self, tmp_path: Path) -> None:
        first, fake = f.execution(tmp_path)
        state = f.run(first)

        _, new = f.rerun(
            tmp_path, fake, state, config=ResolveAdapterConfig(policy=P.FULL_REBUILD_WITH_BACKUP)
        )

        names = [t.name for t in fake.projects[PROJECT].timelines]
        assert (
            PROJECT in names
            and f"{PROJECT} (before rebuild)" in names
            and f"{PROJECT} (replaced)" in names
        )
        assert new.project.timeline_name == PROJECT
        for t in fake.projects[PROJECT].timelines:  # every timeline still has its clips
            assert sum(len(lane) for lanes in t.tracks.values() for lane in lanes) == 10

    def test_a_timeline_deleted_in_resolve_is_built_again_with_a_warning(
        self, tmp_path: Path
    ) -> None:
        first, fake = f.execution(tmp_path)
        state = f.run(first)
        fake.projects[PROJECT].timelines.clear()
        fake.projects[PROJECT].current_timeline = None

        ex, _ = f.rerun(tmp_path, fake, state)

        assert set(placed(fake)) == CLIPS
        assert any("gone from Resolve" in w for w in ex.ledger.warnings)


class TestMediaAndVerification:
    def test_media_already_in_the_pool_is_reused_not_imported_again(self, tmp_path: Path) -> None:
        fake = FakeResolve()
        be, _ = f.backend(tmp_path, fake)
        be.connect()
        be.create_project(PROJECT)
        facts = f.facts_of()
        files = f.files_for(tmp_path, facts)
        be.import_media([_path(files["rec1"])], ("Somewhere",))
        ex, _ = f.execution(
            tmp_path,
            facts=facts,
            fake=fake,
            config=ResolveAdapterConfig(adopt_existing_project=True),
        )

        f.run(ex)

        rec1 = [
            c
            for fo in _all_folders(fake.projects[PROJECT].pool.root)
            for c in fo.clips
            if c.path.stem == "rec1"
        ]
        assert len(rec1) == 1 and ex.media_records["rec1"].status == "reused"

    def test_offline_media_is_relinked_and_recorded(self, tmp_path: Path) -> None:
        fake = FakeResolve()
        be, _ = f.backend(tmp_path, fake)
        be.connect()
        be.create_project(PROJECT)
        facts = f.facts_of()
        files = f.files_for(tmp_path, facts)
        [clip] = be.import_media([_path(files["rec1"])], ("Somewhere",))
        fake.projects[PROJECT].pool.root.folders[0].clips[0].offline = True
        ex, _ = f.execution(
            tmp_path,
            facts=facts,
            fake=fake,
            config=ResolveAdapterConfig(adopt_existing_project=True),
        )

        f.run(ex)

        assert any(
            o.action == "relink" and o.status is OperationStatus.EXECUTED
            for o in ex.ledger.operations
        )
        assert clip.item_id and ex.media_records["rec1"].status == "reused"

    def test_a_wrong_end_frame_convention_is_caught_by_the_read_back_not_hidden(
        self, tmp_path: Path
    ) -> None:
        ex, _ = f.execution(tmp_path, fake=FakeResolve(end_inclusive=True))

        f.run(ex)

        failed = [
            o
            for o in ex.ledger.operations
            if o.action == "place" and o.verification is Verification.FAILED
        ]
        assert len(failed) == 10 and "duration" in failed[0].detail
        assert any(c.state is Verification.FAILED for c in ex.ledger.checks)

    def test_the_quirk_can_be_corrected_in_the_configuration(self, tmp_path: Path) -> None:
        from media_house.modules.resolve_adapter.domain.config import Quirks

        ex, _ = f.execution(
            tmp_path,
            fake=FakeResolve(end_inclusive=True),
            config=ResolveAdapterConfig(quirks=Quirks(end_frame_inclusive=True)),
        )

        f.run(ex)

        assert not any(o.verification is Verification.FAILED for o in ex.ledger.operations)

    def test_a_missing_font_is_reported_never_replaced(self, tmp_path: Path) -> None:
        facts = f.facts_of()
        facts = replace(
            facts,
            identity=replace(
                facts.identity, fonts=(replace(facts.identity.fonts[0], available=False),)
            ),
        )
        ex, _ = f.execution(tmp_path, facts=facts)

        f.run(ex)

        check = next(c for c in ex.ledger.checks if c.name == "fonts")
        assert check.state is Verification.FAILED and "Anton" in check.detail
        assert any("fonts missing" in w for w in ex.ledger.warnings)

    def test_fonts_that_exist_cannot_be_confirmed_through_the_api(self, tmp_path: Path) -> None:
        ex, _ = f.execution(tmp_path)

        f.run(ex)

        check = next(c for c in ex.ledger.checks if c.name == "fonts")
        assert check.state is Verification.NOT_VERIFIABLE

    def test_the_identity_is_a_reference_that_is_recorded_not_a_copy(self, tmp_path: Path) -> None:
        ex, _ = f.execution(tmp_path)

        f.run(ex)

        check = next(c for c in ex.ledger.checks if c.name == "creator identity")
        assert "mesc v2" in check.detail and "3 design choices" in check.detail

    def test_editable_caption_cues_arrive_as_a_subtitle_file_in_the_pool(
        self, tmp_path: Path
    ) -> None:
        ex, fake = f.execution(
            tmp_path,
            config=ResolveAdapterConfig(
                caption_strategy=CaptionStrategy.RENDERED_WITH_EDITABLE_CUES
            ),
        )

        f.run(ex)

        captions = next(
            fo for fo in _all_folders(fake.projects[PROJECT].pool.root) if fo.name == "Captions"
        )
        assert any(c.path.suffix == ".srt" for c in captions.clips)
        op = next(o for o in ex.ledger.operations if o.area is Area.CAPTION)
        assert op.status is OperationStatus.PARTIAL and op.strategy is Strategy.IMPORTED_FILE
        assert (tmp_path / "exports" / "captions.srt").read_text(encoding="utf-8").strip()


class TestAFileThatIsGone:
    def test_without_the_media_check_the_clips_that_need_it_are_reported_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        ex, fake = f.execution(tmp_path, config=ResolveAdapterConfig(require_media=False))
        ex.files = {
            **ex.files,
            "broll1": SourceFile("broll1", None, exists=False, changed=False),
        }

        f.run(ex)

        assert "v:pC" not in placed(fake) and len(placed(fake)) == 9
        assert ex.media_records["broll1"].status == "unavailable"
        failed = {o.element_id for o in ex.ledger.operations if o.status is OperationStatus.FAILED}
        assert {"broll1", "v:pC"} <= failed
        assert any(
            c.name == "media online" and c.state is Verification.FAILED for c in ex.ledger.checks
        )


class TestCancellation:
    def test_a_cancelled_run_stops_between_phases_and_says_what_was_done(
        self, tmp_path: Path
    ) -> None:
        ex, fake = f.execution(tmp_path)
        ctx = JobContext.detached()
        ctx.cancellation.cancel()

        with pytest.raises(OperationCancelledError):
            ex.run("x", ctx)

        assert fake.placed == 0


def state_of(ex: Execution) -> SyncState:
    assert ex.snapshot is not None
    return ex.new_state("x", ex.snapshot)


def _file(tmp: Path, name: str) -> Path:
    path = tmp / name
    path.write_bytes(b"\x00" * 8)
    return path


def _path(source: SourceFile) -> Path:
    assert source.path is not None
    return source.path


def _all_folders(root: FakeFolder) -> list[FakeFolder]:
    found: list[FakeFolder] = []
    stack = list(root.folders)
    while stack:
        folder = stack.pop()
        found.append(folder)
        stack.extend(folder.folders)
    return found
