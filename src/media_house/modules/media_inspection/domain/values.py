"""Vocabulary of media inspection: versions, enums, exact rationals and sourced facts.

The central idea is separating *what the media says* from *what we conclude*:

* ``Sourced[T]`` carries a value together with where it came from (``Provenance``). A value
  that the file does not state is ``unknown``, never a guess dressed up as a fact.
* ``Rational`` keeps exact timing values (29.97 fps is 30000/1001, not "30").
"""

import math
from dataclasses import dataclass
from enum import StrEnum

from media_house.shared.errors import InvariantViolation

#: Identity of the derived inspection document in the Media Library.
INSPECTION_OPERATION = "media_inspection"
#: Bump when what an inspection EXTRACTS or CONCLUDES changes (normalisation, rules, thresholds'
#: meaning). It is part of the cache fingerprint, so old results are retired, not misread.
INSPECTION_VERSION = 1
DOCUMENT_TYPE = "media_inspection"
#: Layout of the stored JSON document. Readers refuse newer schemas instead of guessing.
SCHEMA_VERSION = 1

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


class Provenance(StrEnum):
    """Where a value comes from. Consumers decide how much to trust it from this."""

    DECLARED = "declared"  # stated by the file's own metadata
    DETECTED = "detected"  # measured by reading the media (packet timing, decoding)
    INFERRED = "inferred"  # derived from other values by a rule (e.g. bit depth from pix_fmt)
    UNKNOWN = "unknown"  # not stated and not derivable


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Certainty(StrEnum):
    """How firmly a finding is established. Never stronger than the evidence."""

    MEASURED = "measured"  # a plain observation; whether it matters is for the consumer
    POSSIBLE = "possible"  # suspicious, but the evidence does not prove a problem
    CONFIRMED = "confirmed"  # the evidence contradicts what the file declares or a format rule


class Category(StrEnum):
    CONTAINER = "container"
    VIDEO = "video"
    AUDIO = "audio"
    TIMING = "timing"
    SYNC = "sync"
    COLOR = "color"
    TIMECODE = "timecode"
    INTEGRITY = "integrity"
    METADATA = "metadata"


class Concern(StrEnum):
    """What a downstream module should look at before relying on the asset."""

    SYNC_CHECK = "needs_sync_check"
    TRANSCODE = "needs_transcode"
    COLOR_CHECK = "needs_color_check"
    AUDIO_CHECK = "needs_audio_check"
    REPAIR = "needs_repair"


class Verdict(StrEnum):
    VALID = "valid"
    WARNING = "warning"
    ERROR = "error"


class Depth(StrEnum):
    """How much work an inspection does. ``FULL`` is a superset of ``PROBE``."""

    PROBE = "probe"  # container, stream headers and packet timing (no decoding)
    FULL = "full"  # PROBE plus decoding every stream to find damage


class FrameRateMode(StrEnum):
    CONSTANT = "constant"
    VARIABLE = "variable"
    UNKNOWN = "unknown"


class ScanType(StrEnum):
    PROGRESSIVE = "progressive"
    INTERLACED = "interlaced"
    UNKNOWN = "unknown"


class ChannelClass(StrEnum):
    MONO = "mono"
    STEREO = "stereo"
    MULTICHANNEL = "multichannel"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Sourced[T]:
    """A value and its provenance. ``UNKNOWN`` if and only if there is no value."""

    value: T | None
    provenance: Provenance

    def __post_init__(self) -> None:
        if (self.value is None) != (self.provenance is Provenance.UNKNOWN):
            raise InvariantViolation(
                "A sourced value is unknown exactly when it has no value",
                details={"provenance": self.provenance.value},
            )

    @classmethod
    def declared(cls, value: T) -> "Sourced[T]":
        return cls(value, Provenance.DECLARED)

    @classmethod
    def detected(cls, value: T) -> "Sourced[T]":
        return cls(value, Provenance.DETECTED)

    @classmethod
    def inferred(cls, value: T) -> "Sourced[T]":
        return cls(value, Provenance.INFERRED)

    @classmethod
    def unknown(cls) -> "Sourced[T]":
        return cls(None, Provenance.UNKNOWN)

    @property
    def known(self) -> bool:
        return self.value is not None


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
