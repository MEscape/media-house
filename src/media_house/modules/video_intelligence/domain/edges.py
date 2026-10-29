"""What the picture shows AT one moment: the evidence a cut needs on each side.

A cut joins two moments, but an analysis describes shots. ``edge_state`` answers "what was in the
frame at this second": the main subject's place, size and direction of travel (from its track), the
face cues and framing and light of the shot, and the shot's embedding. It is a pure query over the
stored result (nothing is measured):

* the subject is the shot's main subject track, else the largest person track;
* position is the track point nearest the moment, and only if it lies within ``MAX_GAP`` seconds
  of the track (a subject that left or had not yet arrived has no state, not a guess);
* the direction of travel is the slope of the points within ``VELOCITY_WINDOW`` seconds, and needs
  at least two points; a value that was not measured is ``None``, never 0;
* face cues, framing, light and the embedding belong to the SHOT (they are medians over its frames):
  they are the best evidence of the moment that the analysis stores, and say so by being shot-level.

Times are the analysis' own video clock in seconds.
"""

from dataclasses import dataclass

from media_house.modules.video_intelligence.domain.observations import Track, TrackPoint
from media_house.modules.video_intelligence.domain.result import Shot, VideoAnalysis
from media_house.modules.video_intelligence.domain.values import EntityKind

#: A track reaching no closer than this to the moment gives no state.
MAX_GAP = 0.5
#: Points this close to the moment give the direction of travel.
VELOCITY_WINDOW = 0.4


@dataclass(frozen=True, slots=True, kw_only=True)
class SubjectState:
    track_id: str
    kind: str
    label: str
    #: Anonymous appearance cluster (``person_A``); ``None`` when clustering did not run.
    identity_cluster: str | None
    #: Box centre and height in picture units (0-1, display orientation).
    cx: float
    cy: float
    height: float
    #: Centre travel in picture units per second; ``None`` with fewer than two points.
    vx: float | None
    vy: float | None
    samples: int


@dataclass(frozen=True, slots=True, kw_only=True)
class EdgeState:
    shot_id: str
    scene_id: str | None
    seconds: float
    subject: SubjectState | None
    #: Shot-level face cues (degrees / shares); ``None`` = not measured.
    head_yaw: float | None
    eye_contact: float | None
    visual_speaking: float | None
    framing: str | None
    camera_movement: str | None
    #: Shot-level light: median luma (0-1), log red/blue balance, subject over surround luma.
    luma: float | None
    color_balance: float | None
    subject_to_surround: float | None
    backlit: bool | None
    environment: str | None
    indoor_outdoor: str | None
    objects: tuple[str, ...]
    texts: tuple[str, ...]
    #: The shot's embedding (empty when none was computed) and the model that made it.
    embedding: tuple[float, ...]
    embedding_model: str | None


def edge_state(analysis: VideoAnalysis, seconds: float) -> EdgeState | None:
    """The state of the frame at ``seconds`` (``None`` when no shot of the analysis holds it)."""
    shot = next(
        (s for s in analysis.shots if s.range.start.seconds <= seconds < s.range.end.seconds), None
    )
    if shot is None:
        return None
    meaning = shot.meaning
    embedding = (
        analysis.embedding(meaning.embedding_ref) if meaning.ok and meaning.embedding_ref else None
    )
    faces, lighting, quality = shot.faces, shot.lighting, shot.quality
    return EdgeState(
        shot_id=shot.shot_id,
        scene_id=shot.scene_id,
        seconds=seconds,
        subject=_subject(analysis, shot, seconds),
        head_yaw=faces.head_yaw if faces.ok else None,
        eye_contact=faces.eye_contact if faces.ok else None,
        visual_speaking=faces.visual_speaking if faces.ok else None,
        framing=shot.framing.framing.value if shot.framing.ok and shot.framing.framing else None,
        camera_movement=shot.camera.movement.value
        if shot.camera.ok and shot.camera.movement
        else None,
        luma=quality.metrics.luma_p50 if quality.ok else None,
        color_balance=lighting.color_balance if lighting.ok else None,
        subject_to_surround=lighting.subject_to_surround if lighting.ok else None,
        backlit=lighting.backlit if lighting.ok else None,
        environment=meaning.environment if meaning.ok else None,
        indoor_outdoor=meaning.indoor_outdoor if meaning.ok else None,
        objects=tuple(meaning.objects) if meaning.ok else (),
        texts=tuple(i.text for i in shot.text.items) if shot.text.ok else (),
        embedding=embedding.vector if embedding is not None else (),
        embedding_model=embedding.model if embedding is not None else None,
    )


def _subject(analysis: VideoAnalysis, shot: Shot, seconds: float) -> SubjectState | None:
    track = _main_track(analysis, shot)
    if track is None or not track.points:
        return None
    start, end = track.first.seconds, track.last.seconds
    if seconds < start - MAX_GAP or seconds > end + MAX_GAP:
        return None
    nearest = min(track.points, key=lambda p: abs(p.time.seconds - seconds))
    if abs(nearest.time.seconds - seconds) > MAX_GAP:
        return None
    near = [p for p in track.points if abs(p.time.seconds - seconds) <= VELOCITY_WINDOW]
    vx, vy = _velocity(near)
    box = nearest.box
    return SubjectState(
        track_id=track.track_id,
        kind=track.kind.value,
        label=track.label,
        identity_cluster=track.identity_cluster,
        cx=round((box.x0 + box.x1) / 2, 4),
        cy=round((box.y0 + box.y1) / 2, 4),
        height=round(box.y1 - box.y0, 4),
        vx=vx,
        vy=vy,
        samples=len(near),
    )


def _main_track(analysis: VideoAnalysis, shot: Shot) -> Track | None:
    if shot.entities.ok and shot.entities.main_subject_track_id:
        found = analysis.track(shot.entities.main_subject_track_id)
        if found is not None:
            return found
    people = [
        t for t in analysis.tracks if t.shot_id == shot.shot_id and t.kind is EntityKind.PERSON
    ]
    return max(
        people,
        key=lambda t: max((p.box.y1 - p.box.y0 for p in t.points), default=0.0),
        default=None,
    )


def _velocity(points: list[TrackPoint]) -> tuple[float | None, float | None]:
    ordered = sorted(points, key=lambda p: p.time.seconds)
    if len(ordered) < 2:
        return None, None
    first, last = ordered[0], ordered[-1]
    span = last.time.seconds - first.time.seconds
    if span <= 0:
        return None, None

    def centre(p: TrackPoint) -> tuple[float, float]:
        return (p.box.x0 + p.box.x1) / 2, (p.box.y0 + p.box.y1) / 2

    (ax, ay), (bx, by) = centre(first), centre(last)
    return round((bx - ax) / span, 4), round((by - ay) / span, 4)
