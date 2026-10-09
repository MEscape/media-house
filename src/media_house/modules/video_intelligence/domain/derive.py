"""From stored signals to the result: every section of every shot, assembled in one pure function.

The signals of the analyzers that ran arrive as a mapping; a section whose analyzer did not run is
``not_analyzed`` with a reason, never absent. Everything here is a pure function of the signals,
the profile and the upstream facts, so changing a threshold re-derives in milliseconds and the same
inputs always give the same result.
"""

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

from media_house.core.domain import TimeRange
from media_house.modules.video_intelligence.domain import shot_sections as section
from media_house.modules.video_intelligence.domain.body_cues import describe_body
from media_house.modules.video_intelligence.domain.cinema import (
    crop_safe_regions,
    describe_composition,
    describe_framing,
    describe_geometry,
    describe_lighting,
    main_subject,
)
from media_house.modules.video_intelligence.domain.errors import InvalidAnalysis
from media_house.modules.video_intelligence.domain.events import build_events
from media_house.modules.video_intelligence.domain.face_cues import (
    describe_faces,
    main_faces,
    mouth_activity,
)
from media_house.modules.video_intelligence.domain.geometry import BBox, iou
from media_house.modules.video_intelligence.domain.identity import (
    Cluster,
    TrackAppearance,
    cluster_tracks,
    cosine,
    mean_vector,
)
from media_house.modules.video_intelligence.domain.meaning import (
    classify_content,
    describe_environment,
    find_indicators,
    find_retakes,
    group_scenes,
    shot_embedding,
)
from media_house.modules.video_intelligence.domain.observations import (
    AttentionPoint,
    BodyObservation,
    CameraObservation,
    CompositionObservation,
    ContentTypeObservation,
    ContinuityObservation,
    CropSafeRegion,
    Embedding,
    EntitiesObservation,
    Event,
    Evidence,
    FaceObservation,
    FramingObservation,
    GeometryObservation,
    HandlesObservation,
    IdentityCluster,
    IndicatorObservation,
    Keyframes,
    LightingObservation,
    MotionObservation,
    Overlay,
    QualityAssessment,
    RetakeGroup,
    Scene,
    Score,
    TextObservation,
    Track,
    TrackPoint,
    VisualMeaningObservation,
)
from media_house.modules.video_intelligence.domain.profiles import (
    ProcessingProfile,
    content_profile,
)
from media_house.modules.video_intelligence.domain.result import Curve, Shot
from media_house.modules.video_intelligence.domain.scoring import (
    ShotMeasures,
    score_shot,
    variation,
)
from media_house.modules.video_intelligence.domain.shot_detection import (
    black_frames,
    detect_transitions,
)
from media_house.modules.video_intelligence.domain.signals import (
    AppearanceSignals,
    BodySignals,
    DescriptionSignals,
    DetectionSignals,
    EmbeddingSignals,
    FaceSignals,
    GeometrySignals,
    MotionSignals,
    QualitySignals,
    SaliencySignals,
    ShotSignals,
    TextSignals,
)
from media_house.modules.video_intelligence.domain.source import ProcessingHistory, SourceInfo
from media_house.modules.video_intelligence.domain.text_cues import (
    describe_text,
    find_overlays,
    find_split_screens,
    track_text,
)
from media_house.modules.video_intelligence.domain.tracking import RawTrack, build_tracks
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    AnalyzerState,
    EntityKind,
)

#: Version of every derivation. Bump the one that changes; the result's cache key holds them.
DERIVATION_VERSIONS: dict[str, int] = {
    "shots": 1,
    "handles": 1,
    "keyframes": 1,
    "camera": 1,
    "quality": 1,
    "tracking": 1,
    "identity": 1,
    "faces": 1,
    "body": 1,
    "text": 1,
    "overlays": 1,
    "meaning": 1,
    "scenes": 1,
    "retakes": 1,
    "continuity": 1,
    "cinema": 1,
    "scores": 1,
    "events": 1,
}

