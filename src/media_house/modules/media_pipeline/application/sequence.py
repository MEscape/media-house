"""An edit plan as the clips continuity judges: the one place that knows both shapes.

Only the picture clips matter for cuts; sound flags come from the plan's audio bridges (a J/L cut
near the boundary) and the editor's own cut points (a cut it made inside speech).
"""

from dataclasses import replace
from fractions import Fraction
from itertools import pairwise

from media_house.modules.continuity_intelligence.application.contracts import (
    ProposedClip,
    ProposedSequence,
)
from media_house.modules.editing_intelligence.application.contracts import EditPlan, TrackKind

_BRIDGE_REACH = Fraction(1)


def _f(value: object) -> Fraction:
    return Fraction(value.numerator, value.denominator)  # type: ignore[attr-defined]


def sequence_of(plan: EditPlan) -> ProposedSequence:
    clips = []
    for p in plan.placements:
        if p.track is not TrackKind.VIDEO:
            continue
        start = _f(p.timeline_start)
        clips.append(
            ProposedClip(
                clip_id=p.placement_id,
                asset_id=p.source.asset_id,
                source_start=_f(p.source.start),
                source_end=_f(p.source.end),
                timeline_start=start,
                timeline_end=start + _f(p.timeline_duration),
                section_id=p.section_id,
                beat_id=p.beat_id,
                layer=p.layer,
                reuse_of=p.reuse_of,
                speech_trim_in=p.cut_in is not None,
                speech_trim_out=p.cut_out is not None,
            )
        )
    clips.sort(key=lambda c: (c.layer, c.timeline_start, c.clip_id))
    return ProposedSequence(
        edit_plan_id=plan.edit_plan_id,
        edit_plan_version=plan.plan_version,
        clips=_bridged(clips, plan),
        project_id=plan.project_id,
        story_format=plan.target_format,
    )


def _bridged(clips: list[ProposedClip], plan: EditPlan) -> tuple[ProposedClip, ...]:
    """Mark the main clips on either side of a boundary where sound and picture change apart."""
    main = [c for c in clips if c.layer == 0]
    leaving: set[str] = set()
    entering: set[str] = set()
    for before, after in pairwise(main):
        boundary = after.timeline_start
        for bridge in plan.audio_bridges:
            near = (
                abs(_f(bridge.audio_changes_at) - boundary) <= _BRIDGE_REACH
                or abs(_f(bridge.picture_changes_at) - boundary) <= _BRIDGE_REACH
            )
            if near and bridge.audio_changes_at != bridge.picture_changes_at:
                leaving.add(before.clip_id)
                entering.add(after.clip_id)
    return tuple(
        replace(
            c,
            audio_bridge_out=c.audio_bridge_out or c.clip_id in leaving,
            audio_bridge_in=c.audio_bridge_in or c.clip_id in entering,
        )
        for c in clips
    )
