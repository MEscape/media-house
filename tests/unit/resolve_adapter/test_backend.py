"""The scripting adapter, driven against a stand-in of Resolve's documented object tree.

What this proves: the adapter calls the documented API the documented way, turns every answer
into plain records, and never mistakes a failed call for a successful one. What it cannot prove:
that a real Resolve behaves as documented (the capability matrix says so: NOT_VERIFIED).
"""

from pathlib import Path

import pytest

from media_house.modules.resolve_adapter.domain.errors import (
    ResolveCallFailed,
    ResolveConnectionLost,
)
from media_house.modules.resolve_adapter.domain.live import PlaceRequest
from media_house.modules.resolve_adapter.domain.values import ConnectionState as C
from media_house.modules.resolve_adapter.infrastructure.install import (
    ProcessProbe,
    ResolveInstall,
)
from media_house.modules.resolve_adapter.infrastructure.scripting import (
    ScriptingResolveBackend,
)
from tests.support.resolve_fakes import FakeResolve, FakeRunner
from tests.unit.resolve_adapter.fixtures import backend


def connected(
    tmp_path: Path, fake: FakeResolve | None = None
) -> tuple[ScriptingResolveBackend, FakeResolve]:
    be, fake = backend(tmp_path, fake)
    assert be.connect().state is C.CONNECTED
    return be, fake


def media(tmp_path: Path, name: str = "a.mov") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x00" * 8)
    return path


class TestConnection:
    def test_a_working_call_is_what_makes_it_connected(self, tmp_path: Path) -> None:
        be, _ = backend(tmp_path)

        status = be.connect()

        assert status.connected and status.installed and status.running
        env = status.environment
        assert env is not None and env.edition == "studio" and env.version is not None
        assert env.version.text == "19.0.3.2" and env.operating_system == "win32"
        assert "ProjectManager.CreateProject" in env.methods and "Resolve" in env.probed

    def test_the_free_edition_is_read_from_the_product_name(self, tmp_path: Path) -> None:
        be, _ = backend(tmp_path, FakeResolve(studio=False))

        status = be.connect()

        assert status.environment is not None and status.environment.edition == "free"

    def test_not_installed(self, tmp_path: Path) -> None:
        install = ResolveInstall(tmp_path, tmp_path / "missing.dll", "Resolve.exe", "win32")
        be = ScriptingResolveBackend(
            install, ProcessProbe(FakeRunner(running=False), install), lambda _i: FakeResolve()
        )

        status = be.connect()

        assert status.state is C.NOT_INSTALLED and not status.installed

    def test_installed_but_not_running(self, tmp_path: Path) -> None:
        be, _ = backend(tmp_path, running=False)

        status = be.connect()

        assert status.state is C.NOT_RUNNING and status.installed and not status.running
        assert "Start it" in status.detail

    def test_a_running_process_without_an_answer_is_not_a_connection(self, tmp_path: Path) -> None:
        install = ResolveInstall(tmp_path, tmp_path / "f.dll", "Resolve.exe", "win32")
        (tmp_path / "f.dll").write_bytes(b"\x00")
        be = ScriptingResolveBackend(install, ProcessProbe(FakeRunner(), install), lambda _i: None)

        status = be.connect()

        assert status.state is C.API_UNAVAILABLE and "External scripting" in status.detail

    def test_a_library_that_cannot_be_loaded_is_api_unavailable(self, tmp_path: Path) -> None:
        def broken(_install: ResolveInstall) -> object:
            raise ImportError("DLL load failed")

        install = ResolveInstall(tmp_path, tmp_path / "f.dll", "Resolve.exe", "win32")
        (tmp_path / "f.dll").write_bytes(b"\x00")
        be = ScriptingResolveBackend(install, ProcessProbe(FakeRunner(), install), broken)

        status = be.connect()

        assert status.state is C.API_UNAVAILABLE and "DLL load failed" in status.detail

    def test_a_library_that_crashes_is_a_failed_connection(self, tmp_path: Path) -> None:
        def crash(_install: ResolveInstall) -> object:
            raise RuntimeError("boom")

        install = ResolveInstall(tmp_path, tmp_path / "f.dll", "Resolve.exe", "win32")
        (tmp_path / "f.dll").write_bytes(b"\x00")
        be = ScriptingResolveBackend(install, ProcessProbe(FakeRunner(), install), crash)

        assert be.connect().state is C.CONNECTION_FAILED

    def test_a_resolve_that_dies_while_answering_is_a_failed_connection(
        self, tmp_path: Path
    ) -> None:
        fake = FakeResolve()
        fake.alive = False
        be, _ = backend(tmp_path, fake)

        assert be.connect().state is C.CONNECTION_FAILED

    def test_an_unsupported_version_is_reported_with_the_version(self, tmp_path: Path) -> None:
        be, _ = backend(tmp_path, FakeResolve(version=(17, 4, 0, 0, "")))

        status = be.connect()

        assert status.state is C.UNSUPPORTED_VERSION and "17.4" in status.detail

    def test_an_unanswerable_process_question_still_tries_the_connection(
        self, tmp_path: Path
    ) -> None:
        be, _ = backend(tmp_path, running=None)

        assert be.connect().connected

    def test_calls_before_connecting_say_so(self, tmp_path: Path) -> None:
        be, _ = backend(tmp_path)

        with pytest.raises(ResolveConnectionLost):
            be.probe()

    def test_a_resolve_that_goes_away_is_a_lost_connection_not_a_failed_call(
        self, tmp_path: Path
    ) -> None:
        be, fake = connected(tmp_path)
        be.create_project("p")
        fake.alive = False

        with pytest.raises(ResolveConnectionLost):
            be.list_projects()
        assert not be.alive()


