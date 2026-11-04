"""Profiles are data: every knob of cutout enhancement, QC and selection, with its UI description.

Precedence (each step touches only the keys it names): profile defaults -> channel identity (the
visual style's tokens, mapped by the application) -> the caller's overrides (dotted paths such as
``{"variants.sticker.tilt.degrees": -8}``). Every effect is DISABLED unless a variant enables it:
the clean cutout never carries an effect. Pixel sizes are OUTPUT canvas pixels.

``PROFILE_VERSION`` versions the schema of the knobs; ``VisualAssetProfile.version`` the values.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum, StrEnum
from typing import Any, Final

from media_house.modules.visual_asset_generation.domain.errors import InvalidVisualAssetConfig
from media_house.modules.visual_asset_generation.domain.knobs import (
    colour,
    number,
    range_problems,
    require_valid,
    toggle,
)
from media_house.modules.visual_asset_generation.domain.values import (
    AdjustmentSettings,
    BackgroundRemoval,
    Canvas,
    Glow,
    HexColor,
    JsonValue,
    MaskLimits,
)
from media_house.shared.errors import InvariantViolation

PROFILE_VERSION: Final = 1
DEFAULT_PROFILE: Final = "generic"


def _white() -> HexColor:
    return HexColor("#FFFFFF")


def _black() -> HexColor:
    return HexColor("#000000")


@dataclass(frozen=True, slots=True)
class Shadow:
    """Soft drop shadow cast by the silhouette (outline included)."""

    enabled: bool = field(default=False, metadata=toggle("Drop shadow"))
    color: HexColor = field(default_factory=_black, metadata=colour("Shadow colour"))
    opacity: float = field(
        default=0.45, metadata=number("Shadow opacity", 0.01, 1.0, 0.01, "fraction")
    )
    offset_x: float = field(
        default=0.0, metadata=number("Shadow offset X", -200.0, 200.0, 1.0, "px")
    )
    offset_y: float = field(
        default=8.0, metadata=number("Shadow offset Y", -200.0, 200.0, 1.0, "px")
    )
    blur: float = field(
        default=16.0,
        metadata=number("Shadow softness", 0.0, 200.0, 1.0, "px", "Blur radius (sigma = r/2)."),
    )

    def __post_init__(self) -> None:
        require_valid(self)

    def problems(self) -> list[str]:
        return range_problems(self)

    @property
    def extent(self) -> int:
        if not self.enabled:
            return 0
        offset = max(abs(self.offset_x), abs(self.offset_y))
        return math.ceil(offset) + 1 + math.ceil(1.5 * self.blur) + 1


@dataclass(frozen=True, slots=True)
class Outline:
    """A solid stroke around the silhouette (the sticker edge)."""

    enabled: bool = field(default=False, metadata=toggle("Outline"))
    color: HexColor = field(default_factory=_white, metadata=colour("Outline colour"))
    width: float = field(default=4.0, metadata=number("Outline width", 0.5, 100.0, 0.5, "px"))
    opacity: float = field(
        default=1.0, metadata=number("Outline opacity", 0.01, 1.0, 0.01, "fraction")
    )

    def __post_init__(self) -> None:
        require_valid(self)

    def problems(self) -> list[str]:
        return range_problems(self)

    @property
    def extent(self) -> int:
        return math.ceil(self.width) + 1 if self.enabled else 0


@dataclass(frozen=True, slots=True)
class RimLight:
    """Light along the inside of the subject edge; never leaves the silhouette."""

    enabled: bool = field(default=False, metadata=toggle("Edge light"))
    color: HexColor = field(default_factory=_white, metadata=colour("Edge light colour"))
    width: float = field(default=3.0, metadata=number("Edge light width", 0.5, 50.0, 0.5, "px"))
    intensity: float = field(
        default=0.35, metadata=number("Edge light strength", 0.01, 1.0, 0.01, "fraction")
    )

    def __post_init__(self) -> None:
        require_valid(self)

    def problems(self) -> list[str]:
        return range_problems(self)


@dataclass(frozen=True, slots=True)
class Contrast:
    """Contrast of the subject's own pixels around mid grey (the background plays no part)."""

    enabled: bool = field(default=False, metadata=toggle("Subject contrast"))
    amount: float = field(
        default=0.08,
        metadata=number("Contrast amount", 0.01, 0.5, 0.01, "fraction", "0.08 = +8 %."),
    )

    def __post_init__(self) -> None:
        require_valid(self)

    def problems(self) -> list[str]:
        return range_problems(self)


