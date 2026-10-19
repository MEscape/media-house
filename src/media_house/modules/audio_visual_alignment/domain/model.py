"""The alignment document: typed, immutable, and the only thing downstream consumers read.

Observations (what upstream measured), inferences (what alignment concludes from them) and
uncertainty stay apart. Every record names the upstream things it rests on, keeps the upstream
``FrameTime`` of the picture side as the authority, and carries SEPARATE scores. Missing is
``None`` with a reason somewhere, never zero. Nothing here names an editing action.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import FrameTime, TimeRange
from media_house.modules.audio_visual_alignment.domain.values import (
    SCHEMA_VERSION,
    BoundaryKind,
    CorrectionSource,
    EvidenceGrade,
    EvidenceKind,
    FlagKind,
    HowObtained,
    LinkState,
    OpportunityKind,
    ProbeState,
    Severity,
    StageState,
    Visibility,
    optional_unit_interval,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    kind: EvidenceKind
    #: The module whose result the evidence was read from.
    source: str
    ref: str
    value: float | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Scores:
    """Separate quantities. None of them is a probability and none is combined silently.

    * ``overlap_seconds``: seconds the two intervals share on the common clock.
    * ``coverage_audio`` / ``coverage_video``: that overlap as a share of each interval, so a short
      event inside a long passage is visible as such (high video coverage, low audio coverage).
    * ``correspondence``: Pearson correlation of two activity series, in [-1, 1].
    * ``identity_support``: share of the supporting seconds that agree on one identity, [0, 1].
    * ``strength``: strength of the evidence (see ``scoring.py``), [0, 1]; not calibrated.
    """

    overlap_seconds: float | None = None
    coverage_audio: float | None = None
    coverage_video: float | None = None
    correspondence: float | None = None
    identity_support: float | None = None
    strength: float | None = None

    def __post_init__(self) -> None:
        optional_unit_interval(self.coverage_audio, "coverage_audio")
        optional_unit_interval(self.coverage_video, "coverage_video")
        optional_unit_interval(self.identity_support, "identity_support")
        optional_unit_interval(self.strength, "strength")


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    name: str
    how: HowObtained
    asset_id: str | None = None
    document_id: str | None = None
    #: Identity of the content that was used (library checksum or a digest of a supplied object).
    identity: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ClockCorrection:
    """A shift added to one stream's upstream seconds to reach the common clock.

    ``seconds`` is derived from METADATA (or supplied), never from a signal measurement. The
    upstream seconds stay on every record; this is the only transformation applied to them.
    """

    stream: str
    seconds: float
    source: CorrectionSource
    observed_seconds: float | None = None
    expected_seconds: float | None = None
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class LagProbe:
    """A coarse check of speech activity against mouth movement. It is NEVER applied.

    The mouth signal is a few samples per second, so the probe can reveal an offset of about a
    second or more. It cannot verify lip-sync to a frame, and it does not measure drift.
    """

    state: ProbeState
    lag_seconds: float | None = None
    correlation: float | None = None
    runner_up_correlation: float | None = None
    windows: int = 0
    support_seconds: float = 0.0
    resolution_seconds: float = 0.0
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncAssessment:
    basis: str
    corrections: tuple[ClockCorrection, ...]
    probe: LagProbe
    audio_duration: float | None = None
    video_duration: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotSpeech:
    """What one shot shows while a passage of speech is heard (audio-clock overlap)."""

    shot_id: str
    video_range: TimeRange
    overlap_seconds: float
    speech_seconds: float
    visibility: Visibility
    mouth_samples: int = 0
    mouth_spread: float | None = None
    identity_id: str | None = None
    track_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechSource:
    source_id: str
    segment_id: str
    start: float
    end: float
    speaker: str | None
    #: The visibility with the most speech seconds; the per-shot detail below is the truth.
    visibility: Visibility
    shots: tuple[ShotSpeech, ...]
    scores: Scores
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeakerLink:
    link_id: str
    speaker: str
    identity_id: str
    state: LinkState
    supporting_seconds: float
    contradicting_seconds: float
    shot_ids: tuple[str, ...]
    scores: Scores
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class VideoRef:
    shot_id: str
    range: TimeRange


@dataclass(frozen=True, slots=True, kw_only=True)
class TextMatch:
    """One candidate pairing of a spoken passage with a shot. Several may exist per passage."""

    match_id: str
    passage_id: str
    start: float
    end: float
    text: str
    shot: VideoRef
    #: Seconds between passage and shot on the common clock; 0 when they overlap.
    gap_seconds: float
    grade: EvidenceGrade
    terms: tuple[str, ...]
    rank: int
    similarity: float | None
    similarity_z: float | None
    scores: Scores
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SoundAction:
    """A heard event and a seen event close in time. A candidate, never a proven cause."""

    pair_id: str
    audio_event_id: str
    audio_kind: str
    audio_start: float
    audio_end: float
    video_event_id: str
    video_kind: str
    shot_id: str
    video_time: FrameTime
    delta_seconds: float
    tolerance_seconds: float
    #: Expected number of video events of this kind inside the tolerance window by chance alone.
    chance_hits: float
    candidates_in_window: int
    scores: Scores
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundaryRelation:
    """Speech that runs across a cut: how much is heard on each side, and what each side shows."""

    relation_id: str
    kind: BoundaryKind
    segment_id: str
    boundary_at: float
    from_shot_id: str
    to_shot_id: str
    speech_before_seconds: float
    speech_after_seconds: float
    visibility_before: Visibility
    visibility_after: Visibility


@dataclass(frozen=True, slots=True, kw_only=True)
class Opportunity:
    """An interval for a downstream stage to look at. It states evidence, not a choice."""

    opportunity_id: str
    kind: OpportunityKind
    start: float
    end: float
    shot_ids: tuple[str, ...]
    passage_id: str | None = None
    content_opportunity_id: str | None = None
    #: Content intelligence's own verdict on coverage, passed through unchanged.
    already_covered: bool | None = None
    visible_speaking_seconds: float | None = None
    reasons: tuple[str, ...] = ()
    scores: Scores = field(default_factory=Scores)
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Flag:
    kind: FlagKind
    severity: Severity
    subject: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StageReport:
    stage: str
    state: StageState
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    alignment_version: int
    scoring_version: int
    vocabulary_version: int
    profile_name: str
    profile_version: int
    #: Tools that decided a value (name -> identity), e.g. the embedding model.
    engines: Mapping[str, str]


@dataclass(frozen=True, slots=True, kw_only=True)
class AlignmentDocument:
    alignment_id: str
    asset_id: str
    created_at: datetime
    inputs: tuple[InputRecord, ...]
    sync: SyncAssessment
    speech_sources: tuple[SpeechSource, ...]
    speaker_links: tuple[SpeakerLink, ...]
    text_matches: tuple[TextMatch, ...]
    sound_actions: tuple[SoundAction, ...]
    boundaries: tuple[BoundaryRelation, ...]
    opportunities: tuple[Opportunity, ...]
    flags: tuple[Flag, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    #: What this result cannot tell you, stated once so a consumer need not guess.
    limitations: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    @property
    def video_correction(self) -> float:
        return next((c.seconds for c in self.sync.corrections if c.stream == "video"), 0.0)

    def speech_source(self, segment_id: str) -> SpeechSource | None:
        return next((s for s in self.speech_sources if s.segment_id == segment_id), None)

    def matches_for(self, passage_id: str) -> tuple[TextMatch, ...]:
        return tuple(m for m in self.text_matches if m.passage_id == passage_id)
