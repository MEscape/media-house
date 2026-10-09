"""On-screen text: tracked items per shot, overlay kinds and what kind of screen it looks like.

Overlays are classified by where they sit and how long they stay, from measurements only:

* watermark          the same text in the same place across much of the whole video;
* lower third        short-lived text in the lower part of the picture;
* burned-in caption  different text appearing one after another in the same bottom band;
* split screen       a straight seam across the picture (from the geometry signals).
"""

import math
import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

from media_house.modules.video_intelligence.domain.geometry import BBox, iou
from media_house.modules.video_intelligence.domain.observations import (
    Evidence,
    Overlay,
    TextItem,
    TextObservation,
)
from media_house.modules.video_intelligence.domain.profiles import TextSettings
from media_house.modules.video_intelligence.domain.signals import (
    GeometrySignals,
    ShotSignals,
    TextRow,
    TextSignals,
)
from media_house.modules.video_intelligence.domain.values import AnalyzerState, OverlayKind

_CODE_SYMBOLS = frozenset("{}[]();=<>_#/\\|&*")
_MIN_CODE_ITEMS = 5
_MIN_DOCUMENT_ITEMS = 8
_MIN_INTERFACE_ITEMS = 6
_INTERFACE_MAX_LENGTH = 14
_SEAM_MIN = 0.5
_FULL_CONFIDENCE_FRAMES = 3


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


@dataclass(slots=True)
class TrackedText:
    """One piece of text followed over consecutive analysed frames in one shot."""

    text: str
    key: str
    shot_index: int
    boxes: list[BBox] = field(default_factory=list)
    frames: list[int] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)

    @property
    def first(self) -> int:
        return self.frames[0]

    @property
    def last(self) -> int:
        return self.frames[-1]

    @property
    def box(self) -> BBox:
        n = len(self.boxes)
        return BBox(
            sum(b.x0 for b in self.boxes) / n,
            sum(b.y0 for b in self.boxes) / n,
            sum(b.x1 for b in self.boxes) / n,
            sum(b.y1 for b in self.boxes) / n,
        )

    @property
    def confidence(self) -> float:
        return sum(self.confidences) / len(self.confidences)


def track_text(
    signals: TextSignals, starts: Sequence[int], settings: TextSettings
) -> list[TrackedText]:
    """Follow equal text in nearly the same place over consecutive analysed frames."""
    by_frame: dict[int, list[TextRow]] = {}
    for row in signals.rows:
        if row.confidence >= settings.min_confidence and normalise(row.text):
            by_frame.setdefault(row.frame, []).append(row)
    done: list[TrackedText] = []
    open_items: dict[tuple[str, int], TrackedText] = {}
    serial = 0
    for frame in signals.frames:
        shot = bisect_right(starts, frame) - 1
        seen: set[tuple[str, int]] = set()
        for row in by_frame.get(frame, []):
            key = normalise(row.text)
            slot = next(
                (
                    k
                    for k, item in open_items.items()
                    if k[0] == key
                    and item.shot_index == shot
                    and k not in seen
                    and iou(item.boxes[-1], row.box) >= settings.same_text_iou
                ),
                None,
            )
            if slot is None:
                serial += 1
                slot = (key, serial)
                open_items[slot] = TrackedText(row.text.strip(), key, shot)
            item = open_items[slot]
            item.boxes.append(row.box)
            item.frames.append(frame)
            item.confidences.append(row.confidence)
            seen.add(slot)
        for slot in [s for s in open_items if s not in seen]:
            done.append(open_items.pop(slot))  # not seen this frame: the item has ended
    done.extend(open_items.values())
    return sorted(done, key=lambda t: (t.first, t.key, t.last))


def _screen_kind(items: Sequence[TrackedText], settings: TextSettings) -> str | None:
    texts = [i.text for i in items]
    joined = "".join(texts)
    if not joined:
        return None
    symbols = sum(1 for c in joined if c in _CODE_SYMBOLS) / len(joined)
    if len(texts) >= _MIN_CODE_ITEMS and symbols >= settings.code_symbol_ratio:
        return "code"
    words = sum(len(t.split()) for t in texts) / len(texts)
    if len(texts) >= _MIN_DOCUMENT_ITEMS and words >= 3:
        return "document"
    if len(texts) >= _MIN_INTERFACE_ITEMS and (
        sum(len(t) for t in texts) / len(texts) <= _INTERFACE_MAX_LENGTH
    ):
        return "interface"
    return None


def describe_text(
    items: Sequence[TrackedText],
    signals: TextSignals,
    timeline: ShotSignals,
    first: int,
    end: int,
    settings: TextSettings,
) -> TextObservation:
    analysed = [f for f in signals.frames if first <= f < end]
    if not analysed:
        return TextObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=("no_analysed_frame_in_shot",)
        )
    marks = tuple(timeline.time_of(f) for f in analysed[:3])
    confidence = min(1.0, len(analysed) / _FULL_CONFIDENCE_FRAMES)
    mine = [i for i in items if first <= i.first < end]
    if not mine:
        return TextObservation(
            state=AnalyzerState.OK,
            confidence=confidence,
            evidence=(Evidence(frames=marks, metric="text_recognition"),),
            reasons=("no_text_found",),
        )
    return TextObservation(
        state=AnalyzerState.OK,
        confidence=confidence * sum(i.confidence for i in mine) / len(mine),
        evidence=(
            Evidence(
                frames=tuple(dict.fromkeys(timeline.time_of(i.first) for i in mine[:3])),
                metric="text_recognition",
            ),
        ),
        items=tuple(
            TextItem(
                text=i.text,
                first=timeline.time_of(i.first),
                last=timeline.time_of(i.last),
                box=i.box,
                confidence=i.confidence,
            )
            for i in mine
        ),
        screen_kind=_screen_kind(mine, settings),
    )