_FULL_CONFIDENCE_FRAMES = 3
_ATTENTION_POINT_FRAMES = 3


@dataclass(frozen=True, slots=True)
class Derived:
    shots: tuple[Shot, ...]
    curves: tuple[Curve, ...]
    warnings: tuple[str, ...]
    scenes: tuple[Scene, ...] = ()
    tracks: tuple[Track, ...] = ()
    identities: tuple[IdentityCluster, ...] = ()
    retakes: tuple[RetakeGroup, ...] = ()
    overlays: tuple[Overlay, ...] = ()
    events: tuple[Event, ...] = ()
    embeddings: tuple[Embedding, ...] = ()


@dataclass(frozen=True, slots=True)
class _Signals:
    """The signals of the analyzers that ran, typed (``None``: the analyzer did not run)."""

    motion: MotionSignals | None
    quality: QualitySignals | None
    saliency: SaliencySignals | None
    geometry: GeometrySignals | None
    entities: DetectionSignals | None
    faces: FaceSignals | None
    body: BodySignals | None
    text: TextSignals | None
    embeddings: EmbeddingSignals | None
    appearance: AppearanceSignals | None
    descriptions: DescriptionSignals | None


def _typed[T](
    signals: Mapping[AnalyzerId, object], analyzer: AnalyzerId, kind: type[T]
) -> T | None:
    value = signals.get(analyzer)
    return value if isinstance(value, kind) else None


def _gather(signals: Mapping[AnalyzerId, object]) -> _Signals:
    return _Signals(
        motion=_typed(signals, AnalyzerId.MOTION, MotionSignals),
        quality=_typed(signals, AnalyzerId.QUALITY, QualitySignals),
        saliency=_typed(signals, AnalyzerId.SALIENCY, SaliencySignals),
        geometry=_typed(signals, AnalyzerId.GEOMETRY, GeometrySignals),
        entities=_typed(signals, AnalyzerId.ENTITIES, DetectionSignals),
        faces=_typed(signals, AnalyzerId.FACES, FaceSignals),
        body=_typed(signals, AnalyzerId.BODY, BodySignals),
        text=_typed(signals, AnalyzerId.TEXT, TextSignals),
        embeddings=_typed(signals, AnalyzerId.EMBEDDINGS, EmbeddingSignals),
        appearance=_typed(signals, AnalyzerId.APPEARANCE, AppearanceSignals),
        descriptions=_typed(signals, AnalyzerId.DESCRIPTIONS, DescriptionSignals),
    )


def _require_aligned(timeline: ShotSignals, signals: _Signals) -> None:
    """Signals of different analyzers must describe the same video; a mismatch is a stale cache."""
    last = timeline.frame_count - 1
    problems: list[str] = []
    candidates: dict[str, Sequence[int]] = {
        "motion": signals.motion.frames if signals.motion else (),
        "quality": signals.quality.frames if signals.quality else (),
        "saliency": signals.saliency.frames if signals.saliency else (),
        "geometry": signals.geometry.frames if signals.geometry else (),
        "entities": signals.entities.frames if signals.entities else (),
        "faces": signals.faces.frames if signals.faces else (),
        "body": signals.body.frames if signals.body else (),
        "text": signals.text.frames if signals.text else (),
        "embeddings": signals.embeddings.frames if signals.embeddings else (),
        "appearance": signals.appearance.frames if signals.appearance else (),
        "descriptions": signals.descriptions.frames if signals.descriptions else (),
    }
    for name, frames in candidates.items():
        if frames and max(frames) > last:
            problems.append(f"the {name} signals reach past the last frame of the shot signals")
    if problems:
        raise InvalidAnalysis(tuple(problems))


def _not_run(analyzer: str) -> tuple[str, ...]:
    return (f"{analyzer}_analyzer_not_run",)


