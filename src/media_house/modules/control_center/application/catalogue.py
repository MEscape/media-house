"""The static module catalogue the pipeline overview shows.

SOURCES (nothing here is invented; re-check them when a module changes):

* ``description``, ``limits`` and ``validation``: summarised from each module's ``README.md``
  (sections "Not done / limits", "Known limitations", "Honest limits", "Limitations") as of
  2026-11-09.
* ``pipeline`` (``needs`` / ``wants`` / lane): copied from ``media_pipeline/domain/plan.py``
  ``STAGES``. That table is NOT part of ``media_pipeline.application.contracts``, so it is kept
  here as a static copy; ``tests/unit/control_center/test_catalogue.py`` fails when it drifts.
* ``reads_contracts``: the ``modules.<x>.application.contracts`` imports found in each module's
  non-presentation source on 2026-11-09.
* Layer grouping: as requested for the control center. Note that ``ARCHITECTURE.md`` lists
  ``audio_improvement`` and ``video_improvement`` under Layer 1; here they sit with Layer 2.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class ImplementationStatus(StrEnum):
    IMPLEMENTED = "Implemented"
    IMPLEMENTED_WITH_GAPS = "Implemented with gaps"


class ValidationLevel(StrEnum):
    SYNTHETIC = "Synthetic data only"
    SYNTHETIC_AND_PIPELINE_RUN = "Synthetic data + one pipeline run (no real footage)"
    NO_REAL_RESOLVE = "Never run against a real DaVinci Resolve"


@dataclass(frozen=True, slots=True)
class PipelineTask:
    """One task kind of ``media_pipeline``'s dependency graph."""

    task: str
    lane: str
    #: Must have delivered before the task can run.
    needs: tuple[str, ...] = ()
    #: Waited for when part of the same run, used if present, never required.
    wants: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModuleEntry:
    name: str
    title: str
    description: str
    status: ImplementationStatus
    validation: ValidationLevel
    limits: tuple[str, ...]
    reads_contracts: tuple[str, ...] = ()
    pipeline: PipelineTask | None = None
    #: Where the desktop shows this module (empty: no UI yet).
    ui: str = ""


@dataclass(frozen=True, slots=True)
class LayerEntry:
    key: str
    title: str
    summary: str
    modules: tuple[ModuleEntry, ...] = field(default_factory=tuple)


_S = ValidationLevel.SYNTHETIC
_SP = ValidationLevel.SYNTHETIC_AND_PIPELINE_RUN
_NR = ValidationLevel.NO_REAL_RESOLVE
_OK = ImplementationStatus.IMPLEMENTED
_GAPS = ImplementationStatus.IMPLEMENTED_WITH_GAPS


def _m(
    name: str,
    title: str,
    description: str,
    limits: tuple[str, ...],
    *,
    reads: tuple[str, ...],
    task: PipelineTask | None = None,
    status: ImplementationStatus = _OK,
    validation: ValidationLevel = _S,
    ui: str = "",
) -> ModuleEntry:
    return ModuleEntry(name, title, description, status, validation, limits, reads, task, ui)


_LIB = "media_library"
_INS = "media_inspection"

