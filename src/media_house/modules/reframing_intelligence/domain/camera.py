"""Virtual camera paths: smooth, bounded, and honest about what it cannot hold.

One axis at a time (pan x, pan y, zoom), over the moments at which the subject was observed. Pure
arithmetic on plain lists, so the same input gives the same path.

The planner is given, per moment, a ``[lo, hi]`` interval the window must stay in (it keeps every
required element inside) and a preferred value (where the composition wants it, ``None`` when the
subject was not observed: the window then simply holds). It

* looks AHEAD: the limits are propagated backwards at the speed limit, so the window starts moving
  early enough to be where it must be when the subject gets there;
* waits inside a dead zone (a subject that moves a little does not move the window);
* eases toward the preference at no more than ``speed`` per second, keeps following a subject that
  keeps going the same way, and reverses only after ``dwell`` seconds of stillness (that is what
  suppresses jitter), unless leaving the interval would lose an element;
* never invents a position across a gap: with no observation the window holds.

Containment beats the speed limit: if an element would otherwise leave the picture the window
moves faster than the limit, and the caller reports it.
"""

import math
from dataclasses import dataclass

_TINY = 1e-9


@dataclass(frozen=True, slots=True)
class PathPoint:
    t: float
    #: Top-left corner of the window and its zoom (1 = the widest window of the target's shape).
    x: float
    y: float
    z: float


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(max(value, lo), hi)


def smooth(times: list[float], values: list[float | None], window: float) -> list[float | None]:
    """Noisy observations smoothed by a triangular average over +-``window`` seconds. A moment
    with no observation stays without one; a window of 0 changes nothing. Meaningful motion is
    kept (a straight run of values is unchanged); only jitter shorter than the window is removed."""
    if window <= 0:
        return list(values)
    out: list[float | None] = []
    for i, v in enumerate(values):
        if v is None:
            out.append(None)
            continue
        total, weight = 0.0, 0.0
        for j in range(i, -1, -1):
            if times[i] - times[j] > window:
                break
            w = 1.0 - (times[i] - times[j]) / (window + 1e-9)
            if values[j] is not None:
                total += w * float(values[j] or 0.0)
                weight += w
        for j in range(i + 1, len(times)):
            if times[j] - times[i] > window:
                break
            w = 1.0 - (times[j] - times[i]) / (window + 1e-9)
            if values[j] is not None:
                total += w * float(values[j] or 0.0)
                weight += w
        out.append(total / weight)
    return out


def reach(
    times: list[float], lo: list[float], hi: list[float], speed: list[float]
) -> tuple[list[float], list[float]]:
    """Intervals narrowed so that moving at ``speed`` can still reach every later interval."""
    low, high = list(lo), list(hi)
    for i in range(len(times) - 2, -1, -1):
        step = speed[i + 1] * (times[i + 1] - times[i])
        new_lo, new_hi = max(lo[i], low[i + 1] - step), min(hi[i], high[i + 1] + step)
        if new_lo <= new_hi:
            low[i], high[i] = new_lo, new_hi
    return low, high


