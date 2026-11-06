"""The narrow facts the engine reads from a built timeline.

The domain may import no other module, so ``application/views.py`` maps the timeline builder's
``BuiltTimeline`` into these. Only what a mix needs: the sound clips with their exact positions,
the instructions that act on them, the sources they play, the timebase and the creator identity's
format profile (a reference).
"""

from dataclasses import dataclass

from media_house.core.domain import Rational


@dataclass(frozen=True, slots=True)
class TimebaseFact:
    frame_rate: Rational
    sample_rate: int
    duration_samples: int


@dataclass(frozen=True, slots=True)
class TrackFact:
    track_id: str
    role: str  # dialogue | production | music | ambience | sfx
    name: str


@dataclass(frozen=True, slots=True)
class SourceFact:
    asset_id: str
    original_asset_id: str
    relation: str  # original | improved_audio | library | rendered ...
    name: str
    #: The file's own audio sample rate and channel count when the inspection knew them.
    sample_rate: int | None
    channels: int | None
    checksum: str | None
    available: bool


@dataclass(frozen=True, slots=True)
class ClipFact:
    clip_id: str
    track_id: str
    role: str
    asset_id: str | None
    source_in: Rational
    source_out: Rational
    start_sample: int
    end_sample: int
    gain_db: float
    fade_in: Rational
    fade_out: Rational
    speed: Rational
    channels: str
    provenance: str
    resolved: bool
    video_link: str | None


@dataclass(frozen=True, slots=True)
class InstructionFact:
    """An instruction for the mix: ``duck`` | ``crossfade`` | ``music_tail``."""

    mix_id: str
    kind: str
    clip_ids: tuple[str, ...]
    start_sample: int
    end_sample: int
    key: str
    depth_db: float
    attack_seconds: float
    release_seconds: float
    curve: str
    at_sample: int | None
    before_seconds: float
    after_seconds: float


@dataclass(frozen=True, slots=True)
class MixFacts:
    timeline_id: str
    anchor_asset_id: str
    schema_version: int
    build_version: int
    build_hash: str
    format_profile: str
    timebase: TimebaseFact
    tracks: tuple[TrackFact, ...]
    clips: tuple[ClipFact, ...]
    instructions: tuple[InstructionFact, ...]
    sources: tuple[SourceFact, ...]

    def source(self, asset_id: str) -> SourceFact | None:
        return next((s for s in self.sources if s.asset_id == asset_id), None)
