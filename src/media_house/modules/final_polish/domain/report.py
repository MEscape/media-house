"""Putting the sections together: remaining issues, recommendations, readiness, the package a person
reviews, and the report's identity (derived from what it rests on, never from the clock)."""

from dataclasses import replace

from media_house.core.domain import json_codec
from media_house.modules.final_polish.domain import checks
from media_house.modules.final_polish.domain.facts import (
    FrameFacts,
    MixFacts,
    PolishTimeline,
    QualityFacts,
    ReviewFacts,
    RevisionFacts,
)
from media_house.modules.final_polish.domain.model import (
    FinalPolishReport,
    FinalReviewPackage,
    Finding,
    ReviewFrame,
    SectionResult,
)
from media_house.modules.final_polish.domain.values import (
    POLISH_VERSION,
    Readiness,
    SectionState,
    Severity,
)

_RANK = {Severity.PROBLEM: 2, Severity.WARNING: 1, Severity.NOTE: 0}


def report_identity(
    *,
    timeline_hash: str,
    timeline_id: str,
    quality_report_id: str,
    preview_asset_id: str,
    inputs: tuple[tuple[str, str], ...],
) -> str:
    return (
        "fp_"
        + json_codec.digest(
            [
                timeline_hash,
                timeline_id,
                quality_report_id,
                preview_asset_id,
                POLISH_VERSION,
                sorted(inputs),
            ]
        )[:20]
    )


def readiness_of(sections: tuple[SectionResult, ...]) -> Readiness:
    states = {s.state for s in sections}
    if SectionState.PROBLEM in states:
        return Readiness.NOT_READY
    if SectionState.WARNING in states:
        return Readiness.READY_WITH_WARNINGS
    return Readiness.READY_FOR_FINAL_REVIEW


def remaining(sections: tuple[SectionResult, ...]) -> tuple[Finding, ...]:
    seen: dict[tuple[str, str, int | None], Finding] = {}
    for section in sections:
        for finding in section.findings:
            if finding.severity is Severity.NOTE:
                continue
            key = (finding.code, finding.reference, finding.start_frame)
            held = seen.get(key)
            if held is None or _RANK[finding.severity] > _RANK[held.severity]:
                seen[key] = finding
    return tuple(
        sorted(
            seen.values(),
            key=lambda f: (
                -_RANK[f.severity],
                f.start_frame if f.start_frame is not None else 1 << 60,
            ),
        )
    )


def recommendations(found: tuple[Finding, ...], readiness: Readiness) -> tuple[str, ...]:
    advice = [
        f"Fix before approving: {f.message}" for f in found if f.severity is Severity.PROBLEM
    ][:5]
    advice += [f"Look at: {f.message}" for f in found if f.severity is Severity.WARNING][:5]
    advice.append(
        "Watch the whole video in the editing application, the opening and the ending twice."
        if readiness is not Readiness.NOT_READY
        else "Run another round of review: the video is not ready for a final approval yet."
    )
    return tuple(advice)


def assemble(
    *,
    timeline: PolishTimeline,
    quality: QualityFacts | None,
    mix: MixFacts | None,
    frames: tuple[FrameFacts, ...],
    revisions: RevisionFacts,
    review: ReviewFacts | None,
    preview_asset_id: str,
    created_at: str,
    inputs: tuple[tuple[str, str], ...],
    warnings: tuple[str, ...],
) -> FinalPolishReport:
    empty = QualityFacts("", (), ())
    q = quality or empty
    sections = (
        checks.visual_consistency(q),
        checks.graphic_consistency(q),
        checks.caption_consistency(q),
        checks.transition_review(timeline, q),
        checks.audio_refinement(timeline, q, mix),
        checks.opening_and_ending(timeline),
        checks.final_frames(timeline, frames),
        checks.placeholder_detection(timeline),
        checks.presentation_validation(timeline, quality, revisions, review),
    )
    readiness = readiness_of(sections)
    found = remaining(sections)
    session = review.session_id if review else None
    review_version = review.review_version if review else 0
    package = FinalReviewPackage(
        timeline_id=timeline.timeline_id,
        timeline_version=timeline.version,
        timeline_content_hash=timeline.content_hash,
        review_version=review_version,
        resolve_project=review.resolve_project if review else "",
        resolve_timeline=review.resolve_timeline if review else "",
        quality_report_id=q.report_id,
        frames=tuple(
            ReviewFrame(label=f.label, frame=f.frame, asset_id=f.asset_id) for f in frames
        ),
        outstanding=tuple(f.message for f in found[:20]),
        sections=tuple((s.name, s.state.value) for s in sections),
    )
    report = FinalPolishReport(
        final_polish_report_id=report_identity(
            timeline_hash=timeline.content_hash,
            timeline_id=timeline.timeline_id,
            quality_report_id=q.report_id,
            preview_asset_id=preview_asset_id,
            inputs=inputs,
        ),
        project_id=timeline.project_id,
        timeline_id=timeline.timeline_id,
        timeline_version=timeline.version,
        timeline_content_hash=timeline.content_hash,
        anchor_asset_id=timeline.anchor_asset_id,
        review_session_id=session,
        review_version=review_version,
        created_at=created_at,
        polish_version=POLISH_VERSION,
        visual_consistency_results=sections[0],
        graphic_consistency_results=sections[1],
        caption_consistency_results=sections[2],
        transition_review=sections[3],
        audio_refinement_results=sections[4],
        opening_and_ending_results=sections[5],
        final_frame_results=sections[6],
        placeholder_detection_results=sections[7],
        presentation_validation=sections[8],
        remaining_issues=found,
        recommendations=recommendations(found, readiness),
        approved_revision_references=review.approved_revisions if review else (),
        readiness_status=readiness,
        final_review_package=package,
        quality_report_id=q.report_id,
        preview_asset_id=preview_asset_id,
        inputs=inputs,
        warnings=warnings,
        content_hash="",
    )
    return replace(
        report, content_hash=json_codec.digest(replace(report, created_at="", content_hash=""))
    )
