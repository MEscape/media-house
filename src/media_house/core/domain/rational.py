"""An exact fraction: frame rates, time bases and aspect ratios without floating-point drift."""

import math
from dataclasses import dataclass

from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class Rational:
    """An exact, reduced, positive-denominator fraction (frame rates, time bases, aspect ratios)."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if self.denominator <= 0 or self.numerator < 0:
            raise InvariantViolation(
                "A rational needs a non-negative numerator and a positive denominator",
                details={"numerator": self.numerator, "denominator": self.denominator},
            )
        divisor = math.gcd(self.numerator, self.denominator)
        if divisor > 1:
            object.__setattr__(self, "numerator", self.numerator // divisor)
            object.__setattr__(self, "denominator", self.denominator // divisor)

    @classmethod
    def parse(cls, text: object) -> "Rational | None":
        """``"30000/1001"``, ``"16:9"`` or ``"25"``; ``None`` for anything unusable or zero."""
        if not isinstance(text, str):
            return None
        parts = text.strip().replace(":", "/").split("/")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return None
        if len(numbers) == 1:
            numbers.append(1)
        if len(numbers) != 2 or numbers[0] <= 0 or numbers[1] <= 0:
            return None
        return cls(numbers[0], numbers[1])

    @property
    def value(self) -> float:
        return self.numerator / self.denominator

    @property
    def reciprocal(self) -> "Rational":
        return Rational(self.denominator, self.numerator)

    def __str__(self) -> str:
        return f"{self.numerator}/{self.denominator}"
