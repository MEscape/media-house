"""UI contributed by the shell itself, through the same extension points modules use."""

from collections.abc import Callable
from functools import partial

from PySide6.QtWidgets import QMessageBox, QWidget

from media_house.presentation import strings
from media_house.presentation.components.jobs_panel import JobsPanel
from media_house.presentation.extension import ActionContribution, DockContribution, UiRegistry
from media_house.presentation.models.job_list_model import JobListModel
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.styling import ThemeManager
from media_house.shared.configuration import Theme


class CoreUiContributor:
    def __init__(
        self,
        *,
        theme: ThemeManager,
        runner: UiJobRunner,
        quit_app: Callable[[], None],
        version: str,
        parent_provider: Callable[[], QWidget | None],
    ) -> None:
        self._theme = theme
        self._runner = runner
        self._quit_app = quit_app
        self._version = version
        self._parent_provider = parent_provider

    def contribute(self, registry: UiRegistry) -> None:
        registry.add_action(
            ActionContribution(
                "core.quit", strings.ACTION_QUIT, strings.MENU_FILE, self._quit_app, "Ctrl+Q"
            ),
        )
        for theme, text in (
            (Theme.LIGHT, strings.ACTION_THEME_LIGHT),
            (Theme.DARK, strings.ACTION_THEME_DARK),
            (Theme.SYSTEM, strings.ACTION_THEME_SYSTEM),
        ):
            registry.add_action(
                ActionContribution(
                    f"core.theme.{theme.value}",
                    text,
                    strings.MENU_VIEW,
                    partial(self._theme.apply, theme),
                ),
            )
        registry.add_action(
            ActionContribution("core.about", strings.ACTION_ABOUT, strings.MENU_HELP, self._about),
        )
        registry.add_dock(
            DockContribution(
                "core.jobs",
                strings.DOCK_JOBS,
                lambda: JobsPanel(JobListModel(self._runner), self._runner),
            ),
        )

    def _about(self) -> None:
        QMessageBox.about(
            self._parent_provider(),
            strings.ACTION_ABOUT,
            strings.ABOUT_TEXT.format(version=self._version),
        )
