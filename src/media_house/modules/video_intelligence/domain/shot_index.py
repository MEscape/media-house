"""A compact, searchable projection of one video analysis: one row per shot.

A ``VideoAnalysis`` holds everything measured (curves for every frame, tracks, signals), which is
the right size for the module that owns it and far too large to read for every recording of a
library when a consumer only asks "which shots show this?". The ``ShotIndex`` keeps what a search
needs and nothing else: where the shot is, what it shows (objects, description, text on screen,
environment), how usable it is, how it moves, whether a face speaks in it, its events and its
embedding.

* It is DERIVED: ``build_shot_index`` is a pure function of one analysis, stored as a library
  document keyed by the analysis's identity, so a changed analysis makes a different index.
* A value the analysis could not state is ``None`` (never ``0``): only observations in state ``ok``
  are copied.
* Times are exact (``Rational``) seconds on the analysis's own video clock. Any correction to the
  container clock is the consumer's to apply (the alignment owns it); nothing here guesses one.
* The embedding is stored as half-precision floats in base64 (about 1.4 KB per shot): a search
  needs ranking accuracy, not storage accuracy.
"""

import base64
import hashlib
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from media_house.core.domain import FrameTime, Rational
from media_house.modules.video_intelligence.domain.observations import Event
from media_house.modules.video_intelligence.domain.result import Shot, VideoAnalysis
from media_house.shared.errors import InvariantViolation

#: Bump when the projection changes (a field, a rule): old indexes are then rebuilt, not trusted.
INDEX_VERSION = 1
INDEX_DOCUMENT = "video_intelligence.shot_index"
INDEX_OPERATION = "shot_index"


def exact_time(time: FrameTime) -> Rational:
    """The exact second of a frame time (``pts * timebase``), reduced."""
    value = Fraction(time.pts) * Fraction(time.timebase.numerator, time.timebase.denominator)
    return Rational(value.numerator, value.denominator)


def pack_vector(vector: tuple[float, ...]) -> str:
    """Half-precision floats, base64 (empty text for no vector)."""
    if not vector:
        return ""
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode("ascii")


