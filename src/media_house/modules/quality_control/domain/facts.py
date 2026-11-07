"""What quality control looks at, as its own narrow facts.

The domain imports no other module: ``application/views.py`` reads a built timeline, a mix result,
a continuity analysis, a story plan and a Resolve binding into these. Times are exact fractions of a
second (``Fraction``) or timeline frames; a ``None`` is "not stated", never zero.
"""

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class SettingsFacts:
    width: int
    height: int
    fps: Fraction
    fps_assumed: bool
    duration_frames: int
    #: contain | cover | stretch | none
    fit_mode: str
    sample_rate: int


@dataclass(frozen=True, slots=True)
class SourceFacts:
    source_id: str
    original_asset_id: str
    name: str
    #: video | audio | image
    media_type: str
    available: bool
    duration: Fraction | None
    width: int | None
    height: int | None
    frame_rate: Fraction | None
    used_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TrackFacts:
    track_id: str
    kind: str
    role: str
    name: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ClipFacts:
    """A picture clip."""

    clip_id: str
    track_id: str
    role: str
    #: primary | cover | graphic | caption | vfx | transition_overlay | custom
    kind: str
    name: str
    start_frame: int
    end_frame: int
    source_id: str | None
    original_asset_id: str | None
    #: The part of the RECORDING the clip plays (original clock), when known.
    original_in: Fraction | None
    original_out: Fraction | None
    speed: float
    section_id: str | None
    beat_id: str | None
    unresolved: bool
    enabled: bool
    human: bool
    zone: str
    opacity: float
    scale_x: float
    scale_y: float
    position_x: float
    position_y: float
    #: Timeline frames where the picture is a deliberate freeze (time-map freeze segments).
    freezes: tuple[tuple[int, int], ...]
    #: ``(key, value)`` design choices the clip carries out (typography, preset...).
    design: tuple[tuple[str, str], ...]
    has_fade_out: bool = False


@dataclass(frozen=True, slots=True)
class SoundFacts:
    clip_id: str
    track_id: str
    #: dialogue | production | music | ambience | sfx
    role: str
    start: Fraction
    end: Fraction
    gain_db: float
    fade_in: Fraction
    fade_out: Fraction
    section_id: str | None
    unresolved: bool
    enabled: bool
    source_id: str | None


@dataclass(frozen=True, slots=True)
class TransitionFacts:
    transition_id: str
    #: cut | dissolve | fade | dip | overlay | unsupported
    mode: str
    kind: str
    cut_frame: int
    start_frame: int
    end_frame: int
    from_clip: str | None
    to_clip: str | None
    supported: bool
    handles_sufficient: bool
    #: native | rendered | baked | fallback | unavailable
    execution: str


@dataclass(frozen=True, slots=True)
class SectionFacts:
    section_id: str
    name: str
    start_frame: int
    end_frame: int
    clip_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CueFacts:
    index: int
    start_frame: int
    end_frame: int
    text: str


@dataclass(frozen=True, slots=True)
class ZoneFacts:
    name: str
    kind: str
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class IdentityFacts:
    format_profile: str
    zones: tuple[ZoneFacts, ...]
    #: ``(family, available)``
    fonts: tuple[tuple[str, bool], ...]
    #: ``(slot, required, has_asset)``
    brand_slots: tuple[tuple[str, bool, bool], ...]


@dataclass(frozen=True, slots=True)
class BuilderIssueFacts:
    code: str
    #: error | unsupported | review | warning | info
    severity: str
    message: str
    element_id: str | None
    start_frame: int | None
    end_frame: int | None


