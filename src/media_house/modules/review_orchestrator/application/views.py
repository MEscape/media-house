"""The one place that knows both the timeline builder's model and the review's own facts.

The review's domain imports no other module, so a built timeline is read here into
``TimelineFacts``: which version it is, and one fingerprint per section (everything on the
timeline inside it), so a change can be told from a rebuild that changed nothing.
"""

from media_house.core.domain import json_codec
from media_house.modules.review_orchestrator.domain.timeline import (
    SectionFacts,
    TimelineFacts,
    TimelineRef,
)
from media_house.modules.timeline_builder.application.contracts import (
    AudioClip,
    BuiltTimeline,
    VideoClip,
)

UNSECTIONED = "unsectioned"


def facts_of(timeline: BuiltTimeline, build_asset_id: str | None) -> TimelineFacts:
    ref = TimelineRef(
        timeline_id=timeline.timeline_id,
        version=timeline.build.build_version,
        content_hash=timeline.build.build_hash,
        anchor_asset_id=timeline.anchor_asset_id,
        build_asset_id=build_asset_id,
    )
    clips: dict[str, VideoClip | AudioClip] = {
        **{c.clip_id: c for c in timeline.video_clips()},
        **{c.clip_id: c for c in timeline.audio_clips()},
    }
    sections: list[SectionFacts] = []
    placed: set[str] = set()
    for section in sorted(timeline.sections, key=lambda s: s.order):
        members = [clips[i] for i in section.clip_ids if i in clips]
        placed.update(c.clip_id for c in members)
        sections.append(
            SectionFacts(
                section_id=section.section_id,
                name=section.name,
                start_frame=section.start_frame,
                end_frame=section.end_frame,
                digest=json_codec.digest(sorted((json_codec.canon(c) for c in members), key=str)),
            )
        )
    loose = [c for i, c in sorted(clips.items()) if i not in placed]
    if loose or not sections:
        sections.append(
            SectionFacts(
                section_id=UNSECTIONED,
                name="Outside the sections" if sections else "Timeline",
                start_frame=0,
                end_frame=timeline.settings.duration_frames,
                digest=json_codec.digest(sorted((json_codec.canon(c) for c in loose), key=str)),
            )
        )
    rate = timeline.settings.frame_rate
    return TimelineFacts(
        ref=ref,
        width=timeline.settings.width,
        height=timeline.settings.height,
        frame_rate=f"{rate.numerator}/{rate.denominator}",
        duration_frames=timeline.settings.duration_frames,
        sections=tuple(sections),
    )
