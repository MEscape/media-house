"""Public surface of the style memory: what other modules may read and record.

``StyleGuidance`` answers "what should this generation step know about the creator's style"; the
caller keeps ``Guidance.applied`` and ``Guidance.fingerprint`` with what it generated.
``PreferenceRecorder`` takes the events that teach the memory. A learned rule never overrides a
brief constraint: the consumer holding both enforces that order.
"""

from collections.abc import Sequence
from typing import Protocol

from media_house.modules.style_memory.application.memory import RuleReview
from media_house.modules.style_memory.application.queries import GuidanceQuery
from media_house.modules.style_memory.domain.guidance import Guidance
from media_house.modules.style_memory.domain.model import (
    Exemplar,
    PreferenceEvent,
    StyleProfile,
    StyleRule,
)
from media_house.modules.style_memory.domain.values import (
    Category,
    EventKind,
    Origin,
    Scope,
    Status,
    Strength,
    Task,
)


class StyleGuidance(Protocol):
    def guidance(self, query: GuidanceQuery) -> Guidance: ...


class PreferenceRecorder(Protocol):
    def record(self, event: PreferenceEvent) -> None: ...


class StyleReview(Protocol):
    """ "Was MediaHouse über deinen Stil gelernt hat": list, approve, edit, lock, scope, delete."""

    def list(self, *, status: Status | None = None) -> Sequence[RuleReview]: ...
    def approve(self, rule_id: str, *, strength: Strength | None = None) -> object: ...
    def reject(self, rule_id: str) -> object: ...
    def edit(
        self,
        rule_id: str,
        *,
        text: str | None = None,
        category: Category | None = None,
        strength: Strength | None = None,
        scope: Scope | None = None,
        scope_key: str | None = None,
    ) -> object: ...
    def set_locked(self, rule_id: str, locked: bool) -> object: ...
    def delete(self, rule_id: str) -> object: ...


__all__ = [
    "Category",
    "EventKind",
    "Exemplar",
    "Guidance",
    "GuidanceQuery",
    "Origin",
    "PreferenceEvent",
    "PreferenceRecorder",
    "RuleReview",
    "Scope",
    "Status",
    "Strength",
    "StyleGuidance",
    "StyleProfile",
    "StyleReview",
    "StyleRule",
    "Task",
]
