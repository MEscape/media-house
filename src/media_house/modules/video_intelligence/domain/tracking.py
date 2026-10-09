"""Tracking: detections in successive analysed frames become tracks (ByteTrack-style, pure).

Two passes per frame, like ByteTrack: confident detections are matched to the open tracks first,
the weaker ones may still continue a track that is left over, and only a confident detection can
START a track. Association is by overlap (IoU) with a centre-distance gate for fast movers between
sparse frames. A track never crosses a shot boundary and survives a bounded number of frames
without a detection (occlusion or a missed frame). Deterministic: ties break by input order.
"""

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

from media_house.modules.video_intelligence.domain.geometry import BBox, iou
from media_house.modules.video_intelligence.domain.profiles import TrackingSettings
from media_house.modules.video_intelligence.domain.signals import DetectionRow, DetectionSignals
from media_house.modules.video_intelligence.domain.values import EntityKind

_DIAGONAL = math.sqrt(2.0)


@dataclass(slots=True)
class RawTrack:
    """A track under construction; ``points`` are (frame, box, confidence) in time order."""

    kind: EntityKind
    label: str
    shot_index: int
    points: list[tuple[int, BBox, float]] = field(default_factory=list)

    @property
    def first(self) -> int:
        return self.points[0][0]

    @property
    def last(self) -> int:
        return self.points[-1][0]

    @property
    def box(self) -> BBox:
        return self.points[-1][1]


def _affinity(track: RawTrack, row: DetectionRow, settings: TrackingSettings) -> float:
    """How well a detection continues a track: its IoU, or a weak score for a near centre."""
    if track.kind is not row.kind or track.label != row.label:
        return 0.0
    overlap = iou(track.box, row.box)
    if overlap >= settings.match_iou:
        return overlap
    (tx, ty), (rx, ry) = track.box.center, row.box.center
    distance = math.hypot(tx - rx, ty - ry) / _DIAGONAL
    if distance <= settings.center_gate:
        return 0.01 + 0.2 * (1.0 - distance / settings.center_gate) * max(overlap, 0.05)
    return 0.0


def _assign(
    tracks: list[RawTrack], rows: list[DetectionRow], settings: TrackingSettings
) -> tuple[dict[int, int], list[int]]:
    """Greedy best-first matching. Returns {row index: track index} and the unmatched rows."""
    pairs = sorted(
        (
            (-_affinity(t, r, settings), ti, ri)
            for ti, t in enumerate(tracks)
            for ri, r in enumerate(rows)
        ),
    )
    matched_rows: dict[int, int] = {}
    used_tracks: set[int] = set()
    for negative, ti, ri in pairs:
        if negative >= 0:
            break
        if ri in matched_rows or ti in used_tracks:
            continue
        matched_rows[ri] = ti
        used_tracks.add(ti)
    return matched_rows, [i for i in range(len(rows)) if i not in matched_rows]


def build_tracks(
    detections: DetectionSignals,
    starts: Sequence[int],
    frames_per_second: float,
    settings: TrackingSettings,
) -> list[RawTrack]:
    """Tracks of every entity kind. ``starts`` are the first frames of the shots, ascending."""
    by_frame: dict[int, list[DetectionRow]] = {}
    for row in detections.rows:
        by_frame.setdefault(row.frame, []).append(row)
    max_gap = max(1.0, settings.max_gap_seconds * (frames_per_second or 30.0))

    finished: list[RawTrack] = []
    open_tracks: list[RawTrack] = []
    shot = -1
    for frame in detections.frames:
        current = bisect_right(starts, frame) - 1
        if current != shot:  # a cut: nothing is followed across it
            finished.extend(open_tracks)
            open_tracks, shot = [], current
        still_open: list[RawTrack] = []
        for track in open_tracks:
            (still_open if frame - track.last <= max_gap else finished).append(track)
        open_tracks = still_open

        rows = sorted(
            by_frame.get(frame, []),
            key=lambda r: (
                -r.confidence,
                r.kind.value,
                r.label,
                r.box.x0,
                r.box.y0,
                r.box.x1,
                r.box.y1,
            ),
        )
        strong = [r for r in rows if r.confidence >= settings.start_confidence]
        weak = [r for r in rows if r.confidence < settings.start_confidence]
        matched, unmatched = _assign(open_tracks, strong, settings)
        for ri, ti in matched.items():
            open_tracks[ti].points.append((frame, strong[ri].box, strong[ri].confidence))
        free = [t for i, t in enumerate(open_tracks) if i not in set(matched.values())]
        weak_matched, _ = _assign(free, weak, settings)
        for ri, ti in weak_matched.items():
            free[ti].points.append((frame, weak[ri].box, weak[ri].confidence))
        for ri in unmatched:
            row = strong[ri]
            open_tracks.append(
                RawTrack(row.kind, row.label, shot, [(frame, row.box, row.confidence)])
            )
    finished.extend(open_tracks)
    kept = [t for t in finished if len(t.points) >= settings.min_detections]
    return sorted(
        kept,
        key=lambda t: (
            t.first,
            t.kind.value,
            t.label,
            t.last,
            t.points[0][1].x0,
            t.points[0][1].y0,
        ),
    )
