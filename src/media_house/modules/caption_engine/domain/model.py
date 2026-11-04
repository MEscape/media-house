"""The stored ``caption_plan``: every decision with its evidence, exact times and frames.

Times: ``source_*`` on the recording's clock, ``timeline_*`` from the start of the edit,
``local_*`` from the start of one caption group. All exact ``Rational``; frames are computed
once (``round(t * fps)``, half to even). Pixels are output pixels of the style's output size.
"""

from dataclasses import dataclass
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.caption_engine.domain.values import (
    Action,
    CaptionMode,
    Cue,
    EditRef,
    EmphasisLevel,
    InputRecord,
    QcIssue,
    RenderStatus,
    ResultState,
    StageReport,
    StyleBinding,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionDecision:
    """A person's decision about one segment (``reject`` / ``no_emphasis`` / ``no_emoji``) or one
    word (``correct`` with ``text``). Automation never changes it; it survives replans."""

    item_id: str
    action: Action
    text: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionWord:
    #: ``<asset>:<transcript index>@<clip>`` (a reused clip shows the same word twice).
    word_id: str
    asset_id: str
    transcript_index: int
    #: The transcript text (a person's correction applied), original case and punctuation.
    text: str
    #: What is drawn (the look drops a final full stop); the case is applied by the look.
    display: str
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    timeline_end: Rational
    local_start: Rational
    local_end: Rational
    speaker: str | None
    confidence: float | None
    #: A cut removed part of the spoken word; its times are clamped to the clip.
    clipped: bool
    corrected: bool
    emphasis: EmphasisLevel
    cues: tuple[Cue, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class EmojiDecision:
    emoji_id: str
    emoji: str
    meaning: str
    #: The rule that fired (stem or ``fact:<kind>``) and the word it is synced to.
    trigger: str
    word_id: str
    timeline_start: Rational
    timeline_end: Rational
    local_start: Rational
    #: Animation REQUEST (executed by motion graphics): ``pop`` entry next to the word.
    animation: str
    position: str
    render: RenderStatus
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class LayoutIntent:
    look: str
    zone: str
    #: Where the text block sits inside the zone (``center`` | ``top`` | ``bottom``).
    anchor: str
    #: Estimated text block (motion measures the real font and refuses overflow).
    left: int
    top: int
    width: int
    height: int
    size_px: int
    lines: tuple[str, ...]
    #: Subjects the anchor moved away from (``face`` ...).
    avoided: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class AnimationIntent:
    """The animation REQUEST sent to motion graphics (from the style's motion tokens)."""

    reveal: str
    entry: str
    exit: str
    active_word: bool
    intensity: int
    emphasis_at: tuple[Rational, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderRef:
    status: RenderStatus
    asset_id: str | None = None
    start_frame: int | None = None
    end_frame: int | None = None
    detail: str = ""
    #: The group's first frame inside ``asset_id`` (groups of one caption run share one file).
    asset_start_frame: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionSegment:
    #: ``cap:<sha of the word ids>`` (stable across replans of the same words).
    segment_id: str
    clip_ids: tuple[str, ...]
    section_id: str | None
    speaker: str | None
    mode: CaptionMode
    #: Why it is shown (selection cues, e.g. ``price``, ``key_item``, ``full``).
    reasons: tuple[str, ...]
    #: Subtitle-style text (original case and punctuation).
    text: str
    words: tuple[CaptionWord, ...]
    timeline_start: Rational
    timeline_end: Rational
    start_frame: int
    end_frame: int
    emojis: tuple[EmojiDecision, ...]
    layout: LayoutIntent
    animation: AnimationIntent
    render: RenderRef


@dataclass(frozen=True, slots=True, kw_only=True)
class SubtitleCue:
    index: int
    timeline_start: Rational
    timeline_end: Rational
    lines: tuple[str, ...]
    speaker: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SubtitleExport:
    """Closed captions for YouTube: the full heard transcript, as text (the library refuses
    ``.srt`` files)."""

    language: str
    cues: tuple[SubtitleCue, ...]
    srt: str
    vtt: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptRef:
    asset_id: str
    document_id: str | None
    language: str | None
    alignment: str
    words_heard: int
    words_shown: int
    suspect: int
    low_confidence: int
    split_by_cut: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ReprocessRequest:
    """What the owner of a missing/unreliable input should redo (never done here)."""

    owner: str
    asset_id: str
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    planner_version: int
    vocabulary_version: int
    profile: str
    profile_version: int
    profile_evidence: str
    configuration_fingerprint: str
    engines: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionPlan:
    plan_id: str
    plan_version: int
    supersedes: str | None
    project_id: str | None
    edit: EditRef | None
    style: StyleBinding | None
    created_at: datetime
    state: ResultState
    anchor_asset_id: str
    width: int
    height: int
    fps: Rational
    fps_assumed: bool
    mode: CaptionMode
    mode_reason: str
    transcripts: tuple[TranscriptRef, ...]
    segments: tuple[CaptionSegment, ...]
    subtitles: SubtitleExport | None
    decisions: tuple[CaptionDecision, ...]
    reprocess: tuple[ReprocessRequest, ...]
    checks: tuple[QcIssue, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    schema_version: int

    def segment(self, segment_id: str) -> CaptionSegment | None:
        return next((s for s in self.segments if s.segment_id == segment_id), None)

    @property
    def fully_rendered(self) -> bool:
        return all(
            s.render.status in (RenderStatus.RENDERED, RenderStatus.REUSED) for s in self.segments
        )
