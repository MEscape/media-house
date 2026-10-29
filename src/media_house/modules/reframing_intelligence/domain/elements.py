"""Which elements of a shot a crop must keep, and where they were at every moment.

Only observed evidence decides. The main subject comes from the analysis (its main subject
track, else the largest person); other people are participants when they are tall enough and
present long enough; an object is required only when the story's visual need names it. Anything
else is context.
Nothing is invented: no hands are tracked upstream, so a demonstration that needs hands says that
it cannot protect them (``not_tracked``), it does not guess a box.
"""

import re
from bisect import bisect_left
from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.reframing_intelligence.domain.facts import (
    ElementTrack,
    ShotFacts,
    VisualNeed,
)
from media_house.modules.reframing_intelligence.domain.geometry import Box
from media_house.modules.reframing_intelligence.domain.profile import ReframingProfile
from media_house.modules.reframing_intelligence.domain.values import Importance

_DEMONSTRATING = frozenset({"demonstration", "evidence", "comparison"})
_STOP = frozenset({"the", "and", "that", "this", "with", "for", "from", "your", "his", "her"})
_WORD = re.compile(r"[a-z]{3,}")


@dataclass(frozen=True, slots=True)
class Series:
    """The trusted observations of one track, searchable by time."""

    track: ElementTrack
    times: tuple[float, ...]
    #: The same moments as exact fractions (the keyframe times are taken from these).
    stamps: tuple[Fraction, ...]
    boxes: tuple[Box, ...]
    confidences: tuple[float, ...]
    low_confidence: tuple[float, ...]

    @classmethod
    def of(cls, track: ElementTrack, shot: ShotFacts, min_confidence: float) -> "Series":
        lo, hi = float(shot.start), float(shot.end)
        trusted = [
            s for s in track.samples if lo <= float(s.t) <= hi and s.confidence >= min_confidence
        ]
        weak = tuple(
            float(s.t)
            for s in track.samples
            if lo <= float(s.t) <= hi and s.confidence < min_confidence
        )
        return cls(
            track,
            tuple(float(s.t) for s in trusted),
            tuple(s.t for s in trusted),
            tuple(s.box for s in trusted),
            tuple(s.confidence for s in trusted),
            weak,
        )

    def at(self, t: float, max_gap: float) -> Box | None:
        """The box at ``t``: interpolated between two observations no further apart than
        ``max_gap``, else the nearest one when it is close enough, else ``None`` (a gap)."""
        if not self.times:
            return None
        i = bisect_left(self.times, t)
        before = i - 1 if i > 0 else None
        after = i if i < len(self.times) else None
        if after is not None and abs(self.times[after] - t) < 1e-9:
            return self.boxes[after]
        if before is not None and after is not None:
            span = self.times[after] - self.times[before]
            if span <= max_gap:
                f = (t - self.times[before]) / span
                a, b = self.boxes[before], self.boxes[after]
                return Box(
                    a.x0 + (b.x0 - a.x0) * f,
                    a.y0 + (b.y0 - a.y0) * f,
                    a.x1 + (b.x1 - a.x1) * f,
                    a.y1 + (b.y1 - a.y1) * f,
                )
        nearest = min(
            (k for k in (before, after) if k is not None),
            key=lambda k: abs(self.times[k] - t),
        )
        return self.boxes[nearest] if abs(self.times[nearest] - t) <= max_gap / 2 else None

    def coverage(self, start: float, end: float, max_gap: float) -> float:
        """The share of ``[start, end]`` that lies within half a gap of an observation."""
        if end <= start or not self.times:
            return 0.0
        covered, edge = 0.0, start
        for t in self.times:
            a, b = max(t - max_gap / 2, edge, start), min(t + max_gap / 2, end)
            if b > a:
                covered += b - a
                edge = b
        return min(1.0, covered / (end - start))

    @property
    def mean_confidence(self) -> float | None:
        return sum(self.confidences) / len(self.confidences) if self.confidences else None

    @property
    def tallest(self) -> float:
        return max((b.height for b in self.boxes), default=0.0)


@dataclass(frozen=True, slots=True)
class Chosen:
    series: Series
    importance: Importance
    required: bool
    reason: str
    coverage: float


@dataclass(frozen=True, slots=True)
class Selection:
    chosen: tuple[Chosen, ...]
    #: The main subject (``None`` when nothing usable was tracked).
    primary: Chosen | None
    #: A face track inside the primary person: the better anchor for headroom, never a requirement.
    face: Series | None
    #: Statements about what could not be protected (``not_tracked``): (subject, detail).
    unprotected: tuple[tuple[str, str], ...]


def _tokens(text: str) -> frozenset[str]:
    return frozenset(
        w[:-1] if w.endswith("s") and len(w) > 3 else w
        for w in _WORD.findall(text.lower())
        if w not in _STOP
    )