def derive(
    timeline: ShotSignals,
    signals: Mapping[AnalyzerId, object],
    profile: ProcessingProfile,
    source: SourceInfo,
    history: ProcessingHistory,
) -> Derived:
    s = _gather(signals)
    _require_aligned(timeline, s)
    detection = detect_transitions(timeline, profile.shots)
    black = black_frames(timeline, profile.shots)
    count = timeline.frame_count
    starts = [0, *(t.frame for t in detection.transitions)]
    ends = [*starts[1:], count]
    shot_ids = [f"shot_{first:07d}" for first in starts]
    bounds = section.boundaries(detection.transitions, timeline, black)
    aspect = source.height / source.width
    picture_aspect = source.width / source.height
    fps = timeline.frames_per_second

    tracks, identities = _tracks(timeline, s, starts, shot_ids, profile, fps)
    by_shot: dict[str, list[Track]] = {}
    for track in tracks:
        by_shot.setdefault(track.shot_id, []).append(track)
    faces_all = main_faces(s.faces.rows, profile.faces) if s.faces else {}
    speaking = mouth_activity(faces_all, timeline, profile.faces) if s.faces else {}
    text_items = track_text(s.text, starts, profile.text) if s.text else []
    split = find_split_screens(s.geometry, timeline, starts, profile.text) if s.geometry else []
    overlays = (find_overlays(text_items, s.text, timeline, profile.text) if s.text else []) + split

    # --- per-shot sections that need no neighbours ------------------------------------------------
    vectors = [
        shot_embedding(s.embeddings, first, end) if s.embeddings else ()
        for first, end in zip(starts, ends, strict=True)
    ]
    rows: list[_ShotRows] = []
    for index, (first, end) in enumerate(zip(starts, ends, strict=True)):
        rows.append(
            _shot_rows(
                index,
                first,
                end,
                shot_ids[index],
                timeline,
                s,
                profile,
                source,
                history,
                by_shot.get(shot_ids[index], []),
                faces_all,
                speaking,
                text_items,
                black,
                aspect,
                picture_aspect,
                bounds,
                detection.transitions,
            )
        )

    # --- scenes, retakes and continuity (need all shots) ---------------------------------------
    groups = group_scenes(vectors, profile.meaning) if s.embeddings else []
    scene_of: dict[int, str] = {}
    scenes: list[Scene] = []
    embeddings: list[Embedding] = []
    for group in groups:
        scene_id = f"scene_{starts[group.shot_indices[0]]:07d}"
        for i in group.shot_indices:
            scene_of[i] = scene_id
        first_i, last_i = group.shot_indices[0], group.shot_indices[-1]
        marks = tuple(dict.fromkeys(timeline.time_of(starts[i]) for i in group.shot_indices[:3]))
        members = [vectors[i] for i in group.shot_indices if vectors[i]]
        ref = f"emb_{scene_id}" if members else None
        scenes.append(
            Scene(
                state=AnalyzerState.OK,
                confidence=max(0.0, min(1.0, group.similarity)),
                evidence=(Evidence(frames=marks, metric="embedding"),),
                scene_id=scene_id,
                shot_ids=tuple(shot_ids[i] for i in group.shot_indices),
                range=TimeRange(
                    timeline.time_of(starts[first_i]), section.time_at(timeline, ends[last_i])
                ),
                embedding_ref=ref,
            )
        )
        if ref and s.embeddings:
            embeddings.append(
                Embedding(
                    ref=ref, scope="scene", model=s.embeddings.model, vector=mean_vector(members)
                )
            )
    for i, vector in enumerate(vectors):
        if vector and s.embeddings:
            embeddings.append(
                Embedding(
                    ref=f"emb_{shot_ids[i]}", scope="shot", model=s.embeddings.model, vector=vector
                )
            )

    framing_keys = [_framing_key(r) for r in rows]
    retakes = (
        find_retakes(shot_ids, vectors, framing_keys, timeline, starts, profile.meaning)
        if s.embeddings
        else []
    )
    continuity = [
        _continuity(
            rows[i - 1] if i else None,
            rows[i],
            vectors[i - 1] if i else (),
            vectors[i],
            i,
            shot_ids,
            timeline,
            starts,
            profile,
        )
        for i in range(len(rows))
    ]

    # --- scores (need the content type of each shot) -------------------------------------------
    shots: list[Shot] = []
    for index, r in enumerate(rows):
        profile_of_shot = content_profile(r.content.content_profile)
        measures = ShotMeasures(
            camera=r.camera,
            motion=r.motion,
            quality=r.quality,
            faces=r.faces,
            framing=r.framing,
            composition=r.composition,
            meaning=r.meaning,
            text=r.text,
            indicators=r.indicators,
            subject_frames=r.subject_frames,
            analysed_frames=r.analysed_entity_frames,
            subject_height_variation=r.subject_height_variation,
        )
        shots.append(
            Shot(
                shot_id=r.shot_id,
                index=index,
                range=r.range,
                boundary_in=bounds[index],
                boundary_out=bounds[index + 1],
                handles=r.handles,
                keyframes=r.keyframes,
                camera=r.camera,
                motion=r.motion,
                quality=r.quality,
                entities=r.entities,
                faces=r.faces,
                body=r.body,
                text=r.text,
                scene_id=scene_of.get(index),
                content=r.content,
                meaning=r.meaning,
                continuity=continuity[index],
                indicators=r.indicators,
                framing=r.framing,
                composition=r.composition,
                lighting=r.lighting,
                geometry=r.geometry,
                crop_safe=r.crop_safe,
                scores=score_shot(measures, profile_of_shot, profile.quality, profile.cinema),
            )
        )

    events = build_events(
        timeline=timeline,
        shots=shots,
        tracks=tracks,
        entity_frames=s.entities.frames if s.entities else (),
        flashes=detection.flashes,
        motion=s.motion,
        saliency=s.saliency,
        text_items=text_items,
        text_signals=s.text,
        meaning=profile.meaning,
        text=profile.text,
    )

    warnings: list[str] = []
    if detection.flashes:
        warnings.append(f"{len(detection.flashes)} single-frame brightness flashes were not cuts")
    if timeline.estimated_timestamps:
        warnings.append(f"{timeline.estimated_timestamps} frame timestamps were interpolated")
    curves = section.curves(timeline, s.motion, s.quality, history)
    curves = (*curves, *_face_curves(timeline, faces_all, speaking, history))
    return Derived(
        tuple(shots),
        tuple(curves),
        tuple(warnings),
        tuple(scenes),
        tuple(tracks),
        tuple(identities),
        tuple(retakes),
        tuple(overlays),
        events,
        tuple(embeddings),
    )


