"""What a caller asks for: one entry per needed picture, and how to export it."""

from dataclasses import dataclass, field
from enum import StrEnum

from media_house.core.domain import Rational
from media_house.modules.visual_asset_generation.domain.knobs import (
    colour,
    number,
    range_problems,
    require_valid,
    toggle,
)
from media_house.modules.visual_asset_generation.domain.profile import VariantName
from media_house.modules.visual_asset_generation.domain.values import HexColor, JsonValue


class ImageFormat(StrEnum):
    PNG = "png"
    WEBP = "webp"
    JPEG = "jpeg"

    @property
    def keeps_alpha(self) -> bool:
        return self is not ImageFormat.JPEG

    @property
    def pillow_name(self) -> str:
        return self.value.upper()


@dataclass(frozen=True, slots=True)
class ExportSettings:
    """A delivery copy. Never upscales; never drops transparency silently."""

    format: ImageFormat = field(
        default=ImageFormat.PNG,
        metadata={"label": "Format", "choices": tuple(ImageFormat)},
    )
    #: 0 = keep the size. Otherwise the picture is fitted inside, aspect ratio kept.
    max_width: int = field(default=0, metadata=number("Max width", 0, 8192, 1, "px", "0 = keep."))
    max_height: int = field(default=0, metadata=number("Max height", 0, 8192, 1, "px", "0 = keep."))
    quality: int = field(
        default=90, metadata=number("Quality", 1, 100, 1, "percent", "JPEG and lossy WebP.")
    )
    lossless: bool = field(default=True, metadata=toggle("Lossless WebP"))
    #: Required to export a transparent picture as JPEG (explicit flattening, never implicit).
    flatten_onto: HexColor | None = field(
        default=None, metadata=colour("JPEG background", "Needed for transparent pictures.")
    )

    def __post_init__(self) -> None:
        require_valid(self)

    def problems(self) -> list[str]:
        return range_problems(self)

    def to_config(self) -> dict[str, JsonValue]:
        """Identity: only what changes the bytes for this format."""
        config: dict[str, JsonValue] = {
            "format": self.format.value,
            "max_width": self.max_width,
            "max_height": self.max_height,
        }
        lossy = self.format is ImageFormat.JPEG or (
            self.format is ImageFormat.WEBP and not self.lossless
        )
        if self.format is ImageFormat.WEBP:
            config["lossless"] = self.lossless
        if lossy:
            config["quality"] = self.quality
        if self.format is ImageFormat.JPEG and self.flatten_onto is not None:
            config["flatten_onto"] = self.flatten_onto.value
        return config


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetRequest:
    """One picture a caller needs.

    ``source_asset_id`` is a library IMAGE, or a VIDEO together with ``source_time`` (exact
    seconds on the recording's common clock): then the frame at that time is taken first. The
    clean cutout is always produced; ``variants`` adds enhanced versions of it.
    """

    request_id: str
    source_asset_id: str
    source_time: Rational | None = None
    variants: tuple[VariantName, ...] = ()
    #: Free text for people and the document ("product sticker for rank 3").
    purpose: str = ""
    #: A delivery copy of every produced picture (the PNG cutouts stay as they are).
    export: ExportSettings | None = None

    def wanted(self) -> tuple[VariantName, ...]:
        """CLEAN first, then each other variant once, in the order asked."""
        rest = tuple(dict.fromkeys(v for v in self.variants if v is not VariantName.CLEAN))
        return (VariantName.CLEAN, *rest)

    def to_config(self) -> dict[str, JsonValue]:
        return {
            "request_id": self.request_id,
            "source_asset_id": self.source_asset_id,
            "source_time": None if self.source_time is None else str(self.source_time),
            "variants": [v.value for v in self.wanted()],
            "purpose": self.purpose,
            "export": None if self.export is None else self.export.to_config(),
        }
