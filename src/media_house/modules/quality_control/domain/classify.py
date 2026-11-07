"""One table that says what every kind of finding is: its category, how serious, objective or an
opinion, whether it blocks the export, why it matters and who owns the fix.

Severity follows the impact of the problem, not how much the check trusts itself, with two guards
that hold for every finding: a subjective one is never worse than MEDIUM and never blocks the
export; a CRITICAL one the check is not sure of (confidence under 0.6) is reported as HIGH.
"""

from dataclasses import dataclass

from media_house.core.domain import json_codec
from media_house.modules.quality_control.domain.facts import ClipFacts, TimelineFacts
from media_house.modules.quality_control.domain.model import QualityIssue
from media_house.modules.quality_control.domain.values import (
    SEVERITY_RANK,
    Basis,
    Category,
    Severity,
)

#: Below this a CRITICAL claim is not sure enough to stop an export.
BLOCKING_CONFIDENCE = 0.6


@dataclass(frozen=True, slots=True)
class Rule:
    category: Category
    severity: Severity
    basis: Basis
    why: str
    correction: str
    owner: str
    method: str


def _r(
    category: Category,
    severity: Severity,
    why: str,
    correction: str,
    owner: str,
    method: str,
    basis: Basis = Basis.OBJECTIVE,
) -> Rule:
    return Rule(category, severity, basis, why, correction, owner, method)


