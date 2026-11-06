"""The audio mix specification: WHAT is mixed and HOW, as one versioned, typed document.

It is derived from the built timeline (which owns where each sound is) plus a mix profile and a
delivery profile (which own the technical behaviour). It shares the timeline's stable ids, so an
element here is traceable to the timeline instruction it carries out (``origin_ref``). It names no
editing application. It is not the result: what was actually rendered and measured is
``MixResult`` (``result.py``).
"""

import json
from dataclasses import dataclass

from media_house.core.domain import Rational, json_codec
from media_house.modules.audio_mix_engine.domain.errors import InvalidMixDocument
from media_house.modules.audio_mix_engine.domain.profile import Compressor, EqBand, Limiter
from media_house.modules.audio_mix_engine.domain.values import (
    ENGINE_VERSION,
    SCHEMA_VERSION,
    SPEC_DOCUMENT_TYPE,
    BusRole,
    DependencyState,
    Interpolation,
    Provenance,
    Severity,
)


@dataclass(frozen=True, slots=True)
class Timebase:
    frame_rate: Rational
    sample_rate: int
    duration_samples: int


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_id: str
    asset_id: str
    original_asset_id: str
    relation: str
    name: str
    sample_rate: int | None
    channels: int | None
    checksum: str | None
    available: bool


@dataclass(frozen=True, slots=True)
class MixTrack:
    track_id: str
    name: str
    role: BusRole
    bus_id: str


@dataclass(frozen=True, slots=True)
class MixBus:
    bus_id: str
    role: BusRole
    name: str
    #: The bus this one feeds (``master`` for every bus except the master itself).
    parent: str | None
    gain_db: float
    eq: tuple[EqBand, ...] = ()
    compressor: Compressor | None = None
    pan: float = 0.0
    limiter: Limiter | None = None


@dataclass(frozen=True, slots=True)
class MixClip:
    clip_id: str
    track_id: str
    source_id: str
    #: The part of the FILE it plays (exact seconds) and where it sits (samples of the timebase).
    source_in: Rational
    source_out: Rational
    start_sample: int
    end_sample: int
    gain_db: float
    fade_in: Rational
    fade_out: Rational
    #: ``linear`` | ``equal_power``
    fade_curve: str
    speed: Rational
    channels: str
    pan: float
    provenance: Provenance
    origin_ref: str


@dataclass(frozen=True, slots=True)
class GainPoint:
    sample: int
    db: float


@dataclass(frozen=True, slots=True)
class Automation:
    automation_id: str
    #: ``clip`` | ``bus``
    target_kind: str
    target_id: str
    points: tuple[GainPoint, ...]
    interpolation: Interpolation
    provenance: Provenance
    origin_ref: str


@dataclass(frozen=True, slots=True)
class Duck:
    """The listed clips are lowered while the key (speech) is present: a gain envelope with an
    attack before and a release after the range. Deterministic: it depends on ranges, not audio."""

    duck_id: str
    clip_ids: tuple[str, ...]
    key: str
    start_sample: int
    end_sample: int
    depth_db: float
    attack_seconds: float
    release_seconds: float
    provenance: Provenance
    origin_ref: str


@dataclass(frozen=True, slots=True)
class Crossfade:
    crossfade_id: str
    clip_ids: tuple[str, ...]
    at_sample: int
    before_seconds: float
    after_seconds: float
    curve: str
    origin_ref: str


@dataclass(frozen=True, slots=True)
class Loudness:
    delivery: str
    integrated_lufs: float
    tolerance_lu: float
    max_loudness_range_lu: float | None
    true_peak_dbtp: float


@dataclass(frozen=True, slots=True)
class RenderConfig:
    sample_rate: int
    bit_depth: int
    channels: int
    container: str
    codec: str


@dataclass(frozen=True, slots=True)
class StemSpec:
    stem_id: str
    bus_id: str
    #: Every stem includes source gain, track and bus processing, and stops before the master.
    stage: str


@dataclass(frozen=True, slots=True)
class ValidationRequirements:
    checks: tuple[str, ...]
    min_dialogue_over_music_lu: float
    silence_threshold_db: float
    silence_min_seconds: float
    recombination_tolerance_db: float


@dataclass(frozen=True, slots=True)
class Dependency:
    kind: str
    reference: str
    state: DependencyState
    required_by: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SpecIssue:
    code: str
    severity: Severity
    message: str
    element_id: str | None = None


@dataclass(frozen=True, slots=True)
class SpecProvenance:
    timeline_id: str
    timeline_version: int
    timeline_build_hash: str
    anchor_asset_id: str
    profile: str
    profile_version: int
    delivery: str
    engine_version: int
    #: Overrides a person recorded (``kind:id=value``): a revision can be told from a rebuild.
    overrides: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AudioMixSpecification:
    specification_id: str
    project_id: str | None
    timeline_id: str
    timeline_version: int
    created_at: str
    timebase: Timebase
    sources: tuple[SourceRef, ...]
    tracks: tuple[MixTrack, ...]
    buses: tuple[MixBus, ...]
    clips: tuple[MixClip, ...]
    automation: tuple[Automation, ...]
    ducking: tuple[Duck, ...]
    crossfades: tuple[Crossfade, ...]
    loudness: Loudness
    render: RenderConfig
    stems: tuple[StemSpec, ...]
    validation: ValidationRequirements
    dependencies: tuple[Dependency, ...]
    warnings: tuple[SpecIssue, ...]
    provenance: SpecProvenance
    content_hash: str
    schema_version: int = SCHEMA_VERSION
    engine_version: int = ENGINE_VERSION

    def bus(self, bus_id: str) -> MixBus | None:
        return next((b for b in self.buses if b.bus_id == bus_id), None)

    def track(self, track_id: str) -> MixTrack | None:
        return next((t for t in self.tracks if t.track_id == track_id), None)

    def source(self, source_id: str) -> SourceRef | None:
        return next((s for s in self.sources if s.source_id == source_id), None)

    def clip(self, clip_id: str) -> MixClip | None:
        return next((c for c in self.clips if c.clip_id == clip_id), None)

    def clips_of(self, bus_id: str) -> tuple[MixClip, ...]:
        tracks = {t.track_id for t in self.tracks if t.bus_id == bus_id}
        return tuple(c for c in self.clips if c.track_id in tracks)

    @property
    def render_samples(self) -> int:
        """The timeline's length in samples of the delivery's rate."""
        return round(
            self.timebase.duration_samples * self.render.sample_rate / self.timebase.sample_rate
        )

    @property
    def master(self) -> MixBus:
        found = next((b for b in self.buses if b.role is BusRole.MASTER), None)
        if found is None:
            raise InvalidMixDocument("the specification has no master bus")
        return found


def spec_to_json(spec: AudioMixSpecification) -> str:
    return json_codec.dumps(SPEC_DOCUMENT_TYPE, spec)


def spec_from_json(text: str) -> AudioMixSpecification:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidMixDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != SPEC_DOCUMENT_TYPE:
        raise InvalidMixDocument("not an audio mix specification")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidMixDocument(f"schema version {raw.get('schema_version')!r} is not readable")
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(AudioMixSpecification, body, "specification")
    except (TypeError, ValueError) as exc:
        raise InvalidMixDocument(str(exc)) from exc
    if not isinstance(decoded, AudioMixSpecification):
        raise InvalidMixDocument("not an audio mix specification")
    return decoded
