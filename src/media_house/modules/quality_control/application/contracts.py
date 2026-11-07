"""The Quality Control PUBLIC API for other modules (import this file only).

``quality_control`` looks at a built timeline (and, when it is given one, at a preview render of it)
and reports what is wrong, with the evidence: structure, picture, sound, captions and graphics, the
edit, and how it compares with the plans upstream. It changes nothing, decides nothing creative and
approves nothing.

    result = analyzer.execute(AnalyzeQualityCommand(anchor_asset_id=..., preview_asset_id=...), ctx)
    report = result.value.report            # immutable, bound to one exact timeline version
    report.detected_issues, report.export_blocking_issues, report.severity_summary
    QualityCatalog.latest(anchor, timeline_id=...)      # stored reports, nothing is analysed
    compare_reports(before, after)                      # fixed / still there / new

Blocking: measures the preview file; run as a job.
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.quality_control.application.analyze_quality import (
    AnalyzeQualityCommand,
    QualityError,
    QualityResult,
    StaleStep,
)
from media_house.modules.quality_control.application.events import QualityControlCompleted
from media_house.modules.quality_control.application.ports import LiveChanges, LiveTimelineProbe
from media_house.modules.quality_control.domain.compare import ReportComparison, compare_reports
from media_house.modules.quality_control.domain.errors import (
    InvalidQualityDocument,
    NothingToAnalyze,
    QualityControlError,
)
from media_house.modules.quality_control.domain.model import (
    AreaIndicator,
    CheckRecord,
    ConfidenceMetadata,
    QualityIssue,
    QualityReport,
    SeveritySummary,
)
from media_house.modules.quality_control.domain.profile import (
    PROFILE_VERSION,
    PROFILES,
    QualityProfile,
)
from media_house.modules.quality_control.domain.values import (
    DOCUMENT_TYPE,
    QC_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    SEVERITY_RANK,
    Area,
    AreaState,
    Basis,
    Category,
    CheckState,
    ReviewStatus,
    Severity,
    TechnicalStatus,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class QualityAnalyzer(Protocol):
    def execute(
        self, command: AnalyzeQualityCommand, ctx: JobContext
    ) -> Result[QualityResult, QualityError]:
        """Check the timeline (and its preview), store the report, report to the review."""
        ...


class QualityCatalog(Protocol):
    """Stored reports; nothing is analysed."""

    def latest(
        self,
        anchor_asset_id: str,
        *,
        timeline_id: str | None = None,
        timeline_content_hash: str | None = None,
    ) -> Result[tuple[QualityReport, MediaAssetDto] | None, MediaError]: ...

    def history(
        self, anchor_asset_id: str, timeline_id: str | None = None
    ) -> list[MediaAssetDto]: ...

    def load(self, asset_id: str) -> Result[QualityReport, MediaError | InvalidQualityDocument]: ...


__all__ = [
    "DOCUMENT_TYPE",
    "PROFILES",
    "PROFILE_VERSION",
    "QC_VERSION",
    "RESULT_OPERATION",
    "SCHEMA_VERSION",
    "SEVERITY_RANK",
    "AnalyzeQualityCommand",
    "Area",
    "AreaIndicator",
    "AreaState",
    "Basis",
    "Category",
    "CheckRecord",
    "CheckState",
    "ConfidenceMetadata",
    "InvalidQualityDocument",
    "LiveChanges",
    "LiveTimelineProbe",
    "NothingToAnalyze",
    "QualityAnalyzer",
    "QualityCatalog",
    "QualityControlCompleted",
    "QualityControlError",
    "QualityError",
    "QualityIssue",
    "QualityProfile",
    "QualityReport",
    "QualityResult",
    "ReportComparison",
    "ReviewStatus",
    "Severity",
    "SeveritySummary",
    "StaleStep",
    "TechnicalStatus",
    "compare_reports",
]
