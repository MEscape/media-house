"""The review store: sessions and their history. One port, SQLite behind it."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from media_house.modules.review_orchestrator.domain.session import AuditEntry, ReviewSession


@dataclass(frozen=True, slots=True)
class StoredSession:
    session: ReviewSession
    #: Counts the writes of this session; a save names the one it read (optimistic concurrency).
    revision: int


class ReviewStore(Protocol):
    def create(self, session: ReviewSession, entries: Sequence[AuditEntry]) -> None:
        """Store a new session. Raises ``ConflictError`` if its id exists."""
        ...

    def save(
        self,
        session: ReviewSession,
        entries: Sequence[AuditEntry],
        *,
        expected_revision: int,
    ) -> int:
        """Store the next state and its audit entries in one step; returns the new revision.
        Raises ``ConflictError`` if the session was written since ``expected_revision``."""
        ...

    def get(self, session_id: str) -> StoredSession | None: ...

    def find(
        self, anchor_asset_id: str, *, timeline_id: str | None = None, active_only: bool = False
    ) -> list[ReviewSession]:
        """Sessions of a recording, newest first."""
        ...

    def entries(self, session_id: str, *, after: int = 0, limit: int = 500) -> list[AuditEntry]:
        """The history of a session, oldest first, ``sequence`` assigned."""
        ...