LAYERS: tuple[LayerEntry, ...] = (
    LayerEntry(
        "layer1",
        "Layer 1 - Foundation",
        "Storage, technical ground truth, proxies and asset relations.",
        (
            _m(
                "media_library",
                "Media Library",
                "Single source of truth for original media, derived assets (thumbnails, "
                "transcripts, analyses) and their metadata; the cache every module reuses.",
                (
                    "No audio analysis, background removal or video encoding: other modules do "
                    "that and store results here.",
                ),
                reads=(),
                task=PipelineTask("thumbnail", "light"),
                ui="Media Library page",
            ),
            _m(
                "media_inspection",
                "Media Inspection",
                "Technical ground truth of audio/video assets: streams, properties and "
                "technical problems. Never improves or interprets media.",
                (
                    "Only what ffprobe and a decode pass show; mid-stream changes are not "
                    "detected.",
                    "Unmarked interlacing is not detected.",
                    "Images and image sequences are not inspected yet.",
                    "Drift is judged from packet timestamps (a possible problem, not proof).",
                ),
                reads=(_LIB,),
                task=PipelineTask("inspect", "light"),
            ),
            _m(
                "asset_relationships",
                "Asset Relationships",
                "Stores claims (with evidence) about how two library assets relate, plus the "
                "decisions people make about them; projects recorded lineage at read time.",
                (
                    "Audio fingerprints, visual/semantic similarity, timecode and scene detection "
                    "are not built.",
                    "Detector precision has not been measured on real footage.",
                    "Nothing triggers reconciliation or detection automatically.",
                    "No UI and no domain events yet.",
                ),
                reads=(_INS, _LIB, "proxy_manager"),
                task=PipelineTask("relationships", "light", wants=("inspect",)),
                status=_GAPS,
            ),
            _m(
                "proxy_manager",
                "Proxy Manager",
                "Creates verifiable, replaceable proxies of original video, tracks which one a "
                "source is served from and replaces it without losing the last good one.",
                (
                    "No priority queue, batch or retry policy; no adaptive profile selection.",
                    "Software encoders only; parameters are drafts until benchmarked on real "
                    "footage.",
                ),
                reads=(_INS, _LIB),
                task=PipelineTask("proxy", "cpu", wants=("inspect",)),
            ),
        ),
    ),
    LayerEntry(
        "layer2",
        "Layer 2 - Improvement & Analysis",
        "Cleaner sound and picture; what is heard, seen and said, and how they relate.",
        (
            _m(
                "audio_improvement",
                "Audio Improvement",
                "Makes audio cleaner, more consistent and production-ready as a derived asset; "
                "never interprets content.",
                (
                    "Thresholds calibrated on synthetic recordings.",
                    "Severe clipping cannot be fully restored (reported as a warning).",
                    "Background-music detection inside one recording is not implemented.",
                    "Mono and stereo only (multichannel is downmixed).",
                ),
                reads=(_INS, _LIB),
                task=PipelineTask("audio_improvement", "cpu", wants=("inspect",)),
            ),
            _m(
                "audio_intelligence",
                "Audio Intelligence",
                "Turns an audio/video asset into a typed, word-level transcript timeline with "
                "acoustics, events and editing signals.",
                (
                    "Out of scope: subtitles, rendering, noise reduction/mastering.",
                    "Speaker labels only when diarization is configured.",
                ),
                reads=("audio_improvement", _INS, _LIB),
                task=PipelineTask("audio_analysis", "gpu", wants=("inspect",)),
            ),
            _m(
                "video_improvement",
                "Video Improvement",
                "Makes video natural, consistent and technically correct with local tools; "
                "preserves audio, metadata, timecode and frame count.",
                (
                    "Thresholds set on synthetic footage, not real camera material.",
                    "Corrections are static per clip; changing exposure is only warned about.",
                    "No stabilization, lens or rolling-shutter correction; HDR input refused.",
                ),
                reads=(_INS, _LIB),
                task=PipelineTask("video_improvement", "cpu", wants=("inspect",)),
            ),
            _m(
                "video_intelligence",
                "Video Intelligence",
                "What is visible, when, who/what, where in frame, how it was shot and how sure "
                "each measurement is.",
                (
                    "Push-in and optical zoom look the same; dissolves between moving shots are "
                    "missed.",
                    "Picture-in-picture overlays are not detected; no 'product' class.",
                    "Tracking works on sampled frames; long occlusions start a new track.",
                    "Scenes and content type need the embedding analyzer.",
                ),
                reads=(_INS, _LIB, "video_improvement"),
                task=PipelineTask("video_analysis", "cpu", wants=("inspect",)),
            ),
            _m(
                "content_intelligence",
                "Content Intelligence",
                "What a recording communicates: topics, key statements, relations, importance "
                "and where visuals or on-screen text could help (advisory).",
                (
                    "English and German cues only.",
                    "Without an embedding model, topic/redundancy detection is word overlap.",
                    "Scores are uncalibrated heuristics.",
                ),
                reads=("audio_intelligence", _LIB, "video_intelligence"),
                task=PipelineTask("content_analysis", "gpu", needs=("audio_analysis",)),
            ),
            _m(
                "audio_visual_alignment",
                "Audio-Visual Alignment",
                "Relates what is heard to what is seen from existing analyses, with timing and "
                "evidence strength. Decides nothing.",
                (
                    "No speaker labels without diarization; only the largest face has a mouth "
                    "signal.",
                    "One video and one audio per run.",
                    "Not measured against labelled real footage.",
                ),
                reads=(
                    "audio_intelligence",
                    "content_intelligence",
                    _INS,
                    _LIB,
                    "video_intelligence",
                ),
                task=PipelineTask(
                    "alignment",
                    "light",
                    needs=("audio_analysis", "video_analysis"),
                    wants=("content_analysis", "inspect"),
                ),
            ),
        ),
    ),
    LayerEntry(
        "layer3",
        "Layer 3 - Editorial",
        "Story, rhythm, supporting footage, continuity, framing and the edit plan.",
        (
            _m(
                "story_architecture",
                "Story Architecture",
                "One validated story plan: order, purpose of each part, opening, essentials, "
                "risks and where a picture would help.",
                (
                    "Only as good as the content analysis (English/German cues).",
                    "No chronology model; recordings are concatenated in the given order.",
                    "Validated on synthetic transcripts and hand-built analyses.",
                ),
                reads=(
                    "asset_relationships",
                    "audio_visual_alignment",
                    "content_intelligence",
                    _LIB,
                ),
                task=PipelineTask(
                    "story", "light", needs=("content_analysis",), wants=("alignment",)
                ),
            ),
            _m(
                "editing_intelligence",
                "Editing Intelligence",
                "Turns the story plan and stored analyses into one backend-neutral edit plan: "
                "keep, cut, shorten, reorder, cover. Decides and explains; never edits media.",
                (
                    "No real footage, speech/vision model or long recording tested.",
                    "Cut-point confidence and thresholds are uncalibrated heuristics.",
                    "Optional advisors (coverage, continuity) are not implemented.",
                ),
                reads=(
                    "audio_intelligence",
                    "audio_visual_alignment",
                    "broll_intelligence",
                    "content_intelligence",
                    "continuity_intelligence",
                    _INS,
                    _LIB,
                    "pacing_intelligence",
                    "reframing_intelligence",
                    "story_architecture",
                    "video_intelligence",
                ),
                task=PipelineTask(
                    "edit_plan",
                    "light",
                    needs=("story",),
                    wants=(
                        "alignment",
                        "broll",
                        "inspect",
                        "pacing",
                        "reframing",
                        "video_analysis",
                    ),
                ),
            ),
            _m(
                "continuity_intelligence",
                "Continuity Intelligence",
                "Judges the cuts of a proposed edit: jumps, look/light breaks, eyeline flips, "
                "skipped actions, sound cut through, chronology.",
                (
                    "Evaluated on 18 labelled synthetic cuts; real footage not validated.",
                    "Alternate takes are not searched.",
                    "Separately recorded audio without an alignment is 'unknown'.",
                ),
                reads=(
                    "asset_relationships",
                    "audio_intelligence",
                    "audio_visual_alignment",
                    "broll_intelligence",
                    "content_intelligence",
                    _LIB,
                    "video_intelligence",
                ),
                task=PipelineTask(
                    "continuity",
                    "light",
                    needs=("edit_plan",),
                    wants=("alignment", "audio_analysis", "broll", "inspect", "video_analysis"),
                ),
            ),
            _m(
                "broll_intelligence",
                "B-roll Intelligence",
                "Finds library footage that genuinely supports passages needing it: which asset, "
                "which part, how well it fits and what is missing.",
                (
                    "Not validated on real footage or the real CLIP/sentence models.",
                    "All thresholds are uncalibrated design choices.",
                    "Shots without observations are found only by picture embedding.",
                ),
                reads=(
                    "asset_relationships",
                    "audio_intelligence",
                    "audio_visual_alignment",
                    "content_intelligence",
                    _LIB,
                    "pacing_intelligence",
                    "story_architecture",
                    "video_intelligence",
                ),
                task=PipelineTask(
                    "broll",
                    "light",
                    needs=("content_analysis",),
                    wants=(
                        "alignment",
                        "audio_analysis",
                        "inspect",
                        "pacing",
                        "story",
                        "video_analysis",
                    ),
                ),
            ),
            _m(
                "pacing_intelligence",
                "Pacing Intelligence",
                "Measures where the rhythm helps or hurts the material and recommends fixes with "
                "their cost. Decides no edit.",
                (
                    "No real footage or real speech tested.",
                    "Density and repetition are heuristics; picture findings need a video "
                    "analysis.",
                    "Beat alignment uses the energy envelope, not a music analysis.",
                ),
                reads=(
                    "audio_intelligence",
                    "audio_visual_alignment",
                    "content_intelligence",
                    _INS,
                    _LIB,
                    "story_architecture",
                    "video_intelligence",
                ),
                task=PipelineTask(
                    "pacing",
                    "light",
                    needs=("audio_analysis",),
                    wants=("alignment", "content_analysis", "inspect", "story", "video_analysis"),
                ),
            ),
            _m(
                "reframing_intelligence",
                "Reframing Intelligence",
                "Recommends how each shot is framed for one output format: crop window, moving "
                "keyframes, original framing, or 'no crop works'.",
                (
                    "Evaluated on 15 labelled synthetic situations.",
                    "Positions are only as good as the tracks (whole-person boxes, no hands).",
                    "One target format per analysis.",
                ),
                reads=(
                    "audio_visual_alignment",
                    "continuity_intelligence",
                    _INS,
                    _LIB,
                    "story_architecture",
                    "video_intelligence",
                ),
                task=PipelineTask(
                    "reframing",
                    "light",
                    needs=("video_analysis",),
                    wants=("alignment", "inspect", "story"),
                ),
            ),
        ),
    ),
    LayerEntry(
        "layer4",
        "Layer 4 - Finishing",
        "Channel style, graphics, captions, transitions, effects, music and stills.",
        (
            _m(
                "visual_style_engine",
                "Visual Style Engine",
                "Single source of truth for the channel's look as versioned data, resolved per "
                "project and output into a pinned style.",
                (
                    "Brand asset files (logo, mascot, wordmark) do not exist yet.",
                    "Brand red is a proposal awaiting owner confirmation; fonts are look-alikes.",
                    "Drift validation judges declared usage, not rendered pixels.",
                ),
                reads=(_LIB,),
                task=PipelineTask("visual_style", "light", wants=("edit_plan", "inspect")),
            ),
            _m(
                "motion_graphics_engine",
                "Motion Graphics Engine",
                "Builds, animates, renders and validates graphics (rank cards, price tags, lower "
                "thirds, end screen) and renders captions/transition overlays for others.",
                (
                    "Rendering needs a one-time setup; otherwise renders are 'not_available'.",
                    "CPU-only render speed is roughly 0.3-1 s per 1080p frame.",
                    "Brand asset files do not exist yet (logo sting not drawn).",
                ),
                reads=(
                    "content_intelligence",
                    _LIB,
                    "visual_asset_generation",
                    "visual_style_engine",
                ),
                task=PipelineTask(
                    "graphics",
                    "cpu",
                    needs=("inspect", "visual_style"),
                    wants=("audio_analysis", "edit_plan", "story", "video_analysis"),
                ),
                status=_GAPS,
            ),
            _m(
                "caption_engine",
                "Caption Engine",
                "Decides burned-in captions: which words, grouping, timing, placement, emphasis, "
                "animation request, and the closed-caption text.",
                (
                    "Emoji are drawn only under an identity v2+ style.",
                    "Speakers only with diarization; no translation.",
                    "Validated on synthetic German transcripts.",
                ),
                reads=(
                    "audio_intelligence",
                    "content_intelligence",
                    _INS,
                    _LIB,
                    "motion_graphics_engine",
                    "reframing_intelligence",
                    "video_intelligence",
                    "visual_style_engine",
                ),
                task=PipelineTask(
                    "captions",
                    "cpu",
                    needs=("inspect", "visual_style"),
                    wants=(
                        "audio_analysis",
                        "content_analysis",
                        "edit_plan",
                        "graphics",
                        "reframing",
                        "story",
                        "video_analysis",
                    ),
                ),
            ),
            _m(
                "transition_engine",
                "Transition Engine",
                "Decides the transition between adjacent clips and every audio transition; a "
                "hard cut unless a transition adds meaning.",
                (
                    "Main picture layer only (cover edges stay cuts).",
                    "No moves to music beats.",
                    "Validated on synthetic sequences and a short FFmpeg test clip.",
                ),
                reads=(
                    "audio_intelligence",
                    "continuity_intelligence",
                    _INS,
                    _LIB,
                    "motion_graphics_engine",
                    "pacing_intelligence",
                    "video_intelligence",
                    "visual_style_engine",
                ),
                task=PipelineTask(
                    "transitions",
                    "cpu",
                    needs=("inspect", "visual_style"),
                    wants=(
                        "audio_analysis",
                        "continuity",
                        "edit_plan",
                        "graphics",
                        "pacing",
                        "story",
                        "video_analysis",
                    ),
                ),
            ),
            _m(
                "vfx_engine",
                "VFX Engine",
                "Shot-level effects: punch-ins, speed changes with exact time maps, privacy "
                "concealment, restrained looks; rendered as derived ProRes clips.",
                (
                    "No matting/inpainting model ships: those effects are 'not_available'.",
                    "Video only: no audio is processed.",
                ),
                reads=(
                    "audio_intelligence",
                    "audio_visual_alignment",
                    _INS,
                    _LIB,
                    "reframing_intelligence",
                    "video_intelligence",
                    "visual_style_engine",
                ),
                task=PipelineTask(
                    "vfx",
                    "cpu",
                    needs=("inspect", "visual_style"),
                    wants=("audio_analysis", "edit_plan", "reframing", "video_analysis"),
                ),
                status=_GAPS,
            ),
            _m(
                "music_and_sound_design",
                "Music & Sound Design",
                "Plans music, effects, silence and mix intent from the owner's sound library "
                "(the only audio source); owns licence facts and measurements.",
                (
                    "Mood/genre are source-stated only; vocals and meter are not measured.",
                    "Tracks whose licence is not cleared for commercial use are never selected.",
                    "Validated on synthetic click/drum tracks and synthesised libraries.",
                ),
                reads=("audio_intelligence", _LIB, "pacing_intelligence", "story_architecture"),
                task=PipelineTask(
                    "sound_design",
                    "cpu",
                    needs=("inspect", "visual_style"),
                    wants=(
                        "audio_analysis",
                        "captions",
                        "edit_plan",
                        "graphics",
                        "pacing",
                        "story",
                        "transitions",
                    ),
                ),
                ui="Sound Library page",
            ),
            _m(
                "visual_asset_generation",
                "Visual Asset Generation",
                "Turns library pictures into reusable stills: cutouts, enhanced variants, exact "
                "frame grabs, delivery copies and quality checks. Prefers reuse.",
                (
                    "No image-generation model on this installation: generation is not available.",
                    "Cutouts are 8-bit RGBA PNG; no manual mask correction.",
                    "Never validated on real product photos.",
                ),
                reads=("broll_intelligence", _INS, _LIB, "visual_style_engine"),
                status=_GAPS,
            ),
            _m(
                "thumbnail_design",
                "Thumbnail Design",
                "The channel's YouTube thumbnails as A/B/C variants for Test & Compare: headline, "
                "creator reaction (uploaded, tagged collection or picked from the footage), "
                "product stickers, retailer logo, shop phone and story badge, checked for the "
                "feed.",
                (
                    "One fixed layout (the 2025/26 channel system); no free placement.",
                    "Reaction moments come from per-shot face cues, not from every frame.",
                    "The headline is the owner's: no title is written by a model.",
                ),
                reads=(
                    _LIB,
                    "video_intelligence",
                    "visual_asset_generation",
                    "visual_style_engine",
                ),
                task=PipelineTask(
                    "thumbnail_design",
                    "cpu",
                    needs=("inspect", "visual_style"),
                    wants=("edit_plan", "video_analysis"),
                ),
                status=_GAPS,
                ui="Thumbnail page",
            ),
        ),
    ),
    LayerEntry(
        "layer5",
        "Layer 5 - Timeline",
        "One validated timeline specification, built, carried out in Resolve and mixed.",
        (
            _m(
                "edit_plan_compiler",
                "Edit Plan Compiler",
                "Turns the edit and stored Layer 4 plans into one validated, versioned timeline "
                "specification with exact frames and traced files.",
                (
                    "No project ownership check on library assets.",
                    "Levels and ducking are instructions for the mix engine, not a mix.",
                    "Validated on synthetic fixtures and one synthetic no-speech clip.",
                ),
                reads=(
                    "caption_engine",
                    _INS,
                    _LIB,
                    "motion_graphics_engine",
                    "music_and_sound_design",
                    "reframing_intelligence",
                    "transition_engine",
                    "vfx_engine",
                    "visual_style_engine",
                ),
                task=PipelineTask(
                    "timeline",
                    "light",
                    needs=("inspect", "visual_style"),
                    wants=(
                        "captions",
                        "edit_plan",
                        "graphics",
                        "reframing",
                        "sound_design",
                        "story",
                        "transitions",
                        "vfx",
                    ),
                ),
            ),
            _m(
                "timeline_builder",
                "Timeline Builder",
                "Constructs the application-independent timeline from the specification: tracks, "
                "one source table, processing chains, markers, provenance.",
                (
                    "No nested/compound sequences.",
                    "Square pixels and progressive picture assumed.",
                    "Validated on synthetic fixtures and one synthetic no-speech clip.",
                ),
                reads=("edit_plan_compiler", _LIB, "proxy_manager"),
                task=PipelineTask("timeline_build", "light", needs=("timeline",)),
            ),
            _m(
                "resolve_adapter",
                "Resolve Adapter",
                "Carries a built timeline out in DaVinci Resolve, reads it back to verify, reads "
                "a person's fixes back, renders and checks. The only module that knows Resolve.",
                (
                    "Not run against a real Resolve: behaviour assumed from the documentation.",
                    "Transitions, retime, native effects, moving crops, clip gain are known gaps.",
                    "Colour management only set for rec709.",
                ),
                reads=(_INS, _LIB, "proxy_manager", "timeline_builder"),
                task=PipelineTask(
                    "resolve_execute", "light", needs=("timeline_build",), wants=("audio_mix",)
                ),
                validation=_NR,
                ui="DaVinci Resolve page",
            ),
            _m(
                "audio_mix_engine",
                "Audio Mix Engine",
                "Balances dialogue, music, effects and ambience on buses, renders stems and the "
                "final mix, measures it against a delivery profile. Needs no Resolve.",
                (
                    "Listening quality is not claimed: no one has listened to a real-footage mix.",
                    "No dynamic EQ, multiband compression, gate or de-esser.",
                    "Sample-peak limiter; true peak verified by measurement only.",
                ),
                reads=(_LIB, "timeline_builder"),
                task=PipelineTask("audio_mix", "cpu", needs=("timeline_build",)),
            ),
        ),
    ),
    LayerEntry(
        "layer6",
        "Layer 6 - Review & Export",
        "Quality report, revisions, final polish, renders, export validation and export.",
        (
            _m(
                "quality_control",
                "Quality Control",
                "Looks at a built timeline (and a preview render when given) and reports what is "
                "wrong with evidence. Changes and approves nothing.",
                (
                    "Focus, composition, flicker and caption spelling are not measured.",
                    "Picture checks need a preview render.",
                    "The live Resolve look depends on resolve_adapter (never run on real Resolve).",
                ),
                reads=(
                    "audio_mix_engine",
                    "continuity_intelligence",
                    "edit_plan_compiler",
                    _INS,
                    _LIB,
                    "resolve_adapter",
                    "review_orchestrator",
                    "story_architecture",
                    "timeline_builder",
                ),
                task=PipelineTask("quality_control", "cpu", needs=("timeline_build",)),
                validation=_SP,
            ),
            _m(
                "revision_engine",
                "Revision Engine",
                "Turns quality findings and a person's words into proposals; carries out only "
                "what a person approved, after a checkpoint, and checks the result.",
                (
                    "Hand-offs to other modules are not executed here.",
                    "Rollback restores clips and the mix, not transitions or tracks.",
                    "Resolve read-back is skipped without a Resolve.",
                ),
                reads=(
                    "audio_mix_engine",
                    _LIB,
                    "quality_control",
                    "resolve_adapter",
                    "review_orchestrator",
                    "timeline_builder",
                ),
            ),
            _m(
                "final_polish",
                "Final Polish",
                "The last look at the finished presentation on the exact reviewed version; "
                "reports readiness, fixes nothing and approves nothing.",
                (
                    "Only first, middle and last pictures of each section are looked at.",
                    "Brand consistency is what quality control reports.",
                ),
                reads=(
                    "audio_mix_engine",
                    _LIB,
                    "quality_control",
                    "review_orchestrator",
                    "revision_engine",
                    "timeline_builder",
                ),
                validation=_SP,
            ),
            _m(
                "render_manager",
                "Render Manager",
                "Renders a timeline for review or delivery through one backend, keeps versioned "
                "profiles, queues jobs and checks every finished file.",
                (
                    "The Resolve backend has not run against a real Resolve.",
                    "No pause/resume with Resolve; queue is in-process and not persisted.",
                    "No resource measurement or render time estimate.",
                ),
                reads=(_INS, _LIB, "resolve_adapter", "review_orchestrator", "timeline_builder"),
                validation=_NR,
            ),
            _m(
                "export_validation",
                "Export Validation",
                "Independently validates the exported file: decodes it, measures black/frozen "
                "frames, silence, loudness, peaks, and compares with the approved render.",
                (
                    "Text clipping, safe areas and missing graphics cannot be judged from samples.",
                    "Without an approved review render the verdict is manual review.",
                    "Loudness measured once over the whole file.",
                ),
                reads=(
                    "audio_mix_engine",
                    _INS,
                    _LIB,
                    "render_manager",
                    "review_orchestrator",
                    "timeline_builder",
                ),
            ),
            _m(
                "resolve_export",
                "Resolve Export",
                "Final review, a person's approval and authorization, export through Resolve, "
                "independent file check and delivery. A person decides when the video leaves.",
                (
                    "Not run against a real DaVinci Resolve.",
                    "No platform delivery: nothing is uploaded or published.",
                    "Resolve scripting cannot set bitrate, sample rate or colour per export.",
                ),
                reads=(
                    "audio_mix_engine",
                    "export_validation",
                    "final_polish",
                    _LIB,
                    "quality_control",
                    "render_manager",
                    "resolve_adapter",
                    "review_orchestrator",
                    "timeline_builder",
                ),
                validation=_NR,
                ui="Review page (export authorization)",
            ),
        ),
    ),
    LayerEntry(
        "shared",
        "Shared - Orchestration & Review State",
        "The dependency graph over all layers and the single review state.",
        (
            _m(
                "media_pipeline",
                "Media Pipeline",
                "Orchestrates the other modules as one dependency graph; computes and stores "
                "nothing itself, so a rerun reports 'reused'.",
                (
                    "Validated end to end only on synthetic picture and synthesised speech.",
                    "No retries and no CPU/GPU/RAM measurement.",
                    "Tasks that only want a failed inspection still run on damaged files.",
                ),
                reads=tuple(
                    sorted(
                        {
                            "asset_relationships",
                            "audio_improvement",
                            "audio_intelligence",
                            "audio_mix_engine",
                            "audio_visual_alignment",
                            "broll_intelligence",
                            "caption_engine",
                            "content_intelligence",
                            "continuity_intelligence",
                            "edit_plan_compiler",
                            "editing_intelligence",
                            _INS,
                            _LIB,
                            "motion_graphics_engine",
                            "music_and_sound_design",
                            "pacing_intelligence",
                            "proxy_manager",
                            "quality_control",
                            "reframing_intelligence",
                            "resolve_adapter",
                            "story_architecture",
                            "thumbnail_design",
                            "timeline_builder",
                            "transition_engine",
                            "vfx_engine",
                            "video_improvement",
                            "video_intelligence",
                            "visual_style_engine",
                        }
                    )
                ),
            ),
            _m(
                "review_orchestrator",
                "Review Orchestrator",
                "The one place a review is kept: state, timeline versions, approvals, locks, "
                "checkpoints, export authorization and history.",
                (
                    "Report and plan ids are not re-checked against the library.",
                    "No notifications; events are in-process only.",
                    "A change outside a section's clips voids the whole version's approvals.",
                ),
                reads=(_LIB, "timeline_builder"),
                ui="Review page",
            ),
        ),
    ),
)