@dataclass(frozen=True, slots=True)
class DependencyFacts:
    dependency_id: str
    kind: str
    reference: str
    resolved: bool
    required_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TimelineFacts:
    timeline_id: str
    version: int
    content_hash: str
    anchor_asset_id: str
    project_id: str | None
    settings: SettingsFacts
    tracks: tuple[TrackFacts, ...]
    clips: tuple[ClipFacts, ...]
    sounds: tuple[SoundFacts, ...]
    sources: tuple[SourceFacts, ...]
    transitions: tuple[TransitionFacts, ...]
    sections: tuple[SectionFacts, ...]
    cues: tuple[CueFacts, ...]
    identity: IdentityFacts
    dependencies: tuple[DependencyFacts, ...]
    builder_issues: tuple[BuilderIssueFacts, ...]
    #: Check kinds the builder says need a render (black frames, missing audio, sync...).
    render_checks_pending: tuple[str, ...]
    #: Elements a person deleted / created (provenance), for the intended-versus-actual comparison.
    deleted_by_human: tuple[str, ...] = ()
    #: Items the compiled specification lists (``None``: the specification was not available).
    specification_items: tuple[str, ...] | None = None

    def primary(self) -> tuple[ClipFacts, ...]:
        return tuple(
            sorted(
                (c for c in self.clips if c.kind == "primary" and c.enabled),
                key=lambda c: c.start_frame,
            )
        )

    def clip(self, clip_id: str) -> ClipFacts | None:
        return next((c for c in self.clips if c.clip_id == clip_id), None)

    def source(self, source_id: str | None) -> SourceFacts | None:
        return next((s for s in self.sources if s.source_id == source_id), None)

    def section_of_frame(self, frame: int) -> str | None:
        for section in self.sections:
            if section.start_frame <= frame < section.end_frame:
                return section.section_id
        return None

    def seconds(self, frame: int) -> Fraction:
        return Fraction(frame) / self.settings.fps

    def frame_at(self, seconds: Fraction) -> int:
        return round(seconds * self.settings.fps)


# --- what the other owners already know ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MixIssueFacts:
    code: str
    #: info | warning | error | critical
    severity: str
    #: confirmed | likely | recommendation | intentional
    certainty: str
    message: str
    element_id: str | None
    start_seconds: Fraction | None
    end_seconds: Fraction | None


@dataclass(frozen=True, slots=True)
class MixFacts:
    mix_result_id: str
    delivery: str
    integrated_target_lufs: float | None
    tolerance_lu: float | None
    true_peak_ceiling_dbtp: float | None
    loudness_range_limit_lu: float | None
    state: str
    issues: tuple[MixIssueFacts, ...]


@dataclass(frozen=True, slots=True)
class ContinuityIssueFacts:
    issue_id: str
    kind: str
    #: info | low | medium | high
    level: str
    at_seconds: Fraction | None
    explanation: str
    why_it_matters: str
    detectability: float | None
    #: How likely the difference is deliberate style, [0, 1].
    style_likelihood: float | None
    sections: tuple[str, ...]
    recommended: str
    surfaced: bool


@dataclass(frozen=True, slots=True)
class ContinuityFacts:
    analysis_id: str
    issues: tuple[ContinuityIssueFacts, ...]


@dataclass(frozen=True, slots=True)
class StorySectionFacts:
    section_id: str
    label: str
    order: int
    #: ``reorder_locked`` is set: the order is a hard constraint.
    order_locked: bool


@dataclass(frozen=True, slots=True)
class StoryLinkFacts:
    kind: str
    setup_section: str
    payoff_section: str | None
    #: open | resolved | unresolved ... as the story plan states it.
    state: str


@dataclass(frozen=True, slots=True)
class StoryFacts:
    plan_id: str
    sections: tuple[StorySectionFacts, ...]
    links: tuple[StoryLinkFacts, ...]


@dataclass(frozen=True, slots=True)
class ResolveFacts:
    """What Resolve holds, as last written / read back (and, if asked, as it is now)."""

    bound: bool
    #: The version Resolve was last synchronised to.
    bound_version: int | None
    bound_hash_matches: bool
    open_conflicts: tuple[str, ...]
    unverified_elements: int
    #: ``None``: the live timeline was not looked at.
    live_checked: bool
    live_changes: int | None
    live_note: str = ""
