"""Builds the menu bar from action contributions."""

from collections.abc import Sequence

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMenu

from media_house.presentation import strings
from media_house.presentation.extension import ActionContribution

_LEADING = (strings.MENU_FILE, strings.MENU_VIEW)


def build_menus(
    window: QMainWindow,
    actions: Sequence[ActionContribution],
    *,
    required: Sequence[str] = (),
) -> dict[str, QMenu]:
    """Create menus in a stable order: File, View, others alphabetically, Help.

    ``required`` names menus that must exist even if no action targets them.
    """
    names = list(dict.fromkeys([*required, *(action.menu for action in actions)]))
    middle = sorted(n for n in names if n not in _LEADING and n != strings.MENU_HELP)
    ordered = [n for n in _LEADING if n in names] + middle
    if strings.MENU_HELP in names:
        ordered.append(strings.MENU_HELP)

    menus = {name: window.menuBar().addMenu(f"&{name}") for name in ordered}
    for contribution in actions:
        action = QAction(contribution.text, window)
        action.setObjectName(contribution.id)
        action.setStatusTip(contribution.status_tip)
        if contribution.shortcut:
            action.setShortcut(QKeySequence(contribution.shortcut))
        action.triggered.connect(lambda _checked=False, cb=contribution.callback: cb())
        menus[contribution.menu].addAction(action)
    return menus
