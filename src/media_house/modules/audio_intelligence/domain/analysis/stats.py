"""Robust-statistics helpers. NaN means "unknown": skipped, never treated as 0."""

import math
from collections.abc import Iterable, Sequence
from itertools import pairwise

#: Scale factor turning a median absolute deviation into a standard-deviation equivalent.
MAD_TO_SIGMA = 1.4826


def finite(values: Iterable[float]) -> list[float]:
    return [v for v in values if math.isfinite(v)]


def median(values: Sequence[float]) -> float | None:
    data = sorted(finite(values))
    if not data:
        return None
    mid = len(data) // 2
    return data[mid] if len(data) % 2 else (data[mid - 1] + data[mid]) / 2


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolated percentile, ``p`` in [0, 100]."""
    data = sorted(finite(values))
    if not data:
        return None
    position = (len(data) - 1) * min(100.0, max(0.0, p)) / 100.0
    low, high = math.floor(position), math.ceil(position)
    return data[low] + (data[high] - data[low]) * (position - low)


def robust_sigma(values: Sequence[float], center: float | None = None) -> float | None:
    """Standard-deviation equivalent from the median absolute deviation."""
    data = finite(values)
    if len(data) < 2:
        return None
    mid = median(data) if center is None else center
    spread = median([abs(v - mid) for v in data]) if mid is not None else None
    return None if spread is None else spread * MAD_TO_SIGMA


def std(values: Sequence[float]) -> float | None:
    data = finite(values)
    if len(data) < 2:
        return None
    mean = math.fsum(data) / len(data)
    return math.sqrt(math.fsum((v - mean) ** 2 for v in data) / (len(data) - 1))


def mean(values: Sequence[float]) -> float | None:
    data = finite(values)
    return math.fsum(data) / len(data) if data else None


def slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Least-squares slope of ``ys`` over ``xs`` (units of y per unit of x)."""
    pairs = [(x, y) for x, y in zip(xs, ys, strict=True) if math.isfinite(x) and math.isfinite(y)]
    if len(pairs) < 2:
        return None
    mx = math.fsum(p[0] for p in pairs) / len(pairs)
    my = math.fsum(p[1] for p in pairs) / len(pairs)
    denominator = math.fsum((x - mx) ** 2 for x, _ in pairs)
    if denominator <= 0:
        return None
    return math.fsum((x - mx) * (y - my) for x, y in pairs) / denominator


def power_mean_db(values_db: Sequence[float]) -> float | None:
    """Average of dB values in the POWER domain (dB values must not be averaged arithmetically)."""
    data = finite(values_db)
    if not data:
        return None
    return 10.0 * math.log10(math.fsum(10.0 ** (v / 10.0) for v in data) / len(data))


def semitones(hz: float, reference_hz: float) -> float:
    return 12.0 * math.log2(hz / reference_hz)


def saturate(value: float | None, full_scale: float) -> float | None:
    """Map a non-negative magnitude onto [0, 1]: ``full_scale`` and above give 1.0."""
    if value is None or not math.isfinite(value):
        return None
    return min(1.0, max(0.0, value / full_scale))


def weighted_mean(parts: dict[str, float | None], weights: dict[str, float]) -> float | None:
    """Weighted mean over the parts that exist (weights renormalised); ``None`` if none do."""
    total = weight_sum = 0.0
    for name, value in parts.items():
        weight = weights.get(name, 0.0)
        if value is not None and weight > 0:
            total += weight * value
            weight_sum += weight
    return None if weight_sum <= 0 else min(1.0, max(0.0, total / weight_sum))


def moving_median(values: Sequence[float], width: int) -> list[float]:
    """Median over a centred window, skipping NaN; positions with no data stay NaN."""
    half = width // 2
    result: list[float] = []
    for i in range(len(values)):
        window = finite(values[max(0, i - half) : i + half + 1])
        result.append(median(window) if window else math.nan)  # type: ignore[arg-type]
    return result


def swings(values: Sequence[float], min_delta: float) -> list[tuple[int, int, float]]:
    """Monotone rises/falls of at least ``min_delta`` as ``(start_index, end_index, delta)``.

    Classic zig-zag: a swing ends when the series reverses by ``min_delta`` from its extreme.
    """
    n = len(values)
    if n < 2:
        return []
    lo = hi = 0
    trend = 0
    pivots: list[int] = []
    extreme = 0
    for i in range(1, n):
        v = values[i]
        if trend == 0:
            if v < values[lo]:
                lo = i
            if v > values[hi]:
                hi = i
            if v - values[lo] >= min_delta and lo < i:
                pivots, trend, extreme = [lo], 1, i
            elif values[hi] - v >= min_delta and hi < i:
                pivots, trend, extreme = [hi], -1, i
        elif trend == 1:
            if v > values[extreme]:
                extreme = i
            elif values[extreme] - v >= min_delta:
                pivots.append(extreme)
                trend, extreme = -1, i
        else:
            if v < values[extreme]:
                extreme = i
            elif v - values[extreme] >= min_delta:
                pivots.append(extreme)
                trend, extreme = 1, i
    if trend != 0:
        pivots.append(extreme)
    found: list[tuple[int, int, float]] = []
    for a, b in pairwise(pivots):
        delta = values[b] - values[a]
        if abs(delta) < min_delta:
            continue
        found.append((*_transition(values, a, b), delta))
    return found


def _transition(values: Sequence[float], a: int, b: int) -> tuple[int, int]:
    """Narrow ``[a, b]`` to the actual movement: leave the starting plateau, stop at the end one.

    A swing anchored at the first sample of a long flat stretch would otherwise look like a slow
    glide. Plateau = within 10% of the swing's amplitude of its start/end value.
    """
    tolerance = 0.1 * abs(values[b] - values[a])
    start = a
    while start < b and abs(values[start + 1] - values[a]) <= tolerance:
        start += 1
    end = b
    while end > start and abs(values[end - 1] - values[b]) <= tolerance:
        end -= 1
    return start, end
