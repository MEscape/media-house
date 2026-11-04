"""The ``sound_design_plan`` document: what plays when, why, from which library asset.

Every item keeps its library asset (and the measurement it was placed with), exact
``timeline_*`` / ``source_*`` times (``Rational``) and frames computed once from the edit's
frame rate. Restraint is part of the plan: declined effects, music-free spans and silences are
recorded with their reasons. Levels and ducking are INTENT for audio_improvement; nothing here is
mastered.
"""

from dataclasses import dataclass
from datetime import datetime

from media_house.core.domain import Rational
from media_house.modules.music_and_sound_design.domain.timeline import EditRef
from media_house.modules.music_and_sound_design.domain.values import (
    Alignment,
    BridgeKind,
    CrossfadeKind,
    Decision,
    Declined,
    DuckKey,
    EventSource,
    InputHow,
    LicenceState,
    MusicMode,
    MusicPresence,
    Need,
    OriginalAudioMode,
    QcCategory,
    ResultState,
    SectionRole,
    Severity,
    SfxAlign,
    SoundKind,
    StageState,
)


@dataclass(frozen=True, slots=True)
class ItemDecision:
    """A person's verdict on one plan item (``mus:...`` cue or ``sfx:...`` effect).

    ``REJECTED`` without an asset removes the music/effect there; with an asset it only rules
    that asset out. ``ACCEPTED`` with an asset uses that library asset. Automation never
    overrides a decision; decisions follow the item id across replans.
    """

    item_id: str
    decision: Decision
    asset_id: str | None = None
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class QcIssue:
    code: str
    severity: Severity
    category: QcCategory
    message: str
    item_id: str | None = None
    timeline_start: Rational | None = None
    timeline_end: Rational | None = None
    evidence: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    kind: str
    how: InputHow
    asset_id: str | None = None
    identity: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StageReport:
    stage: str
    state: StageState
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    planner_version: int
    analyzer_version: int
    vocabulary_version: int
    profile: str
    profile_version: int
    profile_schema_version: int
    identity: str
    identity_version: int
    configuration_fingerprint: str
    library_fingerprint: str
    engines: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetRef:
    """A library asset as used by the plan, with what its licence allows."""

    asset_id: str
    kind: SoundKind
    title: str
    #: The stored ``music_analysis`` / ``sfx_profile`` the placement was computed from.
    analysis_asset_id: str | None
    licence_state: LicenceState
    licence_name: str
    attribution_required: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Candidate:
    asset_id: str
    title: str
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class MusicSegment:
    """One continuous read of a track: ``source_*`` seconds of the music file -> the timeline."""

    segment_id: str
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    timeline_end: Rational
    timeline_start_frame: int
    timeline_end_frame: int
    #: Gain to apply to the file for its free-standing (unducked) level.
    gain_db: float
    fade_in: Rational
    fade_out: Rational
    #: 0 for the first read; 1, 2, ... for repeats of the loop region.
    loop_pass: int = 0
    #: The segment starts on a measured downbeat of the track.
    on_downbeat: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class MusicCue:
    """The music decision for one run of sections (one track, or none and why)."""

    cue_id: str
    section_ids: tuple[str, ...]
    role: SectionRole
    presence: MusicPresence
    mode: MusicMode
    timeline_start: Rational
    timeline_end: Rational
    reason: str
    asset: AssetRef | None = None
    score: float | None = None
    energy_target: float | None = None
    #: Measured, from the analysis (``None`` when the track has none).
    energy_measured: float | None = None
    tempo_bpm: float | None = None
    tempo_confidence: float | None = None
    alignment: Alignment | None = None
    segments: tuple[MusicSegment, ...] = ()
    #: Edit points of the track that the arrangement uses (``kind@seconds``).
    edit_points_used: tuple[str, ...] = ()
    alternatives: tuple[Candidate, ...] = ()
    conflicts: tuple[str, ...] = ()
    decision: Decision | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MusicFreeSpan:
    span_id: str
    timeline_start: Rational
    timeline_end: Rational
    mode: MusicMode
    reason: str
    section_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SfxEvent:
    item_id: str
    sync_event_id: str
    sync_source: EventSource
    sync_kind: str
    purpose: str
    asset: AssetRef
    #: The visual moment the sound follows.
    timeline_at: Rational
    timeline_start: Rational
    timeline_end: Rational
    timeline_start_frame: int
    timeline_end_frame: int
    #: The part of the effect file that plays (its useful span).
    source_start: Rational
    source_end: Rational
    align: SfxAlign
    gain_db: float
    priority: float
    confidence: float | None
    reason: str
    alternatives: tuple[Candidate, ...] = ()
    conflicts: tuple[str, ...] = ()
    decision: Decision | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class NoSoundEffect:
    """An event that was considered and deliberately left without an effect."""

    item_id: str
    sync_event_id: str
    sync_source: EventSource
    sync_kind: str
    timeline_at: Rational
    declined: Declined
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OriginalAudio:
    clip_id: str
    asset_id: str
    timeline_start: Rational
    timeline_end: Rational
    mode: OriginalAudioMode
    reason: str
    music_over_it: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class DuckingIntent:
    duck_id: str
    cue_id: str
    key: DuckKey
    timeline_start: Rational
    timeline_end: Rational
    depth_db: float
    attack_seconds: float
    release_seconds: float
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Crossfade:
    crossfade_id: str
    kind: CrossfadeKind
    #: Where the change happens on the timeline (the section boundary or the J/L audio change).
    timeline_at: Rational
    length: Rational
    outgoing_cue_id: str
    incoming_cue_id: str | None
    follows_bridge: str | None = None
    bridge_kind: BridgeKind | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MissingAsset:
    recommendation_id: str
    priority: Need
    kind: SoundKind
    purpose: str
    required_asset: str
    categories: tuple[str, ...]
    timeline_start: Rational
    timeline_end: Rational | None
    min_seconds: float | None
    max_seconds: float | None
    style: str
    reason: str
    vocals: bool = False
    action: str = "manual_asset_acquisition"