class TestProjects:
    def test_create_list_open_and_current(self, tmp_path: Path) -> None:
        be, _ = connected(tmp_path)

        made = be.create_project("A")
        be.create_project("B")

        assert made.name == "A" and made.unique_id and made.database == "Local Database"
        assert be.list_projects() == ("A", "B")
        current = be.current_project()
        assert current is not None and current.name == "B"
        assert be.open_project("A").name == "A"

    def test_a_name_that_exists_is_refused_by_resolve_and_raised(self, tmp_path: Path) -> None:
        be, _ = connected(tmp_path)
        be.create_project("A")

        with pytest.raises(ResolveCallFailed, match="create the project"):
            be.create_project("A")

    def test_opening_a_project_that_does_not_exist_is_a_failure(self, tmp_path: Path) -> None:
        be, _ = connected(tmp_path)

        with pytest.raises(ResolveCallFailed):
            be.open_project("nope")

    def test_settings_are_written_and_read_back_separately(self, tmp_path: Path) -> None:
        be, _ = connected(tmp_path)
        be.create_project("A")

        done = be.set_settings({"timelineFrameRate": "25", "timelineResolutionWidth": "1280"})

        assert done == {"timelineFrameRate": True, "timelineResolutionWidth": True}
        assert be.get_settings(["timelineFrameRate", "timelineResolutionWidth"]) == {
            "timelineFrameRate": "25",
            "timelineResolutionWidth": "1280",
        }

    def test_save_and_export(self, tmp_path: Path) -> None:
        be, fake = connected(tmp_path)
        be.create_project("A")

        be.save_project()
        be.export_project("A", tmp_path / "A.drp")

        assert fake.projects["A"].saved == 1 and (tmp_path / "A.drp").is_file()