# --- tracks and identities ---------------------------------------------------------------------
def _tracks(
    timeline: ShotSignals,
    s: _Signals,
    starts: Sequence[int],
    shot_ids: Sequence[str],
    profile: ProcessingProfile,
    fps: float,
) -> tuple[list[Track], list[IdentityCluster]]:
    if s.entities is None:
        return [], []
    raw = build_tracks(s.entities, starts, fps, profile.tracking)
    ids: list[str] = []
    ordinal: dict[int, int] = {}
    for track in raw:
        ordinal[track.first] = ordinal.get(track.first, 0) + 1
        ids.append(f"trk_{track.first:07d}_{ordinal[track.first]}")

    clusters: list[Cluster] = []
    cluster_of: dict[str, str] = {}
    if s.appearance is not None and s.appearance.rows:
        appearances = []
        for track_id, track in zip(ids, raw, strict=True):
            if track.kind is not EntityKind.PERSON:
                continue
            vectors = [
                row.vector
                for row in s.appearance.rows
                for frame, box, _ in track.points
                if row.frame == frame and iou(row.box, box) >= _APPEARANCE_MATCH_IOU
            ]
            if vectors:
                appearances.append(
                    TrackAppearance(
                        track_id, track.shot_index, track.first, track.last, mean_vector(vectors)
                    )
                )
        clusters = cluster_tracks(appearances, profile.meaning.identity_similarity)
        cluster_of = {t: c.cluster_id for c in clusters for t in c.track_ids}

    tracks = [
        _track(track_id, track, timeline, shot_ids, s.entities, cluster_of.get(track_id))
        for track_id, track in zip(ids, raw, strict=True)
    ]
    identities = []
    for cluster in clusters:
        members = [t for t in tracks if t.track_id in cluster.track_ids]
        marks = tuple(dict.fromkeys(m.first for m in members[:3]))
        identities.append(
            IdentityCluster(
                state=AnalyzerState.OK,
                confidence=max(0.0, min(1.0, cluster.similarity)),
                evidence=(Evidence(frames=marks, metric="appearance_embedding"),),
                cluster_id=cluster.cluster_id,
                track_ids=cluster.track_ids,
                method="appearance_embedding_similarity",
            )
        )
    return tracks, identities


