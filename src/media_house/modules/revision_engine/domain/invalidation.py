"""What a change makes stale, and only that.

A change invalidates what genuinely depends on it. The answers are names (``audio:stem:music``,
``timeline:section:intro``) that the producers' own fingerprints turn into "reuse" or "render
again"; nothing here runs anything. A caption change leaves the video effects alone, a music change
leaves the dialogue restoration alone.
"""

from media_house.modules.revision_engine.domain.values import ChangeKind

#: What each owner's output feeds: the outputs a handed-off change makes stale.
_OWNER_OUTPUTS: dict[str, tuple[str, ...]] = {
    "caption_engine": ("plan:captions", "timeline:captions"),
    "motion_graphics_engine": ("plan:graphics", "render:graphics", "timeline:graphics"),
    "visual_style_engine": ("style", "plan:graphics", "plan:captions"),
    "transition_engine": ("plan:transitions", "timeline:transitions"),
    "vfx_engine": ("plan:vfx", "render:vfx"),
    "music_and_sound_design": ("plan:sound_design", "audio:stem:music", "audio:mix"),
    "pacing_intelligence": ("analysis:pacing", "plan:edit", "timeline:sections"),
    "editing_intelligence": ("plan:edit", "timeline:sections"),
    "broll_intelligence": ("analysis:broll", "plan:edit", "timeline:cover"),
    "continuity_intelligence": ("analysis:continuity", "plan:edit"),
    "reframing_intelligence": ("analysis:reframing", "timeline:crops"),
    "audio_mix_engine": ("audio:mix",),
    "audio_improvement": ("derivative:audio", "audio:stem:dialogue", "audio:mix"),
    "video_improvement": ("derivative:video", "timeline:sources"),
    "render_manager": ("render:review",),
    "resolve_adapter": ("resolve:timeline",),
    "timeline_builder": ("timeline:elements",),
    "story_architecture": ("plan:story", "plan:edit", "timeline:sections"),
    "edit_plan_compiler": ("specification", "timeline:elements"),
}


def invalidations(
    kind: ChangeKind,
    *,
    owner: str,
    clip_ids: tuple[str, ...],
    section_ids: tuple[str, ...],
    bus: str = "",
) -> tuple[str, ...]:
    """The outputs that depend on this change, most specific first."""
    sections = tuple(f"timeline:section:{s}" for s in section_ids)
    elements = tuple(f"timeline:element:{c}" for c in clip_ids)
    match kind:
        case (
            ChangeKind.TRIM_CLIP
            | ChangeKind.DELETE_CLIP
            | ChangeKind.DISABLE_CLIP
            | ChangeKind.SET_OPACITY
        ):
            return (
                *elements,
                *sections,
                "resolve:elements",
                "render:review:sections",
                "quality_report",
            )
        case ChangeKind.MIX_BUS_GAIN:
            return (f"audio:stem:{bus}", "audio:mix", "render:review:audio", "quality_report:audio")
        case ChangeKind.MIX_CLIP_GAIN:
            return (f"audio:stem:{bus}", "audio:mix", "render:review:audio", "quality_report:audio")
        case ChangeKind.REMIX:
            return ("audio:mix", "render:review:audio", "quality_report:audio")
        case ChangeKind.REBUILD_TIMELINE:
            return (
                "timeline:changed-elements",
                "resolve:elements",
                "render:review",
                "quality_report",
            )
        case ChangeKind.RESYNC_RESOLVE:
            return ("resolve:timeline",)
        case ChangeKind.ROLLBACK:
            return (
                "timeline:elements",
                "audio:mix",
                "resolve:timeline",
                "render:review",
                "quality_report",
            )
        case ChangeKind.HANDOFF:
            return (*_OWNER_OUTPUTS.get(owner, ()), *sections, "quality_report")
