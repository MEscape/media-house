"""A time range on the original media timeline (seconds) that says how much to trust each end."""

import math
from dataclasses import dataclass

from media_house.modules.content_intelligence.domain.values import TimeBasis
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class Span:
    """``[start, end]`` in seconds, or both ``None`` when nothing is known (basis ``MISSING``)."""

    start: float | None
    end: float | None
    start_basis: TimeBasis
    end_basis: TimeBasis

    def __post_init__(self) -> None:
        for value, basis, label in (
            (self.start, self.start_basis, "start"),
            (self.end, self.end_basis, "end"),
        ):
            if (value is None) != (basis is TimeBasis.MISSING):
                raise InvariantViolation(f"Span {label}: value and basis disagree")
            if value is not None and (not math.isfinite(value) or value < 0):
                raise InvariantViolation(f"Span {label} must be a finite, non-negative time")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise InvariantViolation("A span must not end before it starts")

    @classmethod
    def missing(cls) -> "Span":
        return cls(None, None, TimeBasis.MISSING, TimeBasis.MISSING)

    @property
    def known(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def duration(self) -> float | None:
        return self.end - self.start if self.start is not None and self.end is not None else None

    @property
    def exact(self) -> bool:
        return self.start_basis.exact and self.end_basis.exact

    def overlaps(self, start: float, end: float) -> bool:
        if self.start is None or self.end is None:
            return False
        return self.end > start and self.start < end

    @classmethod
    def cover(cls, first: "Span", last: "Span") -> "Span":
        """From the start of ``first`` to the end of ``last`` (each end keeps its own basis)."""
        start, end = first.start, last.end
        if start is not None and end is not None and end < start:
            end = start  # overlapping neighbours; never produce an inverted span
        return cls(
            start,
            end,
            first.start_basis,
            last.end_basis if end == last.end else first.start_basis,
        )