def _need_names(track: ElementTrack, needs: tuple[VisualNeed, ...]) -> VisualNeed | None:
    label = _tokens(track.label)
    if not label:
        return None
    return next(
        (n for n in needs if n.purpose in _DEMONSTRATING and label & _tokens(n.concept)), None
    )


def select(
    shot: ShotFacts,
    needs: tuple[VisualNeed, ...],
    profile: ReframingProfile,
    window: tuple[float, float] | None = None,
) -> Selection:
    """``window`` is the size of the widest crop of the target's shape: a person who does not fit
    it is replaced as the main subject by their face when one was tracked inside them (a
    head-and-shoulders crop is the usual answer to a body taller than the window)."""
    motion = profile.motion
    start, end = float(shot.start), float(shot.end)
    series = [Series.of(t, shot, motion.min_confidence) for t in shot.tracks if t.kind != "face"]
    faces = [Series.of(t, shot, motion.min_confidence) for t in shot.tracks if t.kind == "face"]
    series = [s for s in series if s.times]
    faces = [s for s in faces if s.times]

    main = next((s for s in series if s.track.track_id == shot.main_track_id), None)
    if main is None:
        people = [s for s in series if s.track.kind == "person"]
        pool = people or series
        main = max(pool, key=lambda s: (s.tallest, s.track.track_id), default=None)
    chosen: list[Chosen] = []
    primary: Chosen | None = None
    demoted: Series | None = None
    reason = "the shot's main subject"
    if window is not None and main is not None and main.track.kind == "person":
        face = _face_inside(main, faces)
        room = 1.0 - 2 * profile.composition.margin
        if face is not None and any(
            b.width > window[0] * room or b.height > window[1] * room for b in main.boxes
        ):
            demoted, main = main, face
            faces = [f for f in faces if f is not face]
            reason = "the face of the main subject: the whole person does not fit the window"
    if main is None and faces:  # a face is the only thing tracked: it is the subject
        main = max(faces, key=lambda s: (s.tallest, s.track.track_id))
        faces = [f for f in faces if f is not main]
    if main is not None:
        primary = Chosen(
            main, Importance.PRIMARY, True, reason, main.coverage(start, end, motion.max_gap)
        )
        chosen.append(primary)

    unprotected: list[tuple[str, str]] = []
    for s in sorted(series, key=lambda s: s.track.track_id):
        if primary is not None and s is primary.series:
            continue
        cover = s.coverage(start, end, motion.max_gap)
        if s is demoted:
            chosen.append(
                Chosen(
                    s,
                    Importance.CONTEXTUAL,
                    False,
                    "the whole person does not fit the window; their face is kept instead",
                    cover,
                )
            )
            continue
        named = _need_names(s.track, needs)
        participant = (
            primary is not None
            and s.track.kind == primary.series.track.kind == "person"
            and s.tallest >= profile.participants.height_ratio * primary.series.tallest
            and cover >= profile.participants.coverage
        )
        if named is not None:
            chosen.append(
                Chosen(
                    s,
                    Importance.SECONDARY,
                    True,
                    f"named by the story's visual need {named.visual_id} ({named.concept!r})",
                    cover,
                )
            )
        elif participant:
            chosen.append(
                Chosen(
                    s,
                    Importance.SECONDARY,
                    profile.participants.required,
                    "a participant: as tall as the main subject and present for most of the shot",
                    cover,
                )
            )
        else:
            chosen.append(
                Chosen(
                    s, Importance.CONTEXTUAL, False, "tracked, but nothing says it matters", cover
                )
            )
    for need in needs:
        if need.purpose == "demonstration" and not any(
            c.required and c.series.track.kind == "object" for c in chosen
        ):
            unprotected.append(
                (
                    need.visual_id,
                    "the story calls this passage a demonstration, but no tracked object matches "
                    f"{need.concept!r} and the picture analysis tracks no hands: what is being "
                    "shown cannot be protected",
                )
            )
    face = None
    if primary is not None and primary.series.track.kind == "person":
        face = _face_inside(primary.series, faces)
    return Selection(tuple(chosen), primary, face, tuple(unprotected))


def _face_inside(person: Series, faces: list[Series]) -> Series | None:
    best: tuple[int, Series] | None = None
    for f in sorted(faces, key=lambda s: s.track.track_id):
        hits = 0
        for t, box in zip(f.times, f.boxes, strict=True):
            p = person.at(t, 1e9)
            cx, cy = box.center
            if p is not None and p.x0 <= cx <= p.x1 and p.y0 <= cy <= p.y1:
                hits += 1
        if hits and (best is None or hits > best[0]):
            best = (hits, f)
    return best[1] if best else None


def grid(shot: ShotFacts, required: tuple[Chosen, ...]) -> list[Fraction]:
    """The moments the path is planned at: the shot's start and end and every observation of a
    required element (so nothing observed is skipped and nothing is invented in between)."""
    times = {shot.start, shot.end}
    for c in required:
        times.update(c.series.stamps)
    return sorted(times)
