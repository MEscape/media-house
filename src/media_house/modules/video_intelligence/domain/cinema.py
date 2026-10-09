"""Cinematography measurements of a shot: framing, composition, light, horizon, crop-safe windows.

All of it is measured from the main subject's boxes and the picture geometry. Nothing says what
is good or what to change: framing is classified by subject size, composition is a set of
distances, lighting and tilt are measurements (correction belongs to video improvement) and the
crop-safe windows only say where a window of a given aspect keeps the subject inside.

Convention for ``FaceRow.yaw``: positive means the face is turned toward the RIGHT edge of the
picture, so the free space the person looks into ("lead room") is on the right.
"""

import math
from collections.abc import Sequence
from statistics import median

from media_house.core.domain import FrameTime
from media_house.modules.video_intelligence.domain.geometry import BBox, union
from media_house.modules.video_intelligence.domain.observations import (
    AttentionPoint,
    CompositionObservation,
    CropSafeRegion,
    Evidence,
    FaceObservation,
    FramingObservation,
    GeometryObservation,
    LightingObservation,
    Track,
)
from media_house.modules.video_intelligence.domain.profiles import CinemaSettings
from media_house.modules.video_intelligence.domain.signals import GeometrySignals, ShotSignals
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerState,
    EntityKind,
    FramingType,
)

_KIND_PRIORITY = {
    EntityKind.PERSON: 3,
    EntityKind.ANIMAL: 2,
    EntityKind.VEHICLE: 1,
    EntityKind.OBJECT: 0,
    EntityKind.FACE: 3,
}
_FULL_CONFIDENCE_POINTS = 4
#: Facing is only stated beyond this head turn (degrees).
_FACING_MIN_YAW = 8.0
#: Support of a tilt reading and its confidence scale.
_MAX_SEPARATION = 3.0


def main_subject(tracks: Sequence[Track]) -> Track | None:
    """The track that dominates the shot: person first, then by size and how long it is seen."""
    if not tracks:
        return None
    return max(
        tracks,
        key=lambda t: (
            _KIND_PRIORITY[t.kind],
            sum(p.box.area for p in t.points) / len(t.points) * math.log1p(len(t.points)),
            t.track_id,
        ),
    )


