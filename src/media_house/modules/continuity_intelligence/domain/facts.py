"""What the domain reads: a proposed sequence and the evidence at each side of each cut.

The domain imports no other module. The application maps the stored results of the owners into
these plain facts. A value an owner could not state is ``None`` (never 0): the detectors treat it
as unknown and say so, they never read it as "matches" or as "differs".

``ProposedSequence`` is continuity's OWN input contract: a list of clips with source ranges and
timeline places. The caller (the pipeline, a test) builds it from an edit plan; this module does not
import the edit plan, so the editing stage can read continuity results without a cycle. Source
times are exact seconds on the recording's common clock; timeline times are exact seconds from the
start of the edit. They are never converted into each other here.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.continuity_intelligence.domain.values import TransitionKind


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposedClip:
    clip_id: str
    asset_id: str
    source_start: Fraction
    source_end: Fraction
    timeline_start: Fraction
    timeline_end: Fraction
    section_id: str | None = None
    beat_id: str | None = None
    #: 0 = the main picture; above 0 = cover footage that hides what is under it.
    layer: int = 0
    #: A J/L cut: this clip's sound starts before / ends after its picture.
    audio_bridge_in: bool = False
    audio_bridge_out: bool = False
    #: The clip plays material that is also elsewhere in the edit (a cold open).
    reuse_of: str | None = None
    #: The edit trimmed speech to make this boundary (a jump cut is expected, not an accident).
    speech_trim_in: bool = False
    speech_trim_out: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposedSequence:
    """The edit being judged: identified by the plan and version it came from."""

    edit_plan_id: str
    edit_plan_version: int
    clips: tuple[ProposedClip, ...]
    project_id: str | None = None
    #: The story's format, when known (picks the profile).
    story_format: str | None = None

    @property
    def assets(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.asset_id for c in self.clips))


@dataclass(frozen=True, slots=True, kw_only=True)
class NameFact:
    family: str
    value: str
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SubjectFact:
    track_id: str
    identity_cluster: str | None
    cx: float
    cy: float
    height: float
    vx: float | None
    vy: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class EdgeFact:
    """The frame at the boundary: subject, face cues, framing, light, scene, embedding."""

    subject: SubjectFact | None = None
    head_yaw: float | None = None
    eye_contact: float | None = None
    visual_speaking: float | None = None
    framing: str | None = None
    camera_movement: str | None = None
    luma: float | None = None
    color_balance: float | None = None
    subject_to_surround: float | None = None
    backlit: bool | None = None
    environment: str | None = None
    indoor_outdoor: str | None = None
    objects: tuple[str, ...] = ()
    names: tuple[NameFact, ...] = ()
    embedding: tuple[float, ...] = ()
    embedding_model: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioEdge:
    """The sound at the boundary, from the recording's own analysis."""

    #: The word the boundary falls inside (not at its edge), with how far from its nearer edge.
    straddled_word: str | None = None
    word_edge_distance: float | None = None
    #: The boundary falls inside a breath.
    inside_breath: bool = False
    #: Speech is heard within 150 ms on this side of the boundary.
    speech_near: bool = False
    #: Median level (dB) in the 300 ms next to the boundary and the quiet floor (dB) around it.
    level_db: float | None = None
    floor_db: float | None = None
    #: The nearest pause edges (seconds before/after the boundary), where a cleaner cut exists.
    pause_before: Fraction | None = None
    pause_after: Fraction | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SideFact:
    clip_id: str
    asset_id: str
    section_id: str | None
    beat_id: str | None
    #: The clip's source range on the recording's common clock.
    clip_start: Fraction
    clip_end: Fraction
    #: The source second of the boundary (the clip's end for the outgoing side, start for incoming).
    moment: Fraction
    shot_id: str | None = None
    scene_id: str | None = None
    edge: EdgeFact | None = None
    audio: AudioEdge | None = None
    #: The recording's sound/picture sync verdict: ``consistent``, ``offset_indicated``,
    #: ``ambiguous``, ``insufficient`` or ``unknown`` (no alignment).
    sync: str = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitionFact:
    transition_id: str
    kind: TransitionKind
    outgoing: SideFact
    incoming: SideFact
    timeline_at: Fraction
    #: A cutaway lies over the boundary: the join is not seen.
    covered: bool
    #: A J/L cut spreads the sound across the boundary.
    bridged: bool
    speech_trimmed: bool
    section_change: bool
    #: Same recording, but the incoming range lies BEFORE the outgoing one.
    reordered: bool
    #: Seconds skipped on the source between the two ranges (same recording, forward only).
    gap: Fraction | None
    cold_open: bool
    #: Identity of everything the judgement of this transition rests on (cache key of the cut).
    fingerprint: str
    #: Recordings a person confirmed to be takes/angles of the same scene as either side.
    related_assets: tuple[str, ...] = ()
    #: A cutaway candidate that covers this place was found by B-roll (None = not asked).
    cover_available: bool | None = None
