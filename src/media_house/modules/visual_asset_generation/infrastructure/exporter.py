"""Delivery copies: PNG / WebP / JPEG, fitted (never enlarged), alpha kept or refused explicitly."""

from pathlib import Path

from PIL import Image, ImageOps

from media_house.modules.visual_asset_generation.application.ports import ExportReport
from media_house.modules.visual_asset_generation.domain.errors import ExportRefused, ImageUnreadable
from media_house.modules.visual_asset_generation.domain.requests import ExportSettings, ImageFormat

_MAX_ASSET_PIXELS = 200_000_000


class PillowImageExporter:
    """Implements ``ImageExporter``. Resampling is premultiplied (no dark fringe at the edge)."""

    def transparency(self, source: Path) -> bool:
        return _transparent(_open(source))

    def export(self, source: Path, destination: Path, settings: ExportSettings) -> ExportReport:
        image = _open(source)
        transparent = _transparent(image)
        if transparent and not settings.format.keeps_alpha and settings.flatten_onto is None:
            raise ExportRefused(
                f"{settings.format.value} cannot keep transparency; name a background colour "
                "(flatten_onto) to flatten it explicitly, or export PNG/WebP"
            )
        original = image.size
        size = _fitted(original, settings)
        if size != original:
            image = image.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")
        if settings.format is ImageFormat.JPEG:
            if transparent and settings.flatten_onto is not None:
                ground = Image.new("RGBA", image.size, (*settings.flatten_onto.rgb, 255))
                image = Image.alpha_composite(ground, image)
            image.convert("RGB").save(
                destination, "JPEG", quality=settings.quality, subsampling=0, optimize=True
            )
        elif settings.format is ImageFormat.WEBP:
            options: dict[str, object] = {"method": 6, "exact": True}
            if settings.lossless:
                options |= {"lossless": True, "quality": 100}
            else:
                options |= {"quality": settings.quality}
            image.save(destination, "WEBP", **options)
        else:
            image.save(destination, "PNG", optimize=True)
        return ExportReport(
            width=size[0],
            height=size[1],
            kept_size=size == original,
            source_has_transparency=transparent,
        )


def _open(path: Path) -> Image.Image:
    try:
        with Image.open(path) as opened:
            if opened.width * opened.height > _MAX_ASSET_PIXELS:
                raise ImageUnreadable("image is too large")
            opened.load()
            image = ImageOps.exif_transpose(opened)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageUnreadable(str(exc)) from exc
    return image.convert("RGBA")


def _transparent(image: Image.Image) -> bool:
    return image.getchannel("A").getextrema()[0] != 255


def _fitted(size: tuple[int, int], settings: ExportSettings) -> tuple[int, int]:
    width, height = size
    limits = [
        limit / edge
        for limit, edge in ((settings.max_width, width), (settings.max_height, height))
        if limit > 0
    ]
    scale = min([1.0, *limits])
    if scale >= 1.0:
        return size
    return max(1, round(width * scale)), max(1, round(height * scale))