@dataclass(frozen=True, slots=True)
class Tilt:
    """Rotation of the whole sticker (positive = counter-clockwise)."""

    enabled: bool = field(default=False, metadata=toggle("Tilt"))
    degrees: float = field(default=-6.0, metadata=number("Tilt angle", -45.0, 45.0, 0.5, "deg"))

    def __post_init__(self) -> None:
        require_valid(self)

    def problems(self) -> list[str]:
        return range_problems(self)


@dataclass(frozen=True, slots=True)
class Silhouette:
    """Fill the subject with one colour, keeping its exact alpha (the "next rank" teaser)."""

    enabled: bool = field(default=False, metadata=toggle("Silhouette"))
    color: HexColor = field(default_factory=_black, metadata=colour("Silhouette colour"))


@dataclass(frozen=True, slots=True)
class Enhancement:
    """Optional treatments applied to a CLEAN cutout. All disabled = no effect at all."""

    glow: Glow = field(default_factory=Glow)
    shadow: Shadow = field(default_factory=Shadow)
    outline: Outline = field(default_factory=Outline)
    rim: RimLight = field(default_factory=RimLight)
    contrast: Contrast = field(default_factory=Contrast)
    tilt: Tilt = field(default_factory=Tilt)
    silhouette: Silhouette = field(default_factory=Silhouette)
    #: Fully transparent pixels kept between any effect and the canvas edge.
    border: int = field(default=2, metadata=number("Edge clearance", 0, 64, 1, "px", advanced=True))

    def __post_init__(self) -> None:
        require_valid(self)

    def problems(self) -> list[str]:
        return range_problems(self)

    def applied(self) -> tuple[str, ...]:
        """Names of the enabled effects, in the order they are applied."""
        order = ("silhouette", "contrast", "rim", "tilt", "outline", "glow", "shadow")
        return tuple(name for name in order if getattr(self, name).enabled)

    @property
    def extent(self) -> int:
        """Pixels the effects reach beyond the (tilted) silhouette, clearance included."""
        return self.outline.extent + max(self.glow.extent, self.shadow.extent) + self.border

    def to_config(self) -> dict[str, JsonValue]:
        """Canonical identity: only enabled effects appear (disabled leftovers never split it)."""
        config: dict[str, JsonValue] = {}
        for name in self.applied():
            effect = getattr(self, name)
            config[name] = {
                f.name: _plain(getattr(effect, f.name))
                for f in fields(effect)
                if f.name != "enabled"
            }
        if config:
            config["border"] = self.border
        return config


class VariantName(StrEnum):
    """What a request can ask for. ``CLEAN`` is always produced."""

    CLEAN = "clean"
    STICKER = "sticker"
    SILHOUETTE = "silhouette"
    GLOW = "glow"
    SHADOW = "shadow"
    OUTLINE = "outline"


