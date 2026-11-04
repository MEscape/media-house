"""``SoundtrackTimeline``: this module's own narrow view of the edit (never the edit plan itself).

The pipeline maps the edit (or, on the no-speech path, the bare recordings) into this DTO;
``media_pipeline`` is the only place that knows both shapes. Every time is exact
(``Rational``) and carries its domain in its name: ``source_*`` on a recording's container clock,
``timeline_*`` from the start of the edit. A clip is the only bridge between the two
(``timeline = clip.timeline_start + (source - clip.source_start)``, speed 1).

Events from graphics, transitions and captions arrive as ``SyncEvent``s: the owners do not exist
yet and are never imported; the pipeline maps their outputs into this shape later.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.music_and_sound_design.domain.values import (
    BridgeKind,
    ClipAudio,
    EventSource,
    SectionRole,
)


def fraction(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)


def rational(value: Fraction) -> Rational:
    if value < 0:
        raise ValueError(f"negative time {value}")
    return Rational(value.numerator, value.denominator)


@dataclass(frozen=True, slots=True, kw_only=True)
class EditRef:
    edit_plan_id: str
    plan_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TimelineClip:
    clip_id: str
    asset_id: str
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    #: What the edit does with the clip's own sound.
    audio: ClipAudio = ClipAudio.SOURCE_SYNC
    section_id: str | None = None
    #: 0 = main track; higher layers are cover shots over it.
    layer: int = 0

    @property
    def timeline_end(self) -> Rational:
        return rational(
            fraction(self.timeline_start) + fraction(self.source_end) - fraction(self.source_start)
        )

    def to_timeline(self, source: Fraction) -> Fraction | None:
        """``source`` seconds of this clip's recording on the timeline, if the clip shows them."""
        start, end = fraction(self.source_start), fraction(self.source_end)
        if not start <= source <= end:
            return None
        return fraction(self.timeline_start) + source - start


@dataclass(frozen=True, slots=True, kw_only=True)
class TimelineSection:
    section_id: str
    timeline_start: Rational
    timeline_end: Rational
    label: str = ""
    #: An explicit role wins; otherwise it is derived from the beat functions.
    role: SectionRole | None = None
    #: Story beat functions (``hook``, ``payoff``, ...) of the section's beats.
    beat_functions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechSpan:
    timeline_start: Rational
    timeline_end: Rational
    words: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioBridge:
    """A J/L cut: the audio changes at another time than the picture (never moved here)."""

    bridge_id: str
    kind: BridgeKind
    timeline_audio_at: Rational
    timeline_picture_at: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncEvent:
    """A visual moment a sound may follow (graphic animation, transition, caption, edit)."""

    event_id: str
    source: EventSource
    #: The owner's event kind, e.g. ``rank_reveal``, ``pop_in``, ``zoom``, ``emoji``.
    kind: str
    timeline_at: Rational
    #: 0..1: how strong the visual moment is.
    strength: float


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingCue:
    """A pacing recommendation on the timeline (``hold_reveal``, ``add_section_breath``)."""

    cue_id: str
    kind: str
    timeline_start: Rational
    timeline_end: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class OriginalSoundEvent:
    """A loud moment of a clip's own sound (an ``energy_spike``: bang, crackle, impact)."""

    clip_id: str
    kind: str
    timeline_start: Rational
    timeline_end: Rational
    strength: float


@dataclass(frozen=True, slots=True, kw_only=True)
class SoundtrackTimeline:
    #: ``None`` on the no-speech path (no edit plan exists; one clip per recording).
    edit: EditRef | None
    project_id: str | None
    #: The edit's frame rate (or the inspected one); frames are computed only at the edge.
    frame_rate: Rational
    clips: tuple[TimelineClip, ...]
    #: ``True`` when the frame rate is assumed (variable-rate source): seconds stay authoritative.
    frame_rate_assumed: bool = False
    sections: tuple[TimelineSection, ...] = ()
    speech: tuple[SpeechSpan, ...] = ()
    bridges: tuple[AudioBridge, ...] = ()
    events: tuple[SyncEvent, ...] = ()
    pacing: tuple[PacingCue, ...] = ()
    original_events: tuple[OriginalSoundEvent, ...] = ()
    #: Story format name (``top_n``, ``unboxing``, ``short``, ...) when known.
    story_format: str | None = None
    #: End of the bookend span appended after the content (the end screen); ``None`` = none.
    appended_until: Rational | None = None

    @property
    def source_asset_ids(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for clip in self.clips:
            seen.setdefault(clip.asset_id, None)
        return tuple(seen)

    @property
    def anchor_asset_id(self) -> str | None:
        """The first source asset of the edit: the plan is stored as its derived document."""
        return self.clips[0].asset_id if self.clips else None

    @property
    def timeline_end(self) -> Fraction:
        """The content end, or the end of the appended bookend span when there is one."""
        appended = fraction(self.appended_until) if self.appended_until else Fraction(0)
        return max(self.content_end, appended)

    @property
    def content_end(self) -> Fraction:
        return max((fraction(c.timeline_end) for c in self.clips), default=Fraction(0))
