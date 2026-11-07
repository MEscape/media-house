"""The Resolve Export PUBLIC API for other modules (import this file only).

``resolve_export`` is the controlled end of Media House: the final review, a person's approval and
authorization, the export through Resolve (via the render manager), the independent check of the
file, and its delivery. It never exports because automated checks passed: the export starts only
under an authorization a person gave, for exactly the version they approved and the output they were
shown, and it is withdrawn the moment the timeline changes.

    review = approvals.present(session, "youtube_16_9", ctx, ensure_in_resolve=True)
    approvals.approve_final(session, Actor.human("Ada"), review.value.timeline)
    auth = approvals.authorize(session, Actor.human("Ada"), ref, "youtube_16_9")
    done = exporter.execute(
        RunExportCommand(session, auth.value, Actor.human("Ada"), "youtube_16_9", ref), ctx
    )
    exporter.deliver(session, Actor.human("Ada"), done.value.report.export_id)

Blocking: run as jobs. Nothing is uploaded or published.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.resolve_export.application.approvals import (
    ApprovalFailure,
    FinalReview,
    ReadinessReport,
)
from media_house.modules.resolve_export.application.events import (
    DeliveryReady,
    ExportCompleted,
    ExportEvent,
    ExportStarted,
)
from media_house.modules.resolve_export.application.ports import DeliveryStore
from media_house.modules.resolve_export.application.run_export import (
    ExportFailure,
    ExportResult,
    RunExportCommand,
)
from media_house.modules.resolve_export.domain.errors import (
    DeliveryRefused,
    ExportRefused,
    InvalidExportDocument,
    NotReadyForExport,
    ResolveExportError,
)
from media_house.modules.resolve_export.domain.readiness import (
    Blocker,
    ExportReadiness,
    ReadinessFacts,
    assess,
)
from media_house.modules.resolve_export.domain.report import ExportReport
from media_house.modules.resolve_export.domain.values import (
    DOCUMENT_TYPE,
    MODULE_VERSION,
    REPORT_OPERATION,
    SCHEMA_VERSION,
    BlockerKind,
    DeliveryStatus,
    ExportStatus,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ReviewState,
    TimelineRef,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class ExportApprovalService(Protocol):
    """The human side: nothing here exports."""

    def readiness(
        self, session_id: str, profile_id: str
    ) -> Result[ReadinessReport, ApprovalFailure]: ...

    def present(
        self,
        session_id: str,
        profile_id: str,
        ctx: JobContext,
        *,
        ensure_in_resolve: bool = False,
    ) -> Result[FinalReview, ApprovalFailure]: ...

    def approve_final(
        self, session_id: str, actor: Actor, timeline: TimelineRef, *, note: str = ""
    ) -> Result[ReviewState, ApprovalFailure]: ...

    def reject_final(
        self, session_id: str, actor: Actor, timeline: TimelineRef, *, note: str
    ) -> Result[ReviewState, ApprovalFailure]: ...

    def override(
        self, session_id: str, actor: Actor, blocker_id: str, timeline: TimelineRef, *, note: str
    ) -> Result[ReviewState, ApprovalFailure]: ...

    def authorize(
        self, session_id: str, actor: Actor, timeline: TimelineRef, profile_id: str
    ) -> Result[str, ApprovalFailure]: ...

    def approve_delivery(
        self,
        session_id: str,
        actor: Actor,
        export_id: str,
        timeline: TimelineRef,
        *,
        note: str = "",
    ) -> Result[ReviewState, ApprovalFailure]: ...


class FinalExporter(Protocol):
    def execute(
        self, command: RunExportCommand, ctx: JobContext
    ) -> Result[ExportResult, ExportFailure]:
        """Run the authorized export, check the file, record it. A failure is a report."""
        ...

    def retry(
        self, session_id: str, actor: Actor, command: RunExportCommand, ctx: JobContext
    ) -> Result[ExportResult, ExportFailure]: ...

    def cancel(self, render_id: str) -> Result[None, ExportFailure]: ...

    def settle_manual_review(
        self, session_id: str, actor: Actor, export_id: str, *, accept: bool, note: str
    ) -> Result[ExportResult, ExportFailure]: ...

    def deliver(
        self, session_id: str, actor: Actor, export_id: str
    ) -> Result[ExportResult, ExportFailure]:
        """Place a validated (and, if required, approved) file in the delivery folder."""
        ...


class ExportCatalog(Protocol):
    """Stored export reports; nothing is exported."""

    def exports(
        self, anchor_asset_id: str, *, session_id: str | None = None
    ) -> Result[list[tuple[ExportReport, MediaAssetDto]], MediaError]: ...

    def find(
        self, anchor_asset_id: str, export_id: str
    ) -> Result[tuple[ExportReport, MediaAssetDto] | None, MediaError]: ...


__all__ = [
    "DOCUMENT_TYPE",
    "MODULE_VERSION",
    "REPORT_OPERATION",
    "SCHEMA_VERSION",
    "ApprovalFailure",
    "Blocker",
    "BlockerKind",
    "DeliveryReady",
    "DeliveryRefused",
    "DeliveryStatus",
    "DeliveryStore",
    "ExportApprovalService",
    "ExportCatalog",
    "ExportCompleted",
    "ExportEvent",
    "ExportFailure",
    "ExportReadiness",
    "ExportRefused",
    "ExportReport",
    "ExportResult",
    "ExportStarted",
    "ExportStatus",
    "FinalExporter",
    "FinalReview",
    "InvalidExportDocument",
    "NotReadyForExport",
    "ReadinessFacts",
    "ReadinessReport",
    "ResolveExportError",
    "RunExportCommand",
    "assess",
]
