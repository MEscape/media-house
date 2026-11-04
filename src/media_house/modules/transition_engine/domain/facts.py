"""What the planner reads: the edit (``TransitionSequence``, the module's own input DTO) and the
facts the application reads through the owners' catalogs.

Time domains are never mixed (contracts sec. 3): ``source_*`` = exact seconds on the recording's
container clock, ``timeline_*`` = exact seconds from the start of the edit. Every time is an exact
``Rational``; the one mapping between them goes through a clip (speed 1).
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.transition_engine.domain.errors import InvalidTransitionSequence
from media_house.modules.transition_engine.domain.values import BookendKind


def fr(value: Rational) -> Fraction:
    """A ``Rational`` as an exact ``Fraction`` (``Rational`` has no arithmetic)."""
    return Fraction(value.numerator, value.denominator)


def rat(value: Fraction) -> Rational:
    if value < 0:
        raise ValueError(f"a time cannot be negative ({value})")
    return Rational(value.numerator, value.denominator)


# --- the input DTO --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class EditRef:
    """The edit plan the transitions were planned for (``None`` on the no-speech path)."""

    edit_plan_id: str
    plan_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionClip:
    """One placed piece of footage at speed 1 (a placement of the edit)."""

    clip_id: str
    asset_id: str
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    #: 0 = main picture (boundaries are planned here), 1+ = cover layers.
    layer: int = 0
    section_id: str | None = None
    beat_id: str | None = None
    #: The story beat function of ``beat_id`` (``payoff``, ``turning_point``, ``hook``, ...).
    beat_function: str | None = None

    @property
    def duration(self) -> Fraction:
        return fr(self.source_end) - fr(self.source_start)

    @property
    def timeline_end(self) -> Fraction:
        return fr(self.timeline_start) + self.duration


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionMark:
    """A story section of the edit (its purpose is shown in reasons, never parsed)."""

    section_id: str
    order: int = 0
    label: str = ""
    purpose: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionRequest:
    """An editorial TRANSITION request (``SECTION_CHANGE``) at a TIMELINE time."""

    request_id: str
    timeline_at: Rational
    section_id: str | None = None
    priority: float = 0.5
    reason: str = ""
    decision_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioBridgeMark:
    """A J/L cut the edit already decided: the sound changes at another time than the picture.

    ``kind``: ``j_cut`` (incoming sound first), ``l_cut`` (outgoing sound continues) or
    ``bridge``. The picture is never moved."""

    bridge_id: str
    kind: str
    audio_changes_at: Rational
    picture_changes_at: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class Bookend:
    """A brand element at a TIMELINE time: the membership plug (the incoming clip at that time)
    or the end screen (appended after the edit, rendered by motion_graphics_engine)."""

    kind: BookendKind
    timeline_at: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionSequence:
    """The transition engine's own view of an edit (mapped by the pipeline from the edit plan,
    or from the inspection on the no-speech path: ``edit=None``, one clip per recording,
    timeline = source, no sections, requests, bridges or speech)."""

    edit: EditRef | None
    project_id: str | None
    frame_rate: Rational
    clips: tuple[TransitionClip, ...]
    sections: tuple[SectionMark, ...] = ()
    requests: tuple[TransitionRequest, ...] = ()
    bridges: tuple[AudioBridgeMark, ...] = ()
    bookends: tuple[Bookend, ...] = ()
    #: Story format or editing profile name (selects the default transition profile).
    format: str | None = None
    #: The frame rate was assumed (VFR or unknown source): seconds stay authoritative.
    frame_rate_assumed: bool = False

    @property
    def anchor_asset_id(self) -> str:
        """The first source asset of the edit: plans are documents of this asset."""
        return self.clips[0].asset_id

    def main(self) -> tuple[TransitionClip, ...]:
        """The main picture layer in timeline order."""
        return tuple(
            sorted(
                (c for c in self.clips if c.layer == 0),
                key=lambda c: (fr(c.timeline_start), c.clip_id),
            )
        )

    def check(self) -> None:
        """Raise ``InvalidTransitionSequence`` for a malformed sequence."""
        if fr(self.frame_rate) <= 0:
            raise InvalidTransitionSequence("the frame rate must be positive")
        ids = [c.clip_id for c in self.clips]
        if len(set(ids)) != len(ids):
            raise InvalidTransitionSequence("clip ids are not unique")
        for c in self.clips:
            if c.duration <= 0:
                raise InvalidTransitionSequence(f"clip {c.clip_id} has no duration")
            if c.layer < 0:
                raise InvalidTransitionSequence(f"clip {c.clip_id} has a negative layer")

    @classmethod
    def no_speech(
        cls,
        asset_ids_and_durations: tuple[tuple[str, Rational], ...],
        *,
        frame_rate: Rational,
        frame_rate_assumed: bool = False,
        format_hint: str | None = None,
        bookends: tuple[Bookend, ...] = (),
    ) -> "TransitionSequence":
        """The no-speech path (contracts sec. 2): every recording back to back at speed 1."""
        clips: list[TransitionClip] = []
        at = Fraction(0)
        for asset_id, duration in asset_ids_and_durations:
            clips.append(
                TransitionClip(
                    clip_id=f"clip:{asset_id}",
                    asset_id=asset_id,
                    source_start=Rational(0, 1),
                    source_end=duration,
                    timeline_start=rat(at),
                )
            )
            at += fr(duration)
        return cls(
            edit=None,
            project_id=None,
            frame_rate=frame_rate,
            clips=tuple(clips),
            bookends=bookends,
            format=format_hint,
            frame_rate_assumed=frame_rate_assumed,
        )


# --- facts read through the owners' catalogs -------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class WordSpan:
    """One transcribed word on the recording's clock (ms precision)."""

    start: Fraction
    end: Fraction


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceFacts:
    """One recording. ``None`` = not known (never guessed)."""

    asset_id: str
    #: Container start and duration in seconds; ``duration_basis`` says where they come from.
    start: Fraction = Fraction(0)
    duration: Fraction | None = None
    duration_basis: str = "unknown"  # inspection | library | unknown
    frame_rate: Fraction | None = None
    variable_frame_rate: bool | None = None
    sample_rate: int | None = None
    #: Words of the stored audio timeline; ``words_known`` is False without one.
    words: tuple[WordSpan, ...] = ()
    words_known: bool = False

    @property
    def end(self) -> Fraction | None:
        return None if self.duration is None else self.start + self.duration