@dataclass(frozen=True, slots=True, kw_only=True)
class Credit:
    asset_id: str
    title: str
    #: The manifest's exact attribution text, for the video description.
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SoundDesignPlan:
    plan_id: str
    plan_version: int
    supersedes: str | None
    project_id: str | None
    edit: EditRef | None
    #: Always ``None``: the sound identity is separate from the visual identity.
    style: str | None
    anchor_asset_id: str
    created_at: datetime
    state: ResultState
    profile: str
    identity: str
    identity_version: int
    frame_rate: Rational
    frame_rate_assumed: bool
    timeline_duration: Rational
    music: tuple[MusicCue, ...]
    music_free: tuple[MusicFreeSpan, ...]
    sfx: tuple[SfxEvent, ...]
    no_sfx: tuple[NoSoundEffect, ...]
    original_audio: tuple[OriginalAudio, ...]
    ducking: tuple[DuckingIntent, ...]
    crossfades: tuple[Crossfade, ...]
    missing_assets: tuple[MissingAsset, ...]
    credits: tuple[Credit, ...]
    #: The person decisions this plan applied (carried to the next plan of the same edit).
    decisions: tuple[ItemDecision, ...]
    checks: tuple[QcIssue, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    #: sha256 of the plan's items (no ids of runs, no time stamps): same plan -> same hash.
    plan_hash: str
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    schema_version: int

    @property
    def credit_lines(self) -> str:
        """The exact credits for the video description, one block per attributed asset."""
        return "\n\n".join(c.text for c in self.credits)

    def used_asset_ids(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for cue in self.music:
            if cue.asset is not None and cue.segments:
                seen.setdefault(cue.asset.asset_id, None)
        for event in self.sfx:
            seen.setdefault(event.asset.asset_id, None)
        return tuple(seen)
