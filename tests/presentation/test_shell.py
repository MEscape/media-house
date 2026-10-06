from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QWidget,
)
from pytestqt.qtbot import QtBot

from media_house.presentation.application_window import MainWindow
from media_house.presentation.core_ui import CoreUiContributor
from media_house.presentation.extension import (
    ActionContribution,
    DockContribution,
    UiRegistry,
    ViewContribution,
)
from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.presentation.qt.window_state import WindowStateStore
from media_house.presentation.styling import ThemeManager
from media_house.presentation.styling.theme import DARK, LIGHT
from media_house.shared.configuration import Theme
from media_house.shared.errors import ConfigurationError
from tests.support.fakes import ImmediateJobScheduler

pytestmark = pytest.mark.presentation


def trigger(window: QMainWindow, action_id: str) -> None:
    action = window.findChild(QAction, action_id)
    assert action is not None, action_id
    action.trigger()


def pages(window: QMainWindow) -> QStackedWidget:
    stack = window.centralWidget()
    assert isinstance(stack, QStackedWidget)
    return stack


class TestRegistry:
    def test_duplicate_contribution_ids_are_rejected(self) -> None:
        registry = UiRegistry()
        registry.add_view(ViewContribution("v", "V", QWidget))
        with pytest.raises(ConfigurationError, match="already registered"):
            registry.add_view(ViewContribution("v", "Other", QWidget))
        registry.add_action(ActionContribution("a", "A", "File", lambda: None))
        with pytest.raises(ConfigurationError):
            registry.add_action(ActionContribution("a", "A2", "File", lambda: None))

    def test_registry_exposes_immutable_snapshots(self) -> None:
        registry = UiRegistry()
        registry.add_dock(DockContribution("d", "D", QWidget))
        assert isinstance(registry.docks, tuple)


class TestMainWindow:
    def make(self, qtbot: QtBot, tmp_path: Path, calls: list[str]) -> MainWindow:
        registry = UiRegistry()
        registry.add_action(
            ActionContribution("x.hello", "Say Hello", "Zeta", lambda: calls.append("hi"))
        )
        registry.add_action(ActionContribution("x.help", "Help Me", "Help", lambda: None))
        registry.add_action(ActionContribution("x.file", "Open", "File", lambda: None))
        registry.add_action(ActionContribution("x.alpha", "Alpha", "Alpha", lambda: None))
        registry.add_view(ViewContribution("p1", "Page One", lambda: QLabel("one")))
        registry.add_view(ViewContribution("p2", "Page Two", lambda: QLabel("two")))
        registry.add_dock(DockContribution("dock.x", "My Dock", lambda: QLabel("dock")))
        status = StatusReporter()
        window = MainWindow(
            registry,
            title="T",
            status=status,
            state_store=WindowStateStore(tmp_path / "state.ini"),
        )
        qtbot.addWidget(window)
        self.status = status  # keep the reporter alive for the window's lifetime
        return window

    def test_menus_are_ordered_file_view_others_help(self, qtbot: QtBot, tmp_path: Path) -> None:
        window = self.make(qtbot, tmp_path, [])
        titles = [a.text().replace("&", "") for a in window.menuBar().actions()]
        assert titles == ["File", "View", "Alpha", "Zeta", "Help"]  # View holds the dock toggle

    def test_actions_invoke_contributed_callbacks(self, qtbot: QtBot, tmp_path: Path) -> None:
        calls: list[str] = []
        window = self.make(qtbot, tmp_path, calls)
        trigger(window, "x.hello")
        assert calls == ["hi"]

    def test_pages_are_created_lazily_on_first_visit(self, qtbot: QtBot, tmp_path: Path) -> None:
        window = self.make(qtbot, tmp_path, [])
        navigation = window.findChild(QListWidget)
        assert navigation is not None
        assert pages(window).count() == 1  # only page one built
        navigation.setCurrentRow(1)
        assert pages(window).count() == 2
        assert isinstance(pages(window).currentWidget(), QLabel)

    def test_contributed_docks_get_a_toggle_in_the_view_menu(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        window = self.make(qtbot, tmp_path, [])
        assert "dock.x" in window.docks
        view_menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&View")
        assert any(a.text() == "My Dock" for a in view_menu.actions())

    def test_status_messages_are_shown_and_state_is_persisted_on_close(
        self,
        qtbot: QtBot,
        tmp_path: Path,
    ) -> None:
        window = self.make(qtbot, tmp_path, [])
        assert window.statusBar().currentMessage() == "Ready"
        self.status.show("Saved", 0)
        assert window.statusBar().currentMessage() == "Saved"
        window.show()
        window.close()
        assert (tmp_path / "state.ini").is_file()

    def test_window_without_contributions_still_opens(self, qtbot: QtBot) -> None:
        window = MainWindow(UiRegistry())
        qtbot.addWidget(window)
        assert isinstance(window, QMainWindow)


class TestCoreUi:
    def test_core_contributions_are_registered_through_the_public_extension_points(
        self,
        qtbot: QtBot,
        qapp: QApplication,
    ) -> None:
        dispatcher = UiDispatcher()
        runner = UiJobRunner(
            ImmediateJobScheduler(), dispatcher, on_unhandled_error=lambda _r: None
        )
        quits: list[bool] = []
        registry = UiRegistry()
        CoreUiContributor(
            theme=ThemeManager(qapp, Theme.LIGHT),
            runner=runner,
            quit_app=lambda: quits.append(True),
            version="9.9",
            parent_provider=lambda: None,
        ).contribute(registry)
        ids = {a.id for a in registry.actions}
        assert {
            "core.quit",
            "core.about",
            "core.theme.dark",
            "core.theme.light",
            "core.theme.system",
        } <= ids
        assert [d.id for d in registry.docks] == ["core.jobs"]

        window = MainWindow(registry)
        qtbot.addWidget(window)
        trigger(window, "core.quit")
        assert quits == [True]


class TestTheme:
    def test_themes_apply_centralised_stylesheets(self, qapp: QApplication) -> None:
        manager = ThemeManager(qapp, Theme.LIGHT)
        assert LIGHT.background in qapp.styleSheet()
        manager.apply(Theme.DARK)
        assert DARK.background in qapp.styleSheet()
        assert LIGHT.background not in qapp.styleSheet()
        assert manager.theme is Theme.DARK

    def test_system_theme_resolves_to_a_concrete_palette(self, qapp: QApplication) -> None:
        manager = ThemeManager(qapp, Theme.SYSTEM)
        assert manager.resolved_tokens in (LIGHT, DARK)
        assert "$" not in qapp.styleSheet()  # every placeholder was substituted