_APPEARANCE_MATCH_IOU = 0.9


def _track(
    track_id: str,
    raw: RawTrack,
    timeline: ShotSignals,
    shot_ids: Sequence[str],
    detections: DetectionSignals,
    cluster: str | None,
) -> Track:
    frames = [f for f in detections.frames if raw.first <= f <= raw.last]
    occluded = max(0, len(frames) - len(raw.points))
    points = tuple(
        TrackPoint(time=timeline.time_of(f), box=b, confidence=c) for f, b, c in raw.points
    )
    marks = tuple(dict.fromkeys(points[i].time for i in (0, len(points) // 2, len(points) - 1)))
    mean_confidence = sum(c for _, _, c in raw.points) / len(raw.points)
    return Track(
        state=AnalyzerState.OK,
        confidence=min(1.0, mean_confidence * min(1.0, len(raw.points) / 4)),
        evidence=(Evidence(frames=marks, metric="detection_boxes"),),
        track_id=track_id,
        kind=raw.kind,
        label=raw.label,
        shot_id=shot_ids[raw.shot_index],
        first=points[0].time,
        last=points[-1].time,
        points=points,
        occluded_frames=occluded,
        identity_cluster=cluster,
    )


# --- one shot's sections -----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _ShotRows:
    shot_id: str
    range: TimeRange
    handles: HandlesObservation
    keyframes: Keyframes
    camera: CameraObservation
    motion: MotionObservation
    quality: QualityAssessment
    entities: EntitiesObservation
    faces: FaceObservation
    body: BodyObservation
    text: TextObservation
    content: ContentTypeObservation
    meaning: VisualMeaningObservation
    indicators: IndicatorObservation
    framing: FramingObservation
    composition: CompositionObservation
    lighting: LightingObservation
    geometry: GeometryObservation
    crop_safe: tuple[CropSafeRegion, ...]
    subject: Track | None
    subject_frames: int
    analysed_entity_frames: int
    subject_height_variation: float | None
    color_balance: float | None
    luma: float | None
    identity: str | None


def _shot_rows(
    index: int,
    first: int,
    end: int,
    shot_id: str,
    timeline: ShotSignals,
    s: _Signals,
    profile: ProcessingProfile,
    source: SourceInfo,
    history: ProcessingHistory,
    tracks: Sequence[Track],
    faces_all: Mapping[int, object],
    speaking: Mapping[int, float],
    text_items: Sequence[object],
    black: Sequence[bool],
    aspect: float,
    picture_aspect: float,
    bounds: Sequence[object],
    transitions: Sequence[object],
) -> _ShotRows:
    del index, faces_all, bounds  # the shot is addressed by its frames, not its position
    rng = TimeRange(timeline.time_of(first), section.time_at(timeline, end))
    handles = section.handles(timeline, profile, first, end, tuple(transitions))  # type: ignore[arg-type]
    keyframes = section.keyframes(timeline, s.quality, first, end)
    camera = section.camera_section(s.motion, timeline, first, end, aspect, profile, history)
    motion = section.motion_section(s.motion, timeline, first, end, aspect, profile)
    quality = section.quality_section(s.quality, timeline, first, end, profile, source, history)

    analysed = [f for f in s.entities.frames if first <= f < end] if s.entities else []
    subject = main_subject(tracks)
    entities = _entities(s.entities, analysed, tracks, subject, timeline)
    faces = (
        describe_faces(s.faces, timeline, first, end, profile.faces, dict(speaking))
        if s.faces
        else FaceObservation(state=AnalyzerState.NOT_ANALYZED, reasons=_not_run("faces"))
    )
    body = (
        describe_body(s.body, timeline, first, end)
        if s.body
        else BodyObservation(state=AnalyzerState.NOT_ANALYZED, reasons=_not_run("body"))
    )
    text = (
        describe_text(text_items, s.text, timeline, first, end, profile.text)  # type: ignore[arg-type]
        if s.text
        else TextObservation(state=AnalyzerState.NOT_ANALYZED, reasons=_not_run("text"))
    )
    content = (
        classify_content(s.embeddings, timeline, first, end, profile.meaning)
        if s.embeddings
        else ContentTypeObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=_not_run("embeddings")
        )
    )
    attention = _attention(s.saliency, first, end)
    meaning = _meaning(s, timeline, first, end, tracks, attention, profile, shot_id)
    indicators = find_indicators(s.embeddings, timeline, black, first, end, profile.meaning)

    geometry_rows = (
        [i for i, f in enumerate(s.geometry.frames) if first <= f < end] if s.geometry else []
    )
    entity_boxes: dict[int, list[BBox]] = {}
    if s.entities:
        for row in s.entities.rows:
            if first <= row.frame < end:
                entity_boxes.setdefault(row.frame, []).append(row.box)
    if s.entities is None:
        framing = FramingObservation(state=AnalyzerState.NOT_ANALYZED, reasons=_not_run("entities"))
        composition = CompositionObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=_not_run("entities")
        )
    else:
        framing = describe_framing(subject, faces if faces.ok else None, profile.cinema)
        composition = describe_composition(
            subject,
            entity_boxes,
            faces.head_yaw if faces.ok else None,
            geometry_rows,
            s.geometry,
            profile.cinema,
        )
    if s.geometry is None:
        lighting = LightingObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=_not_run("geometry")
        )
        geometry = GeometryObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=_not_run("geometry")
        )
    else:
        lighting = describe_lighting(s.geometry, timeline, geometry_rows, profile.cinema)
        geometry = describe_geometry(s.geometry, timeline, geometry_rows, profile.cinema)
    marks = (
        tuple(dict.fromkeys(p.time for p in subject.points[:3]))
        if subject
        else (timeline.time_of(first),)
    )
    crop_safe = (
        crop_safe_regions(subject, attention, marks, picture_aspect, profile.cinema)
        if s.entities is not None or s.saliency is not None
        else tuple(
            CropSafeRegion(state=AnalyzerState.NOT_ANALYZED, aspect=a, reasons=_not_run("entities"))
            for a in profile.cinema.crop_aspects
        )
    )
    return _ShotRows(
        shot_id=shot_id,
        range=rng,
        handles=handles,
        keyframes=keyframes,
        camera=camera,
        motion=motion,
        quality=quality,
        entities=entities,
        faces=faces,
        body=body,
        text=text,
        content=content,
        meaning=meaning,
        indicators=indicators,
        framing=framing,
        composition=composition,
        lighting=lighting,
        geometry=geometry,
        crop_safe=crop_safe,
        subject=subject,
        subject_frames=len(subject.points) if subject else 0,
        analysed_entity_frames=len(analysed),
        subject_height_variation=variation([p.box.height for p in subject.points])
        if subject
        else None,
        color_balance=lighting.color_balance if lighting.ok else None,
        luma=quality.metrics.luma_p50 if quality.ok else None,
        identity=subject.identity_cluster if subject else None,
    )


