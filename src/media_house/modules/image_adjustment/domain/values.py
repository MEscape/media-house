"""Adjustment settings and mask statistics: immutable, validated, JSON-describable."""

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum

from media_house.modules.image_adjustment.domain.errors import SegmentationRejected
from media_house.shared.errors import InvariantViolation

#: Same shape as the media library's ``JsonValue`` (aliases are structural).
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

#: Bump whenever the pixel pipeline changes: older results stop matching and are rebuilt.
ADJUSTMENT_VERSION = 1
ADJUSTMENT_OPERATION = "image_adjustment"
#: ISNet general-use shredded dark, busy packaging; BiRefNet keeps it whole.
DEFAULT_MODEL = "birefnet-general-lite"

_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})$")
_MAX_CANVAS = 4096
_MIN_CANVAS = 64
_MAX_EFFECT = 500.0
#: The subject must keep at least this many pixels per side after padding and glow.
_MIN_SUBJECT_AREA = 16


class Centering(StrEnum):
    CENTER = "center"
    CENTER_OF_MASS = "center_of_mass"


@dataclass(frozen=True, slots=True)
class HexColor:
    """An sRGB colour, normalised to ``#RRGGBB`` upper case."""

    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"#[0-9A-F]{6}", self.value):
            raise InvariantViolation(
                f"Invalid colour {self.value!r}",
                user_message="Colours look like #FF2A5F.",
            )

    @classmethod
    def of(cls, raw: str) -> "HexColor":
        match = _HEX_COLOR.match(raw.strip())
        if match is None:
            raise InvariantViolation(
                f"Invalid colour {raw!r}",
                user_message="Colours look like #FF2A5F.",
            )
        return cls(f"#{match.group(1).upper()}")

    @property
    def rgb(self) -> tuple[int, int, int]:
        return int(self.value[1:3], 16), int(self.value[3:5], 16), int(self.value[5:7], 16)


@dataclass(frozen=True, slots=True)
class BackgroundRemoval:
    enabled: bool = True
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise InvariantViolation("Background removal model must not be empty")


@dataclass(frozen=True, slots=True)
class Glow:
    """Soft coloured light behind the subject, in OUTPUT canvas pixels.

    ``spread`` grows the subject silhouette; ``radius`` then blurs it (Gaussian,
    sigma = radius / 2). ``opacity`` scales the result.
    """

    enabled: bool = False
    color: HexColor = field(default_factory=lambda: HexColor("#FF2A5F"))
    opacity: float = 0.85
    radius: float = 30.0
    spread: float = 12.0

    def __post_init__(self) -> None:
        if not (math.isfinite(self.opacity) and 0.0 < self.opacity <= 1.0):
            raise InvariantViolation("Glow opacity must be in (0, 1]")
        for name, value in (("radius", self.radius), ("spread", self.spread)):
            if not (math.isfinite(value) and 0.0 <= value <= _MAX_EFFECT):
                raise InvariantViolation(f"Glow {name} must be between 0 and {_MAX_EFFECT:g}")

    @property
    def sigma(self) -> float:
        return self.radius / 2.0

    @property
    def extent(self) -> int:
        """Pixels the glow can reach beyond the subject silhouette (0 when disabled)."""
        if not self.enabled:
            return 0
        return math.ceil(self.spread) + 1 + math.ceil(3.0 * self.sigma) + 1


@dataclass(frozen=True, slots=True)
class Canvas:
    width: int = 1080
    height: int = 1080
    padding: int = 60
    centering: Centering = Centering.CENTER

    def __post_init__(self) -> None:
        for name, value in (("width", self.width), ("height", self.height)):
            if not _MIN_CANVAS <= value <= _MAX_CANVAS:
                raise InvariantViolation(
                    f"Canvas {name} must be {_MIN_CANVAS}-{_MAX_CANVAS}",
                    user_message=f"The output {name} must be {_MIN_CANVAS}-{_MAX_CANVAS} pixels.",
                )
        if self.padding < 0:
            raise InvariantViolation("Padding must not be negative")


@dataclass(frozen=True, slots=True)
class MaskStats:
    """What the segmentation produced: share of foreground pixels and its bounding box."""

    foreground_ratio: float
    #: ``(left, top, right, bottom)`` in source pixels, ``None`` if there is no foreground.
    bbox: tuple[int, int, int, int] | None


@dataclass(frozen=True, slots=True)
class MaskLimits:
    """Sanity limits for an AI mask. They only reject; they never change a valid result."""

    min_foreground_ratio: float = 0.005
    max_foreground_ratio: float = 0.95

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_foreground_ratio < self.max_foreground_ratio <= 1.0:
            raise InvariantViolation("Mask limits must satisfy 0 <= min < max <= 1")

    def check(self, stats: MaskStats) -> None:
        if stats.bbox is None or stats.foreground_ratio <= 0.0:
            raise SegmentationRejected("no subject found")
        if stats.foreground_ratio < self.min_foreground_ratio:
            raise SegmentationRejected(
                f"subject covers only {stats.foreground_ratio:.2%} of the image",
            )
        if stats.foreground_ratio > self.max_foreground_ratio:
            raise SegmentationRejected(
                f"background not removed, subject covers {stats.foreground_ratio:.2%}",
            )


@dataclass(frozen=True, slots=True)
class AdjustmentSettings:
    """Everything that influences the output pixels (plus the acceptance limits)."""

    background_removal: BackgroundRemoval = field(default_factory=BackgroundRemoval)
    glow: Glow = field(default_factory=Glow)
    canvas: Canvas = field(default_factory=Canvas)
    mask_limits: MaskLimits = field(default_factory=MaskLimits)

    def __post_init__(self) -> None:
        margin = 2 * (self.canvas.padding + self.glow.extent)
        if min(self.canvas.width, self.canvas.height) - margin < _MIN_SUBJECT_AREA:
            raise InvariantViolation(
                "Padding and glow leave no room for the subject",
                user_message="Padding and glow are too large for this output size.",
            )

    def to_config(self) -> dict[str, JsonValue]:
        """Canonical description used for the processing fingerprint.

        Disabled effects collapse to ``{"enabled": False}`` so irrelevant leftovers (an unused
        glow colour) never create duplicate variants. ``mask_limits`` only decide whether a
        result is accepted, not how it looks, so they are not part of the identity.
        """
        removal: dict[str, JsonValue] = {"enabled": self.background_removal.enabled}
        if self.background_removal.enabled:
            removal["model"] = self.background_removal.model
        glow: dict[str, JsonValue] = {"enabled": self.glow.enabled}
        if self.glow.enabled:
            glow |= {
                "color": self.glow.color.value,
                "opacity": self.glow.opacity,
                "radius": self.glow.radius,
                "spread": self.glow.spread,
            }
        return {
            "background_removal": removal,
            "glow": glow,
            "canvas": {
                "width": self.canvas.width,
                "height": self.canvas.height,
                "padding": self.canvas.padding,
                "centering": self.canvas.centering.value,
            },
        }