class TestMediaPool:
    def test_folders_are_created_once_and_nested(self, tmp_path: Path) -> None:
        be, fake = connected(tmp_path)
        be.create_project("A")

        be.ensure_folder(("Media House", "Graphics"))
        be.ensure_folder(("Media House", "Graphics"))

        root = fake.projects["A"].pool.root
        [house] = root.folders
        assert house.name == "Media House" and [f.name for f in house.folders] == ["Graphics"]

    def test_imported_media_is_listed_with_its_folder_and_file(self, tmp_path: Path) -> None:
        be, _ = connected(tmp_path)
        be.create_project("A")
        path = media(tmp_path)

        [imported] = be.import_media([path], ("Media House", "Footage"))

        assert imported.path == str(path) and imported.folder == ("Media House", "Footage")
        [listed] = be.pool_items()
        assert listed.item_id == imported.item_id and listed.media_type == "video"
        assert (listed.width, listed.height, listed.proxy) == (1920, 1080, "none")

    def test_a_file_resolve_cannot_import_is_simply_absent(self, tmp_path: Path) -> None:
        be, _ = connected(tmp_path)
        be.create_project("A")

        assert be.import_media([tmp_path / "missing.mov"], ("Media House",)) == ()

    def test_a_proxy_is_linked_and_shows_in_the_pool(self, tmp_path: Path) -> None:
        be, _ = connected(tmp_path)
        be.create_project("A")
        [item] = be.import_media([media(tmp_path)], ("M",))
        proxy = media(tmp_path, "a_proxy.mp4")

        assert be.link_proxy(item.item_id, proxy)
        [listed] = be.pool_items()

        assert listed.proxy == "linked" and listed.proxy_path == str(proxy)

    def test_relinking_brings_a_file_back_online(self, tmp_path: Path) -> None:
        be, fake = connected(tmp_path)
        be.create_project("A")
        [item] = be.import_media([media(tmp_path)], ("M",))
        fake.projects["A"].pool.root.folders[0].clips[0].offline = True

        assert be.pool_items()[0].offline
        be.relink([item.item_id], tmp_path)

        assert not be.pool_items()[0].offline

    def test_a_pool_item_that_is_not_there_is_a_failure(self, tmp_path: Path) -> None:
        be, _ = connected(tmp_path)
        be.create_project("A")

        with pytest.raises(ResolveCallFailed, match="pool item"):
            be.link_proxy("nope", tmp_path)


