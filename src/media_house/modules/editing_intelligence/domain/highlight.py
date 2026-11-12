"""A short cut from a long edit: the one stretch of kept sentences that stands on its own.

Every run of consecutive kept sentences (story order) whose spoken length fits the wanted range is
a candidate. It must start on a sentence that starts cleanly and end on one that ends cleanly. The
score rewards a hook-like opening or a section's own start, highlight-worthy content throughout
and a strong last line, and penalises a stretch that needs context from before it or wanders over
many sections. The best
candidate wins (the earliest on a tie), so the same plan always gives the same short.
"""

from collections.abc import Mapping, Sequence
from fractions import Fraction

from media_house.modules.editing_intelligence.domain.model import Decision
from media_house.modules.editing_intelligence.domain.units import EditUnit
from media_house.modules.editing_intelligence.domain.values import Action

#: A short pause the edit keeps between two sentences (seconds), added to the spoken length.
_GAP = Fraction(15, 100)
_STRONG = frozenset({"hook", "payoff", "turning_point", "claim"})
#: Starting where a section starts (a product's introduction) makes the short self-explaining.
_OPENS = 0.6


def highlight_window(
    units: Sequence[EditUnit],
    decisions: Mapping[str, Decision],
    low: float,
    high: float,
) -> tuple[EditUnit, ...]:
    """The best stretch of kept, timed sentences lasting ``low``-``high`` s (empty: none fits)."""
    kept = [
        (u, u.extent.seconds)
        for u in sorted(units, key=lambda u: u.order)
        if u.extent is not None and decisions[u.decision_id].action is not Action.CUT
    ]
    best: tuple[float, int, int] | None = None
    lo, hi = Fraction(str(low)), Fraction(str(high))
    for i, (first, _) in enumerate(kept):
        if not first.unit.starts_clean:
            continue
        total = Fraction(0)
        for j in range(i, len(kept)):
            unit, seconds = kept[j]
            total += seconds + (_GAP if j > i else 0)
            if total > hi:
                break
            if total < lo or not unit.unit.ends_clean:
                continue
            opens = i == 0 or kept[i - 1][0].section_id != first.section_id
            score = _score([u for u, _ in kept[i : j + 1]]) + _OPENS * opens
            if best is None or score > best[0]:
                best = (score, i, j)
    return () if best is None else tuple(u for u, _ in kept[best[1] : best[2] + 1])


def _score(window: Sequence[EditUnit]) -> float:
    first, last = window[0], window[-1]
    inside = {u.unit.unit_id for u in window}
    highlight = sum((u.segment.highlight or 0.0) if u.segment else 0.0 for u in window)
    needs = {
        need
        for u in window
        if u.segment is not None
        for need in u.segment.requires_context
        if need not in inside
    }
    return (
        2.0 * ((first.segment.hook or 0.0) if first.segment else 0.0)
        + 2.0 * highlight / len(window)
        + 1.0
        * ((last.segment.highlight or last.segment.importance or 0.0) if last.segment else 0.0)
        + 0.3 * any(u.function in _STRONG for u in window)
        - 0.5 * min(len(needs), 3)
        - 0.15 * (len({u.section_id for u in window}) - 1)
    )