#: ``media_pipeline`` task kind -> the module that owns its result. ``deliver`` is the
#: pipeline's own final step (it attaches the audio mix to the rendered picture).
TASK_OWNERS: Mapping[str, str] = MappingProxyType(
    {
        **{m.pipeline.task: m.name for layer in LAYERS for m in layer.modules if m.pipeline},
        "deliver": "media_pipeline",
    }
)
DELIVER_TASK = PipelineTask(
    "deliver",
    "cpu",
    needs=("resolve_execute", "audio_mix"),
    wants=("timeline_build", "edit_plan", "captions", "graphics"),
)

DEPENDENCY_NOTE = (
    "Dependencies are not a fixed order. The pipeline runs any requested task as soon as what it "
    "REQUIRES has delivered; inputs it USES IF AVAILABLE are waited for when part of the same run "
    "but never block it. Tasks without a path between them can run in parallel."
)

VALIDATION_NOTE = (
    "Every module has been validated on synthetic data only. The resolve_* modules and the "
    "Resolve render backend have never been run against a real DaVinci Resolve."
)


def all_modules() -> tuple[ModuleEntry, ...]:
    return tuple(m for layer in LAYERS for m in layer.modules)


def find_module(name: str) -> ModuleEntry | None:
    return next((m for m in all_modules() if m.name == name), None)


def task_label(task: str) -> str:
    """``audio_analysis`` -> ``audio_analysis (audio_intelligence)``."""
    owner = TASK_OWNERS.get(task)
    return f"{task} ({owner})" if owner else task


def dependants(name: str) -> tuple[tuple[str, bool], ...]:
    """Modules whose pipeline task requires (``True``) or may use (``False``) this module's task."""
    entry = find_module(name)
    if entry is None or entry.pipeline is None:
        return ()
    task = entry.pipeline.task
    found: list[tuple[str, bool]] = []
    for other in all_modules():
        p = other.pipeline
        if p is not None and (task in p.needs or task in p.wants):
            found.append((other.name, task in p.needs))
    return tuple(found)
