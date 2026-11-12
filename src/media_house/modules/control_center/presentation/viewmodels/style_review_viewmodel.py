"""Dein Stil: what the memory learned, for the person to approve, change, lock or delete."""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from media_house.modules.style_memory.application.contracts import Strength, StyleReview
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.shared.errors import Err


class StyleReviewViewModel(QObject):
    rules_changed = Signal(object)  # Sequence[RuleReview]
    message_changed = Signal(str)

    def __init__(
        self, review: StyleReview, jobs: UiJobRunner, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._review = review
        self._jobs = jobs

    def load(self) -> None:
        self._jobs.submit(
            "Stil laden",
            lambda _c: self._review.list(),
            on_success=lambda rows: self.rules_changed.emit(rows),
        )

    def _change(self, name: str, op: Callable[[], Any]) -> None:
        def done(result: Any) -> None:
            if isinstance(result, Err):
                self.message_changed.emit(result.error.user_message)
            self.load()

        self._jobs.submit(name, lambda _c: op(), on_success=done)

    def approve(self, rule_id: str, mandatory: bool = False) -> None:
        strength = Strength.MANDATORY if mandatory else None
        self._change("Regel übernehmen", lambda: self._review.approve(rule_id, strength=strength))

    def reject(self, rule_id: str) -> None:
        self._change("Regel ablehnen", lambda: self._review.reject(rule_id))

    def toggle_lock(self, rule_id: str, locked: bool) -> None:
        self._change("Regel sperren", lambda: self._review.set_locked(rule_id, locked))

    def delete(self, rule_id: str) -> None:
        self._change("Regel löschen", lambda: self._review.delete(rule_id))
