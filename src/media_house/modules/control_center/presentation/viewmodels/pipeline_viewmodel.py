"""Pipeline overview state: the static, sourced module catalogue and the selected module."""

from PySide6.QtCore import QObject, Signal

from media_house.modules.control_center.application.catalogue import (
    LAYERS,
    LayerEntry,
    ModuleEntry,
    all_modules,
    find_module,
)


class PipelineViewModel(QObject):
    module_selected = Signal(object)  # ModuleEntry | None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._selected: ModuleEntry | None = None

    @property
    def layers(self) -> tuple[LayerEntry, ...]:
        return LAYERS

    @property
    def module_count(self) -> int:
        return len(all_modules())

    @property
    def selected(self) -> ModuleEntry | None:
        return self._selected

    def select(self, name: str | None) -> None:
        found = find_module(name) if name else None
        if found != self._selected:
            self._selected = found
            self.module_selected.emit(found)