class TestTimeline:
    def setup(self, tmp_path: Path) -> tuple[ScriptingResolveBackend, FakeResolve, str]:
        be, fake = connected(tmp_path)
        be.create_project("A")
        [item] = be.import_media([media(tmp_path)], ("M",))
        be.create_timeline("T", "00:00:00:00")
        be.ensure_tracks(2, 2, "stereo")
        return be, fake, item.item_id

    def place(self, be: ScriptingResolveBackend, pool: str, element: str, **kw: int) -> None:
        values = {
            "track_index": 1,
            "record_frame": 0,
            "start_frame": 10,
            "end_frame": 110,
            "media_type": 1,
        }
        values.update(kw)
        placed = be.place([PlaceRequest(element, pool, **values)])
        assert [p.element_id for p in placed] == [element]

    def test_a_timeline_starts_at_zero_so_frames_are_what_the_builder_says(
        self, tmp_path: Path
    ) -> None:
        be, _, _ = self.setup(tmp_path)

        [info] = be.list_timelines()

        assert (
            info.name == "T"
            and info.start_frame == 0
            and (info.video_tracks, info.audio_tracks) == (2, 2)
        )

    def test_tracks_are_only_added_never_removed(self, tmp_path: Path) -> None:
        be, _, _ = self.setup(tmp_path)

        be.ensure_tracks(1, 1, "stereo")

        [info] = be.list_timelines()
        assert (info.video_tracks, info.audio_tracks) == (2, 2)

    def test_a_placed_clip_is_read_back_with_its_real_position_and_length(
        self, tmp_path: Path
    ) -> None:
        be, _, pool = self.setup(tmp_path)

        self.place(be, pool, "v:a", record_frame=40)
        snapshot = be.read_timeline()

        [clip] = snapshot.items
        assert (clip.track_kind, clip.track_index, clip.start, clip.duration) == (
            "video",
            1,
            40,
            100,
        )
        assert clip.left_offset == 10 and clip.media_item_id == pool and clip.enabled
        assert snapshot.duration == 140

    def test_the_measured_exclusive_end_frame_gives_the_requested_length(
        self, tmp_path: Path
    ) -> None:
        be, _, pool = self.setup(tmp_path)

        self.place(be, pool, "v:a", start_frame=0, end_frame=24)

        assert be.read_timeline().items[0].duration == 24

    def test_a_resolve_with_an_inclusive_end_frame_shows_up_as_a_different_length(
        self, tmp_path: Path
    ) -> None:
        be, _ = connected(tmp_path, FakeResolve(end_inclusive=True))
        be.create_project("A")
        [item] = be.import_media([media(tmp_path)], ("M",))
        be.create_timeline("T", "00:00:00:00")

        self.place(be, item.item_id, "v:a", start_frame=0, end_frame=24)

        assert be.read_timeline().items[0].duration == 25  # read back, so the mismatch is visible

    def test_an_occupied_slot_places_nothing(self, tmp_path: Path) -> None:
        be, _, pool = self.setup(tmp_path)
        self.place(be, pool, "v:a")

        placed = be.place([PlaceRequest("v:b", pool, 1, 1, 50, 0, 49)])

        assert placed == () and len(be.read_timeline().items) == 1

    def test_a_track_that_does_not_exist_places_nothing(self, tmp_path: Path) -> None:
        be, _, pool = self.setup(tmp_path)

        assert be.place([PlaceRequest("v:a", pool, 1, 9, 0, 0, 9)]) == ()

    def test_picture_and_sound_go_to_their_own_tracks(self, tmp_path: Path) -> None:
        be, _, pool = self.setup(tmp_path)

        self.place(be, pool, "v:a")
        self.place(be, pool, "a:a", media_type=2, track_index=2)

        kinds = {(i.track_kind, i.track_index) for i in be.read_timeline().items}
        assert kinds == {("video", 1), ("audio", 2)}

    def test_properties_and_enabled_state_are_written_and_read_back(self, tmp_path: Path) -> None:
        be, _, pool = self.setup(tmp_path)
        self.place(be, pool, "v:a")
        item_id = be.read_timeline().items[0].item_id

        done = be.set_item(item_id, {"Opacity": 50.0, "ZoomX": 1.2, "Nonsense": 1.0}, False)

        assert done == {"Opacity": True, "ZoomX": True, "Nonsense": False, "enabled": True}
        clip = be.read_timeline().items[0]
        assert clip.prop("Opacity") == 50.0 and clip.prop("ZoomX") == 1.2 and not clip.enabled

    def test_a_tag_on_the_clip_survives_a_move_by_a_person(self, tmp_path: Path) -> None:
        be, fake, pool = self.setup(tmp_path)
        self.place(be, pool, "v:a")
        item_id = be.read_timeline().items[0].item_id
        assert be.add_marker(item_id, 0, "Cream", "A", "note", 1, "mh:clip:v:a")

        fake.projects["A"].current_timeline.tracks["video"][0][0].start = 77  # type: ignore[union-attr]

        clip = be.read_timeline().items[0]
        assert clip.tag == "v:a" and clip.start == 77

    def test_timeline_markers_are_added_found_by_their_tag_and_removed(
        self, tmp_path: Path
    ) -> None:
        be, _, _ = self.setup(tmp_path)

        assert be.add_marker("timeline", 12, "Red", "TODO", "do it", 3, "mh:gap:x:y")
        [marker] = be.read_timeline().markers
        assert (marker.frame, marker.color, marker.custom_data, marker.duration) == (
            12,
            "Red",
            "mh:gap:x:y",
            3,
        )
        assert be.remove_marker("timeline", "mh:gap:x:y")
        assert be.read_timeline().markers == ()

    def test_a_second_marker_on_the_same_frame_takes_the_next_free_frame(
        self, tmp_path: Path
    ) -> None:
        be, _, _ = self.setup(tmp_path)

        assert be.add_marker("timeline", 12, "Red", "A", "", 1, "mh:a")
        assert be.add_marker("timeline", 12, "Blue", "B", "", 1, "mh:b")

        assert sorted(m.frame for m in be.read_timeline().markers) == [12, 13]
        assert be.remove_marker("timeline", "mh:b")
        assert [m.custom_data for m in be.read_timeline().markers] == ["mh:a"]

    def test_deleting_and_linking(self, tmp_path: Path) -> None:
        be, _, pool = self.setup(tmp_path)
        self.place(be, pool, "v:a")
        self.place(be, pool, "a:a", media_type=2, track_index=1)
        ids = [i.item_id for i in be.read_timeline().items]

        assert be.link_items(ids)
        be.delete_items(ids[:1])

        assert len(be.read_timeline().items) == 1

    def test_a_timeline_can_be_duplicated_and_renamed_never_overwritten(
        self, tmp_path: Path
    ) -> None:
        be, _, _ = self.setup(tmp_path)

        copy = be.duplicate_timeline("T", "T (copy)")
        be.rename_timeline("T", "T (old)")

        assert copy.name == "T (copy)"
        assert {t.name for t in be.list_timelines()} == {"T (copy)", "T (old)"}

    def test_a_timeline_name_that_exists_is_refused(self, tmp_path: Path) -> None:
        be, _, _ = self.setup(tmp_path)

        with pytest.raises(ResolveCallFailed):
            be.create_timeline("T", "00:00:00:00")

    def test_opening_an_unknown_timeline_is_a_failure(self, tmp_path: Path) -> None:
        be, _, _ = self.setup(tmp_path)

        with pytest.raises(ResolveCallFailed, match="find the timeline"):
            be.open_timeline("nope")