@dataclass(frozen=True, slots=True)
class Variants:
    """The enhanced presets. Each enables only what its name says."""

    sticker: Enhancement = field(
        default_factory=lambda: Enhancement(
            glow=Glow(enabled=True, color=_white(), opacity=0.95, radius=8.0, spread=6.0),
            tilt=Tilt(enabled=True, degrees=-6.0),
        )
    )
    silhouette: Enhancement = field(
        default_factory=lambda: Enhancement(
            silhouette=Silhouette(enabled=True),
            glow=Glow(enabled=True, color=_white(), opacity=0.9, radius=10.0, spread=4.0),
        )
    )
    glow: Enhancement = field(
        default_factory=lambda: Enhancement(
            glow=Glow(enabled=True, color=_white(), opacity=0.6, radius=24.0, spread=6.0)
        )
    )
    shadow: Enhancement = field(default_factory=lambda: Enhancement(shadow=Shadow(enabled=True)))
    outline: Enhancement = field(default_factory=lambda: Enhancement(outline=Outline(enabled=True)))

    def of(self, name: VariantName) -> Enhancement | None:
        """The preset of an enhanced variant, ``None`` for the clean cutout."""
        return None if name is VariantName.CLEAN else getattr(self, name.value)


@dataclass(frozen=True, slots=True)
class CutoutDefaults:
    """How the clean cutout is made (its identity is ``AdjustmentSettings.to_config``)."""

    background_removal: BackgroundRemoval = field(default_factory=BackgroundRemoval)
    canvas: Canvas = field(default_factory=Canvas)
    mask_limits: MaskLimits = field(default_factory=MaskLimits)

    def settings(self) -> AdjustmentSettings:
        """The clean cutout: never an effect, so it is the same asset ``ImageAdjuster`` makes."""
        return AdjustmentSettings(
            background_removal=self.background_removal,
            glow=Glow(enabled=False),
            canvas=self.canvas,
            mask_limits=self.mask_limits,
        )


@dataclass(frozen=True, slots=True)
class QcThresholds:
    """When a produced image is reported. They only judge; they never change pixels."""

    max_rim_deviation: float = field(
        default=0.15,
        metadata=number(
            "Edge halo tolerance",
            0.01,
            1.0,
            0.01,
            "fraction",
            "Mean colour difference between the soft edge and the subject next to it.",
            advanced=True,
        ),
    )
    max_residue_ratio: float = field(
        default=0.001,
        metadata=number(
            "Leftover background tolerance",
            0.0,
            0.1,
            0.0005,
            "fraction",
            "Share of faint pixels detached from the subject.",
            advanced=True,
        ),
    )
    upscale_warning: float = field(
        default=1.5,
        metadata=number(
            "Upscale warning", 1.0, 8.0, 0.1, "factor", "Enlarging more looks soft.", advanced=True
        ),
    )
    min_source_edge: int = field(
        default=320,
        metadata=number(
            "Smallest source side",
            16,
            4096,
            1,
            "px",
            "Smaller sources are reported.",
            advanced=True,
        ),
    )

    def __post_init__(self) -> None:
        require_valid(self)

    def problems(self) -> list[str]:
        return range_problems(self)


@dataclass(frozen=True, slots=True)
class SelectionSettings:
    """How existing library images are matched to a concept (no model involved)."""

    approved_tag: str = field(
        default="approved", metadata={"label": "Approved tag", "help": "Marks approved images."}
    )
    brand_tag: str = field(
        default="brand", metadata={"label": "Brand tag", "help": "Marks brand assets."}
    )
    min_score: float = field(
        default=0.5,
        metadata=number("Minimum match", 0.05, 1.0, 0.05, "fraction", "Share of concept words."),
    )
    max_candidates: int = field(default=10, metadata=number("Candidates", 1, 100, 1, "count"))

    def __post_init__(self) -> None:
        require_valid(self)

    def problems(self) -> list[str]:
        problems = range_problems(self)
        if not self.approved_tag.strip() or not self.brand_tag.strip():
            problems.append("tags must not be empty")
        return problems


@dataclass(frozen=True, slots=True)
class VisualAssetProfile:
    name: str
    description: str
    version: int = 1
    cutout: CutoutDefaults = field(default_factory=CutoutDefaults)
    variants: Variants = field(default_factory=Variants)
    qc: QcThresholds = field(default_factory=QcThresholds)
    selection: SelectionSettings = field(default_factory=SelectionSettings)

    def problems(self) -> list[str]:
        """Cross-field checks (each group already refused its own out-of-range values)."""
        problems: list[str] = []
        canvas = self.cutout.canvas
        for name in VariantName:
            preset = self.variants.of(name)
            if preset is not None and 2 * preset.extent >= min(canvas.width, canvas.height) - 16:
                problems.append(f"the {name.value} effects leave no room on the canvas")
        return problems


