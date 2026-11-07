# ruff: noqa: E501 - the template table is one finding per line.
"""From findings to proposals: which change would answer each finding, who owns it, who may
approve it, what it touches, and what is left out and why.

The planner decides nothing creative: it states a change a person can approve, reject or modify,
and says exactly where it applies. It does not duplicate what the owners know about pacing,
captions or B-roll; for those it names the owner and the request, and says plainly that this module
does not carry them out. A change that would touch a locked section, work a person did, or a clip
they asked to keep is never hidden: it is listed as a conflict they must decide.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from media_house.core.domain import json_codec
from media_house.modules.revision_engine.domain.facts import (
    IssueFacts,
    MixView,
    ReviewView,
    TimelineView,
)
from media_house.modules.revision_engine.domain.invalidation import invalidations
from media_house.modules.revision_engine.domain.model import ProposedChange, Skipped
from media_house.modules.revision_engine.domain.values import (
    ChangeKind,
    ChangeStatus,
    Level,
    Origin,
)

_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "optional": 0}
_LEVEL_OF = {
    "automatic": Level.AUTOMATIC,
    "suggested": Level.SUGGESTED,
    "manual_only": Level.MANUAL_ONLY,
}
_STRICT = {Level.AUTOMATIC: 0, Level.SUGGESTED: 1, Level.MANUAL_ONLY: 2}
#: A person's own change to a bus is never larger than this in one step (dB).
MAX_STEP_DB = 6.0
#: A level change smaller than this is not worth a rendering.
MIN_STEP_DB = 1.5


@dataclass(frozen=True, slots=True)
class Draft:
    """A change before it is given its identity, level and conflicts."""

    kind: ChangeKind
    owner: str
    operation: str
    title: str
    description: str
    clip_ids: tuple[str, ...] = ()
    section_ids: tuple[str, ...] = ()
    track_ids: tuple[str, ...] = ()
    params: tuple[tuple[str, str], ...] = ()
    expected: str = ""
    side_effects: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    #: Level the change needs regardless of its operation's authority.
    minimum_level: Level = Level.AUTOMATIC
    bus: str = ""
    issue_ids: tuple[str, ...] = ()
    fingerprints: tuple[str, ...] = ()
    instruction_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanningInput:
    timeline: TimelineView
    issues: tuple[IssueFacts, ...]
    review: ReviewView
    mix: MixView
    protected_clips: frozenset[str] = frozenset()
    #: Propose again what a person rejected (they asked for another look).
    reconsider: bool = False
    include_optional: bool = False
    #: Drafts that come from a person's words, planned with the findings.
    extra: tuple[Draft, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedRevisions:
    changes: tuple[ProposedChange, ...]
    skipped: tuple[Skipped, ...]


# --- the mapping from a finding to a change ------------------------------------------------------

Template = Callable[[IssueFacts, TimelineView, MixView], list[Draft]]


def _where(issue: IssueFacts, timeline: TimelineView) -> str:
    if issue.start_frame is None:
        return "the timeline"
    at = float(timeline.fps and issue.start_frame / float(timeline.fps))
    section = timeline.section_at(issue.start_frame)
    return f"{section.name if section else 'the timeline'} at {at:.1f} s"


def _handoff(
    owner: str,
    operation: str,
    intent: str,
    title: str,
    *,
    level: Level = Level.SUGGESTED,
    expected: str = "",
    side: tuple[str, ...] = (),
) -> Template:
    def draft(issue: IssueFacts, timeline: TimelineView, mix: MixView) -> list[Draft]:
        del mix
        place = _where(issue, timeline)
        return [
            Draft(
                kind=ChangeKind.HANDOFF,
                owner=owner,
                operation=operation,
                title=title,
                description=(
                    f"{place}: {issue.description} Ask {owner} to {intent}. "
                    f"{issue.suggested_correction}"
                ),
                clip_ids=issue.clips,
                section_ids=issue.sections,
                track_ids=issue.tracks,
                params=(("intent", intent), ("issue_code", issue.code)),
                expected=expected
                or f"The finding '{issue.code}' no longer appears in the next report.",
                side_effects=side,
                minimum_level=level,
            )
        ]

    return draft


def _rebuild(operation: str, title: str, why: str) -> Template:
    def draft(issue: IssueFacts, timeline: TimelineView, mix: MixView) -> list[Draft]:
        del mix
        return [
            Draft(
                kind=ChangeKind.REBUILD_TIMELINE,
                owner="timeline_builder",
                operation=operation,
                title=title,
                description=(
                    f"{_where(issue, timeline)}: {issue.description} Build the timeline again from "
                    f"its specification; {why}"
                ),
                clip_ids=issue.clips,
                section_ids=issue.sections,
                params=(("reason", issue.code),),
                expected="The affected clips play their footage; unchanged elements and a "
                "person's own edits are kept.",
                side_effects=("Elements whose inputs changed are rebuilt.",),
            )
        ]

    return draft


def _trim(issue: IssueFacts, timeline: TimelineView, mix: MixView) -> list[Draft]:
    del mix
    place = _where(issue, timeline)
    if issue.code == "structure.gap":
        gap = int(issue.measure("gap_frames", "0") or 0)
        previous, following = issue.measure("previous_clip"), issue.measure("next_clip")
        if previous:
            clip = timeline.clip(previous)
            if clip is None:
                return []
            end = clip.end_frame + gap
            return [
                Draft(
                    kind=ChangeKind.TRIM_CLIP,
                    owner="timeline_builder",
                    operation="local_creative_revision",
                    title=f"Close the {gap}-frame gap after '{clip.name}'",
                    description=f"{place}: {issue.description} Extend '{clip.name}' by {gap} frames "
                    f"to frame {end}.",
                    clip_ids=(clip.clip_id,),
                    section_ids=issue.sections,
                    params=(("clip", clip.clip_id), ("end_frame", str(end))),
                    expected="No gap on the main picture.",
                    side_effects=(
                        "The shot is held longer and needs enough footage after its end.",
                    ),
                    alternatives=(
                        "Move the next shot earlier.",
                        "Fill the gap with cover footage.",
                    ),
                )
            ]
        clip = timeline.clip(following)
        if clip is None:
            return []
        return [
            Draft(
                kind=ChangeKind.TRIM_CLIP,
                owner="timeline_builder",
                operation="local_creative_revision",
                title=f"Start '{clip.name}' {gap} frames earlier",
                description=f"{place}: {issue.description} Start '{clip.name}' at frame "
                f"{clip.start_frame - gap}.",
                clip_ids=(clip.clip_id,),
                section_ids=issue.sections,
                params=(("clip", clip.clip_id), ("start_frame", str(clip.start_frame - gap))),
                expected="The main picture starts at the start of the timeline.",
                side_effects=("Needs enough footage before the shot's first frame.",),
            )
        ]
    if issue.code == "structure.overlap":
        earlier = timeline.clip(issue.measure("earlier_clip"))
        later = timeline.clip(issue.measure("later_clip"))
        if earlier is None or later is None:
            return []
        return [
            Draft(
                kind=ChangeKind.TRIM_CLIP,
                owner="timeline_builder",
                operation="local_creative_revision",
                title=f"Trim '{earlier.name}' where '{later.name}' begins",
                description=f"{place}: {issue.description} End '{earlier.name}' at frame "
                f"{later.start_frame}.",
                clip_ids=(earlier.clip_id,),
                section_ids=issue.sections,
                params=(("clip", earlier.clip_id), ("end_frame", str(later.start_frame))),
                expected="The clips no longer overlap.",
            )
        ]
    clip = timeline.clip(issue.measure("clip"))
    if clip is None:
        return []
    end = int(issue.measure("timeline_end", str(timeline.duration_frames)))
    return [
        Draft(
            kind=ChangeKind.TRIM_CLIP,
            owner="timeline_builder",
            operation="technical_repair",
            title=f"Trim '{clip.name}' to the end of the timeline",
            description=f"{place}: {issue.description} End '{clip.name}' at frame {end}.",
            clip_ids=(clip.clip_id,),
            section_ids=issue.sections,
            params=(("clip", clip.clip_id), ("end_frame", str(end))),
            expected="Nothing runs past the end of the timeline.",
        )
    ]


def _dialogue(issue: IssueFacts, timeline: TimelineView, mix: MixView) -> list[Draft]:
    del mix
    try:
        quiet = float(issue.measure("quietest_lufs"))
        loud = float(issue.measure("loudest_lufs"))
        middle = float(issue.measure("median_lufs"))
    except ValueError:
        return []
    drafts: list[Draft] = []
    for clip_id, level in (
        (issue.measure("quietest_clip"), quiet),
        (issue.measure("loudest_clip"), loud),
    ):
        sound = timeline.sound(clip_id)
        delta = max(-MAX_STEP_DB, min(MAX_STEP_DB, middle - level))
        if sound is None or abs(delta) < MIN_STEP_DB:
            continue
        drafts.append(
            Draft(
                kind=ChangeKind.MIX_CLIP_GAIN,
                owner="audio_mix_engine",
                operation="local_creative_revision",
                title=f"Change the level of dialogue clip {clip_id} by {delta:+.1f} dB",
                description=f"{_where(issue, timeline)}: {issue.description} Move this clip from "
                f"{level:.1f} LUFS toward the typical {middle:.1f} LUFS ({delta:+.1f} dB).",
                clip_ids=(clip_id,),
                section_ids=(sound.section_id,) if sound.section_id else issue.sections,
                params=(("clip", clip_id), ("delta_db", f"{delta:.1f}")),
                expected="Dialogue levels sit within the allowed spread.",
                side_effects=(
                    "The dialogue stem and the final mix are rendered again; music is not.",
                ),
                bus="dialogue",
            )
        )
    del loud
    return drafts


def _remix(issue: IssueFacts, timeline: TimelineView, mix: MixView) -> list[Draft]:
    del mix
    return [
        Draft(
            kind=ChangeKind.REMIX,
            owner="audio_mix_engine",
            operation="technical_repair",
            title="Mix the sound again against the delivery target",
            description=f"{_where(issue, timeline)}: {issue.description} Render the mix again "
            "with the delivery profile's loudness target, then render a new preview.",
            params=(("reason", issue.code),),
            expected="Loudness within the delivery tolerance.",
            side_effects=("Only the final mix is rendered again; stems are reused.",),
        )
    ]


def _resync(issue: IssueFacts, timeline: TimelineView, mix: MixView) -> list[Draft]:
    del mix
    return [
        Draft(
            kind=ChangeKind.RESYNC_RESOLVE,
            owner="resolve_adapter",
            operation="technical_repair",
            title="Bring Resolve and the built timeline together",
            description=f"{_where(issue, timeline)}: {issue.description}",
            params=(
                ("direction", "read_back" if issue.code == "resolve.manual_changes" else "write"),
            ),
            expected="Resolve holds the current version, and a person's edits there are kept here.",
            side_effects=("A person's own edits in Resolve are kept, never overwritten.",),
        )
    ]


_O = "local_creative_revision"
TEMPLATES: dict[str, Template] = {
    "structure.media_missing": _rebuild(
        "asset_reconnection", "Reconnect the missing footage", "the file is looked up again now."
    ),
    "structure.unresolved_clip": _rebuild(
        "technical_repair",
        "Rebuild a clip whose dependency is missing",
        "once the missing item has been produced.",
    ),
    "structure.dependency_open": _rebuild(
        "technical_repair",
        "Rebuild after producing the missing dependency",
        "once the missing item has been produced.",
    ),
    "structure.element_missing": _rebuild(
        "technical_repair",
        "Restore an element the specification lists",
        "the specification is the reference.",
    ),
    "structure.builder_error": _rebuild(
        "technical_repair", "Rebuild after fixing the builder's error", "once its cause is fixed."
    ),
    "graphic.missing_asset": _rebuild(
        "technical_repair",
        "Rebuild after producing the missing graphic",
        "once it has been rendered.",
    ),
    "video.black_frames": _rebuild(
        "technical_repair",
        "Restore the footage that is missing at the black frames",
        "missing or offline footage is looked up again.",
    ),
    "video.frozen_frames": _rebuild(
        "technical_repair", "Restore the footage that stalls", "missing footage is looked up again."
    ),
    "structure.gap": _trim,
    "structure.overlap": _trim,
    "structure.beyond_timeline": _trim,
    "audio.dialogue_inconsistent": _dialogue,
    "audio.loudness_off": _remix,
    "audio.true_peak": _remix,
    "audio.clipping": _remix,
    "resolve.stale_version": _resync,
    "resolve.manual_changes": _resync,
    "resolve.conflict_open": _handoff(
        "resolve_adapter",
        "override_decision",
        "settle each open conflict",
        "Decide the open Resolve conflicts",
        level=Level.MANUAL_ONLY,
    ),
    "structure.stretch": _handoff(
        "reframing_intelligence", _O, "use a uniform scale (fit or crop)", "Fix a stretched picture"
    ),
    "structure.heavy_upscale": _handoff(
        "reframing_intelligence", _O, "choose a lower punch-in", "Reduce a heavy enlargement"
    ),
    "structure.transition_unsupported": _handoff(
        "transition_engine",
        _O,
        "choose a transition that can be carried out",
        "Replace a transition that cannot run",
    ),
    "structure.handles_short": _handoff(
        "transition_engine",
        _O,
        "shorten the transition or use a hard cut",
        "Fix a transition without enough footage",
    ),
    "structure.transition_unavailable": _handoff(
        "transition_engine",
        _O,
        "produce the transition again",
        "Restore a transition that could not be produced",
    ),
    "structure.builder_review": _handoff(
        "timeline_builder", _O, "present the open decision", "Decide a waiting conflict"
    ),
    "structure.builder_unsupported": _handoff(
        "timeline_builder",
        _O,
        "carry the instruction out another way",
        "Handle an unsupported instruction",
    ),
    "structure.element_unexpected": _handoff(
        "timeline_builder", _O, "confirm the element belongs", "Check an unexpected element"
    ),
    "structure.no_sound": _handoff(
        "audio_mix_engine", _O, "mix the planned sound", "Add the missing sound"
    ),
    "structure.no_primary_track": _rebuild(
        "technical_repair", "Rebuild the timeline", "it has no main picture."
    ),
    "structure.frame_rate_assumed": _handoff(
        "media_inspection",
        "technical_repair",
        "inspect the footage for its real frame rate",
        "Read the real frame rate",
        level=Level.AUTOMATIC,
    ),
    "structure.odd_size": _handoff(
        "edit_plan_compiler",
        "technical_repair",
        "use an even picture size",
        "Use an even picture size",
    ),
    "editing.flash_frame": _handoff(
        "editing_intelligence",
        _O,
        "remove the flash frame and close up the gap",
        "Remove a flash frame",
    ),
    "editing.repeated_footage": _handoff(
        "editing_intelligence",
        _O,
        "replace one use of the repeated footage",
        "Remove repeated footage",
    ),
    "editing.shot_too_long": _handoff(
        "pacing_intelligence", _O, "shorten the shot or cover it", "Shorten a long shot"
    ),
    "editing.shot_too_short": _handoff(
        "pacing_intelligence", _O, "give the shot more time", "Give a very short shot more time"
    ),
    "editing.weak_opening": _handoff(
        "pacing_intelligence", _O, "open with the strongest moment", "Strengthen the opening"
    ),
    "editing.jump_cut": _handoff(
        "continuity_intelligence", _O, "cover or smooth the jump cut", "Smooth a jump cut"
    ),
    "editing.static_stretch": _handoff(
        "broll_intelligence", _O, "cover the static stretch", "Add visual variation"
    ),
    "editing.abrupt_ending": _handoff(
        "transition_engine", _O, "close the video with a fade", "Close the ending"
    ),
    "caption.overlap": _handoff(
        "caption_engine", _O, "re-time the overlapping captions", "Re-time overlapping captions"
    ),
    "caption.too_fast": _handoff(
        "caption_engine", _O, "lengthen the caption", "Lengthen a fast caption"
    ),
    "caption.reading_speed": _handoff(
        "caption_engine", _O, "shorten the text or give it more time", "Make a caption readable"
    ),
    "caption.too_dense": _handoff(
        "caption_engine", _O, "split or shorten the caption", "Split a dense caption"
    ),
    "caption.beyond_timeline": _handoff(
        "caption_engine", _O, "re-time or remove the caption", "Fix a caption past the end"
    ),
    "caption.empty": _handoff(
        "caption_engine", _O, "remove or restore the caption", "Fix an empty caption"
    ),
    "caption.missing": _handoff(
        "caption_engine", _O, "plan captions for the speech", "Add captions"
    ),
    "caption.emoji_flash": _handoff(
        "caption_engine", _O, "hold the emoji longer or drop it", "Fix a flashing emoji"
    ),
    "caption.style_inconsistent": _handoff(
        "caption_engine", _O, "use one caption style", "Make captions consistent"
    ),
    "graphic.unknown_zone": _handoff(
        "motion_graphics_engine",
        _O,
        "place the graphic in a layout zone",
        "Place a graphic in a zone",
    ),
    "graphic.outside_frame": _handoff(
        "motion_graphics_engine",
        _O,
        "move the graphic inside the frame",
        "Bring a graphic into the frame",
    ),
    "graphic.overlap": _handoff(
        "motion_graphics_engine",
        _O,
        "separate the overlapping graphics",
        "Separate overlapping graphics",
    ),
    "graphic.font_missing": _handoff(
        "visual_style_engine",
        "technical_repair",
        "provide the missing font",
        "Provide a missing font",
    ),
    "graphic.brand_missing": _handoff(
        "visual_style_engine",
        "technical_repair",
        "provide the missing brand asset",
        "Provide a missing brand asset",
    ),
    "graphic.style_inconsistent": _handoff(
        "visual_style_engine", _O, "use one style per graphic kind", "Make graphics consistent"
    ),
    "render.decode_errors": _handoff(
        "render_manager",
        "render_preparation",
        "render again with the same settings",
        "Render again",
        level=Level.AUTOMATIC,
    ),
    "render.duration_mismatch": _handoff(
        "render_manager",
        "render_preparation",
        "render again and check the end of the timeline",
        "Render again",
        level=Level.AUTOMATIC,
    ),
    "render.size_mismatch": _handoff(
        "render_manager",
        "render_preparation",
        "correct the render size",
        "Correct the render size",
        level=Level.AUTOMATIC,
    ),
    "render.rate_mismatch": _handoff(
        "render_manager",
        "render_preparation",
        "correct the render frame rate",
        "Correct the render frame rate",
        level=Level.AUTOMATIC,
    ),
    "video.no_video_stream": _handoff(
        "render_manager",
        "render_preparation",
        "render again",
        "Render again",
        level=Level.AUTOMATIC,
    ),
    "video.black_edge": _handoff(
        "timeline_builder", _O, "trim to the first and last real picture", "Trim a black edge"
    ),
    "visual.crushed_shadows": _handoff(
        "video_improvement", _O, "lift the shadows of the source", "Lift crushed shadows"
    ),
    "visual.clipped_highlights": _handoff(
        "video_improvement",
        _O,
        "recover the highlights or pick another shot",
        "Treat clipped highlights",
    ),
    "visual.underexposed": _handoff(
        "video_improvement", _O, "raise the exposure of the source", "Raise the exposure"
    ),
    "visual.overexposed": _handoff(
        "video_improvement", _O, "lower the exposure of the source", "Lower the exposure"
    ),
    "visual.soft_focus": _handoff(
        "editing_intelligence", _O, "replace the soft shot", "Replace a soft shot"
    ),
    "visual.color_mismatch": _handoff(
        "video_improvement", _O, "match the colour of the neighbouring shots", "Match shot colour"
    ),
    "visual.unexpected_borders": _handoff(
        "reframing_intelligence", _O, "fill the frame", "Remove unexpected borders"
    ),
    "visual.bad_crop": _handoff("reframing_intelligence", _O, "centre the crop", "Centre a crop"),
    "audio.missing": _handoff(
        "render_manager",
        "render_preparation",
        "render again with the audio mix",
        "Render again with sound",
        level=Level.AUTOMATIC,
    ),
    "audio.sync_drift": _handoff(
        "render_manager",
        "render_preparation",
        "check the audio offset and render again",
        "Correct a sync drift",
        level=Level.AUTOMATIC,
    ),
    "audio.range_wide": _handoff(
        "audio_mix_engine", _O, "compress the dynamics a little", "Narrow the loudness range"
    ),
    "audio.unexpected_silence": _handoff(
        "audio_mix_engine", _O, "find the clip that should play", "Restore missing sound"
    ),
    "audio.abrupt_end": _handoff("audio_mix_engine", _O, "add a fade out", "Fade out the ending"),
    "audio.mix_finding": _handoff(
        "audio_mix_engine", _O, "act on the mix engine's finding", "Act on a mix finding"
    ),
    "continuity.finding": _handoff(
        "continuity_intelligence", _O, "apply its suggested repair", "Repair a continuity issue"
    ),
    "story.section_missing": _handoff(
        "story_architecture",
        _O,
        "confirm the section was cut on purpose",
        "Confirm a missing section",
        level=Level.MANUAL_ONLY,
    ),
    "story.order_deviates": _handoff(
        "story_architecture",
        _O,
        "confirm the section order",
        "Confirm the section order",
        level=Level.MANUAL_ONLY,
    ),
    "story.payoff_missing": _handoff(
        "story_architecture",
        _O,
        "restore the payoff or drop the setup",
        "Resolve a setup without payoff",
    ),
}


def _template_for(code: str) -> Template:
    if code in TEMPLATES:
        return TEMPLATES[code]
    family = code.split(".")[0]
    if family == "audio":
        return TEMPLATES["audio.mix_finding"]
    if family == "continuity":
        return TEMPLATES["continuity.finding"]
    return _handoff("timeline_builder", _O, "look at the finding", "Look at a finding")


# --- planning ------------------------------------------------------------------------------------


def _level(draft: Draft, review: ReviewView, approved_sections: tuple[str, ...]) -> Level:
    base = _LEVEL_OF[review.authority_of(draft.operation)]
    level = max(base, draft.minimum_level, key=lambda lv: _STRICT[lv])
    if approved_sections and any(s in approved_sections for s in draft.section_ids):
        level = Level.MANUAL_ONLY  # it would replace something a person already approved
    return level


def _origin_conflicts(draft: Draft, timeline: TimelineView) -> list[str]:
    found = []
    for clip_id in draft.clip_ids:
        clip = timeline.clip(clip_id)
        if (
            clip is not None
            and clip.origin is Origin.HUMAN_MANUAL
            and draft.kind is not ChangeKind.HANDOFF
        ):
            found.append(f"'{clip.name}' was changed by a person; the revision would overwrite it.")
        sound = timeline.sound(clip_id)
        if (
            sound is not None
            and sound.origin is Origin.HUMAN_MANUAL
            and draft.kind is not ChangeKind.HANDOFF
        ):
            found.append(
                f"sound clip {clip_id} was changed by a person; the revision would overwrite it."
            )
    return found


def _key(draft: Draft, severity: str) -> str:
    return (
        "rk_"
        + json_codec.digest(
            [draft.kind.value, draft.owner, sorted(draft.fingerprints), draft.params, severity]
        )[:20]
    )


def _merge(drafts: list[Draft]) -> list[Draft]:
    """The same change asked for by several findings is one change that answers all of them."""
    merged: dict[tuple[object, ...], Draft] = {}
    for draft in drafts:
        identity = (draft.kind, draft.owner, draft.params, draft.clip_ids, draft.title)
        held = merged.get(identity)
        if held is None:
            merged[identity] = draft
            continue
        merged[identity] = replace(
            held,
            issue_ids=(*held.issue_ids, *draft.issue_ids),
            fingerprints=(*held.fingerprints, *draft.fingerprints),
            section_ids=tuple(dict.fromkeys((*held.section_ids, *draft.section_ids))),
        )
    return list(merged.values())


def plan(data: PlanningInput) -> PlannedRevisions:
    timeline = data.timeline
    skipped: list[Skipped] = []
    drafts: list[Draft] = []
    severity_of: dict[str, str] = {}
    ordered = sorted(
        data.issues, key=lambda i: (-_RANK.get(i.severity, 0), i.start_frame or 0, i.issue_id)
    )
    for issue in ordered:
        if issue.severity == "optional" and not data.include_optional:
            skipped.append(
                Skipped(
                    issue_id=issue.issue_id,
                    code=issue.code,
                    reason="an optional suggestion; ask for it to include it",
                )
            )
            continue
        made = [replace_ids(d, issue) for d in _template_for(issue.code)(issue, timeline, data.mix)]
        if not made:
            skipped.append(
                Skipped(
                    issue_id=issue.issue_id,
                    code=issue.code,
                    reason="no concrete change could be derived from the measurements",
                )
            )
            continue
        for draft in made:
            severity_of[draft.title] = issue.severity
        drafts.extend(made)
    drafts = _merge([*drafts, *data.extra])
    order = {
        ChangeKind.REBUILD_TIMELINE: 0,
        ChangeKind.TRIM_CLIP: 1,
        ChangeKind.DELETE_CLIP: 1,
        ChangeKind.DISABLE_CLIP: 1,
        ChangeKind.SET_OPACITY: 1,
        ChangeKind.MIX_BUS_GAIN: 2,
        ChangeKind.MIX_CLIP_GAIN: 2,
        ChangeKind.REMIX: 3,
        ChangeKind.HANDOFF: 4,
        ChangeKind.RESYNC_RESOLVE: 5,
        ChangeKind.ROLLBACK: 0,
    }
    drafts.sort(key=lambda d: order[d.kind])
    changes: list[ProposedChange] = []
    for number, draft in enumerate(drafts, start=1):
        key = _key(draft, severity_of.get(draft.title, "medium"))
        if key in data.review.rejected_keys and not data.reconsider:
            for issue_id in draft.issue_ids:
                skipped.append(
                    Skipped(
                        issue_id=issue_id,
                        code="",
                        reason=f"'{draft.title}' was rejected earlier and nothing has changed",
                    )
                )
            continue
        conflicts = _origin_conflicts(draft, timeline)
        for section in draft.section_ids:
            if section in data.review.locked_sections:
                conflicts.append(f"section '{section}' is locked; a person must unlock it first.")
        for clip_id in draft.clip_ids:
            if clip_id in data.protected_clips:
                conflicts.append(f"clip {clip_id} was marked to keep exactly as it is.")
        level = _level(draft, data.review, data.review.approved_sections)
        if conflicts:
            level = Level.MANUAL_ONLY
        changes.append(
            ProposedChange(
                proposal_id=f"p{number}",
                proposal_key=key,
                kind=draft.kind,
                owner_module=draft.owner,
                operation=draft.operation,
                level=level,
                title=draft.title,
                description=draft.description,
                issue_ids=draft.issue_ids,
                issue_fingerprints=draft.fingerprints,
                instruction_ids=draft.instruction_ids,
                clip_ids=draft.clip_ids,
                track_ids=draft.track_ids,
                section_ids=draft.section_ids,
                params=draft.params,
                expected_improvement=draft.expected,
                side_effects=draft.side_effects,
                invalidates=invalidations(
                    draft.kind,
                    owner=draft.owner,
                    clip_ids=draft.clip_ids,
                    section_ids=draft.section_ids,
                    bus=draft.bus,
                ),
                conflicts=tuple(conflicts),
                alternatives=draft.alternatives,
                status=ChangeStatus.CONFLICT if conflicts else ChangeStatus.PROPOSED,
            )
        )
    return PlannedRevisions(tuple(changes), tuple(skipped))


def replace_ids(draft: Draft, issue: IssueFacts) -> Draft:
    return replace(draft, issue_ids=(issue.issue_id,), fingerprints=(issue.fingerprint,))


def strictest(levels: list[Level]) -> Level:
    return max(levels, key=lambda lv: _STRICT[lv], default=Level.AUTOMATIC)