C, S, B = Category, Severity, Basis
RULES: dict[str, Rule] = {
    # --- structure -------------------------------------------------------------------------
    "structure.media_missing": _r(
        C.STRUCTURE,
        S.CRITICAL,
        "Footage that is not there cannot be played or exported.",
        "Reconnect or re-import the file, then rebuild the clips that use it.",
        "timeline_builder",
        "timeline source table",
    ),
    "structure.unresolved_clip": _r(
        C.STRUCTURE,
        S.HIGH,
        "The clip is kept but cannot show what was planned.",
        "Resolve the missing dependency (asset, graphic, plan) and rebuild the clip.",
        "timeline_builder",
        "clip state",
    ),
    "structure.dependency_open": _r(
        C.STRUCTURE,
        S.HIGH,
        "Something the timeline needs has not been produced yet.",
        "Run the producer named by the dependency, then rebuild.",
        "edit_plan_compiler",
        "dependency manifest",
    ),
    "structure.gap": _r(
        C.STRUCTURE,
        S.HIGH,
        "A gap on the main picture shows as black or as the layer below.",
        "Close the gap or place the intended clip; if it is deliberate, say so with a marker.",
        "timeline_builder",
        "gap scan of the main picture track",
    ),
    "structure.overlap": _r(
        C.STRUCTURE,
        S.HIGH,
        "Overlapping clips on one track hide each other.",
        "Move or trim one of the clips.",
        "timeline_builder",
        "overlap scan",
    ),
    "structure.beyond_timeline": _r(
        C.STRUCTURE,
        S.HIGH,
        "A clip past the end of the timeline is cut off in the export.",
        "Trim the clip or extend the timeline.",
        "timeline_builder",
        "range scan",
    ),
    "structure.no_primary_track": _r(
        C.STRUCTURE,
        S.CRITICAL,
        "Without a main picture track there is no video.",
        "Rebuild the timeline from its specification.",
        "timeline_builder",
        "track scan",
    ),
    "structure.no_sound": _r(
        C.STRUCTURE,
        S.MEDIUM,
        "The timeline has picture but no sound at all.",
        "Check that the audio was meant to be left out.",
        "audio_mix_engine",
        "audio clip count",
    ),
    "structure.frame_rate_assumed": _r(
        C.STRUCTURE,
        S.MEDIUM,
        "A guessed frame rate can make motion judder or drift.",
        "Inspect the footage so the real frame rate is known, then rebuild.",
        "media_inspection",
        "timeline settings",
    ),
    "structure.odd_size": _r(
        C.STRUCTURE,
        S.MEDIUM,
        "Odd picture sizes are refused by common video encoders.",
        "Use even width and height.",
        "edit_plan_compiler",
        "timeline settings",
    ),
    "structure.stretch": _r(
        C.STRUCTURE,
        S.HIGH,
        "A picture scaled differently in width and height looks distorted.",
        "Use the same scale on both axes (fit or crop instead of stretch).",
        "reframing_intelligence",
        "clip transform",
    ),
    "structure.heavy_upscale": _r(
        C.STRUCTURE,
        S.MEDIUM,
        "Strong enlargement makes the picture soft or blocky.",
        "Use a lower punch-in or a higher-resolution source.",
        "reframing_intelligence",
        "clip transform",
    ),
    "structure.transition_unsupported": _r(
        C.STRUCTURE,
        S.MEDIUM,
        "The planned transition cannot be carried out; the cut stays hard.",
        "Choose a supported transition or add it by hand in the editing application.",
        "transition_engine",
        "transition table",
    ),
    "structure.handles_short": _r(
        C.STRUCTURE,
        S.MEDIUM,
        "Not enough footage beyond the cut for the planned transition.",
        "Shorten the transition or use a hard cut.",
        "transition_engine",
        "transition handles",
    ),
    "structure.transition_unavailable": _r(
        C.STRUCTURE,
        S.HIGH,
        "A transition that could not be produced leaves the cut without its effect.",
        "Re-run the transition render or change the transition.",
        "transition_engine",
        "transition execution",
    ),
    "structure.builder_error": _r(
        C.STRUCTURE,
        S.CRITICAL,
        "The timeline builder reports an error.",
        "Fix the cause named in the message and rebuild.",
        "timeline_builder",
        "builder findings",
    ),
    "structure.builder_review": _r(
        C.STRUCTURE,
        S.MEDIUM,
        "A decision is waiting for a person.",
        "Decide which version to keep.",
        "timeline_builder",
        "builder findings",
    ),
    "structure.builder_unsupported": _r(
        C.STRUCTURE,
        S.MEDIUM,
        "An instruction could not be carried out and was kept, not replaced.",
        "Do it by hand or change the instruction.",
        "timeline_builder",
        "builder findings",
    ),
    "structure.element_missing": _r(
        C.STRUCTURE,
        S.HIGH,
        "The compiled specification lists an element the timeline does not have.",
        "Rebuild the timeline from the specification.",
        "timeline_builder",
        "specification comparison",
    ),
    "structure.element_unexpected": _r(
        C.STRUCTURE,
        S.LOW,
        "The timeline has an element the specification does not list.",
        "Check whether it was added on purpose.",
        "timeline_builder",
        "specification comparison",
    ),
    # --- Resolve ---------------------------------------------------------------------------
    "resolve.stale_version": _r(
        C.RESOLVE_SYNC,
        S.MEDIUM,
        "Resolve holds an older version than the timeline.",
        "Carry the current timeline out in Resolve again.",
        "resolve_adapter",
        "binding comparison",
    ),
    "resolve.conflict_open": _r(
        C.RESOLVE_SYNC,
        S.HIGH,
        "A change on both sides is undecided; Resolve renders are blocked.",
        "Decide which side wins for each conflict.",
        "resolve_adapter",
        "binding conflicts",
    ),
    "resolve.unverified": _r(
        C.RESOLVE_SYNC,
        S.LOW,
        "Some clips could not be read back from Resolve.",
        "Look at them in Resolve.",
        "resolve_adapter",
        "binding elements",
    ),
    "resolve.manual_changes": _r(
        C.RESOLVE_SYNC,
        S.LOW,
        "Changes made in Resolve are not in the built timeline yet.",
        "Read them back so they are kept and checked.",
        "resolve_adapter",
        "live timeline read",
    ),
    # --- editing ---------------------------------------------------------------------------
    "editing.flash_frame": _r(
        C.EDITING,
        S.MEDIUM,
        "A clip of a few frames shows as a flash and is usually a slip.",
        "Remove the clip or give it its intended length.",
        "editing_intelligence",
        "shot length",
    ),
    "editing.repeated_footage": _r(
        C.EDITING,
        S.MEDIUM,
        "The same moment is shown twice on the main picture.",
        "Remove one use or replace it with other footage.",
        "editing_intelligence",
        "source range overlap",
    ),
    "editing.shot_too_long": _r(
        C.EDITING,
        S.LOW,
        "A very long shot may lose the viewer.",
        "Cut it down or add cover footage.",
        "pacing_intelligence",
        "shot length",
        B.SUBJECTIVE,
    ),
    "editing.shot_too_short": _r(
        C.EDITING,
        S.LOW,
        "A very short shot may feel rushed.",
        "Give it more time or cut it.",
        "pacing_intelligence",
        "shot length",
        B.SUBJECTIVE,
    ),
    "editing.jump_cut": _r(
        C.EDITING,
        S.OPTIONAL,
        "A cut inside one continuous take can read as a jump.",
        "Add cover footage or accept it as the style.",
        "continuity_intelligence",
        "source continuity",
        B.SUBJECTIVE,
    ),
    "editing.weak_opening": _r(
        C.EDITING,
        S.LOW,
        "A long first shot delays the hook.",
        "Open with the strongest moment or shorten the first shot.",
        "pacing_intelligence",
        "opening length",
        B.SUBJECTIVE,
    ),
    "editing.abrupt_ending": _r(
        C.EDITING,
        S.LOW,
        "The video stops without any closing fade or tail.",
        "Add a closing beat or a fade.",
        "transition_engine",
        "ending check",
        B.SUBJECTIVE,
    ),
    "editing.static_stretch": _r(
        C.EDITING,
        S.OPTIONAL,
        "A long stretch with nothing changing on screen can feel flat.",
        "Consider cover footage, a graphic or a punch-in.",
        "broll_intelligence",
        "visual variation",
        B.SUBJECTIVE,
    ),
    # --- captions and graphics -------------------------------------------------------------
    "caption.overlap": _r(
        C.CAPTIONS,
        S.HIGH,
        "Two captions at once are unreadable.",
        "Re-time the captions so they do not overlap.",
        "caption_engine",
        "cue timing",
    ),
    "caption.too_fast": _r(
        C.CAPTIONS,
        S.MEDIUM,
        "A caption that vanishes too quickly cannot be read.",
        "Lengthen the caption or merge it with its neighbour.",
        "caption_engine",
        "cue timing",
    ),
    "caption.reading_speed": _r(
        C.CAPTIONS,
        S.MEDIUM,
        "Too many characters per second cannot be read comfortably.",
        "Shorten the text or give it more time.",
        "caption_engine",
        "cue timing",
    ),
    "caption.too_dense": _r(
        C.CAPTIONS,
        S.MEDIUM,
        "A caption with too much text crowds the picture.",
        "Split or shorten the caption.",
        "caption_engine",
        "cue text",
    ),
    "caption.beyond_timeline": _r(
        C.CAPTIONS,
        S.HIGH,
        "A caption past the end is never shown.",
        "Re-time or remove it.",
        "caption_engine",
        "cue range",
    ),
    "caption.empty": _r(
        C.CAPTIONS,
        S.MEDIUM,
        "An empty caption shows nothing.",
        "Remove it or restore its text.",
        "caption_engine",
        "cue text",
    ),
    "caption.missing": _r(
        C.CAPTIONS,
        S.LOW,
        "A video with speech and no captions loses viewers who watch muted.",
        "Plan captions for the speech.",
        "caption_engine",
        "caption presence",
        B.SUBJECTIVE,
    ),
    "caption.emoji_flash": _r(
        C.CAPTIONS,
        S.LOW,
        "An emoji shown for a split second reads as a glitch.",
        "Hold it longer or drop it.",
        "caption_engine",
        "emoji timing",
        B.SUBJECTIVE,
    ),
    "caption.style_inconsistent": _r(
        C.CAPTIONS,
        S.MEDIUM,
        "Captions in different styles look unintended.",
        "Use one caption style throughout.",
        "caption_engine",
        "design choices",
    ),
    "graphic.unknown_zone": _r(
        C.GRAPHICS,
        S.MEDIUM,
        "A graphic outside the layout zones may collide with the platform's interface.",
        "Place it in a zone of the channel identity.",
        "motion_graphics_engine",
        "zone check",
    ),
    "graphic.outside_frame": _r(
        C.GRAPHICS,
        S.HIGH,
        "Part of the graphic is cut off by the edge of the picture.",
        "Move it back inside the frame and the safe area.",
        "motion_graphics_engine",
        "transform bounds",
    ),
    "graphic.overlap": _r(
        C.GRAPHICS,
        S.MEDIUM,
        "Graphics on top of each other hide one another.",
        "Move or re-time one of them.",
        "motion_graphics_engine",
        "overlap scan",
    ),
    "graphic.font_missing": _r(
        C.GRAPHICS,
        S.HIGH,
        "A missing font makes text render in another typeface.",
        "Install or replace the font of the channel identity.",
        "visual_style_engine",
        "identity fonts",
    ),
    "graphic.brand_missing": _r(
        C.GRAPHICS,
        S.HIGH,
        "A required brand asset is missing.",
        "Add the logo or brand asset to the library.",
        "visual_style_engine",
        "identity brand slots",
    ),
    "graphic.missing_asset": _r(
        C.GRAPHICS,
        S.HIGH,
        "A graphic could not be produced and is not shown.",
        "Render the graphic again.",
        "motion_graphics_engine",
        "clip state",
    ),
    "graphic.style_inconsistent": _r(
        C.GRAPHICS,
        S.LOW,
        "Graphics of one kind in different styles look unintended.",
        "Use one style per kind of graphic.",
        "visual_style_engine",
        "design choices",
    ),
    # --- the rendered picture --------------------------------------------------------------
    "render.decode_errors": _r(
        C.TECHNICAL_VIDEO,
        S.CRITICAL,
        "The file has errors where it is decoded: it may be corrupted.",
        "Render again; if it repeats, find the clip that breaks.",
        "render_manager",
        "full decode",
    ),
    "render.duration_mismatch": _r(
        C.TECHNICAL_VIDEO,
        S.HIGH,
        "The render is not as long as the timeline: something is missing or added.",
        "Render again; check the end of the timeline.",
        "render_manager",
        "duration comparison",
    ),
    "render.size_mismatch": _r(
        C.TECHNICAL_VIDEO,
        S.HIGH,
        "The render is not the size of the timeline.",
        "Check the render settings.",
        "render_manager",
        "stream comparison",
    ),
    "render.rate_mismatch": _r(
        C.TECHNICAL_VIDEO,
        S.HIGH,
        "The render's frame rate is not the timeline's.",
        "Check the render settings.",
        "render_manager",
        "stream comparison",
    ),
    "video.no_video_stream": _r(
        C.TECHNICAL_VIDEO,
        S.CRITICAL,
        "The file has no picture.",
        "Render again.",
        "render_manager",
        "stream comparison",
    ),
    "video.black_frames": _r(
        C.TECHNICAL_VIDEO,
        S.CRITICAL,
        "Black frames nobody planned look like a failure.",
        "Find the missing or offline clip at that place and restore it.",
        "timeline_builder",
        "black frame detection",
    ),
    "video.black_edge": _r(
        C.TECHNICAL_VIDEO,
        S.LOW,
        "A black frame at the very start or end is usually an encoder or trim slip.",
        "Trim to the first and last real picture.",
        "timeline_builder",
        "black frame detection",
    ),
    "video.frozen_frames": _r(
        C.TECHNICAL_VIDEO,
        S.HIGH,
        "A picture that stops moving by accident looks like a stall.",
        "Find the clip that is missing or stuck at that place.",
        "timeline_builder",
        "freeze detection",
    ),
    # --- looks -----------------------------------------------------------------------------
    "visual.crushed_shadows": _r(
        C.VISUAL,
        S.MEDIUM,
        "Large dark areas with no detail look muddy.",
        "Lift the shadows of the source or choose another shot.",
        "video_improvement",
        "frame statistics",
    ),
    "visual.clipped_highlights": _r(
        C.VISUAL,
        S.MEDIUM,
        "Blown-out highlights lose detail that cannot be recovered.",
        "Lower the exposure of the source or choose another shot.",
        "video_improvement",
        "frame statistics",
    ),
    "visual.underexposed": _r(
        C.VISUAL,
        S.MEDIUM,
        "A very dark picture is hard to watch.",
        "Raise the exposure of the source.",
        "video_improvement",
        "frame statistics",
    ),
    "visual.overexposed": _r(
        C.VISUAL,
        S.MEDIUM,
        "A very bright picture washes out.",
        "Lower the exposure of the source.",
        "video_improvement",
        "frame statistics",
    ),
    "visual.soft_focus": _r(
        C.VISUAL,
        S.LOW,
        "A noticeably softer shot may be out of focus.",
        "Look at the shot; replace it if it is not sharp.",
        "editing_intelligence",
        "frame sharpness",
    ),
    "visual.color_mismatch": _r(
        C.VISUAL,
        S.MEDIUM,
        "A colour jump between neighbouring shots looks like a mistake.",
        "Match the shots or let the look be set again.",
        "video_improvement",
        "frame colour comparison",
    ),
    "visual.unexpected_borders": _r(
        C.VISUAL,
        S.MEDIUM,
        "Black bars where the frame should be filled.",
        "Check the fit mode and the crop of the clip.",
        "reframing_intelligence",
        "border detection",
    ),
    "visual.bad_crop": _r(
        C.VISUAL,
        S.MEDIUM,
        "Unequal black borders suggest an off-centre crop.",
        "Check the crop of the clip.",
        "reframing_intelligence",
        "border detection",
    ),
    # --- sound -----------------------------------------------------------------------------
    "audio.missing": _r(
        C.AUDIO,
        S.CRITICAL,
        "The timeline has sound but the render has none.",
        "Render again with the audio mix.",
        "audio_mix_engine",
        "stream comparison",
    ),
    "audio.sync_drift": _r(
        C.AUDIO,
        S.HIGH,
        "Sound and picture of different length drift apart.",
        "Check the audio offset and the render.",
        "render_manager",
        "stream timing",
    ),
    "audio.loudness_off": _r(
        C.AUDIO,
        S.MEDIUM,
        "Loudness off the delivery target plays too quiet or too loud.",
        "Mix again with the delivery profile's target.",
        "audio_mix_engine",
        "loudness measurement",
    ),
    "audio.true_peak": _r(
        C.AUDIO,
        S.HIGH,
        "Peaks above the ceiling distort on some platforms.",
        "Lower the master level or the limiter ceiling.",
        "audio_mix_engine",
        "true-peak measurement",
    ),
    "audio.clipping": _r(
        C.AUDIO,
        S.HIGH,
        "Samples at full scale are distorted.",
        "Lower the level before the master.",
        "audio_mix_engine",
        "sample-peak measurement",
    ),
    "audio.range_wide": _r(
        C.AUDIO,
        S.LOW,
        "A very wide loudness range makes quiet parts hard to hear.",
        "Compress the dynamics a little.",
        "audio_mix_engine",
        "loudness range",
    ),
    "audio.dialogue_inconsistent": _r(
        C.AUDIO,
        S.MEDIUM,
        "Dialogue that jumps in level makes the viewer reach for the volume.",
        "Level the dialogue clips more closely.",
        "audio_mix_engine",
        "per-clip loudness",
    ),
    "audio.unexpected_silence": _r(
        C.AUDIO,
        S.HIGH,
        "Silence where sound was planned is a missing or muted clip.",
        "Find the clip that should be playing there.",
        "audio_mix_engine",
        "silence detection",
    ),
    "audio.abrupt_end": _r(
        C.AUDIO,
        S.MEDIUM,
        "Sound that stops dead at the end clicks or feels cut off.",
        "Add a fade out.",
        "audio_mix_engine",
        "end level",
    ),
    # --- plan comparison -------------------------------------------------------------------
    "story.section_missing": _r(
        C.STORY,
        S.MEDIUM,
        "A section the story plan has is not on the timeline (it may have been cut on purpose).",
        "Confirm the section was meant to go.",
        "story_architecture",
        "story plan comparison",
    ),
    "story.order_deviates": _r(
        C.STORY,
        S.MEDIUM,
        "The sections are not in the order the story plan recommends.",
        "Confirm the order was meant.",
        "story_architecture",
        "story plan comparison",
    ),
    "audio.mix_finding": _r(
        C.AUDIO,
        S.MEDIUM,
        "The audio mix engine measured a problem in the mix.",
        "See the message; mix again after the cause is fixed.",
        "audio_mix_engine",
        "audio mix validation",
    ),
    "continuity.finding": _r(
        C.CONTINUITY,
        S.MEDIUM,
        "Continuity intelligence found a cut that may not read smoothly.",
        "See the suggested repair of the continuity analysis.",
        "continuity_intelligence",
        "continuity analysis",
    ),
    "story.payoff_missing": _r(
        C.STORY,
        S.MEDIUM,
        "A setup has no payoff on the timeline: the viewer is left waiting.",
        "Restore the payoff or drop the setup.",
        "story_architecture",
        "story plan comparison",
    ),
}


