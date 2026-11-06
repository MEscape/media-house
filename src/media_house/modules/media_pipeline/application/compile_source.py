"""The edit as the timeline compiler's own input: the one place that maps it for Layer 5.

Layer 5 modules never import the ``EditPlan`` (editing's allowlist); like every Layer 4 module
the compiler owns a narrow ``TimelineSource``. Placements keep everything a timeline needs that
the neutral Layer 4 view drops: the audio behaviour, fades, the picture/sound link, the declared
frame counts and the framing recommendations by id. Without an edit (no speech path) the
recording is one clip, timeline = source, speed 1.
"""

from media_house.modules.edit_plan_compiler.application.contracts import (
    EditRef,
    FramingRef,
    SourceBridge,
    SourceClip,
    SourceSection,
    TimelineSource,
)
from media_house.modules.editing_intelligence.application.contracts import EditPlan, TrackKind
from media_house.modules.media_pipeline.application.edit_view import EditView, fr, rat


def timeline_source(view: EditView, plan: EditPlan | None) -> TimelineSource:
    if plan is None:
        clips = tuple(
            SourceClip(
                clip_id=c.clip_id,
                asset_id=c.asset_id,
                source_start=c.source_start,
                source_end=c.source_end,
                timeline_start=c.timeline_start,
                timeline_duration=rat(fr(c.source_end) - fr(c.source_start)),
                picture=c.picture,
                layer=c.layer,
                audio=c.audio,
                section_id=c.section_id,
                beat_id=c.beat_id,
            )
            for c in view.clips
        )
        edit = None
    else:
        clips = tuple(
            SourceClip(
                clip_id=p.placement_id,
                asset_id=p.source.asset_id,
                source_start=p.source.start,
                source_end=p.source.end,
                timeline_start=p.timeline_start,
                timeline_duration=p.timeline_duration,
                picture=p.track is TrackKind.VIDEO,
                layer=p.layer,
                audio=p.audio.value,
                section_id=p.section_id,
                beat_id=p.beat_id,
                start_frame=p.start_frame,
                duration_frames=p.duration_frames,
                fade_in=p.fade_in,
                fade_out=p.fade_out,
                linked=p.linked,
                reuse_of=p.reuse_of,
                framing=tuple(
                    FramingRef(
                        recommendation_id=f.recommendation_id,
                        analysis_id=f.analysis_id,
                        target=f.target,
                        strategy=f.strategy,
                        executable=f.executable,
                        source_start=f.start,
                        source_end=f.end,
                    )
                    for f in p.framing
                ),
            )
            for p in plan.placements
        )
        edit = EditRef(edit_plan_id=plan.edit_plan_id, plan_version=plan.plan_version)
    return TimelineSource(
        edit=edit,
        project_id=view.project_id,
        anchor_asset_id=view.anchor_asset_id,
        frame_rate=view.frame_rate,
        frame_rate_assumed=view.frame_rate_assumed,
        duration=view.duration,
        story_format=view.story_format,
        clips=clips,
        sections=tuple(
            SourceSection(
                section_id=s.section_id,
                order=s.order,
                label=s.label,
                timeline_start=s.timeline_start,
                timeline_end=s.timeline_end,
            )
            for s in view.sections
        ),
        bridges=tuple(
            SourceBridge(
                bridge_id=b.bridge_id, kind=b.kind, audio_at=b.audio_at, picture_at=b.picture_at
            )
            for b in view.bridges
        ),
    )


__all__ = ["timeline_source"]
