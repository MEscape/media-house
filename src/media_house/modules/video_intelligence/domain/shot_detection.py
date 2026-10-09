"""Shot boundaries from the per-frame signals: hard cuts, dissolves and fades through black.

Pure functions of ``ShotSignals`` and ``ShotSettings``: deterministic, no media access, so every
threshold is testable on hand-made signals and re-tunable without decoding.

A boundary is addressed by the FIRST FRAME OF THE NEW SHOT. For a gradual transition that is the
centre of the transition, which spans ``first..last`` (inclusive).
"""

from dataclasses import dataclass
from statistics import mean, median

from media_house.modules.video_intelligence.domain.profiles import ShotSettings
from media_house.modules.video_intelligence.domain.signals import ShotSignals
from media_house.modules.video_intelligence.domain.values import BoundaryKind

#: Differences below this are treated as no-signal when judging whether a change stands out.
_CONTEXT_FLOOR = 0.005
#: Frames whose excess change reaches this share of the peak belong to a dissolve.
_ACTIVE_SHARE = 0.4


@dataclass(frozen=True, slots=True)
class Transition:
    kind: BoundaryKind
    #: First frame of the new shot.
    frame: int
    #: First and last frame (inclusive) of the transition; equal to ``frame`` for a hard cut.
    first: int
    last: int
    confidence: float
    black_adjacent: bool = False

    @property
    def gradual(self) -> bool:
        return self.last > self.first


@dataclass(frozen=True, slots=True)
class Detection:
    transitions: tuple[Transition, ...]
    #: Single-frame brightness outliers that were NOT counted as cuts.
    flashes: tuple[int, ...]


def black_frames(signals: ShotSignals, settings: ShotSettings) -> tuple[bool, ...]:
    return tuple(
        m <= settings.black_luma and s <= settings.black_std
        for m, s in zip(signals.luma_mean, signals.luma_std, strict=True)
    )


def detect_transitions(signals: ShotSignals, settings: ShotSettings) -> Detection:
    black = black_frames(signals, settings)
    fades = _fades(signals, settings, black)
    dissolves = _dissolves(signals, settings, fades)
    taken = [(t.first, t.last) for t in (*fades, *dissolves)]
    cuts, flashes = _hard_cuts(signals, settings, black, taken)
    ordered = sorted((*fades, *dissolves, *cuts), key=lambda t: t.frame)
    return Detection(_without_collisions(ordered, signals.frame_count), tuple(flashes))


# --- fades through black -------------------------------------------------------------------------
def _ramp(values: tuple[float, ...], start: int, step: int, floor: int, ceiling: int) -> int:
    """Frames, walking from ``start`` by ``step``, over which brightness keeps rising (not flat)."""
    count = 0
    position = start
    while floor <= position + step <= ceiling and values[position + step] > values[position]:
        count += 1
        position += step
    return count


def _fades(
    signals: ShotSignals, settings: ShotSettings, black: tuple[bool, ...]
) -> list[Transition]:
    found: list[Transition] = []
    count = signals.frame_count
    index = 0
    while index < count:
        if not black[index]:
            index += 1
            continue
        end = index
        while end + 1 < count and black[end + 1]:
            end += 1
        # runs touching either end of the video are fades in or out, not boundaries between shots
        if index > 0 and end < count - 1:
            down = _ramp(signals.luma_mean, index, -1, 0, count - 1)
            up = _ramp(signals.luma_mean, end, 1, 0, count - 1)
            if down >= settings.fade_min_frames and up >= settings.fade_min_frames:
                first, last = index - down, end + up
                found.append(
                    Transition(
                        BoundaryKind.FADE_THROUGH_BLACK,
                        (first + last + 1) // 2,
                        first,
                        last,
                        confidence=min(1.0, 0.5 + 0.05 * min(down, up)),
                        black_adjacent=True,
                    )
                )
        index = end + 1
    return found


# --- dissolves ------------------------------------------------------------------------------------
def _dissolves(
    signals: ShotSignals, settings: ShotSettings, fades: list[Transition]
) -> list[Transition]:
    gap = signals.long_gap
    count = signals.frame_count
    candidate = [False] * count
    for i in range(gap, count):
        if signals.diff_long[i] >= settings.dissolve_diff and (
            max(signals.diff1[i - gap + 1 : i + 1]) <= settings.dissolve_step_max
        ):
            candidate[i] = True

    found: list[Transition] = []
    index = 0
    while index < count:
        if not candidate[index]:
            index += 1
            continue
        end = index
        while end + 1 < count and candidate[end + 1]:
            end += 1
        found.extend(_dissolve_in(signals, settings, fades, index, end))
        index = end + 1
    return found