def _entities(
    entities: DetectionSignals | None,
    analysed: Sequence[int],
    tracks: Sequence[Track],
    subject: Track | None,
    timeline: ShotSignals,
) -> EntitiesObservation:
    if entities is None:
        return EntitiesObservation(state=AnalyzerState.NOT_ANALYZED, reasons=_not_run("entities"))
    if not analysed:
        return EntitiesObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=("no_analysed_frame_in_shot",)
        )
    counts: dict[str, int] = {}
    for track in tracks:
        counts[track.kind.value] = counts.get(track.kind.value, 0) + 1
    return EntitiesObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(analysed) / _FULL_CONFIDENCE_FRAMES),
        evidence=(
            Evidence(frames=tuple(timeline.time_of(f) for f in analysed[:3]), metric="detection"),
        ),
        reasons=() if tracks else ("no_entity_found",),
        track_ids=tuple(t.track_id for t in tracks),
        counts=dict(sorted(counts.items())),
        main_subject_track_id=subject.track_id if subject else None,
        analysed_frames=len(analysed),
    )


def _attention(saliency: SaliencySignals | None, first: int, end: int) -> AttentionPoint | None:
    if saliency is None:
        return None
    rows = [i for i, f in enumerate(saliency.frames) if first <= f < end]
    if not rows:
        return None
    n = len(rows)
    return AttentionPoint(
        x=sum(saliency.cx[i] for i in rows) / n,
        y=sum(saliency.cy[i] for i in rows) / n,
        strength=sum(saliency.peak[i] for i in rows) / n,
    )


