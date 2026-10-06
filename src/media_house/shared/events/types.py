"""Event base types.

Three kinds of "event" exist in Media-House, and they must not be confused:

* **Domain events** - something that happened in the domain model
  (``WorkspaceCreated``). Recorded by aggregates, published by use cases.
* **Application events** - something that happened in a workflow
  (``WorkspaceValidationCompleted``). Published by use cases / jobs.
* **UI events** - Qt signals. They never travel over the :class:`EventBus`.

Neither base type carries a clock: ``occurred_at`` is always supplied by the
caller so the domain stays deterministic.
"""

from dataclasses import dataclass, field
from datetime import datetime

from media_house.shared.types.ids import new_id


@dataclass(frozen=True, kw_only=True, slots=True)
class DomainEvent:
    occurred_at: datetime
    event_id: str = field(default_factory=new_id)


@dataclass(frozen=True, kw_only=True, slots=True)
class ApplicationEvent:
    occurred_at: datetime
    event_id: str = field(default_factory=new_id)
