"""Contribution descriptors and the registry the main window is built from.

A module contributes by implementing :class:`UiContributor`. The shell iterates
the registry and never references any module directly.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from media_house.shared.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class ActionContribution:
    id: str
    text: str
    menu: str
    callback: Callable[[], None]
    shortcut: str | None = None
    status_tip: str = ""


@dataclass(frozen=True, slots=True)
class ViewContribution:
    """A page in the central area, listed in the navigation panel. Created lazily."""

    id: str
    title: str
    factory: Callable[[], QWidget]


@dataclass(frozen=True, slots=True)
class DockContribution:
    id: str
    title: str
    factory: Callable[[], QWidget]
    area: Qt.DockWidgetArea = Qt.DockWidgetArea.BottomDockWidgetArea


class UiContributor(Protocol):
    def contribute(self, registry: "UiRegistry") -> None: ...


class UiRegistry:
    def __init__(self) -> None:
        self._actions: list[ActionContribution] = []
        self._views: list[ViewContribution] = []
        self._docks: list[DockContribution] = []

    @property
    def actions(self) -> tuple[ActionContribution, ...]:
        return tuple(self._actions)

    @property
    def views(self) -> tuple[ViewContribution, ...]:
        return tuple(self._views)

    @property
    def docks(self) -> tuple[DockContribution, ...]:
        return tuple(self._docks)

    def add_action(self, action: ActionContribution) -> None:
        self._add(self._actions, action)

    def add_view(self, view: ViewContribution) -> None:
        self._add(self._views, view)

    def add_dock(self, dock: DockContribution) -> None:
        self._add(self._docks, dock)

    @staticmethod
    def _add[C: (ActionContribution, ViewContribution, DockContribution)](
        target: list[C],
        item: C,
    ) -> None:
        if any(existing.id == item.id for existing in target):
            raise ConfigurationError(f"UI contribution id '{item.id}' is already registered")
        target.append(item)