def follow(
    times: list[float],
    lo: list[float],
    hi: list[float],
    want: list[float | None],
    speed: list[float],
    dead: list[float],
    dwell: float,
    min_change: float,
    tau: float = 0.0,
) -> list[float]:
    """One coordinate of the window's top-left corner at every moment. ``tau`` (seconds) eases the
    approach to the goal (an exponential, not a straight dash); 0 goes at the speed limit."""
    low, high = reach(times, lo, hi, speed)
    first = want[0] if want[0] is not None else (low[0] + high[0]) / 2
    state = _clamp(first, low[0], high[0])
    path = [state]
    last_dir = 0.0
    moved_before = False
    # the goal's own velocity: easing toward a moving goal lags it by velocity x tau, so the aim is
    # moved ahead by that much (anticipation from observed motion, not a guess about the future)
    pace = [0.0] * len(times)
    for i in range(1, len(times)):
        before, now = want[i - 1], want[i]
        if before is not None and now is not None and times[i] > times[i - 1]:
            pace[i] = (now - before) / (times[i] - times[i - 1])
    still_since = float("-inf")
    for i in range(1, len(times)):
        dt = times[i] - times[i - 1]
        goal = want[i]
        inside = low[i] - _TINY <= state <= high[i] + _TINY
        new = _clamp(state, low[i], high[i])
        if goal is not None:
            goal = _clamp(goal, low[i], high[i])
            gap = goal - state
            if inside and abs(gap) <= max(dead[i], min_change):
                aim = state  # the subject is inside the dead zone: the window stays
            else:
                # outside the zone the window keeps the subject at the zone's edge, so a steady
                # walker is followed at the walker's own pace instead of in lurches
                aim = (goal if not inside else goal - math.copysign(dead[i], gap)) + pace[i] * tau
            reverse = (aim - state) * last_dir < -min_change  # a real turn, not a wobble
            if inside and reverse and times[i] - still_since < dwell:
                aim = state  # a change of direction waits: that is the jitter dwell suppresses
            if aim != state:
                limit = speed[i] * dt
                step = (aim - state) * (1.0 - math.exp(-dt / tau)) if tau > 0 else aim - state
                new = _clamp(state + _clamp(step, -limit, limit), low[i], high[i])
        moved = abs(new - state) > _TINY
        if moved:
            last_dir = 1.0 if new > state else -1.0
        elif moved_before:
            still_since = times[i]  # the window just came to rest
        moved_before = moved
        state = new
        path.append(state)
    return path


def follow_zoom(
    times: list[float], need: list[float], want: list[float], rate: float
) -> list[float]:
    """The zoom at every moment: at least ``need`` (wide enough to hold what is required), as
    close to ``want`` as ``rate`` per second allows, never wider than 1."""
    n = len(times)
    lower = list(need)
    for i in range(n - 2, -1, -1):
        lower[i] = max(need[i], lower[i + 1] - rate * (times[i + 1] - times[i]))
    state = _clamp(want[0], min(lower[0], 1.0), 1.0)
    path = [state]
    for i in range(1, n):
        limit = rate * (times[i] - times[i - 1])
        state = _clamp(state + _clamp(want[i] - state, -limit, limit), min(lower[i], 1.0), 1.0)
        path.append(state)
    return path


def simplify(
    points: list[PathPoint], width: list[float], tolerance: float, zoom_tolerance: float
) -> list[PathPoint]:
    """The fewest points whose straight lines stay within tolerance of the path (Douglas-Peucker).
    Position error is measured in crop widths (``width`` per point), zoom error in zoom units."""
    if len(points) <= 2:
        return list(points)
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        worst, index = 0.0, -1
        for i in range(a + 1, b):
            f = (points[i].t - points[a].t) / (points[b].t - points[a].t)
            dx = abs(points[i].x - (points[a].x + (points[b].x - points[a].x) * f)) / width[i]
            dy = abs(points[i].y - (points[a].y + (points[b].y - points[a].y) * f)) / width[i]
            dz = abs(points[i].z - (points[a].z + (points[b].z - points[a].z) * f))
            error = max(dx / tolerance, dy / tolerance, dz / zoom_tolerance)
            if error > worst:
                worst, index = error, i
        if index >= 0 and worst > 1.0:
            keep.add(index)
            stack += [(a, index), (index, b)]
    return [points[i] for i in sorted(keep)]


def peak_speed(points: list[PathPoint], width: list[float]) -> tuple[float, float]:
    """Largest window-centre speed (crop widths per second) and zoom rate between points."""
    fastest, zooming = 0.0, 0.0
    for a, b, w in zip(points, points[1:], width[1:], strict=False):
        dt = b.t - a.t
        if dt <= 0:
            continue
        fastest = max(fastest, max(abs(b.x - a.x), abs(b.y - a.y)) / w / dt)
        zooming = max(zooming, abs(b.z - a.z) / dt)
    return fastest, zooming