def _meaning(
    s: _Signals,
    timeline: ShotSignals,
    first: int,
    end: int,
    tracks: Sequence[Track],
    attention: AttentionPoint | None,
    profile: ProcessingProfile,
    shot_id: str,
) -> VisualMeaningObservation:
    environment = (
        describe_environment(s.embeddings, first, end, profile.meaning) if s.embeddings else None
    )
    objects = tuple(sorted({t.label for t in tracks if t.kind is not EntityKind.PERSON}))
    description = None
    if s.descriptions:
        inside = [i for i, f in enumerate(s.descriptions.frames) if first <= f < end]
        if inside:
            description = s.descriptions.texts[inside[len(inside) // 2]]
    peaks: tuple[object, ...] = ()
    marks: list[int] = []
    saliency = s.saliency
    if saliency is not None:
        rows = [i for i, f in enumerate(saliency.frames) if first <= f < end]
        marks.extend(saliency.frames[i] for i in rows[:3])
        if len(rows) >= _ATTENTION_POINT_FRAMES:
            base = median(saliency.peak[i] for i in rows)
            top = sorted(
                (i for i in rows if saliency.peak[i] - base >= profile.meaning.attention_peak_rise),
                key=lambda i: (-saliency.peak[i], i),
            )[:3]
            peaks = tuple(timeline.time_of(saliency.frames[i]) for i in sorted(top))
    if s.embeddings:
        marks.extend(f for f in s.embeddings.frames if first <= f < end)
    marks.extend(t.first.frame for t in tracks[:2])
    if not (environment or attention or objects or description or marks):
        return VisualMeaningObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=("no_meaning_analyzer_ran",)
        )
    frames = sorted(set(marks))[:3] or [first]
    return VisualMeaningObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(marks) / _FULL_CONFIDENCE_FRAMES) if marks else 0.3,
        evidence=(
            Evidence(frames=tuple(timeline.time_of(f) for f in frames), metric="visual_content"),
        ),
        indoor_outdoor=environment.indoor_outdoor if environment else None,
        environment=environment.place if environment else None,
        environment_scores=environment.scores if environment else {},
        objects=objects,
        main_attention=attention,
        attention_peaks=peaks,  # type: ignore[arg-type]
        description=description,
        embedding_ref=f"emb_{shot_id}"
        if s.embeddings and shot_embedding(s.embeddings, first, end)
        else None,
    )


