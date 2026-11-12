"""Brief to Cut and Dein Stil: passive widgets over the real workflow (fake analysis)."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QListWidgetItem, QPushButton, QTableWidgetItem, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from media_house.modules.control_center.presentation.viewmodels.brief_to_cut_viewmodel import (
    BriefToCutViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.style_review_viewmodel import (
    StyleReviewViewModel,
)
from media_house.modules.control_center.presentation.views.brief_to_cut_view import (
    BriefToCutView,
    DropZone,
)
from media_house.modules.control_center.presentation.views.style_review_view import (
    StyleReviewView,
)
from media_house.modules.production_project.application.story_step import RewriteProposal
from media_house.modules.production_project.domain.compliance import TimelineFacts
from media_house.modules.style_memory.application.memory import DistillPreferences, ReviewRules
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.errors import Ok
from media_house.shared.errors.handler import ErrorReport
from tests.support.fakes import FixedClock, ImmediateJobScheduler
from tests.unit.production_project.fakes import FakeEvidence, FakeRunner, FakeTimelines
from tests.unit.production_project.world import build_world

pytestmark = pytest.mark.presentation


def _t_list(item: QListWidgetItem | None) -> str:
    assert item is not None
    return item.text()


def _t(item: QTableWidgetItem | QTreeWidgetItem | None) -> str:
    assert item is not None
    return item.text(0) if isinstance(item, QTreeWidgetItem) else item.text()


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "brief_to_cut" / "lidl_brief.txt"


@pytest.fixture
def jobs(dispatcher: UiDispatcher, unhandled_errors: list[ErrorReport]) -> UiJobRunner:
    return UiJobRunner(
        ImmediateJobScheduler(), dispatcher, on_unhandled_error=unhandled_errors.append
    )


def test_the_wizard_takes_a_brief_to_a_script_and_shows_every_step(
    qtbot: QtBot, jobs: UiJobRunner, unhandled_errors: list[ErrorReport]
) -> None:
    world = build_world(FakeEvidence({}, {}))
    vm = BriefToCutViewModel(world.workflow, jobs, StatusReporter())
    view = BriefToCutView(vm)
    qtbot.addWidget(view)

    # step 1: no project yet -> a friendly message, nothing breaks
    vm.extract("x", "")
    assert "Projekt" in view._banner.text()

    view._title.setText("Lidl Feuerwerk")
    view._client.setText("Lidl")
    view._create()
    assert view._projects.count() == 1 and vm.project_id

    view._brief.setPlainText(FIXTURE.read_text(encoding="utf-8"))
    view._ideas.setPlainText("Das gibt es bei keinem anderen Discounter.")
    vm.extract(view._brief.toPlainText(), view._ideas.toPlainText())
    groups = [_t(view._model.topLevelItem(i)) for i in range(view._model.topLevelItemCount())]
    assert {"Vorgaben", "Fakten", "Termine", "Pflichtelemente", "Offene Fragen"} <= set(groups)

    # step 2: risks, and the safe alternative is one click
    vm.run_risks()
    assert view._risk_table.rowCount() >= 1
    view._risk_table.selectRow(0)
    view._decide(keep=False)
    assert "replaced" in _t(view._risk_table.item(0, 0))

    # step 3: concepts, script, scenes, fields, plan
    vm.brainstorm()
    assert view._concept_list.count() == 3
    view._concept_list.setCurrentRow(0)
    view._choose()
    assert _t_list(view._concept_list.item(0)).startswith("✓")
    vm.generate_script()
    assert view._scenes.rowCount() == 17 and "## S01" in view._script.toPlainText()
    assert view._slots.rowCount() >= 25
    vm.plan_material()
    assert view._requirements.rowCount() >= 13
    vm.refresh_report()
    assert view._lint.count() > 0 and "min" in view._length.text()

    # editing a Technik-Feld goes through the model
    item = view._slots.item(0, 2)
    assert item is not None
    item.setText("30")
    opened = world.workflow.open_project(vm.project_id)
    assert isinstance(opened, Ok) and opened.value.slots[0].value == "30"
    assert unhandled_errors == []


def test_a_test_run_shows_what_the_timeline_showed_and_the_winner_is_taken(
    qtbot: QtBot, jobs: UiJobRunner, unhandled_errors: list[ErrorReport]
) -> None:
    facts = TimelineFacts(300.0, ((0.0, 300.0),), ("cut",), 2, (), (), cuts=25)
    runner = FakeRunner()
    world = build_world(
        FakeEvidence({}, {}), runner=runner, timelines=FakeTimelines({"anchor-1": facts})
    )
    vm = BriefToCutViewModel(world.workflow, jobs, StatusReporter())
    view = BriefToCutView(vm)
    qtbot.addWidget(view)
    view._title.setText("Lidl Feuerwerk")
    view._client.setText("Lidl")
    view._create()
    vm.extract(FIXTURE.read_text(encoding="utf-8"), "")
    vm.build_context()

    vm.run_variant("normal", [Path("a.mp4")])

    assert runner.calls and "Testlauf" in view._context_counts["normal"].text()
    assert "5.0 Schnitte/min" in view._context_counts["normal"].text()
    assert view._context_counts["minimal"].text().endswith("Wörter")  # not run: only the count
    vm.attach_timeline()
    opened = world.workflow.open_project(vm.project_id)
    assert isinstance(opened, Ok) and opened.value.timeline == facts
    vm.choose_context()  # one run is not enough to pick: a plain message, no dialog
    assert view._banner.text() != ""
    assert unhandled_errors == []


def test_a_proposal_is_a_diff_and_nothing_applies_without_uebernehmen(
    qtbot: QtBot, jobs: UiJobRunner
) -> None:
    world = build_world(FakeEvidence({}, {}))
    vm = BriefToCutViewModel(world.workflow, jobs, StatusReporter())
    view = BriefToCutView(vm)
    qtbot.addWidget(view)

    assert not view._apply.isEnabled()
    vm.proposal_changed.emit(
        RewriteProposal("S02", "kürzer", "Zeile eins\nZeile zwei", "Zeile eins\nZeile drei")
    )
    assert view._apply.isEnabled()
    diff = view._diff.toPlainText()
    assert "-Zeile zwei" in diff and "+Zeile drei" in diff
    vm.discard_proposal()
    assert not view._apply.isEnabled() and view._diff.toPlainText() == ""


def test_the_drop_zone_hands_a_dropped_folder_to_the_ingest(qtbot: QtBot, tmp_path: Path) -> None:
    seen: list[Path] = []
    zone = DropZone(seen.append)
    qtbot.addWidget(zone)

    assert zone.acceptDrops() and "Alles hier reinziehen" in zone.text()
    zone._on_folder(tmp_path)
    assert seen == [tmp_path]


def test_unsaved_script_survives_other_updates_and_dock_is_available_in_every_step(
    qtbot: QtBot, jobs: UiJobRunner
) -> None:
    world = build_world(FakeEvidence({}, {}))
    vm = BriefToCutViewModel(world.workflow, jobs, StatusReporter())
    view = BriefToCutView(vm)
    qtbot.addWidget(view)
    view._title.setText("Mein Video")
    view._create()
    vm.extract(FIXTURE.read_text(encoding="utf-8"), "")
    vm.generate_script()
    edited = view._script.toPlainText().replace("Einstieg", "Mein Einstieg")
    view._script.setPlainText(edited)

    vm.plan_material()
    assert view._script.toPlainText() == edited
    view._generate_script()
    assert "ungespeicherte" in view._banner.text()
    vm.save_script(edited)
    assert not view._has_unsaved_script()
    for step in range(5):
        view._go(step)
        assert view._brainstorm_dock.parentWidget() is view._stack.parentWidget()
    assert view._material_slots.topLevelItemCount() > 0
    folder = view._material_slots.topLevelItem(0)
    assert folder is not None
    requirement = folder.child(0)
    assert requirement is not None
    view._material_slots.setCurrentItem(requirement)
    assert "Dateiname:" in view._material_detail.toPlainText()


def test_context_is_editable_and_unsaved_changes_survive_project_updates(
    qtbot: QtBot, jobs: UiJobRunner
) -> None:
    world = build_world(FakeEvidence({}, {}))
    vm = BriefToCutViewModel(world.workflow, jobs, StatusReporter())
    view = BriefToCutView(vm)
    qtbot.addWidget(view)
    vm.create_project("Mein Video", "", "generic", 2027, sponsored=False)
    vm.extract(FIXTURE.read_text(encoding="utf-8"), "")
    vm.build_context()
    field = view._context_views["normal"]
    assert not field.isReadOnly()
    field.setPlainText("Mein noch ungespeicherter Kontext.")
    vm.run_risks()
    assert field.toPlainText() == "Mein noch ungespeicherter Kontext."
    vm.edit_context("normal", field.toPlainText())
    project = world.workflow.open_project(vm.project_id)
    assert isinstance(project, Ok)
    assert (
        next(v.text for v in project.value.contexts if v.level == "normal") == field.toPlainText()
    )


def test_opening_an_empty_project_clears_the_previous_script(
    qtbot: QtBot, jobs: UiJobRunner
) -> None:
    world = build_world(FakeEvidence({}, {}))
    vm = BriefToCutViewModel(world.workflow, jobs, StatusReporter())
    view = BriefToCutView(vm)
    qtbot.addWidget(view)
    vm.create_project("Erstes Video", "", "generic", 2027)
    vm.extract(FIXTURE.read_text(encoding="utf-8"), "")
    vm.generate_script()
    assert view._script.toPlainText()
    vm.create_project("Zweites Video", "", "generic", 2027)
    assert not view._script.toPlainText()
    assert not view._brief.toPlainText()


def test_the_style_page_lists_candidates_and_nothing_is_binding_until_approved(
    qtbot: QtBot, jobs: UiJobRunner
) -> None:
    from media_house.modules.style_memory.application.library import SeedStyleMemory
    from tests.unit.style_memory.fakes import InMemoryStyleStore

    store = InMemoryStyleStore()
    clock = FixedClock()
    SeedStyleMemory(store, clock).execute()
    DistillPreferences(store, clock).execute()
    review = ReviewRules(store, clock)
    vm = StyleReviewViewModel(review, jobs)
    view = StyleReviewView(vm)
    qtbot.addWidget(view)
    vm.load()

    table = view._table
    texts = [_t(table.item(r, 0)) for r in range(table.rowCount())]
    row = next(i for i, t in enumerate(texts) if "aus dem Kontext fließen" in t)
    assert _t(table.item(row, 5)) == "candidate"
    table.selectRow(row)
    view._approve()
    assert _t(table.item(row, 5)) == "active"
    assert all(isinstance(b, QPushButton) for b in view.findChildren(QPushButton))
