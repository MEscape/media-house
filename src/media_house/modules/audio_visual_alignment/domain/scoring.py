"""Interval arithmetic, correlation and the one place that turns several signals into a strength.

``strength`` is the strength of the EVIDENCE, not a probability, and it is not calibrated
(``SCORING_VERSION`` 1). It is built so that:

* the weakest signal limits it (a mean may exceed the weakest present component by at most
  ``_SPREAD``), so one great signal cannot hide a poor one;
* a missing signal is left out, never counted as zero or as support;
* a single signal alone is discounted (``_SOLE_SIGNAL``): one coincidence is not corroboration;
* contradicting evidence (a share in [0, 1]) scales it down.
"""

import math
from collections.abc import Sequence

_SPREAD = 0.25
_SOLE_SIGNAL = 0.6
_MIN_CORRELATION_POINTS = 3


def overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Seconds shared by two intervals (0 when they only touch or do not meet)."""
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def gap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Seconds between two intervals (0 when they overlap or touch)."""
    return max(0.0, max(a_start, b_start) - min(a_end, b_end))


def coverage(shared: float, length: float) -> float | None:
    """``shared`` as a share of ``length``; ``None`` for an empty interval."""
    return None if length <= 0 else min(1.0, shared / length)


def combine(components: Sequence[float | None], contradiction: float = 0.0) -> float | None:
    """Strength of evidence from the signals that exist; ``None`` when none exists."""
    present = [c for c in components if c is not None]
    if not present:
        return None
    value = min(sum(present) / len(present), min(present) + _SPREAD)
    if len(present) == 1:
        value *= _SOLE_SIGNAL
    value *= 1.0 - min(1.0, max(0.0, contradiction))
    return round(min(1.0, max(0.0, value)), 4)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Correlation of two equal-length series; ``None`` for too few points or a flat series."""
    n = len(xs)
    if n != len(ys) or n < _MIN_CORRELATION_POINTS:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return max(-1.0, min(1.0, covariance / math.sqrt(var_x * var_y)))


def spread(values: Sequence[float]) -> float | None:
    """Population standard deviation, ``None`` for fewer than two values."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
