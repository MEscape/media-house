"""Two reports of the same timeline compared: what is fixed, what is still there, what is new."""

from dataclasses import dataclass

from media_house.modules.quality_control.domain.model import QualityIssue, QualityReport
from media_house.modules.quality_control.domain.values import SEVERITY_RANK, Severity


@dataclass(frozen=True, slots=True)
class ReportComparison:
    before_report_id: str
    after_report_id: str
    #: Findings of the earlier report that are gone.
    resolved: tuple[QualityIssue, ...]
    #: Findings still there at the same seriousness or better.
    persisting: tuple[QualityIssue, ...]
    #: Findings still there and more serious than before.
    worsened: tuple[QualityIssue, ...]
    #: Findings that were not there before.
    introduced: tuple[QualityIssue, ...]

    @property
    def regressions(self) -> tuple[QualityIssue, ...]:
        """New or worsened findings that matter (not opinions)."""
        return tuple(
            i
            for i in (*self.introduced, *self.worsened)
            if SEVERITY_RANK[i.severity] >= SEVERITY_RANK[Severity.MEDIUM]
        )


def compare_reports(before: QualityReport, after: QualityReport) -> ReportComparison:
    old = {i.fingerprint: i for i in before.detected_issues}
    new = {i.fingerprint: i for i in after.detected_issues}
    resolved = tuple(i for fp, i in old.items() if fp not in new)
    introduced = tuple(i for fp, i in new.items() if fp not in old)
    worsened = tuple(
        i
        for fp, i in new.items()
        if fp in old and SEVERITY_RANK[i.severity] > SEVERITY_RANK[old[fp].severity]
    )
    persisting = tuple(i for fp, i in new.items() if fp in old and i not in worsened)
    return ReportComparison(
        before.quality_report_id,
        after.quality_report_id,
        resolved,
        persisting,
        worsened,
        introduced,
    )
