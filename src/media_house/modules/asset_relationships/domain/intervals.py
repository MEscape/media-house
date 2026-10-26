"""Exact time on an explicitly named clock.

Seconds are ``fractions.Fraction`` (signed, exact): ``core.domain.Rational`` is non-negative and
container times can be negative. Intervals are half-open ``[start, end)`` and carry the coordinates
they are measured in; nothing is compared across clock domains silently.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.asset_relationships.domain.values import ClockDomain, OffsetOrigin
from media_house.shared.errors import InvariantViolation


def _require_exact(value: object, what: str) -> None:
    if not isinstance(value, Fraction):
        raise InvariantViolation(f"{what} must be an exact Fraction, never a float or int")


@dataclass(frozen=True, slots=True)
class TimeCoordinates:
    """Which clock and what its zero means (for example ``container_start``)."""

    domain: ClockDomain
    origin: str

    def __post_init__(self) -> None:
        if not self.origin or self.origin != self.origin.strip():
            raise InvariantViolation("Time coordinates must say what zero means")


@dataclass(frozen=True, slots=True)
class TimeInterval:
    """Half-open ``[start, end)``; an instant (``start == end``) is not an interval."""

    start: Fraction
    end: Fraction
    coordinates: TimeCoordinates

    def __post_init__(self) -> None:
        _require_exact(self.start, "start")
        _require_exact(self.end, "end")
        if not self.start < self.end:
            raise InvariantViolation(
                "An interval must start before it ends", details={"start": str(self.start)}
            )

    def overlaps(self, other: "TimeInterval") -> bool:
        if self.coordinates != other.coordinates:
            raise InvariantViolation("Intervals on different clocks cannot be compared")
        return self.start < other.end and other.start < self.end

    def widened(self, margin: Fraction) -> "TimeInterval":
        return TimeInterval(self.start - margin, self.end + margin, self.coordinates)

    def shifted(self, seconds: Fraction, into: TimeCoordinates) -> "TimeInterval":
        """The same span moved by ``seconds`` and expressed in ``into``."""
        return TimeInterval(self.start + seconds, self.end + seconds, into)


@dataclass(frozen=True, slots=True)
class ClockOffset:
    """``target_time = source_time + seconds``. ``uncertainty=None`` means unknown, not zero."""

    seconds: Fraction
    uncertainty: Fraction | None
    origin: OffsetOrigin

    def __post_init__(self) -> None:
        _require_exact(self.seconds, "seconds")
        if self.uncertainty is not None:
            _require_exact(self.uncertainty, "uncertainty")
            if self.uncertainty < 0:
                raise InvariantViolation("Offset uncertainty must not be negative")

    def inverted(self) -> "ClockOffset":
        """The offset in the opposite direction (used when a symmetric pair is put in order)."""
        return ClockOffset(-self.seconds, self.uncertainty, self.origin)