PROFILES: Final[dict[str, VisualAssetProfile]] = {
    p.name: p
    for p in (
        VisualAssetProfile(
            name=DEFAULT_PROFILE,
            description=(
                "Clean 1080x1080 cutouts; restrained presets: sticker = 6 px white glow + 6 deg "
                "tilt (channel discovery: 6-10 px, 5-10 deg), silhouette = black fill + white glow."
            ),
        ),
    )
}


@dataclass(frozen=True, slots=True)
class VisualAssetConfig:
    """Profile name + overrides by dotted path (``{"variants.sticker.glow.radius": 10}``)."""

    profile: str = DEFAULT_PROFILE
    overrides: Mapping[str, JsonValue] = field(default_factory=dict)

    def resolve(self, identity: Mapping[str, JsonValue] | None = None) -> VisualAssetProfile:
        """Profile defaults, then the channel identity's values, then the overrides.

        Raises ``InvalidVisualAssetConfig`` for an unknown profile or setting, a wrong type or a
        value the setting refuses.
        """
        base = PROFILES.get(self.profile)
        if base is None:
            raise InvalidVisualAssetConfig(
                f"unknown profile {self.profile!r}; choose one of {', '.join(sorted(PROFILES))}"
            )
        profile: Any = base
        for layer in (identity or {}, self.overrides):
            for path, value in sorted(layer.items()):
                profile = _override(profile, path.split("."), value, path)
        problems = profile.problems()
        if problems:
            raise InvalidVisualAssetConfig("; ".join(problems))
        resolved: VisualAssetProfile = profile
        return resolved


def fingerprint(profile: VisualAssetProfile) -> dict[str, JsonValue]:
    """Everything the profile decides, as plain JSON (metadata is not part of it)."""
    return {f.name: _plain(getattr(profile, f.name)) for f in fields(profile)}


def _plain(value: object) -> JsonValue:
    if isinstance(value, HexColor):
        return value.value
    if isinstance(value, Enum):
        return str(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    raise TypeError(f"not JSON describable: {value!r}")


def _override(target: Any, parts: list[str], value: JsonValue, path: str) -> Any:
    head, rest = parts[0], parts[1:]
    if not is_dataclass(target) or isinstance(target, HexColor):
        raise InvalidVisualAssetConfig(f"unknown setting {path!r}")
    if head not in {f.name for f in fields(target)} or head in {"name", "version"}:
        raise InvalidVisualAssetConfig(f"unknown setting {path!r}")
    current = getattr(target, head)
    new = _override(current, rest, value, path) if rest else _coerce(current, value, path)
    instance: Any = target
    try:
        return replace(instance, **{head: new})
    except InvariantViolation as exc:
        raise InvalidVisualAssetConfig(f"{path}: {exc}") from exc


def _coerce(current: object, value: JsonValue, path: str) -> object:
    """``value`` as the type of the setting it replaces (int is accepted for float settings)."""
    try:
        if isinstance(current, HexColor) and isinstance(value, str):
            return HexColor.of(value)
        if isinstance(current, Enum) and isinstance(value, str):
            return type(current)(value)
    except (InvariantViolation, ValueError) as exc:
        raise InvalidVisualAssetConfig(f"{path}: {value!r} is not allowed") from exc
    if is_dataclass(current):
        raise InvalidVisualAssetConfig(f"{path!r} is a group of settings, not a single value")
    if isinstance(current, bool):
        ok = isinstance(value, bool)
    elif isinstance(current, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    elif isinstance(current, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(current))
    if not ok:
        raise InvalidVisualAssetConfig(f"{path}: expected {type(current).__name__}, got {value!r}")
    return float(value) if isinstance(current, float) else value  # type: ignore[arg-type]
