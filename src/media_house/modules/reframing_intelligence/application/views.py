"""Upstream results -> the narrow facts the planner reads.

This is the only place that knows the shape of a video analysis, an inspection, a story plan and a
continuity analysis. Every mapping keeps what an owner could not state as ``None``/absent (never a
guess) and keeps times exact: a frame's time is ``pts x time base``, shifted onto the recording's
common clock by the alignment's video correction (0 and ``corrected=False`` without one).
"""

from fractions import Fraction

from media_house.modules.continuity_intelligence.application.contracts import (
    ContinuityAnalysis,
)
from media_house.modules.continuity_intelligence.application.contracts import (
    Decision as ContinuityDecision,
)
from media_house.modules.continuity_intelligence.application.contracts import (
    IssueType as ContinuityIssueType,
)
from media_house.modules.media_inspection.application.contracts import (
    InspectionResult,
    VideoStream,
)
from media_house.modules.reframing_intelligence.domain.engine import digest
from media_house.modules.reframing_intelligence.domain.facts import (
    BeatHint,
    ContinuityFacts,
    CutHint,
    ElementTrack,
    Sample,
    ShotFacts,
    SourceFacts,
    StoryFacts,
    VisualNeed,
)
from media_house.modules.reframing_intelligence.domain.geometry import Box, SourceGeometry
from media_house.modules.story_architecture.application.contracts import StoryPlan
from media_house.modules.video_intelligence.application.contracts import (
    BBox,
    FrameTime,
    ScoreName,
    VideoAnalysis,
)

_CLOCK_PRECISION = 1_000_000


def exact(time: FrameTime, correction: Fraction) -> Fraction:
    """A frame's time as exact seconds on the common clock."""
    base = time.timebase
    return Fraction(time.pts) * Fraction(base.numerator, base.denominator) + correction


def seconds(value: float | None) -> Fraction | None:
    return None if value is None else Fraction(value).limit_denominator(_CLOCK_PRECISION)


def box_of(box: BBox) -> Box:
    return Box(box.x0, box.y0, box.x1, box.y1)


# --- the recording -----------------------------------------------------------------------------
def geometry_of(analysis: VideoAnalysis, inspection: InspectionResult | None) -> SourceGeometry:
    """The source as the boxes see it. The boxes are fractions of the analysed picture's DISPLAY
    size, so that size is the coordinate system; the stored size and pixel shape come from the
    stored inspection when it describes the same display size, else they are assumed equal."""
    info = analysis.source
    video = _video_of(inspection)
    if video is not None and video.geometry.display_size == (info.width, info.height):
        g = video.geometry
        turned = (g.rotation.value or 0) % 180 == 90
        stored = (g.height, g.width) if turned else (g.width, g.height)
        return SourceGeometry(
            display_width=info.width,
            display_height=info.height,
            sample_width=stored[0],
            sample_height=stored[1],
            rotation=g.rotation.value or 0,
            exact=True,
        )
    return SourceGeometry(
        display_width=info.width,
        display_height=info.height,
        sample_width=info.width,
        sample_height=info.height,
        rotation=info.rotation if info.rotation in (0, 90, 180, 270) else 0,
        exact=False,
    )


def _video_of(inspection: InspectionResult | None) -> VideoStream | None:
    if inspection is None or not inspection.inspection.status.usable:
        return None
    return inspection.inspection.primary_video