def _overlay(
    kind: OverlayKind,
    timeline: ShotSignals,
    first: int,
    last: int,
    confidence: float,
    box: BBox | None,
    text: str,
) -> Overlay:
    return Overlay(
        state=AnalyzerState.OK,
        confidence=min(1.0, confidence),
        evidence=(
            Evidence(
                frames=tuple(dict.fromkeys((timeline.time_of(first), timeline.time_of(last)))),
                metric="text_position_and_duration",
            ),
        ),
        kind=kind,
        first=timeline.time_of(first),
        last=timeline.time_of(last),
        box=box,
        text=text,
    )


def find_overlays(
    items: Sequence[TrackedText],
    signals: TextSignals,
    timeline: ShotSignals,
    settings: TextSettings,
) -> list[Overlay]:
    """Watermarks, lower thirds and burned-in captions among the tracked text of the video."""
    overlays: list[Overlay] = []
    remaining = list(items)
    total = len(signals.frames)

    groups: dict[tuple[str, int, int], list[TrackedText]] = {}
    for item in items:
        cx, cy = item.box.center
        groups.setdefault((item.key, round(cx * 20), round(cy * 20)), []).append(item)
    for members in groups.values():
        covered = sum(len(m.frames) for m in members)
        if (
            total
            and covered >= settings.watermark_min_frames
            and covered / total >= settings.watermark_share
        ):
            first = min(m.first for m in members)
            last = max(m.last for m in members)
            overlays.append(
                _overlay(
                    OverlayKind.WATERMARK,
                    timeline,
                    first,
                    last,
                    covered / total,
                    members[0].box,
                    members[0].text,
                )
            )
            remaining = [i for i in remaining if i not in members]

    by_shot: dict[int, list[TrackedText]] = {}
    for item in remaining:
        if item.box.y0 >= settings.caption_top:
            by_shot.setdefault(item.shot_index, []).append(item)
    captioned: set[int] = set()
    for members in by_shot.values():
        distinct = {m.key for m in members}
        if len(distinct) > settings.caption_min_changes:
            first, last = min(m.first for m in members), max(m.last for m in members)
            overlays.append(
                _overlay(
                    OverlayKind.BURNED_IN_CAPTION,
                    timeline,
                    first,
                    last,
                    len(distinct) / (len(distinct) + 2),
                    None,
                    members[0].text,
                )
            )
            captioned.update(id(m) for m in members)

    for item in remaining:
        seconds = (timeline.pts[item.last] - timeline.pts[item.first]) * timeline.timebase.value
        if (
            id(item) not in captioned
            and item.box.y0 >= settings.lower_third_top
            and item.box.height <= settings.lower_third_max_height
            and seconds <= settings.lower_third_max_seconds
        ):
            overlays.append(
                _overlay(
                    OverlayKind.LOWER_THIRD,
                    timeline,
                    item.first,
                    item.last,
                    item.confidence * min(1.0, len(item.frames) / 2),
                    item.box,
                    item.text,
                )
            )
    return overlays


def find_split_screens(
    geometry: GeometrySignals,
    timeline: ShotSignals,
    starts: Sequence[int],
    settings: TextSettings,
) -> list[Overlay]:
    """A straight seam across the picture in most analysed frames of a shot: a split screen."""
    ends = [*starts[1:], timeline.frame_count]
    overlays: list[Overlay] = []
    for first, end in zip(starts, ends, strict=True):
        rows = [i for i, f in enumerate(geometry.frames) if first <= f < end]
        seams = [
            i
            for i in rows
            if max(geometry.seam_vertical[i], geometry.seam_horizontal[i]) >= _SEAM_MIN
        ]
        if len(rows) >= 2 and len(seams) / len(rows) >= settings.seam_share:
            strength = sum(
                max(geometry.seam_vertical[i], geometry.seam_horizontal[i]) for i in seams
            ) / len(seams)
            overlays.append(
                _overlay(
                    OverlayKind.SPLIT_SCREEN,
                    timeline,
                    geometry.frames[seams[0]],
                    geometry.frames[seams[-1]],
                    strength * len(seams) / len(rows),
                    None,
                    "",
                )
            )
    return overlays


def text_changed(before: Sequence[TrackedText], after: Sequence[TrackedText]) -> bool:
    """Whether the set of texts on screen differs between two moments."""
    left, right = {t.key for t in before}, {t.key for t in after}
    return (
        bool(left or right)
        and left != right
        and not math.isclose(len(left & right), len(left | right))
    )