@dataclass(frozen=True, slots=True, kw_only=True)
class EdgeFact:
    """What the picture shows at one side of a cut (from the stored video analysis)."""

    shot_id: str
    scene_id: str | None = None
    environment: str | None = None
    indoor_outdoor: str | None = None
    camera_movement: str | None = None
    #: Direction the camera moved (0 right, 90 up, counter-clockwise) and its speed.
    camera_direction: float | None = None
    camera_speed: float | None = None
    subject_cx: float | None = None
    subject_cy: float | None = None
    subject_vx: float | None = None
    subject_vy: float | None = None
    has_text: bool = False
    luma: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuityFact:
    """continuity_intelligence's judgement of one cut (by the clip ids of both sides)."""

    outgoing_clip: str
    incoming_clip: str
    #: ``type:level`` of every finding a person did not reject.
    issues: tuple[str, ...] = ()
    #: An ``AUDIO_CROSSFADE`` repair owned by ``transition_engine`` was proposed.
    audio_crossfade: bool = False
    #: Shot compatibility (0-1) when continuity paired the shots, else ``None``.
    compatibility: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingCue:
    """A pacing recommendation on a SOURCE span (``add_section_breath``, ``hold_reveal``)."""

    kind: str
    asset_id: str
    start: Fraction
    end: Fraction


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleLimits:
    """What the channel identity says about transitions (from ``ResolvedStyle``)."""

    document_id: str | None
    fingerprint: str
    identity_version: int
    profile: str
    intensity_ceiling: int
    #: Section id -> ceiling, where a section override changed it.
    section_ceilings: tuple[tuple[str, int], ...] = ()
    end_screen_transition: str = "pixel_dissolve"
    membership_transition: str = "zoom_tunnel"
    move_easing: str = "move"
    width: int = 1920
    height: int = 1080
    #: The identity's transition language (identity v2+; empty = the profile alone decides).
    approved: tuple[str, ...] = ()
    prohibited: tuple[str, ...] = ()
    #: Kinds used only as their brand bookend (zoom tunnel -> plug, pixel dissolve -> end screen).
    bookend_only: tuple[str, ...] = ()

    def ceiling(self, section_id: str | None) -> int:
        return dict(self.section_ceilings).get(section_id or "", self.intensity_ceiling)