def _framing_key(row: _ShotRows) -> tuple[float, float, float] | None:
    c, f = row.composition, row.framing
    if (
        f.ok
        and f.subject_height is not None
        and c.ok
        and c.subject_x is not None
        and c.subject_y is not None
    ):
        return (f.subject_height, c.subject_x, c.subject_y)
    return None


def _continuity(
    previous: _ShotRows | None,
    row: _ShotRows,
    previous_vector: tuple[float, ...],
    vector: tuple[float, ...],
    index: int,
    shot_ids: Sequence[str],
    timeline: ShotSignals,
    starts: Sequence[int],
    profile: ProcessingProfile,
) -> ContinuityObservation:
    if previous is None:
        return ContinuityObservation(state=AnalyzerState.NOT_APPLICABLE, reasons=("first_shot",))
    similarity = cosine(previous_vector, vector) if previous_vector and vector else None
    key_a, key_b = _framing_key(previous), _framing_key(row)
    framing = delta = None
    if key_a is not None and key_b is not None:
        delta = ((key_a[1] - key_b[1]) ** 2 + (key_a[2] - key_b[2]) ** 2) ** 0.5
        framing = 1.0 - min(1.0, 0.5 * abs(key_a[0] - key_b[0]) / 0.5 + 0.5 * delta / 0.5)
    luma = (
        abs(previous.luma - row.luma)
        if previous.luma is not None and row.luma is not None
        else None
    )
    balance = (
        abs(previous.color_balance - row.color_balance)
        if previous.color_balance is not None and row.color_balance is not None
        else None
    )
    same: bool | None = None
    if previous.subject is not None and row.subject is not None:
        if previous.identity and row.identity:
            same = previous.identity == row.identity
        elif similarity is not None:
            same = (
                previous.subject.kind is row.subject.kind
                and similarity >= profile.meaning.scene_similarity
            )
    likelihood: float | None = None
    if same is False:
        likelihood = 0.0
    elif same and framing is not None:
        likelihood = framing * (similarity if similarity is not None else 1.0)
    measured = [v for v in (similarity, framing, luma, balance) if v is not None]
    if not measured:
        return ContinuityObservation(
            state=AnalyzerState.UNKNOWN,
            reasons=("no_measurement_for_the_relation",),
            previous_shot_id=shot_ids[index - 1],
        )
    return ContinuityObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(measured) / 3),
        evidence=(
            Evidence(
                frames=(
                    timeline.time_of(max(0, starts[index] - 1)),
                    timeline.time_of(starts[index]),
                ),
                metric="adjacent_shot_comparison",
            ),
        ),
        previous_shot_id=shot_ids[index - 1],
        embedding_similarity=similarity,
        framing_similarity=framing,
        subject_position_delta=delta,
        luma_delta=luma,
        color_balance_delta=balance,
        same_subject=same,
        jump_cut_likelihood=likelihood,
    )


def _face_curves(
    timeline: ShotSignals,
    faces: Mapping[int, object],
    speaking: Mapping[int, float],
    history: ProcessingHistory,
) -> list[Curve]:
    curves: list[Curve] = []
    mouth = {f: row.mouth_open for f, row in faces.items()}  # type: ignore[attr-defined]
    for name, series in (("mouth_activity", mouth), ("visual_speaking", dict(speaking))):
        if not series:
            continue
        frames = tuple(sorted(series))
        curves.append(
            Curve(
                name=name,
                unit="fraction",
                source=AnalyzerId.FACES,
                measured_on=history.measured_on,
                timebase=timeline.timebase,
                frames=frames,
                pts=tuple(timeline.pts[f] for f in frames),
                values=tuple(float(series[f]) for f in frames),
            )
        )
    return curves


__all__ = ["DERIVATION_VERSIONS", "Derived", "Score", "bisect_left", "derive"]
