"""The Caption Engine PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``caption_engine``.

The engine decides captions: whether (mode per format profile / project / section), which
spoken words (selection), how they are grouped, timed and placed (segmentation, layout), what
is highlighted or decorated (emphasis, emoji), which animation is REQUESTED, and the subtitle
text for closed captions. It never transcribes (audio_intelligence) and never draws
(motion_graphics_engine executes its ``GraphicKind.CAPTION`` groups).

    result = planner.execute(PlanCaptionsCommand(timeline=CaptionTimeline(...), style=style_ref,
                                                  render=True), ctx)
    plan = result.value.plan                 # stored ``caption_plan`` of the edit's anchor
    plan.subtitles.srt, plan.subtitles.vtt   # YouTube closed captions (text in the plan)
    to_srt(cues) / to_vtt(cues)              # the pure exporters

``CaptionCatalog`` reads stored plans (``latest`` / ``history`` / ``load``); music and sound
design syncs to ``CaptionSegment.timeline_start`` and emphasised ``CaptionWord``s.
Blocking calls (rendering runs Chrome through HyperFrames): run them as jobs.
"""

from typing import Protocol

from media_house.modules.caption_engine.application.plan_captions import (
    LIMITATIONS,
    PlanCaptionsCommand,
    PlanCaptionsError,
    PlanCaptionsResult,
)
from media_house.modules.caption_engine.domain.engine import EMOJI_NOT_RENDERED
from media_house.modules.caption_engine.domain.errors import (
    CaptionEngineError,
    InvalidCaptionConfig,
    InvalidCaptionDocument,
    InvalidCaptionPlan,
    InvalidCaptionTimeline,
    StyleNotAvailable,
)
from media_house.modules.caption_engine.domain.facts import CaptionClip, CaptionTimeline
from media_house.modules.caption_engine.domain.model import (
    AnimationIntent,
    CaptionDecision,
    CaptionPlan,
    CaptionSegment,
    CaptionWord,
    EmojiDecision,
    LayoutIntent,
    Provenance,
    RenderRef,
    ReprocessRequest,
    SubtitleCue,
    SubtitleExport,
    TranscriptRef,
)
from media_house.modules.caption_engine.domain.profile import (
    ASPECT_PROFILE,
    DEFAULT_PROFILE,
    PROFILES,
    STORY_FORMAT_PROFILE,
    Animation,
    CaptionConfig,
    CaptionProfile,
    Emoji,
    EmojiRule,
    Emphasis,
    Layout,
    LexiconEntry,
    SectionOverride,
    Segmentation,
    Selection,
    Subtitles,
    Transcript,
    knob_fields,
)
from media_house.modules.caption_engine.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.caption_engine.domain.subtitles import to_srt, to_vtt
from media_house.modules.caption_engine.domain.values import (
    DOCUMENT_TYPE,
    PLANNER_VERSION,
    PROFILE_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    Action,
    CaptionMode,
    Cue,
    EditRef,
    EmphasisLevel,
    InputHow,
    InputRecord,
    QcCategory,
    QcIssue,
    RenderStatus,
    ResultState,
    Severity,
    StageReport,
    StageState,
    StyleBinding,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class CaptionPlanner(Protocol):
    def execute(
        self, command: PlanCaptionsCommand, ctx: JobContext
    ) -> Result[PlanCaptionsResult, PlanCaptionsError]:
        """Plan (and optionally render) and store (or reuse) the captions of one edit."""
        ...


class CaptionCatalog(Protocol):
    """Stored caption plans. Nothing is computed."""

    def latest(
        self, anchor_asset_id: str, *, project_id: str | None = None
    ) -> Result[tuple[CaptionPlan, MediaAssetDto] | None, MediaError]: ...

    def history(self, anchor_asset_id: str) -> list[MediaAssetDto]: ...

    def load(
        self, plan_asset_id: str
    ) -> Result[CaptionPlan, MediaError | InvalidCaptionDocument]: ...


__all__ = [
    "ASPECT_PROFILE",
    "DEFAULT_PROFILE",
    "DOCUMENT_TYPE",
    "EMOJI_NOT_RENDERED",
    "LIMITATIONS",
    "PLANNER_VERSION",
    "PROFILES",
    "PROFILE_VERSION",
    "RESULT_OPERATION",
    "SCHEMA_VERSION",
    "STORY_FORMAT_PROFILE",
    "Action",
    "Animation",
    "AnimationIntent",
    "CaptionCatalog",
    "CaptionClip",
    "CaptionConfig",
    "CaptionDecision",
    "CaptionEngineError",
    "CaptionMode",
    "CaptionPlan",
    "CaptionPlanner",
    "CaptionProfile",
    "CaptionSegment",
    "CaptionTimeline",
    "CaptionWord",
    "Cue",
    "EditRef",
    "Emoji",
    "EmojiDecision",
    "EmojiRule",
    "Emphasis",
    "EmphasisLevel",
    "InputHow",
    "InputRecord",
    "InvalidCaptionConfig",
    "InvalidCaptionDocument",
    "InvalidCaptionPlan",
    "InvalidCaptionTimeline",
    "Layout",
    "LayoutIntent",
    "LexiconEntry",
    "PlanCaptionsCommand",
    "PlanCaptionsError",
    "PlanCaptionsResult",
    "Provenance",
    "QcCategory",
    "QcIssue",
    "RenderRef",
    "RenderStatus",
    "ReprocessRequest",
    "ResultState",
    "SectionOverride",
    "Segmentation",
    "Selection",
    "Severity",
    "StageReport",
    "StageState",
    "StyleBinding",
    "StyleNotAvailable",
    "SubtitleCue",
    "SubtitleExport",
    "Subtitles",
    "Transcript",
    "TranscriptRef",
    "knob_fields",
    "plan_from_json",
    "plan_to_json",
    "to_srt",
    "to_vtt",
]
