"""The Export Validation PUBLIC API for other modules (import this file only).

``export_validation`` looks at the file that was actually exported, never at what the render said
about itself: it inspects it, decodes it to the end, measures black frames, silence, loudness and
peaks, compares sampled pictures with the approved review render and checks the file against the
approved timeline version and the delivery profile. It changes nothing, re-edits nothing, re-renders
nothing and advances no review: it returns a report with one of four verdicts and, for every
problem, who owns the fix.

    outcome = validator.execute(ValidateExportCommand(output_asset_id=..., approved=ref,
                                                      delivery_id="youtube_16_9"), ctx)
    outcome.value.status            # PASS / WARNING / FAIL / MANUAL_REVIEW_REQUIRED
    outcome.value.review_status     # the same verdict for ReviewSessions.record_export_validation
    outcome.value.report.detected_problems, .not_checked, .recommended_next_action

Blocking (a full decode): run as a job.
"""

from typing import Protocol

from media_house.modules.export_validation.application.catalog import ValidationHistory
from media_house.modules.export_validation.application.events import ExportValidated
from media_house.modules.export_validation.application.validate import (
    IntentionalSpan,
    ValidateExportCommand,
    ValidationFailure,
    ValidationOutcome,
)
from media_house.modules.export_validation.domain.profile import (
    DELIVERY_PROFILES,
    DeliveryProfile,
    InvalidDeliveryProfile,
    UnknownDeliveryProfile,
    get_delivery_profile,
)
from media_house.modules.export_validation.domain.report import (
    ApprovedReference,
    AreaStatus,
    ExportValidationReport,
    Finding,
    InvalidValidationDocument,
    NotChecked,
    ValidationCheck,
    report_from_json,
    report_to_json,
)
from media_house.modules.export_validation.domain.values import (
    REPORT_OPERATION,
    VALIDATOR_VERSION,
    Category,
    Certainty,
    CheckState,
    NextAction,
    Route,
    Severity,
    Status,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class ExportValidator(Protocol):
    def execute(
        self, command: ValidateExportCommand, ctx: JobContext
    ) -> Result[ValidationOutcome, ValidationFailure]:
        """Validate the exported file against the approved timeline and the delivery profile."""
        ...


class ExportValidationCatalog(Protocol):
    def history(
        self, anchor_asset_id: str, *, output_asset_id: str | None = None
    ) -> list[ExportValidationReport]: ...

    def get(
        self, anchor_asset_id: str, export_validation_id: str
    ) -> ExportValidationReport | None: ...

    def latest_for_export(
        self, anchor_asset_id: str, export_id: str
    ) -> ExportValidationReport | None: ...


__all__ = [
    "DELIVERY_PROFILES",
    "REPORT_OPERATION",
    "VALIDATOR_VERSION",
    "ApprovedReference",
    "AreaStatus",
    "Category",
    "Certainty",
    "CheckState",
    "DeliveryProfile",
    "ExportValidated",
    "ExportValidationCatalog",
    "ExportValidationReport",
    "ExportValidator",
    "Finding",
    "IntentionalSpan",
    "InvalidDeliveryProfile",
    "InvalidValidationDocument",
    "NextAction",
    "NotChecked",
    "Route",
    "Severity",
    "Status",
    "UnknownDeliveryProfile",
    "ValidateExportCommand",
    "ValidationCheck",
    "ValidationFailure",
    "ValidationHistory",
    "ValidationOutcome",
    "get_delivery_profile",
    "report_from_json",
    "report_to_json",
]