def _bucket(timeline: TimelineFacts, start: int | None) -> str:
    if start is None:
        return ""
    return str(start // max(1, round(timeline.settings.fps * 2)))


def clip_sections(
    timeline: TimelineFacts, clips: tuple[ClipFacts, ...], frames: tuple[int, ...]
) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for clip in clips:
        if clip.section_id:
            found[clip.section_id] = None
    for frame in frames:
        section = timeline.section_of_frame(frame)
        if section:
            found[section] = None
    return tuple(found)


def make_issue(
    timeline: TimelineFacts,
    code: str,
    description: str,
    *,
    severity: Severity | None = None,
    confidence: float = 0.9,
    start_frame: int | None = None,
    end_frame: int | None = None,
    clips: tuple[ClipFacts, ...] = (),
    tracks: tuple[str, ...] = (),
    sources: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
    evidence_assets: tuple[str, ...] = (),
    analysis_refs: tuple[str, ...] = (),
    sections: tuple[str, ...] = (),
    why: str | None = None,
    correction: str | None = None,
    origin: str = "quality_control",
    key: str = "",
    measurements: tuple[tuple[str, str], ...] = (),
    code_name: str | None = None,
    basis: Basis | None = None,
) -> QualityIssue:
    """``code`` picks the rule; ``code_name`` (carried-over findings) names the finding itself."""
    rule = RULES[code]
    kind = basis or rule.basis
    level = severity or rule.severity
    if kind is Basis.SUBJECTIVE and SEVERITY_RANK[level] > SEVERITY_RANK[Severity.MEDIUM]:
        level = Severity.MEDIUM
    if level is Severity.CRITICAL and confidence < BLOCKING_CONFIDENCE:
        level = Severity.HIGH
    clip_ids = tuple(c.clip_id for c in clips)
    frames = tuple(f for f in (start_frame, end_frame) if f is not None)
    sources_found = dict.fromkeys(
        (*sources, *(c.original_asset_id for c in clips if c.original_asset_id))
    )
    fingerprint = json_codec.digest(
        [
            code_name or code,
            sorted(clip_ids),
            key,
            "" if clip_ids else _bucket(timeline, start_frame),
        ]
    )[:20]
    return QualityIssue(
        issue_id="q_" + json_codec.digest([fingerprint, timeline.content_hash])[:16],
        fingerprint=fingerprint,
        category=rule.category,
        code=code_name or code,
        severity=level,
        confidence=max(0.0, min(1.0, confidence)),
        basis=kind,
        description=description,
        why_it_matters=why or rule.why,
        suggested_correction=correction or rule.correction,
        owner_hint=rule.owner,
        detection_method=rule.method,
        blocks_export=level is Severity.CRITICAL and kind is Basis.OBJECTIVE,
        start_frame=start_frame,
        end_frame=end_frame,
        affected_clips=clip_ids,
        affected_tracks=tuple(dict.fromkeys((*tracks, *(c.track_id for c in clips)))),
        source_assets=tuple(s for s in sources_found if s),
        sections=sections or clip_sections(timeline, clips, frames),
        evidence=evidence,
        measurements=measurements,
        evidence_assets=evidence_assets,
        analysis_refs=analysis_refs,
        origin=origin,
    )
