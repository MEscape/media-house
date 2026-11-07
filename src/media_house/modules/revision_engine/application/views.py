"""The one place that knows both the other modules' models and the revision engine's own facts."""

from fractions import Fraction

from media_house.modules.quality_control.application.contracts import QualityReport
from media_house.modules.review_orchestrator.application.contracts import (
    Operation,
    ReviewSession,
    SectionStatus,
)
from media_house.modules.revision_engine.domain.facts import (
    ClipView,
    IssueFacts,
    ReviewView,
    SectionView,
    SoundView,
    TimelineView,
    TransitionView,
)
from media_house.modules.revision_engine.domain.values import Origin
from media_house.modules.timeline_builder.application.contracts import BuiltTimeline

EDITOR_PREFIX = "revision_engine:"


def _seconds(value: object) -> Fraction:
    numerator = getattr(value, "numerator", 0)
    denominator = getattr(value, "denominator", 1)
    return Fraction(int(numerator), int(denominator))


def issue_facts(report: QualityReport) -> tuple[IssueFacts, ...]:
    return tuple(
        IssueFacts(
            issue_id=i.issue_id,
            fingerprint=i.fingerprint,
            code=i.code,
            severity=i.severity.value,
            basis=i.basis.value,
            confidence=i.confidence,
            description=i.description,
            suggested_correction=i.suggested_correction,
            owner_hint=i.owner_hint,
            start_frame=i.start_frame,
            end_frame=i.end_frame,
            clips=i.affected_clips,
            tracks=i.affected_tracks,
            sections=i.sections,
            measurements=i.measurements,
            blocks_export=i.blocks_export,
        )
        for i in report.detected_issues
    )


def _origin(provenance: str, element_id: str, editors: dict[str, str]) -> Origin:
    if provenance in ("ai", "system"):
        return Origin.MEDIA_HOUSE
    if provenance.startswith("human"):
        editor = editors.get(element_id, "")
        return (
            Origin.AUTOMATED_REVISION if editor.startswith(EDITOR_PREFIX) else Origin.HUMAN_MANUAL
        )
    return Origin.UNKNOWN


def timeline_view(timeline: BuiltTimeline) -> TimelineView:
    editors: dict[str, str] = {}
    for record in timeline.edit_log:  # oldest first: the last editor of an element wins
        editors[record.element_id] = record.editor
    roles = {t.track_id: t.role.value for t in timeline.tracks}
    rate = timeline.settings.frame_rate
    return TimelineView(
        timeline_id=timeline.timeline_id,
        version=timeline.build.build_version,
        content_hash=timeline.build.build_hash,
        anchor_asset_id=timeline.anchor_asset_id,
        project_id=timeline.project_id,
        fps=Fraction(rate.numerator, rate.denominator),
        duration_frames=timeline.settings.duration_frames,
        clips=tuple(
            ClipView(
                clip_id=c.clip_id,
                track_id=c.track_id,
                role=roles.get(c.track_id, ""),
                kind=c.kind.value,
                name=c.name,
                start_frame=c.start_frame,
                end_frame=c.end_frame,
                section_id=c.section_id,
                enabled=c.enabled,
                origin=_origin(c.provenance.value, c.clip_id, editors),
            )
            for c in timeline.video_clips()
        ),
        sounds=tuple(
            SoundView(
                clip_id=c.clip_id,
                track_id=c.track_id,
                role=c.role.value,
                start=_seconds(c.timeline_start),
                end=_seconds(c.timeline_end),
                section_id=c.section_id,
                origin=_origin(c.provenance.value, c.clip_id, editors),
            )
            for c in timeline.audio_clips()
        ),
        transitions=tuple(
            TransitionView(t.transition_id, t.mode.value, t.cut_frame, t.from_clip, t.to_clip)
            for t in timeline.transitions
        ),
        sections=tuple(
            SectionView(s.section_id, s.name, s.start_frame, s.end_frame)
            for s in sorted(timeline.sections, key=lambda s: s.order)
        ),
    )


def provenance_counts(view: TimelineView) -> tuple[tuple[str, str], ...]:
    counts: dict[str, int] = {}
    origins = [c.origin for c in view.clips] + [s.origin for s in view.sounds]
    for origin in origins:
        counts[origin.value] = counts.get(origin.value, 0) + 1
    return tuple(sorted((k, str(v)) for k, v in counts.items()))


def review_view(session: ReviewSession) -> ReviewView:
    return ReviewView(
        session_id=session.session_id,
        locked_sections=session.locked_sections(),
        approved_sections=tuple(
            s.section_id
            for s in session.sections
            if s.status in (SectionStatus.APPROVED, SectionStatus.LOCKED)
        ),
        rejected_keys=session.rejected_subjects(),
        authority=tuple((op.value, session.policy.authority(op).value) for op in Operation),
    )