def _dissolve_in(
    signals: ShotSignals,
    settings: ShotSettings,
    fades: list[Transition],
    index: int,
    end: int,
) -> list[Transition]:
    """Locate the dissolve behind a run of long-gap changes: where the per-frame change rises
    clearly above its background, centred on where that excess change is concentrated."""
    gap = signals.long_gap
    low = max(1, index - 2 * gap)
    high = min(signals.frame_count, end + gap + 1)
    window = signals.diff1[low:high]
    baseline = sorted(window)[len(window) // 5]
    excess = [max(0.0, d - baseline) for d in window]
    peak = max(excess)
    if peak <= 0:
        return []
    active = [k for k, e in enumerate(excess) if e >= _ACTIVE_SHARE * peak]
    first, last = low + active[0], low + active[-1]
    mass = sum(excess[k] for k in active)
    centre = low + round(sum(k * excess[k] for k in active) / mass)
    if (
        not settings.dissolve_min_frames <= last - first + 1 <= settings.dissolve_max_frames
        or not first < centre <= last
        or any(f.first - gap <= centre <= f.last + gap for f in fades)
        or not _stands_out(signals, settings, first, last)
    ):
        return []
    height = max(signals.diff_long[index : end + 1])
    return [
        Transition(
            BoundaryKind.DISSOLVE,
            centre,
            first,
            last,
            # gradual change is easily confused with motion: never fully confident
            confidence=min(0.85, 0.35 + height / (4 * settings.dissolve_diff)),
        )
    ]


def _stands_out(signals: ShotSignals, settings: ShotSettings, first: int, last: int) -> bool:
    """Do the frames of the candidate change clearly more than the quiet frames beside them?"""
    gap = signals.long_gap
    inside = mean(signals.diff1[first : last + 1])
    sides = [
        mean(window)
        for window in (
            signals.diff1[max(1, first - gap) : first],
            signals.diff1[last + 1 : last + 1 + gap],
        )
        if window
    ]
    if not sides:
        return False
    return inside >= settings.dissolve_ratio * max(max(sides), _CONTEXT_FLOOR / 2)


# --- hard cuts ----------------------------------------------------------------------------------
def _hard_cuts(
    signals: ShotSignals,
    settings: ShotSettings,
    black: tuple[bool, ...],
    taken: list[tuple[int, int]],
) -> tuple[list[Transition], list[int]]:
    count = signals.frame_count
    radius = settings.context_radius
    candidates: list[tuple[int, float]] = []
    flashes: list[int] = []
    for i in range(1, count):
        step = signals.diff1[i]
        if step < settings.cut_diff:
            continue
        if any(first <= i <= last for first, last in taken):
            continue
        around = [
            signals.diff1[j]
            for j in range(max(1, i - radius), min(count, i + radius + 1))
            if j != i
        ]
        if around and step < settings.context_ratio * max(median(around), _CONTEXT_FLOOR):
            continue  # a busy stretch, this step does not stand out from it
        returns_after = i + 1 < count and signals.diff2[i + 1] <= settings.flash_return_ratio * step
        returned_from = i >= 2 and signals.diff2[i] <= settings.flash_return_ratio * step
        if returns_after or returned_from:
            flashes.append(i)
            continue
        candidates.append((i, step))

    kept: list[tuple[int, float]] = []
    for frame, step in candidates:
        if kept and frame - kept[-1][0] <= settings.suppress_frames:
            if step > kept[-1][1]:
                kept[-1] = (frame, step)
            continue
        kept.append((frame, step))

    cuts = [
        Transition(
            BoundaryKind.HARD_CUT,
            frame,
            frame,
            frame,
            confidence=min(
                1.0,
                0.5 * min(1.0, step / (2 * settings.cut_diff))
                + 0.5 * min(1.0, signals.hist1[frame] / (2 * settings.cut_hist)),
            ),
            black_adjacent=black[frame] or black[frame - 1],
        )
        for frame, step in kept
    ]
    return cuts, sorted(set(flashes))


def _without_collisions(ordered: list[Transition], count: int) -> tuple[Transition, ...]:
    """Boundaries must be strictly increasing and leave every shot at least one frame."""
    result: list[Transition] = []
    for transition in ordered:
        if not 0 < transition.frame < count:
            continue
        if result and transition.frame <= result[-1].frame:
            continue
        result.append(transition)
    return tuple(result)
