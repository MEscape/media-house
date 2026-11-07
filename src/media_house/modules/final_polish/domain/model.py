"""A final polish report: whether the finished edit looks, sounds and reads as complete, and what
a person should look at before approving it."""

from dataclasses import dataclass, field

from media_house.modules.final_polish.domain.values import (
    SCHEMA_VERSION,
    Readiness,
    SectionState,
    Severity,
    Source,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    code: str
    severity: Severity
    message: str
    source: Source = Source.FINAL_POLISH
    start_frame: int | None = None
    end_frame: int | None = None
    clips: tuple[str, ...] = ()
    #: The finding of another module this restates (a quality issue id).
    reference: str = ""
    evidence_assets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionResult:
    name: str
    state: SectionState
    findings: tuple[Finding, ...] = ()
    metrics: tuple[tuple[str, str], ...] = ()
    #: Why nothing was checked (a missing preview, no mix result).
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewFrame:
    """A representative picture of the final version, for the person who will watch it."""

    label: str
    frame: int
    asset_id: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class FinalReviewPackage:
    """What a person needs to review the final version in the editing application."""

    timeline_id: str
    timeline_version: int
    timeline_content_hash: str
    review_version: int
    #: The project and timeline in the editing application, when the timeline was carried out there.
    resolve_project: str
    resolve_timeline: str
    quality_report_id: str
    frames: tuple[ReviewFrame, ...]
    outstanding: tuple[str, ...]
    sections: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class FinalPolishReport:
    final_polish_report_id: str
    project_id: str | None
    timeline_id: str
    timeline_version: int
    timeline_content_hash: str
    anchor_asset_id: str
    review_session_id: str | None
    review_version: int
    created_at: str
    polish_version: int
    visual_consistency_results: SectionResult
    graphic_consistency_results: SectionResult
    caption_consistency_results: SectionResult
    transition_review: SectionResult
    audio_refinement_results: SectionResult
    opening_and_ending_results: SectionResult
    final_frame_results: SectionResult
    placeholder_detection_results: SectionResult
    presentation_validation: SectionResult
    remaining_issues: tuple[Finding, ...]
    recommendations: tuple[str, ...]
    approved_revision_references: tuple[str, ...]
    readiness_status: Readiness
    final_review_package: FinalReviewPackage
    quality_report_id: str
    preview_asset_id: str
    inputs: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]
    content_hash: str
    #: A reminder that travels with the data: a report is an assessment, never an approval.
    approval: str = "not_approved"
    schema_version: int = field(default=SCHEMA_VERSION)

    def sections(self) -> tuple[SectionResult, ...]:
        return (
            self.visual_consistency_results,
            self.graphic_consistency_results,
            self.caption_consistency_results,
            self.transition_review,
            self.audio_refinement_results,
            self.opening_and_ending_results,
            self.final_frame_results,
            self.placeholder_detection_results,
            self.presentation_validation,
        )
