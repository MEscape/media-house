"""What the domain reads: plain facts, mapped from the owners' stored results by the application.

The domain imports no other module. A fact states what an owner measured or decided, together
with how sure it is; a value the owner could not state is ``None`` (never ``0``). Two kinds of
facts exist:

* the PASSAGE side (``TargetFact``): what is said, what the story needs, what is on screen;
* the FOOTAGE side (``ShotFact``): what a shot of the library shows, how usable it is, which
  recording it belongs to, and what is known about that recording's own sound.

Times on the passage side are floats of the recording's common clock (as the owners give them);
times on the footage side are exact ``Fraction``s of the same clock (the recording's alignment
correction is already applied when one exists, and ``ShotFact.clock`` says so).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction

from media_house.modules.broll_intelligence.domain.values import AssetVia, ClockBasis, TargetOrigin


@dataclass(frozen=True, slots=True, kw_only=True)
class NameFact:
    """A product-like name: ``family`` (brand or line, empty for a bare code) and ``value``."""

    family: str
    value: str
    text: str

    @property
    def key(self) -> str:
        return f"{self.family} {self.value}".strip()

    @property
    def stem(self) -> str:
        letters = ""
        for char in self.value:
            if not char.isalpha():
                break
            letters += char
        return letters


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceFact:
    """A pointing phrase of the passage ("this model") and what the content analysis made of it."""

    phrase: str
    #: ``resolved``, ``ambiguous`` or ``unresolved``.
    state: str
    name: NameFact | None = None
    reason: str = ""
    #: It points at an activity ("the same process"), not at a named thing.
    about_activity: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class MainPicture:
    """What the recording shows while the passage is spoken (the picture an insert would cover)."""

    shot_id: str | None = None
    #: A visible face is speaking in it (``None`` = not analysed).
    speaking_face: bool | None = None
    #: How hard the cut INTO this shot is (``None`` = not measured).
    jump_cut_likelihood: float | None = None
    environment: str | None = None
    indoor_outdoor: str | None = None
    framing: str | None = None
    #: Seconds the picture has shown the same shot.
    shot_seconds: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetFact:
    target_id: str
    origin: TargetOrigin
    asset_id: str
    start: float
    end: float
    #: The passage, and the statements before it (nearest last), for context.
    text: str
    context: tuple[str, ...] = ()
    #: The verbatim phrase the picture should show (content/story; never invented).
    concept: str = ""
    #: A story ``VisualPurpose`` value, or ``""``.
    purpose: str = ""
    #: A content ``VisualCategory`` value, or ``""``.
    category: str = ""
    #: ``essential`` / ``optional`` / ``""`` (the story's word on whether the picture is needed).
    necessity: str = ""
    strength: float | None = None
    already_covered: bool = False
    section_id: str | None = None
    beat_id: str | None = None
    beat_function: str = ""
    story_visual_id: str | None = None
    segment_id: str | None = None
    names: tuple[NameFact, ...] = ()
    references: tuple[ReferenceFact, ...] = ()
    #: The passage negates, doubts or imagines what it names.
    negated: bool = False
    main: MainPicture | None = None
    #: Delivery that deserves the speaker's face (emphasis, laughter, an emotional beat).
    emphatic: bool | None = None
    #: Why pacing or continuity asked for a change of picture here.
    reason: str = ""
    #: How much the story cares about this place, 0..1 (``None`` without a story).
    importance: float | None = None

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True, kw_only=True)
class EventFact:
    """Something that changed in a shot (a gesture, text appearing, a movement peak)."""

    kind: str
    start: Fraction
    end: Fraction | None = None
    strength: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SoundActionFact:
    """An alignment-confirmed pairing of a sound with the movement that makes it."""

    video_time: Fraction
    audio_kind: str
    #: Seconds between the sound and the movement.
    delta: float
    #: ``corroborated`` / ``indicated`` / ``suggestive`` (ordinal, from the alignment).
    grade: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceAudioFact:
    """What the footage's own sound contains (from its audio analysis), on the common clock."""

    speech: tuple[tuple[Fraction, Fraction], ...] = ()
    #: Non-speech events: kind, start, end.
    events: tuple[tuple[str, Fraction, Fraction], ...] = ()
    identity: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationFact:
    other_asset_id: str
    #: ``alternate_take_of``, ``temporally_overlaps_with``, ``exact_duplicate_of`` ...
    kind: str
    #: ``recorded`` (lineage), ``confirmed`` (a person said so), ``detected`` (a proposal).
    certainty: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotFact:
    #: The ORIGINAL asset: the id every recommendation uses.
    asset_id: str
    #: The stored version the evidence came from (a proxy or improved version), and which kind.
    analyzed_asset_id: str
    via: AssetVia
    asset_name: str
    shot_id: str
    index: int
    start: Fraction
    end: Fraction
    clock: ClockBasis
    asset_duration: Fraction | None = None
    tags: tuple[str, ...] = ()
    scene_id: str | None = None
    objects: tuple[str, ...] = ()
    entity_labels: tuple[str, ...] = ()
    description: str | None = None
    environment: str | None = None
    indoor_outdoor: str | None = None
    content_label: str | None = None
    texts: tuple[str, ...] = ()
    screen_kind: str | None = None
    #: Names found in the shot's text, the asset's name/tags and its description, with the place.
    names: tuple[tuple[NameFact, str], ...] = ()
    scores: tuple[tuple[str, float], ...] = ()
    sharpness: float | None = None
    motion_energy: float | None = None
    static_fraction: float | None = None
    camera_movement: str | None = None
    framing: str | None = None
    face_fraction: float | None = None
    visual_speaking: float | None = None
    indicators: tuple[str, ...] = ()
    head_seconds: float | None = None
    tail_seconds: float | None = None
    duplicate_group: str | None = None
    events: tuple[EventFact, ...] = ()
    audio: SourceAudioFact | None = None
    sound_actions: tuple[SoundActionFact, ...] = ()
    #: The recording's alignment says its sound and picture are in sync (``None`` = unknown).
    sync_established: bool | None = None
    relations: tuple[RelationFact, ...] = ()
    #: Identity of the analysis the facts came from (provenance and cache key).
    analysis_identity: str = ""

    @property
    def key(self) -> str:
        return f"{self.asset_id}#{self.shot_id}"

    @property
    def seconds(self) -> Fraction:
        return self.end - self.start

    def score(self, name: str) -> float | None:
        return dict(self.scores).get(name)


