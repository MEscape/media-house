"""What the planner works from, as its own narrow facts (the domain imports no other module):
the findings of a quality report, the timeline as it stands, and what the review has decided."""

from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.revision_engine.domain.values import Origin


@dataclass(frozen=True, slots=True)
class IssueFacts:
    issue_id: str
    fingerprint: str
    code: str
    #: critical | high | medium | low | optional
    severity: str
    #: objective | subjective
    basis: str
    confidence: float
    description: str
    suggested_correction: str
    owner_hint: str
    start_frame: int | None
    end_frame: int | None
    clips: tuple[str, ...]
    tracks: tuple[str, ...]
    sections: tuple[str, ...]
    measurements: tuple[tuple[str, str], ...]
    blocks_export: bool

    def measure(self, name: str, default: str = "") -> str:
        return next((v for k, v in self.measurements if k == name), default)


@dataclass(frozen=True, slots=True)
class ClipView:
    clip_id: str
    track_id: str
    #: The track's role: primary | cover | graphics | captions | effects
    role: str
    kind: str
    name: str
    start_frame: int
    end_frame: int
    section_id: str | None
    enabled: bool
    origin: Origin


@dataclass(frozen=True, slots=True)
class SoundView:
    clip_id: str
    track_id: str
    #: dialogue | production | music | ambience | sfx
    role: str
    start: Fraction
    end: Fraction
    section_id: str | None
    origin: Origin


@dataclass(frozen=True, slots=True)
class TransitionView:
    transition_id: str
    mode: str
    cut_frame: int
    from_clip: str | None
    to_clip: str | None


@dataclass(frozen=True, slots=True)
class SectionView:
    section_id: str
    name: str
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True)
class TimelineView:
    timeline_id: str
    version: int
    content_hash: str
    anchor_asset_id: str
    project_id: str | None
    fps: Fraction
    duration_frames: int
    clips: tuple[ClipView, ...]
    sounds: tuple[SoundView, ...]
    transitions: tuple[TransitionView, ...]
    sections: tuple[SectionView, ...]

    def clip(self, clip_id: str) -> ClipView | None:
        return next((c for c in self.clips if c.clip_id == clip_id), None)

    def sound(self, clip_id: str) -> SoundView | None:
        return next((s for s in self.sounds if s.clip_id == clip_id), None)

    def primary(self) -> tuple[ClipView, ...]:
        return tuple(
            sorted((c for c in self.clips if c.role == "primary"), key=lambda c: c.start_frame)
        )

    def section(self, section_id: str) -> SectionView | None:
        return next((s for s in self.sections if s.section_id == section_id), None)

    def section_at(self, frame: int) -> SectionView | None:
        return next((s for s in self.sections if s.start_frame <= frame < s.end_frame), None)

    def frame_of(self, seconds: Fraction) -> int:
        return round(seconds * self.fps)

    def fps_seconds(self, frame: int) -> Fraction:
        return Fraction(frame) / self.fps


@dataclass(frozen=True, slots=True)
class ReviewView:
    """What the review has decided, as the planner needs it."""

    session_id: str
    locked_sections: tuple[str, ...]
    #: Sections with a current approval (their creative decisions are not replaced lightly).
    approved_sections: tuple[str, ...]
    #: ``ProposedChange.proposal_key`` values a person rejected.
    rejected_keys: frozenset[str]
    #: Effective authority per operation name: ``automatic`` | ``suggested`` | ``manual_only``.
    authority: tuple[tuple[str, str], ...]

    def authority_of(self, operation: str) -> str:
        return dict(self.authority).get(operation, "suggested")


@dataclass(frozen=True, slots=True)
class MixView:
    """The audio mix overrides already in effect, as the engine records them (``bus:music=-3``)."""

    overrides: tuple[str, ...] = ()