def unpack_vector(text: str, dimension: int) -> tuple[float, ...]:
    if not text:
        return ()
    raw = base64.b64decode(text)
    if len(raw) != 2 * dimension:
        raise InvariantViolation(f"embedding has {len(raw) // 2} values, expected {dimension}")
    return struct.unpack(f"<{dimension}e", raw)


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexedEvent:
    """Something that changed inside a shot (a movement peak, text appearing, a gesture)."""

    kind: str
    start: Rational
    end: Rational | None = None
    strength: float | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexedShot:
    shot_id: str
    index: int
    start: Rational
    end: Rational
    frames: int
    scene_id: str | None = None
    #: What the picture shows, as the analysis observed it (empty = not observed, not "nothing").
    objects: tuple[str, ...] = ()
    entity_labels: tuple[str, ...] = ()
    description: str | None = None
    environment: str | None = None
    indoor_outdoor: str | None = None
    content_label: str | None = None
    #: Text read from the picture, in reading order, without repeats.
    texts: tuple[str, ...] = ()
    screen_kind: str | None = None
    #: ``ScoreName`` value -> score, only for scores the analysis could state.
    scores: Mapping[str, float] = field(default_factory=dict)
    sharpness: float | None = None
    motion_energy: float | None = None
    static_fraction: float | None = None
    camera_movement: str | None = None
    framing: str | None = None
    subject_kind: str | None = None
    #: A face is visible in this fraction of the analysed frames; ``None`` = faces not analysed.
    face_fraction: float | None = None
    #: Fraction of the shot in which a visible face appears to speak (mouth movement cue).
    visual_speaking: float | None = None
    indicators: tuple[str, ...] = ()
    head_seconds: float | None = None
    tail_seconds: float | None = None
    #: Shots that repeat this one (retake or near duplicate) share a group id.
    duplicate_group: str | None = None
    events: tuple[IndexedEvent, ...] = ()
    embedding_f16: str = ""

    def vector(self, dimension: int) -> tuple[float, ...]:
        return unpack_vector(self.embedding_f16, dimension)

    @property
    def seconds(self) -> Fraction:
        return Fraction(self.end.numerator, self.end.denominator) - Fraction(
            self.start.numerator, self.start.denominator
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotIndex:
    asset_id: str
    #: Content hash of the analysed asset version and the identity of the analysis it came from.
    asset_checksum: str
    analysis_identity: str
    profile_name: str
    #: The asset this version was derived from (an improved version or a proxy), else ``None``.
    derived_from_asset_id: str | None
    duration_seconds: float | None
    frame_rate: Rational | None
    variable_frame_rate: bool | None
    #: Camera make/model and reel/clip names from the file's own metadata.
    camera: str | None
    reel_id: str | None
    clip_id: str | None
    embedding_model: str | None
    embedding_dimension: int
    shots: tuple[IndexedShot, ...] = ()
    warnings: tuple[str, ...] = ()
    index_version: int = INDEX_VERSION

    def shot(self, shot_id: str) -> IndexedShot | None:
        return next((s for s in self.shots if s.shot_id == shot_id), None)


def analysis_identity(analysis: VideoAnalysis) -> str:
    """What makes two analyses of one asset interchangeable for a consumer of the index."""
    p = analysis.provenance
    parts = (
        analysis.asset_id,
        analysis.asset_checksum,
        str(p.schema_version),
        str(p.processing_version),
        p.profile_name,
        str(p.profile_version),
        str(p.vocabulary_version),
        str(sorted(p.analyzer_versions.items())),
        str(sorted(p.engines.items())),
        str(len(analysis.shots)),
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def build_shot_index(analysis: VideoAnalysis) -> ShotIndex:
    """The searchable rows of ``analysis`` (pure; nothing is measured here)."""
    groups = _duplicate_groups(analysis)
    events: dict[str, list[IndexedEvent]] = {}
    for event in analysis.events:
        if event.kind.value == "shot_boundary":
            continue
        events.setdefault(event.shot_id, []).append(_event(event))
    rows: list[IndexedShot] = []
    dimension = 0
    model: str | None = None
    for shot in analysis.shots:
        vector, embedding_model = _vector(analysis, shot)
        if vector:
            if dimension and len(vector) != dimension:
                raise InvariantViolation("shot embeddings of one analysis differ in size")
            dimension, model = len(vector), embedding_model
        rows.append(_row(shot, groups.get(shot.shot_id), events.get(shot.shot_id, ()), vector))
    source = analysis.source
    camera = " ".join(p for p in (source.camera_make, source.camera_model) if p) or None
    return ShotIndex(
        asset_id=analysis.asset_id,
        asset_checksum=analysis.asset_checksum,
        analysis_identity=analysis_identity(analysis),
        profile_name=analysis.provenance.profile_name,
        derived_from_asset_id=analysis.history.derived_from_asset_id,
        duration_seconds=source.duration_seconds,
        frame_rate=source.frame_rate,
        variable_frame_rate=source.variable_frame_rate,
        camera=camera,
        reel_id=source.reel_id,
        clip_id=source.clip_id,
        embedding_model=model,
        embedding_dimension=dimension,
        shots=tuple(rows),
        warnings=tuple(analysis.warnings),
    )


def _vector(analysis: VideoAnalysis, shot: Shot) -> tuple[tuple[float, ...], str | None]:
    ref = shot.meaning.embedding_ref if shot.meaning.ok else None
    embedding = analysis.embedding(ref) if ref else None
    if embedding is None or not embedding.vector:
        return (), None
    return embedding.vector, embedding.model


def _duplicate_groups(analysis: VideoAnalysis) -> dict[str, str]:
    return {
        shot_id: group.group_id
        for group in analysis.retakes
        if group.ok
        for shot_id in group.shot_ids
    }


def _event(event: Event) -> IndexedEvent:
    return IndexedEvent(
        kind=event.kind.value,
        start=exact_time(event.time),
        end=exact_time(event.end) if event.end is not None else None,
        strength=event.strength,
        detail=event.detail,
    )


def _row(
    shot: Shot,
    duplicate_group: str | None,
    events: Sequence[IndexedEvent],
    vector: tuple[float, ...],
) -> IndexedShot:
    meaning = shot.meaning
    text = shot.text
    faces = shot.faces
    quality = shot.quality
    camera = shot.camera
    motion = shot.motion
    framing = shot.framing
    ordered: list[str] = []
    if text.ok:
        for item in sorted(text.items, key=lambda i: i.first.pts):
            cleaned = " ".join(item.text.split())
            if cleaned and cleaned not in ordered:
                ordered.append(cleaned)
    return IndexedShot(
        shot_id=shot.shot_id,
        index=shot.index,
        start=exact_time(shot.range.start),
        end=exact_time(shot.range.end),
        frames=shot.range.frames,
        scene_id=shot.scene_id,
        objects=tuple(meaning.objects) if meaning.ok else (),
        entity_labels=tuple(sorted(shot.entities.counts)) if shot.entities.ok else (),
        description=meaning.description if meaning.ok else None,
        environment=meaning.environment if meaning.ok else None,
        indoor_outdoor=meaning.indoor_outdoor if meaning.ok else None,
        content_label=shot.content.label if shot.content.ok else None,
        texts=tuple(ordered),
        screen_kind=text.screen_kind if text.ok else None,
        scores={s.name.value: s.value for s in shot.scores if s.ok and s.value is not None},
        sharpness=quality.metrics.sharpness if quality.ok else None,
        motion_energy=motion.mean_energy if motion.ok else None,
        static_fraction=motion.static_fraction if motion.ok else None,
        camera_movement=camera.movement.value if camera.ok and camera.movement else None,
        framing=framing.framing.value if framing.ok and framing.framing else None,
        subject_kind=framing.subject_kind if framing.ok else None,
        face_fraction=_face_fraction(shot),
        visual_speaking=faces.visual_speaking if faces.ok else None,
        indicators=tuple(shot.indicators.reasons) if shot.indicators.ok else (),
        head_seconds=shot.handles.head_seconds if shot.handles.ok else None,
        tail_seconds=shot.handles.tail_seconds if shot.handles.ok else None,
        duplicate_group=duplicate_group,
        events=tuple(events),
        embedding_f16=pack_vector(vector),
    )


def _face_fraction(shot: Shot) -> float | None:
    faces = shot.faces
    if not faces.ok:
        return None
    analysed = shot.entities.analysed_frames
    if analysed <= 0:
        return None
    return min(1.0, faces.face_frames / analysed)
