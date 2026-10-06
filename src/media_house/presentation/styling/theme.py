"""Theme tokens + one stylesheet template. Widgets never carry their own stylesheets."""

from dataclasses import asdict, dataclass
from importlib import resources
from string import Template

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from media_house.shared.configuration import Theme


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    background: str
    surface: str
    text: str
    text_muted: str
    accent: str
    accent_text: str
    border: str


LIGHT = ThemeTokens("#f6f7f9", "#ffffff", "#1b1f24", "#57606a", "#0b5fff", "#ffffff", "#c9d1d9")
DARK = ThemeTokens("#14171c", "#1d2127", "#e6e8eb", "#9aa4b0", "#5b9bff", "#0b1320", "#323943")


class ThemeManager:
    def __init__(self, app: QApplication, initial: Theme) -> None:
        self._app = app
        self._template = Template(
            resources.files("media_house.presentation")
            .joinpath("resources/styles/base.qss")
            .read_text(encoding="utf-8"),
        )
        self._theme = initial
        self._resolved = LIGHT
        app.setStyle("Fusion")
        self.apply(initial)

    @property
    def theme(self) -> Theme:
        return self._theme

    @property
    def resolved_tokens(self) -> ThemeTokens:
        return self._resolved

    def apply(self, theme: Theme) -> None:
        self._theme = theme
        self._resolved = self._resolve(theme)
        self._app.setStyleSheet(self._template.substitute(asdict(self._resolved)))

    def _resolve(self, theme: Theme) -> ThemeTokens:
        if theme is Theme.DARK:
            return DARK
        if theme is Theme.LIGHT:
            return LIGHT
        scheme = self._app.styleHints().colorScheme()
        return DARK if scheme == Qt.ColorScheme.Dark else LIGHT
