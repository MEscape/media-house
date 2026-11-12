"""Render the real Brief-to-Cut widget with an ALDI brief, without models or a visible desktop.

Run: uv run --no-sync python -m scratch.preview_brief_to_cut
Screenshots and geometry.json are written under scratch/brief-to-cut-preview.
"""

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget, QWidget
from tests.support.fakes import ImmediateJobScheduler
from tests.unit.production_project.fakes import FakeEvidence
from tests.unit.production_project.world import build_world

from media_house.modules.control_center.presentation.viewmodels.brief_to_cut_viewmodel import (
    BriefToCutViewModel,
)
from media_house.modules.control_center.presentation.views.brief_to_cut_view import BriefToCutView
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.presentation.styling.theme import ThemeManager
from media_house.shared.configuration import Theme

if TYPE_CHECKING:
    from media_house.shared.errors.handler import ErrorReport


def _geometry(widget: QWidget, root: QWidget) -> dict[str, Any]:
    position = widget.mapTo(root, QPoint(0, 0))
    return {
        "position": [position.x(), position.y()],
        "size": [widget.width(), widget.height()],
        "minimum_hint": [widget.minimumSizeHint().width(), widget.minimumSizeHint().height()],
        "visible": widget.isVisibleTo(root),
    }


def _capture(
    app: QApplication, view: BriefToCutView, target: Path, width: int, height: int, name: str
) -> dict[str, Any]:
    for _ in range(3):
        app.processEvents()
    screenshot = target / f"{width}x{height}-{name}.png"
    if not view.grab().save(str(screenshot)):
        raise RuntimeError(f"Could not save {screenshot}")
    clipped_buttons = []
    outside_buttons = []
    for button in view.findChildren(QPushButton):
        if not button.isVisibleTo(view):
            continue
        text = button.text().replace("&", "")
        if button.fontMetrics().horizontalAdvance(text) + 22 > button.width():
            clipped_buttons.append(text)
        top_left = button.mapTo(view, QPoint(0, 0))
        bottom_right = button.mapTo(view, button.rect().bottomRight())
        if not view.rect().contains(top_left) or not view.rect().contains(bottom_right):
            outside_buttons.append(text)
    return {
        "screenshot": str(screenshot),
        "requested": [width, height],
        "actual": [view.width(), view.height()],
        "minimum_hint": [view.minimumSizeHint().width(), view.minimumSizeHint().height()],
        "clipped_button_labels": clipped_buttons,
        "buttons_outside_view": outside_buttons,
        "stack": _geometry(view._stack, view),
        "dock": _geometry(view._brainstorm_dock, view),
        "brief": _geometry(view._brief, view),
        "script": _geometry(view._script, view),
        "material_slots": _geometry(view._material_slots, view),
        "material_detail": _geometry(view._material_detail, view),
        "page_minimums": [
            [
                view._stack.widget(i).minimumSizeHint().width(),
                view._stack.widget(i).minimumSizeHint().height(),
            ]
            for i in range(view._stack.count())
        ],
        "wide_children": sorted(
            [
                {
                    "type": widget.metaObject().className(),
                    "name": widget.accessibleName() or widget.objectName(),
                    "minimum_hint": [
                        widget.minimumSizeHint().width(),
                        widget.minimumSizeHint().height(),
                    ],
                }
                for widget in view._stack.findChildren(QWidget)
                if widget.minimumSizeHint().width() >= 700
            ],
            key=lambda item: item["minimum_hint"][0],
            reverse=True,
        )[:15],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brief",
        type=Path,
        default=Path.home() / "Downloads" / "ALDI Feuerwerk 202627 \u2013 Video-Briefing.pdf",
    )
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "brief-to-cut-preview"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication(["brief-to-cut-preview", "-platform", "offscreen"])
    # The offscreen Windows plugin does not enumerate system fonts automatically.
    for font_name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf"):
        font = Path("C:/Windows/Fonts") / font_name
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    if "Segoe UI" in QFontDatabase.families():
        app.setFont(QFont("Segoe UI", 9))
    theme = ThemeManager(app, Theme.DARK)
    errors: list[ErrorReport] = []
    dispatcher = UiDispatcher()
    jobs = UiJobRunner(ImmediateJobScheduler(), dispatcher, errors.append)
    world = build_world(FakeEvidence({}, {}))
    vm = BriefToCutViewModel(world.workflow, jobs, StatusReporter())
    view = BriefToCutView(vm)
    vm.create_project("ALDI Feuerwerk 2026/27", "ALDI", "brochure_review", 2026, sponsored=False)
    vm.load_brief_file(args.brief)
    if not view._brief.toPlainText().strip():
        raise RuntimeError(view._banner.text() or "The briefing reader returned no text")
    vm.extract(
        view._brief.toPlainText(),
        "Fachliche Einordnung statt Werbesprache. Vergleiche nur innerhalb des Angebots.",
        use_model=False,
    )
    vm.run_risks()
    vm.brainstorm()
    view._concept_list.setCurrentRow(0)
    view._choose()
    vm.generate_script()
    vm.plan_material()
    vm.build_context()
    if errors:
        raise RuntimeError([error.user_message for error in errors])
    if not view._script.toPlainText().strip():
        raise RuntimeError(view._banner.text() or "The workflow generated no script")
    report: list[dict[str, Any]] = []
    for width, height in ((1600, 1000), (1280, 900)):
        view.setFixedSize(width, height)
        view.show()
        view._go(0)
        report.append(_capture(app, view, args.output, width, height, "brief"))
        view._go(2)
        tabs = view._stack.currentWidget()
        if not isinstance(tabs, QTabWidget):
            raise RuntimeError("The story page is no longer a tab widget")
        tabs.setCurrentIndex(1)
        report.append(_capture(app, view, args.output, width, height, "script"))
        view._go(3)
        for index in range(view._material_slots.topLevelItemCount()):
            folder = view._material_slots.topLevelItem(index)
            if folder is not None and folder.text(0).startswith("01_cam") and folder.childCount():
                view._material_slots.setCurrentItem(folder.child(0))
                break
        report.append(_capture(app, view, args.output, width, height, "material"))
    (args.output / "geometry.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({"theme": theme.theme.value, "captures": report}, ensure_ascii=False, indent=2)
    )
    view.close()


if __name__ == "__main__":
    main()