def _marks(track: Track) -> tuple[FrameTime, ...]:
    points = track.points
    return tuple(dict.fromkeys(points[i].time for i in (0, len(points) // 2, len(points) - 1)))


def _mean_box(track: Track) -> BBox:
    n = len(track.points)
    return BBox(
        sum(p.box.x0 for p in track.points) / n,
        sum(p.box.y0 for p in track.points) / n,
        sum(p.box.x1 for p in track.points) / n,
        sum(p.box.y1 for p in track.points) / n,
    )


def _no_subject(cls: type, reason: str):  # type: ignore[no-untyped-def]
    return cls(state=AnalyzerState.UNKNOWN, reasons=(reason,))


def describe_framing(
    subject: Track | None, faces: FaceObservation | None, settings: CinemaSettings
) -> FramingObservation:
    if subject is None:
        return _no_subject(FramingObservation, "no_subject_detected")  # type: ignore[no-any-return]
    box = _mean_box(subject)
    face_height = (
        faces.mean_face_height
        if faces is not None and faces.ok and faces.face_frames and faces.mean_face_height
        else None
    )
    if subject.kind is EntityKind.PERSON and face_height is not None:
        height = face_height
        if height >= settings.extreme_close_up_face:
            framing = FramingType.EXTREME_CLOSE_UP
        elif height >= settings.close_up_face:
            framing = FramingType.CLOSE_UP
        elif height >= settings.medium_face or box.height >= settings.medium_person:
            framing = FramingType.MEDIUM
        else:
            framing = FramingType.WIDE
    else:
        height = box.height
        if height >= settings.close_up_person:
            framing = FramingType.CLOSE_UP
        elif height >= settings.medium_person:
            framing = FramingType.MEDIUM
        else:
            framing = FramingType.WIDE
    margin = settings.edge_margin
    touching = sum(
        1
        for p in subject.points
        if p.box.x0 <= margin
        or p.box.y0 <= margin
        or p.box.x1 >= 1 - margin
        or p.box.y1 >= 1 - margin
    )
    return FramingObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(subject.points) / _FULL_CONFIDENCE_POINTS)
        * (subject.confidence or 0.0),
        evidence=(Evidence(frames=_marks(subject), metric="subject_box"),),
        framing=framing,
        subject_kind=subject.kind.value,
        subject_height=height,
        cut_by_frame_edge=touching / len(subject.points) >= 0.5,
    )


def describe_composition(
    subject: Track | None,
    entity_boxes: dict[int, list[BBox]],
    face_yaw: float | None,
    geometry_rows: Sequence[int],
    geometry: GeometrySignals | None,
    settings: CinemaSettings,
) -> CompositionObservation:
    if subject is None:
        return _no_subject(CompositionObservation, "no_subject_detected")  # type: ignore[no-any-return]
    box = _mean_box(subject)
    x, y = box.center
    third = min(abs(x - 1 / 3), abs(x - 2 / 3))
    lead = None
    if face_yaw is not None and abs(face_yaw) >= _FACING_MIN_YAW:
        lead = (1.0 - box.x1) if face_yaw > 0 else box.x0
    covered = [min(1.0, sum(b.area for b in boxes)) for boxes in entity_boxes.values()]
    clutter = separation = None
    if geometry is not None and geometry_rows:
        outside = float(median(geometry.edge_outside[i] for i in geometry_rows))
        inside = float(median(geometry.edge_inside[i] for i in geometry_rows))
        clutter = outside
        separation = min(1.0, (inside / (outside + 1e-6)) / _MAX_SEPARATION) if inside > 0 else None
    return CompositionObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(subject.points) / _FULL_CONFIDENCE_POINTS)
        * (subject.confidence or 0.0),
        evidence=(Evidence(frames=_marks(subject), metric="subject_box"),),
        subject_x=x,
        subject_y=y,
        headroom=box.y0,
        lead_room=lead,
        thirds_offset=third,
        centered=abs(x - 0.5) <= settings.center_tolerance,
        empty_space=1.0 - sum(covered) / len(covered) if covered else None,
        background_clutter=clutter,
        subject_separation=separation,
    )


def describe_lighting(
    geometry: GeometrySignals,
    timeline: ShotSignals,
    rows: Sequence[int],
    settings: CinemaSettings,
) -> LightingObservation:
    if not rows:
        return LightingObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=("no_analysed_frame_in_shot",)
        )
    with_subject = [
        i for i in rows if geometry.subject_luma[i] > 0 and geometry.surround_luma[i] > 0
    ]
    ratio = (
        float(median(geometry.subject_luma[i] / geometry.surround_luma[i] for i in with_subject))
        if with_subject
        else None
    )
    shadow = float(median(geometry.shadow_contrast[i] for i in rows))
    balance = float(
        median(
            math.log(max(geometry.mean_red[i], 1e-4) / max(geometry.mean_blue[i], 1e-4))
            for i in rows
        )
    )
    return LightingObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(rows) / _FULL_CONFIDENCE_POINTS),
        evidence=(
            Evidence(
                frames=tuple(timeline.time_of(geometry.frames[i]) for i in rows[:3]),
                metric="colour_and_light",
            ),
        ),
        reasons=() if ratio is not None else ("no_subject_to_compare",),
        subject_to_surround=ratio,
        shadow_contrast=shadow,
        color_balance=balance,
        backlit=None if ratio is None else ratio < settings.backlight_ratio,
        harsh_shadows=shadow >= settings.harsh_shadow,
    )


