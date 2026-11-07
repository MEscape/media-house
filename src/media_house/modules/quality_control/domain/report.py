"""Putting findings together: ordering, summary, the indicator of each area, the report's identity.

The identity of a report is derived from everything it rests on (the timeline's content, the
preview that was looked at, the profile, the version of this module, the checks that ran), never
from the clock: the same inputs give the same report, a changed input gives a new one.
"""

from collections.abc import Sequence
from dataclasses import replace

from media_house.core.domain import json_codec
from media_house.modules.quality_control.domain.facts import TimelineFacts
from media_house.modules.quality_control.domain.model import (
    AreaIndicator,
    CheckRecord,
    ConfidenceMetadata,
    QualityIssue,
    QualityReport,
    SeveritySummary,
)
from media_house.modules.quality_control.domain.profile import PROFILE_VERSION, QualityProfile
from media_house.modules.quality_control.domain.values import (
    QC_VERSION,
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

_AREA_OF: dict[Category, Area] = {
    Category.STRUCTURE: Area.TECHNICAL,
    Category.TECHNICAL_VIDEO: Area.TECHNICAL,
    Category.RESOLVE_SYNC: Area.TECHNICAL,
    Category.VISUAL: Area.VISUAL,
    Category.GRAPHICS: Area.VISUAL,
    Category.CAPTIONS: Area.VISUAL,
    Category.AUDIO: Area.AUDIO,
    Category.EDITING: Area.EDITING,
    Category.CONTINUITY: Area.EDITING,
    Category.STORY: Area.STORY,
}
_SUMMARY = {
    AreaState.OK: "nothing found",
    AreaState.WARNING: "minor findings",
    AreaState.PROBLEM: "serious findings",
    AreaState.NOT_CHECKED: "not checked",
}


def area_of(category: Category) -> Area:
    return _AREA_OF[category]


def dedupe(issues: Sequence[QualityIssue]) -> list[QualityIssue]:
    """One finding per id (the same problem seen twice keeps the more serious, surer one), most
    serious first, then in time."""
    best: dict[str, QualityIssue] = {}
    for issue in issues:
        held = best.get(issue.issue_id)
        if held is None or (SEVERITY_RANK[issue.severity], issue.confidence) > (
            SEVERITY_RANK[held.severity],
            held.confidence,
        ):
            best[issue.issue_id] = issue
    return sorted(
        best.values(),
        key=lambda i: (
            -SEVERITY_RANK[i.severity],
            i.start_frame if i.start_frame is not None else 1 << 60,
            i.issue_id,
        ),
    )


def summarize(issues: Sequence[QualityIssue]) -> SeveritySummary:
    count = {s: sum(1 for i in issues if i.severity is s) for s in Severity}
    return SeveritySummary(
        critical=count[Severity.CRITICAL],
        high=count[Severity.HIGH],
        medium=count[Severity.MEDIUM],
        low=count[Severity.LOW],
        optional=count[Severity.OPTIONAL],
        objective=sum(1 for i in issues if i.basis is Basis.OBJECTIVE),
        subjective=sum(1 for i in issues if i.basis is Basis.SUBJECTIVE),
    )


def indicator(
    area: Area,
    issues: Sequence[QualityIssue],
    scope: Sequence[CheckRecord],
    metrics: tuple[tuple[str, str], ...] = (),
) -> AreaIndicator:
    mine = [i for i in issues if area_of(i.category) is area]
    planned = [c for c in scope if c.area is area]
    ran = [c for c in planned if c.state is CheckState.RAN]
    if not ran:
        state = AreaState.NOT_CHECKED
    elif any(i.severity in (Severity.CRITICAL, Severity.HIGH) for i in mine):
        state = AreaState.PROBLEM
    elif any(i.severity in (Severity.MEDIUM, Severity.LOW) for i in mine):
        state = AreaState.WARNING
    else:
        state = AreaState.OK
    return AreaIndicator(
        area=area,
        state=state,
        issue_count=len(mine),
        summary=f"{_SUMMARY[state]} ({len(ran)} of {len(planned)} checks ran)",
        metrics=metrics,
    )


def technical_status(issues: Sequence[QualityIssue]) -> TechnicalStatus:
    if any(i.blocks_export for i in issues):
        return TechnicalStatus.BLOCKED
    if any(i.severity is Severity.HIGH for i in issues):
        return TechnicalStatus.PROBLEMS
    if any(i.severity in (Severity.MEDIUM, Severity.LOW) for i in issues):
        return TechnicalStatus.WARNINGS
    return TechnicalStatus.OK


def report_identity(
    *,
    timeline_hash: str,
    timeline_id: str,
    preview_asset_id: str,
    profile: QualityProfile,
    inputs: tuple[tuple[str, str], ...],
) -> str:
    """Derived from everything the report rests on, never from the clock or the checks' outcome."""
    digest = json_codec.digest(
        [
            timeline_hash,
            timeline_id,
            preview_asset_id,
            profile,
            PROFILE_VERSION,
            QC_VERSION,
            sorted(inputs),
        ]
    )
    return "qr_" + digest[:20]


def assemble(
    *,
    timeline: TimelineFacts,
    profile: QualityProfile,
    issues: Sequence[QualityIssue],
    scope: Sequence[CheckRecord],
    created_at: str,
    review_session_id: str | None,
    preview_asset_id: str,
    previous: QualityReport | None,
    inputs: tuple[tuple[str, str], ...],
    metrics: dict[Area, tuple[tuple[str, str], ...]],
    warnings: tuple[str, ...],
    limits: tuple[str, ...],
) -> QualityReport:
    found = dedupe(issues)
    report_id = report_identity(
        timeline_hash=timeline.content_hash,
        timeline_id=timeline.timeline_id,
        preview_asset_id=preview_asset_id,
        profile=profile,
        inputs=inputs,
    )
    summary = summarize(found)
    status = technical_status(found)
    indicators = {a: indicator(a, found, scope, metrics.get(a, ())) for a in Area}
    sections = tuple(dict.fromkeys(s for i in found for s in i.sections))
    evidence = tuple(dict.fromkeys(e for i in found for e in i.evidence_assets))
    confident = [i.confidence for i in found]
    same = previous is not None and previous.quality_report_id == report_id
    report = QualityReport(
        quality_report_id=report_id,
        report_version=(previous.report_version if same else (previous.report_version + 1))
        if previous is not None
        else 1,
        supersedes=None
        if previous is None
        else (previous.supersedes if same else previous.quality_report_id),
        project_id=timeline.project_id,
        timeline_id=timeline.timeline_id,
        timeline_version=timeline.version,
        timeline_content_hash=timeline.content_hash,
        anchor_asset_id=timeline.anchor_asset_id,
        review_session_id=review_session_id,
        created_at=created_at,
        quality_control_version=QC_VERSION,
        profile=profile.name,
        profile_version=PROFILE_VERSION,
        preview_asset_id=preview_asset_id,
        analysis_scope=tuple(scope),
        technical_status=status,
        technical_indicators=indicators[Area.TECHNICAL],
        visual_quality_indicators=indicators[Area.VISUAL],
        audio_quality_indicators=indicators[Area.AUDIO],
        editing_quality_indicators=indicators[Area.EDITING],
        story_quality_indicators=indicators[Area.STORY],
        detected_issues=tuple(found),
        severity_summary=summary,
        evidence_references=evidence,
        affected_sections=sections,
        suggested_corrections=tuple(i.issue_id for i in found),
        export_blocking_issues=tuple(i.issue_id for i in found if i.blocks_export),
        warnings=warnings,
        confidence_metadata=ConfidenceMetadata(
            sources=tuple(c.name for c in scope if c.state is CheckState.RAN),
            mean_issue_confidence=round(sum(confident) / len(confident), 3) if confident else None,
            limits=limits,
        ),
        review_status=ReviewStatus.NEEDS_ATTENTION
        if status in (TechnicalStatus.BLOCKED, TechnicalStatus.PROBLEMS)
        else ReviewStatus.READY_FOR_HUMAN_REVIEW,
        inputs=inputs,
        content_hash="",
    )
    return _sealed(report)


def _sealed(report: QualityReport) -> QualityReport:
    """The content hash covers what the report says, not when it was made."""
    body = replace(report, created_at="", content_hash="")
    return replace(report, content_hash=json_codec.digest(body))
