"""The one place that knows both the other modules' models and the final polish's own facts."""

from fractions import Fraction

from media_house.modules.audio_mix_engine.application.contracts import DELIVERIES, MixResult
from media_house.modules.final_polish.domain import facts as f
from media_house.modules.quality_control.application.contracts import QualityReport
from media_house.modules.review_orchestrator.application.contracts import (
    ApprovalKind,
    ReviewSession,
    SectionStatus,
    Verdict,
)
from media_house.modules.revision_engine.application.contracts import (
    EDITOR_PREFIX,
    ChangeStatus,
    PlanStatus,
    RevisionPlan,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimeline


def _seconds(value: object) -> Fraction:
    return Fraction(int(getattr(value, "numerator", 0)), int(getattr(value, "denominator", 1)))


def _origin(provenance: str, element_id: str, editors: dict[str, str]) -> str:
    if provenance in ("ai", "system"):
        return "media_house"
    if provenance.startswith("human"):
        return (
            "automated_revision"
            if editors.get(element_id, "").startswith(EDITOR_PREFIX)
            else "human_manual"
        )
    return "unknown"


def polish_timeline(timeline: BuiltTimeline) -> f.PolishTimeline:
    editors = {r.element_id: r.editor for r in timeline.edit_log}
    rate = timeline.settings.frame_rate
    return f.PolishTimeline(
        timeline_id=timeline.timeline_id,
        version=timeline.build.build_version,
        content_hash=timeline.build.build_hash,
        anchor_asset_id=timeline.anchor_asset_id,
        project_id=timeline.project_id,
        fps=Fraction(rate.numerator, rate.denominator),
        duration_frames=timeline.settings.duration_frames,
        clips=tuple(
            f.PolishClip(
                clip_id=c.clip_id,
                kind=c.kind.value,
                name=c.name,
                start_frame=c.start_frame,
                end_frame=c.end_frame,
                enabled=c.enabled,
                unresolved=c.state.value == "unresolved",
                origin=_origin(c.provenance.value, c.clip_id, editors),
                section_id=c.section_id,
            )
            for c in timeline.video_clips()
        ),
        sounds=tuple(
            f.PolishSound(
                clip_id=c.clip_id,
                role=c.role.value,
                start=_seconds(c.timeline_start),
                end=_seconds(c.timeline_end),
                fade_out=_seconds(c.fade_out),
                enabled=c.enabled,
            )
            for c in timeline.audio_clips()
        ),
        transitions=tuple(
            f.PolishTransition(
                t.transition_id,
                t.mode.value,
                t.cut_frame,
                t.start_frame,
                t.end_frame,
                t.from_clip,
                t.to_clip,
            )
            for t in timeline.transitions
        ),
        markers=tuple(
            f.PolishMarker(m.marker_id, m.kind, m.frame, m.name, m.note) for m in timeline.markers
        ),
        cues=tuple(
            f.PolishCue(c.index, c.start_frame, c.end_frame, c.text)
            for c in (timeline.subtitles.cues if timeline.subtitles else ())
        ),
        sections=tuple(
            f.PolishSection(s.section_id, s.name, s.start_frame, s.end_frame)
            for s in sorted(timeline.sections, key=lambda s: s.order)
        ),
    )


def quality_facts(report: QualityReport) -> f.QualityFacts:
    return f.QualityFacts(
        report_id=report.quality_report_id,
        issues=tuple(
            f.QualityIssueFacts(
                issue_id=i.issue_id,
                code=i.code,
                severity=i.severity.value,
                basis=i.basis.value,
                description=i.description,
                blocks_export=i.blocks_export,
                start_frame=i.start_frame,
                end_frame=i.end_frame,
                clips=i.affected_clips,
                evidence_assets=i.evidence_assets,
            )
            for i in report.detected_issues
        ),
        not_run=tuple(c.name for c in report.analysis_scope if c.state.value != "ran"),
    )


def mix_facts(result: MixResult) -> f.MixFacts:
    delivery = next((d for d in DELIVERIES if d.key == result.delivery), None)
    measured = result.mix.measurement.integrated_lufs if result.mix else None
    return f.MixFacts(
        state=result.state.value,
        delivery=result.delivery,
        errors=tuple(
            (i.code, i.severity.value, i.message)
            for i in result.issues
            if i.severity.value in ("error", "critical") and i.certainty.value != "intentional"
        ),
        integrated_lufs=measured,
        target_lufs=delivery.integrated_lufs if delivery else None,
        tolerance_lu=delivery.tolerance_lu if delivery else None,
    )


def revision_facts(plans: list[RevisionPlan]) -> f.RevisionFacts:
    ordered = sorted(plans, key=lambda p: p.created_at)
    executed = tuple(
        p.revision_plan_id
        for p in ordered
        if p.execution_status in (PlanStatus.EXECUTED, PlanStatus.PARTIAL)
    )
    pending = tuple(
        p.revision_plan_id for p in ordered if p.execution_status is PlanStatus.APPROVED
    )
    rejected_applied = tuple(
        element
        for p in ordered
        for c in p.proposed_changes
        if c.status is ChangeStatus.REJECTED
        for element in c.changed_elements
    )
    return f.RevisionFacts(executed, rejected_applied, pending)


def review_facts(session: ReviewSession) -> f.ReviewFacts:
    drifted = tuple(
        s.section_id for s in session.sections if s.status is SectionStatus.LOCKED and s.drift
    )
    approved = tuple(
        a.subject_id
        for a in session.approvals
        if a.kind is ApprovalKind.REVISION
        and a.verdict is Verdict.APPROVED
        and a.consumed_by_version is not None
    )
    return f.ReviewFacts(
        session_id=session.session_id,
        review_version=session.review_version,
        session_content_hash=session.timeline.content_hash,
        locked_sections=session.locked_sections(),
        drifted_locks=drifted,
        approved_revisions=approved,
        resolve_project=session.resolve_project_ref,
        resolve_timeline=session.resolve_timeline_ref,
    )
