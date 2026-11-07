"""A quality report: what was checked, what was found, on which exact version of the timeline."""

from dataclasses import dataclass, field

from media_house.modules.quality_control.domain.values import (
    SCHEMA_VERSION,
    Area,
    AreaState,
    Basis,
    Category,
    CheckState,
    ReviewStatus,
    Severity,
    TechnicalStatus,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityIssue:
    """One finding with the evidence for it. Frames are timeline frames; none is invented."""

    issue_id: str
    #: Stable across analyses of the same problem (same kind, same place), so a later report can
    #: tell "still there" from "new" and a rejected suggestion is not made again.
    fingerprint: str
    category: Category
    code: str
    severity: Severity
    #: How sure the check is that this is a real problem, [0, 1].
    confidence: float
    basis: Basis
    description: str
    why_it_matters: str
    suggested_correction: str
    #: The module that owns the correction (a hint; ``revision_engine`` routes).
    owner_hint: str
    detection_method: str
    blocks_export: bool = False
    start_frame: int | None = None
    end_frame: int | None = None
    affected_clips: tuple[str, ...] = ()
    affected_tracks: tuple[str, ...] = ()
    source_assets: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    #: What was measured or seen, in words.
    evidence: tuple[str, ...] = ()
    #: The same as named numbers and ids a later step can act on (``gap_frames`` ...).
    measurements: tuple[tuple[str, str], ...] = ()
    #: Library assets (extracted frames) of the exact version that was analysed.
    evidence_assets: tuple[str, ...] = ()
    #: The upstream analyses this rests on (a continuity analysis id, a mix result id...).
    analysis_refs: tuple[str, ...] = ()
    #: Which module found it first when it is carried over (``quality_control`` otherwise).
    origin: str = "quality_control"


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckRecord:
    """One check of the analysis: it ran, or why it did not."""

    name: str
    area: Area
    state: CheckState
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class AreaIndicator:
    area: Area
    state: AreaState
    issue_count: int
    summary: str
    #: Named numbers behind the verdict (shot count, loudness, ...), written as text.
    metrics: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SeveritySummary:
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    optional: int = 0
    objective: int = 0
    subjective: int = 0

    @property
    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low + self.optional

    def count(self, severity: Severity) -> int:
        return {
            Severity.CRITICAL: self.critical,
            Severity.HIGH: self.high,
            Severity.MEDIUM: self.medium,
            Severity.LOW: self.low,
            Severity.OPTIONAL: self.optional,
        }[severity]


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfidenceMetadata:
    #: Evidence the analysis had (a render was looked at, a mix result was read...).
    sources: tuple[str, ...]
    #: Mean confidence of the issues, ``None`` without issues.
    mean_issue_confidence: float | None
    #: What this report cannot say.
    limits: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityReport:
    quality_report_id: str
    #: 1 for the first report of a timeline, then counting up; ``supersedes`` names the one before.
    report_version: int
    supersedes: str | None
    project_id: str | None
    timeline_id: str
    timeline_version: int
    #: The content hash of the analysed version: evidence belongs to exactly this.
    timeline_content_hash: str
    anchor_asset_id: str
    review_session_id: str | None
    created_at: str
    quality_control_version: int
    profile: str
    profile_version: int
    #: The preview render that was looked at (empty: the timeline alone was analysed).
    preview_asset_id: str
    analysis_scope: tuple[CheckRecord, ...]
    technical_status: TechnicalStatus
    visual_quality_indicators: AreaIndicator
    audio_quality_indicators: AreaIndicator
    editing_quality_indicators: AreaIndicator
    story_quality_indicators: AreaIndicator
    technical_indicators: AreaIndicator
    detected_issues: tuple[QualityIssue, ...]
    severity_summary: SeveritySummary
    evidence_references: tuple[str, ...]
    affected_sections: tuple[str, ...]
    #: Issue ids with a correction to suggest, most serious first.
    suggested_corrections: tuple[str, ...]
    export_blocking_issues: tuple[str, ...]
    warnings: tuple[str, ...]
    confidence_metadata: ConfidenceMetadata
    review_status: ReviewStatus
    #: Every input this report rests on, as named references.
    inputs: tuple[tuple[str, str], ...]
    content_hash: str
    #: A reminder that travels with the data: a report is an assessment, never an approval.
    approval: str = "not_approved"
    schema_version: int = field(default=SCHEMA_VERSION)

    def issue(self, issue_id: str) -> QualityIssue | None:
        return next((i for i in self.detected_issues if i.issue_id == issue_id), None)

    def issues_of(self, severity: Severity) -> tuple[QualityIssue, ...]:
        return tuple(i for i in self.detected_issues if i.severity is severity)

    def blocking(self) -> tuple[QualityIssue, ...]:
        return tuple(i for i in self.detected_issues if i.blocks_export)