@dataclass(frozen=True, slots=True, kw_only=True)
class Match:
    """What the embedding models said about one (passage, shot) pair. Model estimates."""

    #: Cosine similarity of the passage's words and the shot's picture embedding.
    visual_similarity: float | None = None
    #: That similarity against the same passage's similarity to every embedded shot of the pool
    #: (standard score): how unusual the match is, which raw CLIP similarities cannot say.
    visual_z: float | None = None
    #: Cosine similarity of the passage and the shot's description (sentence embeddings).
    text_similarity: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RetrievalFacts:
    #: ``(target_id, shot key)`` -> what the models said.
    matches: Mapping[tuple[str, str], Match] = field(default_factory=dict)
    #: Pairs of shot keys whose pictures are near-identical -> similarity.
    similar: Mapping[frozenset[str], float] = field(default_factory=dict)
    visual_model: str | None = None
    text_model: str | None = None
    #: Why a model-based signal is missing (``visual_unavailable`` ...), for the result.
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class PoolFact:
    """What was searched: so "nothing suitable" can be told from "nothing was looked at"."""

    assets: int
    with_shots: int
    shots: int
    shots_with_embedding: int
    #: ``(asset id, reason)`` for assets that could not be searched.
    skipped: tuple[tuple[str, str], ...] = ()
    #: Library facts that bound the answer ("pool capped at 500 assets").
    limits: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryFacts:
    story_plan_id: str
    plan_version: int
    stored_as: str | None
    target_format: str