class TestRender:
    def setup(self, tmp_path: Path, fake: FakeResolve | None = None) -> ScriptingResolveBackend:
        be, _ = connected(tmp_path, fake)
        be.create_project("A")
        return be

    def test_what_this_resolve_can_render(self, tmp_path: Path) -> None:
        be = self.setup(tmp_path)

        options = be.render_options(None)

        assert ("MP4", "mp4") in options.formats and options.current_format == "mp4"
        assert ("H.264", "H264") in be.render_options("mp4").codecs

    def test_format_codec_and_settings_are_set_and_readable(self, tmp_path: Path) -> None:
        be = self.setup(tmp_path)

        done = be.configure_render("mov", "ProRes422HQ", {"TargetDir": "x", "FormatWidth": 1920})

        assert all(done.values())
        now = be.render_options("mov")
        assert (now.current_format, now.current_codec) == ("mov", "ProRes422HQ")

    def test_a_job_runs_to_completion(self, tmp_path: Path) -> None:
        be = self.setup(tmp_path)
        be.configure_render("mp4", "H264", {"TargetDir": str(tmp_path), "CustomName": "out"})

        job = be.submit_render()
        first, last = be.render_status(job), be.render_status(job)

        assert first.status == "Rendering" and not first.finished and first.percent == 40.0
        assert last.status == "Complete" and last.finished and (tmp_path / "out.mp4").is_file()

    def test_a_failed_job_carries_the_error(self, tmp_path: Path) -> None:
        be = self.setup(tmp_path, FakeResolve(render_script=("Failed",)))

        status = be.render_status(be.submit_render())

        assert status.status == "Failed" and status.finished and status.error == "disk full"

    def test_stopping_a_render(self, tmp_path: Path) -> None:
        be = self.setup(tmp_path)

        be.stop_render()
