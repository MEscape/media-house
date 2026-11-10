"""Control Center application services with fakes of the other modules' contracts."""

from pathlib import Path

from media_house.modules.control_center.application.resolve_status import CheckResolveStatus
from media_house.modules.control_center.application.sound_library import (
    CommercialUse,
    SoundFilter,
    SoundLibraryService,
    apply_filter,
)
from media_house.modules.review_orchestrator.application.contracts import Actor, Verdict
from media_house.modules.revision_engine.application.contracts import DecideRevisionCommand
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.unit.control_center.fakes import (
    REF,
    FakeImporter,
    FakeInspector,
    FakeSoundCatalog,
    ReviewBackend,
    build_workspace,
    connected_inspection,
    sound,
)


class TestResolveStatus:
    def test_offline_installation(self) -> None:
        status = CheckResolveStatus(FakeInspector()).execute()
        assert not status.connected
        assert status.state == "not_running"
        assert status.version == ""
        assert status.capabilities  # the registry, all marked "nothing was checked"
        assert {c.state for c in status.capabilities} <= {"not_verified", "unsupported"}

    def test_connected_installation(self) -> None:
        status = CheckResolveStatus(FakeInspector(connected_inspection())).execute()
        assert status.connected
        assert status.version == "19.0.3.2"
        assert status.edition == "studio"
        assert status.current_project == "Demo"
        assert status.projects == ("Demo", "Other")


class TestSoundLibrary:
    def service(self) -> SoundLibraryService:
        catalog = FakeSoundCatalog(
            [
                sound("a", "Bright Intro", commercial=True, attribution="Music by X"),
                sound("b", "Night Drive", commercial=None),
                sound("c", "Whoosh", commercial=False, kind="sfx", author="Y"),
            ]
        )
        return SoundLibraryService(catalog, FakeImporter())

    def test_rows_carry_licence_state_and_planner_selectability(self) -> None:
        rows = {r.asset_id: r for r in self.service().rows()}
        assert rows["a"].commercial_use is CommercialUse.YES and rows["a"].selectable
        assert rows["a"].attribution_required and rows["a"].attribution_text == "Music by X"
        assert rows["b"].commercial_use is CommercialUse.UNKNOWN and not rows["b"].selectable
        assert rows["c"].commercial_use is CommercialUse.NO and not rows["c"].selectable

    def test_filters(self) -> None:
        rows = self.service().rows()
        assert [r.asset_id for r in apply_filter(rows, SoundFilter(kind="sfx"))] == ["c"]
        assert [r.asset_id for r in apply_filter(rows, SoundFilter(selectable_only=True))] == ["a"]
        unknown = apply_filter(rows, SoundFilter(commercial_use=CommercialUse.UNKNOWN))
        assert [r.asset_id for r in unknown] == ["b"]
        assert [r.asset_id for r in apply_filter(rows, SoundFilter(text="  y "))] == ["c"]

    def test_import_summarises_the_report(self) -> None:
        result = self.service().import_manifest(Path("m.json"), JobContext.detached())
        assert isinstance(result, Ok)
        assert result.value.imported == 1 and result.value.not_cleared == ("x.wav",)

    def test_import_failure_is_a_message(self) -> None:
        service = SoundLibraryService(FakeSoundCatalog([]), FakeImporter(fail=True))
        result = service.import_manifest(Path("m.json"), JobContext.detached())
        assert isinstance(result, Err) and "manifest" in result.error


class TestReviewWorkspace:
    def test_reads_go_through_the_contracts(self) -> None:
        backend = ReviewBackend()
        ws = build_workspace(backend)
        assert [a.asset_id for a in ws.anchors()] == ["asset-1"]
        assert [s.session_id for s in ws.sessions("asset-1")] == ["s-1"]
        snap = ws.snapshot("s-1")
        assert isinstance(snap, Ok)
        assert snap.value.plans[0].revision_plan_id == "plan-1"
        assert snap.value.history[0].summary == "Review opened"
        assert isinstance(ws.snapshot("nope"), Err)
        assert all(p.purpose in {"delivery", "master"} for p in ws.export_profiles())

    def test_changes_record_a_human_actor(self) -> None:
        backend = ReviewBackend()
        ws = build_workspace(backend)
        assert isinstance(ws.lock_section("s-1", "intro", timeline=REF, reviewer="Bo", note=""), Ok)
        assert isinstance(ws.add_feedback("s-1", " too slow ", author="Bo"), Ok)
        assert isinstance(
            ws.decide_proposal(
                "s-1", "plan-1", "p1", Verdict.APPROVED, timeline=REF, reviewer="Bo"
            ),
            Ok,
        )
        lock, feedback, decide = backend.calls
        assert lock[1][3] == Actor.human("Bo")
        assert feedback[1][1] == "too slow"
        command = decide[1][0]
        assert isinstance(command, DecideRevisionCommand)
        assert command.actor == Actor.human("Bo") and command.timeline == REF

    def test_person_input_is_required_where_the_contract_records_it(self) -> None:
        ws = build_workspace(ReviewBackend())
        assert isinstance(ws.add_feedback("s-1", "   ", author="Bo"), Err)
        assert isinstance(ws.unlock_section("s-1", "main", reviewer="Bo", reason=" "), Err)
        modified = ws.decide_proposal(
            "s-1", "plan-1", "p1", Verdict.MODIFIED, timeline=REF, reviewer="Bo"
        )
        assert isinstance(modified, Err)

    def test_refusals_become_messages(self) -> None:
        ws = build_workspace(ReviewBackend(refuse="The section is locked."))
        result = ws.lock_section("s-1", "main", timeline=REF, reviewer="Bo", note="")
        assert result == Err("The section is locked.")
