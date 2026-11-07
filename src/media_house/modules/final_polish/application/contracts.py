"""The Final Polish PUBLIC API for other modules (import this file only).

``final_polish`` is the last look at the finished presentation, on the exact version the review is
at: the opening and the ending, transitions as a whole, placeholders and unfinished elements, the
final pictures and sound, and whether the review's own decisions are reflected (approved revisions
in, rejected ones not, locks as they were, a person's edits kept). It reuses what quality control
and the audio mix already found and says where each finding came from. It changes nothing and
approves nothing: the report says how ready the final review is, and a person approves the video.

    done = polisher.execute(PolishCommand(review_session_id=...), ctx)
    done.value.report.readiness_status, .remaining_issues, .final_review_package
    PolishCatalog.latest(anchor, timeline_content_hash=...)

Blocking: reads stored results and may sample a few pictures; run as a job.
"""

from typing import Protocol

from media_house.modules.final_polish.application.events import FinalPolishCompleted
from media_house.modules.final_polish.application.polish_final import (
    PolishCommand,
    PolishError,
    PolishResult,
)
from media_house.modules.final_polish.domain.errors import (
    FinalPolishError,
    InvalidPolishDocument,
    NotPolishable,
)
from media_house.modules.final_polish.domain.model import (
    FinalPolishReport,
    FinalReviewPackage,
    Finding,
    ReviewFrame,
    SectionResult,
)
from media_house.modules.final_polish.domain.values import (
    DOCUMENT_TYPE,
    POLISH_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    Readiness,
    SectionState,
    Severity,
    Source,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class FinalPolisher(Protocol):
    def execute(self, command: PolishCommand, ctx: JobContext) -> Result[PolishResult, PolishError]:
        """Check the finished presentation of the review's version and prepare the final review."""
        ...


class PolishCatalog(Protocol):
    """Stored polish reports; nothing is analysed."""

    def latest(
        self,
        anchor_asset_id: str,
        *,
        timeline_id: str | None = None,
        timeline_content_hash: str | None = None,
    ) -> Result[tuple[FinalPolishReport, MediaAssetDto] | None, MediaError]: ...

    def history(
        self, anchor_asset_id: str, timeline_id: str | None = None
    ) -> list[MediaAssetDto]: ...

    def load(
        self, asset_id: str
    ) -> Result[FinalPolishReport, MediaError | InvalidPolishDocument]: ...


__all__ = [
    "DOCUMENT_TYPE",
    "POLISH_VERSION",
    "RESULT_OPERATION",
    "SCHEMA_VERSION",
    "FinalPolishCompleted",
    "FinalPolishError",
    "FinalPolishReport",
    "FinalPolisher",
    "FinalReviewPackage",
    "Finding",
    "InvalidPolishDocument",
    "NotPolishable",
    "PolishCatalog",
    "PolishCommand",
    "PolishError",
    "PolishResult",
    "Readiness",
    "ReviewFrame",
    "SectionResult",
    "SectionState",
    "Severity",
    "Source",
]
