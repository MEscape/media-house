"""Vocabulary and versions of video improvement."""

from dataclasses import dataclass
from enum import StrEnum

from media_house.shared.errors import InvariantViolation

#: Same shape as the media library's ``JsonValue`` (aliases are structural).
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type Parameter = float | str | bool

VIDEO_OPERATION = "video_improvement"
#: Bump when planning, guards, colour maths or stage order change in a way that alters results.
#: Engine and profile changes are part of the fingerprint on their own.
PROCESSING_VERSION = 1
PROVENANCE_SCHEMA_VERSION = 1
#: Key of the asset metadata that carries the processing provenance (see ``provenance.py``).
METADATA_KEY = "video_processing"


class ProcessingStage(StrEnum):
    """One kind of processing. The order below is the order of the filter chain."""

    DENOISE = "denoise"  # on the source signal, before any colour change
    COLOR = "color"  # one colour-managed transform (baked into a 3D LUT)
    SHARPEN = "sharpen"  # on the finished picture


STAGE_ORDER = (ProcessingStage.DENOISE, ProcessingStage.COLOR, ProcessingStage.SHARPEN)


class StageStatus(StrEnum):
    APPLIED = "applied"
    #: Analysis found it unnecessary (or impossible); the picture was left alone.
    SKIPPED = "skipped"
    #: The stage ran, the output was measured, it made things worse and it was left out.
    BYPASSED = "bypassed"


class ProfileOrigin(StrEnum):
    """How the source profile was decided. Earlier entries outrank later ones."""

    EXPLICIT = "explicit"  # the caller named it
    INSPECTION = "inspection"  # camera or colour metadata from a Media Inspection result
    EMBEDDED_METADATA = "embedded_metadata"  # the same metadata, read from the file itself
    DETECTED = "detected"  # a safe convention (untagged HD video is Rec.709)
    DEFAULT = "default"  # nothing known: the generic profile


class FactsSource(StrEnum):
    INSPECTION = "inspection"
    PROBE = "probe"


@dataclass(frozen=True, slots=True)
class FrameRate:
    """An exact frame rate (29.97 is 30000/1001)."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if self.numerator <= 0 or self.denominator <= 0:
            raise InvariantViolation("A frame rate needs positive numerator and denominator")

    @property
    def value(self) -> float:
        return self.numerator / self.denominator

    def __str__(self) -> str:
        return f"{self.numerator}/{self.denominator}"

    @classmethod
    def parse(cls, text: object) -> "FrameRate | None":
        if not isinstance(text, str) or "/" not in text:
            return None
        head, _, tail = text.partition("/")
        try:
            numerator, denominator = int(head), int(tail)
        except ValueError:
            return None
        if numerator <= 0 or denominator <= 0:
            return None
        return cls(numerator, denominator)
