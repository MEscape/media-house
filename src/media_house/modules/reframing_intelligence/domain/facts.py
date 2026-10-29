"""What the planner reads: narrow facts the application maps out of the upstream results.

The domain never sees a video analysis, a story plan or a continuity analysis. The application turns
the parts a crop needs into these small values (boxes, times, a few measured numbers), so the
planner is testable with plain geometry and the upstream contracts can grow without touching it.
Times are exact ``Fraction`` seconds on the recording's common clock; positions are normalised to
the display-oriented picture (see ``values``). A value an owner could not state is ``None``.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from media_house.modules.reframing_intelligence.domain.geometry import Box, SourceGeometry


@dataclass(frozen=True, slots=True)
class Sample:
    """Where one tracked element was at one moment, and how strongly the tracker believed it."""

    t: Fraction
    box: Box
    confidence: float


@dataclass(frozen=True, slots=True)
class ElementTrack:
    """One entity followed through one shot (a track never crosses a cut)."""

    track_id: str
    #: ``person``, ``face``, ``animal``, ``vehicle`` or ``object``.
    kind: str
    label: str
    samples: tuple[Sample, ...]
    #: Anonymous appearance cluster (``person_A``) when the analysis clustered identities.
    cluster: str | None = None


@dataclass(frozen=True, slots=True)
class ShotFacts:
    shot_id: str
    scene_id: str | None
    start: Fraction
    end: Fraction
    tracks: tuple[ElementTrack, ...]
    #: The shot's main subject track, when the analysis chose one.
    main_track_id: str | None = None
    #: ``wide`` ... ``extreme_close_up`` as the analysis judged the shot.
    framing: str | None = None
    #: Technical usability of the shot (0-1, higher better; the picture analysis folds sharpness,
    #: noise and exposure into it), when measured.
    usability: float | None = None
    #: Shot-level head yaw in degrees (the sign convention is not validated upstream).
    head_yaw: float | None = None
    #: Where the picture's attention sits (x, y), when no subject is tracked.
    attention: tuple[float, float] | None = None
    #: The group of repeated takes the shot belongs to.
    retake_group: str | None = None

    @property
    def seconds(self) -> float:
        return float(self.end - self.start)


@dataclass(frozen=True, slots=True)
class SourceFacts:
    asset_id: str
    geometry: SourceGeometry
    shots: tuple[ShotFacts, ...]
    #: Added to the analysis' own clock to reach the container clock; 0 with no alignment.
    clock_correction: Fraction = Fraction(0)
    corrected: bool = False
    #: Identity of the picture analysis the facts come from (a cache key part).
    identity: str = ""


@dataclass(frozen=True, slots=True)
class BeatHint:
    beat_id: str
    section_id: str
    function: str
    asset_id: str
    #: Where the beat lies in the recording (common clock); ``None`` = not stated.
    start: Fraction | None
    end: Fraction | None


@dataclass(frozen=True, slots=True)
class VisualNeed:
    """A passage the story says needs a picture of something (``demonstration``, ``evidence``)."""

    visual_id: str
    section_id: str
    purpose: str
    necessary: bool
    concept: str
    asset_id: str
    start: Fraction | None
    end: Fraction | None


@dataclass(frozen=True, slots=True)
class StoryFacts:
    story_plan_id: str
    plan_version: int
    #: The story's format (picks the profile when none is named).
    story_format: str | None = None
    beats: tuple[BeatHint, ...] = ()
    needs: tuple[VisualNeed, ...] = ()


@dataclass(frozen=True, slots=True)
class CutHint:
    """One cut a stored continuity analysis judged, as two source moments."""

    out_asset: str
    leaves: Fraction
    in_asset: str
    resumes: Fraction
    risk: float | None
    #: The judged break was a jump cut (the same picture continues across the cut).
    jump_cut: bool = False


@dataclass(frozen=True, slots=True)
class ContinuityFacts:
    analysis_id: str
    cuts: tuple[CutHint, ...] = ()
    identity: str = ""


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything the engine plans from, besides the profile and the target."""

    sources: tuple[SourceFacts, ...]
    story: StoryFacts | None = None
    continuity: ContinuityFacts | None = None
    #: Previous decisions by segment key (see ``Recommendation.segment_key``).
    decisions: dict[str, tuple[str, str]] = field(default_factory=dict)