def source_facts(
    analysis: VideoAnalysis,
    asset_id: str,
    inspection: InspectionResult | None,
    correction: float,
    corrected: bool,
) -> SourceFacts:
    shift = seconds(correction) or Fraction(0)
    by_shot: dict[str, list[ElementTrack]] = {}
    for track in analysis.tracks:
        if not track.ok or not track.points:
            continue
        by_shot.setdefault(track.shot_id, []).append(
            ElementTrack(
                track_id=track.track_id,
                kind=track.kind.value,
                label=track.label,
                samples=tuple(
                    Sample(exact(p.time, shift), box_of(p.box), p.confidence)
                    for p in sorted(track.points, key=lambda p: p.time.pts)
                ),
                cluster=track.identity_cluster,
            )
        )
    retakes = {sid: g.group_id for g in analysis.retakes for sid in g.shot_ids}
    shots = []
    for s in analysis.shots:
        usable = next((x for x in s.scores if x.name is ScoreName.TECHNICAL_USABILITY), None)
        meaning = s.meaning
        shots.append(
            ShotFacts(
                shot_id=s.shot_id,
                scene_id=s.scene_id,
                start=exact(s.range.start, shift),
                end=exact(s.range.end, shift),
                tracks=tuple(by_shot.get(s.shot_id, ())),
                main_track_id=s.entities.main_subject_track_id if s.entities.ok else None,
                framing=s.framing.framing.value if s.framing.ok and s.framing.framing else None,
                usability=usable.value if usable is not None and usable.ok else None,
                head_yaw=s.faces.head_yaw if s.faces.ok else None,
                attention=(
                    (meaning.main_attention.x, meaning.main_attention.y)
                    if meaning.ok and meaning.main_attention is not None
                    else None
                ),
                retake_group=retakes.get(s.shot_id),
            )
        )
    p = analysis.provenance
    return SourceFacts(
        asset_id=asset_id,
        geometry=geometry_of(analysis, inspection),
        shots=tuple(shots),
        clock_correction=shift,
        corrected=corrected,
        identity=digest(
            [
                analysis.asset_checksum,
                p.profile_name,
                p.profile_version,
                sorted(p.analyzer_versions.items()),
                sorted(p.derivation_versions.items()),
            ]
        ),
    )


# --- the story ---------------------------------------------------------------------------------
def story_facts(plan: StoryPlan) -> StoryFacts:
    beats = tuple(
        BeatHint(
            beat_id=b.beat_id,
            section_id=b.section_id,
            function=b.function.value,
            asset_id=b.source.asset_id,
            start=seconds(b.source.start),
            end=seconds(b.source.end),
        )
        for b in plan.beats
    )
    needs = tuple(
        VisualNeed(
            visual_id=v.visual_id,
            section_id=v.section_id,
            purpose=v.purpose.value,
            necessary=v.necessity.value == "essential",
            concept=v.concept,
            asset_id=v.source.asset_id,
            start=seconds(v.source.start),
            end=seconds(v.source.end),
        )
        for v in plan.visual_opportunities
    )
    return StoryFacts(
        story_plan_id=plan.story_plan_id,
        plan_version=plan.plan_version,
        story_format=plan.target_format,
        beats=beats,
        needs=needs,
    )


# --- continuity --------------------------------------------------------------------------------
def continuity_facts(analysis: ContinuityAnalysis) -> ContinuityFacts:
    """The cuts a continuity analysis judged: where they were, how risky, whether a jump cut."""
    cuts = []
    for t in analysis.transitions:
        leaves = Fraction(t.outgoing_at.numerator, t.outgoing_at.denominator)
        resumes = Fraction(t.incoming_at.numerator, t.incoming_at.denominator)
        verdict = analysis.risk_at(
            t.outgoing_asset, float(leaves), t.incoming_asset, float(resumes)
        )
        jump = any(
            i.type is ContinuityIssueType.JUMP_CUT and i.decision is not ContinuityDecision.REJECTED
            for i in analysis.issues_at(t.transition_id)
        )
        cuts.append(
            CutHint(
                out_asset=t.outgoing_asset,
                leaves=leaves,
                in_asset=t.incoming_asset,
                resumes=resumes,
                risk=None if verdict is None else verdict[0],
                jump_cut=jump,
            )
        )
    return ContinuityFacts(
        analysis_id=analysis.continuity_analysis_id,
        cuts=tuple(cuts),
        identity=digest(
            [
                analysis.continuity_analysis_id,
                [
                    [c.out_asset, str(c.leaves), c.in_asset, str(c.resumes), c.risk, c.jump_cut]
                    for c in cuts
                ],
            ]
        ),
    )
