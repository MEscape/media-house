"""Module registration: how the review orchestrator plugs into the application.

It owns one small database (``review.db``: sessions and their history). Timelines are read through
the timeline builder's catalog; nothing here probes, renders, edits or exports anything.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.review_orchestrator.application.contracts import (
    ReviewCatalog,
    ReviewSessions,
)
from media_house.modules.review_orchestrator.application.orchestrator import ReviewOrchestrator
from media_house.modules.review_orchestrator.application.ports import ReviewStore
from media_house.modules.review_orchestrator.infrastructure.sqlite_store import SqliteReviewStore
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths


class ReviewOrchestratorModule:
    name: str = "review_orchestrator"

    def register(self, container: Container) -> None:
        container.register_factory(
            ReviewStore, lambda c: SqliteReviewStore(c.resolve(AppPaths).data_dir / "review.db")
        )
        container.register_factory(
            ReviewOrchestrator,
            lambda c: ReviewOrchestrator(
                c.resolve(ReviewStore),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(Clock),
                c.resolve(EventPublisher),
            ),
        )
        container.register_factory(ReviewSessions, lambda c: c.resolve(ReviewOrchestrator))
        container.register_factory(ReviewCatalog, lambda c: c.resolve(ReviewOrchestrator))