def describe_geometry(
    geometry: GeometrySignals,
    timeline: ShotSignals,
    rows: Sequence[int],
    settings: CinemaSettings,
) -> GeometryObservation:
    if not rows:
        return GeometryObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=("no_analysed_frame_in_shot",)
        )
    horizon = [i for i in rows if geometry.horizon_support[i] >= settings.tilt_min_support]
    vertical = [i for i in rows if geometry.vertical_support[i] >= settings.tilt_min_support]
    reasons = []
    if not horizon:
        reasons.append("no_horizon_line_found")
    if not vertical:
        reasons.append("no_vertical_lines_found")
    support = [*horizon, *vertical]
    if not support:
        return GeometryObservation(state=AnalyzerState.UNKNOWN, reasons=tuple(reasons))
    return GeometryObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, len(support) / (2 * _FULL_CONFIDENCE_POINTS)),
        evidence=(
            Evidence(
                frames=tuple(timeline.time_of(geometry.frames[i]) for i in support[:3]),
                metric="line_orientation",
            ),
        ),
        reasons=tuple(reasons),
        horizon_tilt=float(median(geometry.horizon_tilt[i] for i in horizon)) if horizon else None,
        vertical_tilt=float(median(geometry.vertical_tilt[i] for i in vertical))
        if vertical
        else None,
    )


# --- crop-safe windows -------------------------------------------------------------------------
def _aspect(text: str) -> float:
    width, _, height = text.partition(":")
    return int(width) / int(height)


def crop_safe_regions(
    subject: Track | None,
    attention: AttentionPoint | None,
    marks: Sequence[FrameTime],
    picture_aspect: float,
    settings: CinemaSettings,
) -> tuple[CropSafeRegion, ...]:
    """For each aspect, the window of that shape that follows the subject (or the eye).

    ``picture_aspect`` is width / height of the picture. The window is as large as the picture
    allows for its shape, placed to contain the subject's whole travel in the shot if it fits.
    """
    target: BBox | None = union([p.box for p in subject.points]) if subject else None
    note: tuple[str, ...] = ()
    if target is None and attention is not None:
        half = 0.1
        target = BBox.clamped(
            attention.x - half, attention.y - half, attention.x + half, attention.y + half
        )
        note = ("window_follows_attention_not_a_subject",)
    if target is None:
        return tuple(
            CropSafeRegion(
                state=AnalyzerState.UNKNOWN, aspect=a, reasons=("no_subject_or_attention",)
            )
            for a in settings.crop_aspects
        )
    evidence = (Evidence(frames=tuple(marks), metric="subject_box"),)
    regions: list[CropSafeRegion] = []
    for aspect in settings.crop_aspects:
        wanted = _aspect(aspect)
        if wanted <= picture_aspect:  # narrower than the picture: full height
            height, width = 1.0, wanted / picture_aspect
        else:  # wider: full width
            width, height = 1.0, picture_aspect / wanted
        cx, cy = target.center
        x0 = min(max(cx - width / 2, 0.0), 1.0 - width)
        y0 = min(max(cy - height / 2, 0.0), 1.0 - height)
        window = BBox(x0, y0, x0 + width, y0 + height)
        margin = min(
            (target.x0 - window.x0) / window.width,
            (window.x1 - target.x1) / window.width,
            (target.y0 - window.y0) / window.height,
            (window.y1 - target.y1) / window.height,
        )
        inside = margin >= 0
        reasons = list(note)
        if not inside:
            reasons.append("subject_travel_wider_than_window")
        elif margin < settings.crop_margin:
            reasons.append("subject_close_to_window_edge")
        regions.append(
            CropSafeRegion(
                state=AnalyzerState.OK,
                confidence=min(1.0, (subject.confidence if subject else 0.5) or 0.5),
                evidence=evidence,
                reasons=tuple(reasons),
                aspect=aspect,
                window=window,
                margin=margin,
                subject_inside=inside,
            )
        )
    return tuple(regions)
