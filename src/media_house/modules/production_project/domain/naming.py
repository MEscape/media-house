"""File names: natural order, the project scheme, awkward formats.

Scheme: ``{slug}_{role}[_{nn}][_{label}].{ext}``: lowercase, hyphens, no umlauts or spaces, a
zero-padded index where files of one role are ordered. The original name stays in the manifest.
File dates are never used for order: cameras report 2016.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from media_house.modules.production_project.domain.numbers import slugify
from media_house.modules.production_project.domain.values import Role

_DIGITS = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple[tuple[int, int, str], ...]:
    """``talking_2`` sorts before ``talking_10``; case is ignored; ties keep the original text."""
    parts = _DIGITS.split(name.casefold())
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts if p != "")


def natural_sorted(names: Iterable[str]) -> list[str]:
    return sorted(names, key=lambda n: (natural_key(n), n))


def target_name(slug: str, role: Role, *, index: int | None, label: str, ext: str) -> str:
    """The project name of a file."""
    parts = [slugify(slug), role.value]
    if index is not None:
        parts.append(f"{index:02d}")
    if label.strip():
        parts.append(slugify(label))
    return "_".join(p for p in parts if p) + "." + ext.lower().lstrip(".")


@dataclass(frozen=True, slots=True)
class Normalization:
    """What a file needs before the edit engine takes it: its stem, extension and conversion."""

    stem: str
    ext: str
    #: The extension to convert to ("" = keep the file as it is).
    convert_to: str
    #: The reason, for the manifest.
    reason: str = ""


#: Extensions the edit engine cannot place and the format they become.
_CONVERT = {"webp": "png", "avif": "png", "heic": "jpg", "heif": "jpg", "bmp": "png", "gif": "png"}


def normalize(filename: str, *, is_vector: bool = False) -> Normalization:
    """``logo.svg.webp`` is a raster picture named like a vector: it becomes a PNG (with its
    transparency). A real SVG (``is_vector``) is kept."""
    name = filename.strip()
    stem, _, ext = name.rpartition(".")
    if not stem:
        return Normalization(name, "", "")
    ext = ext.lower()
    inner = stem.rpartition(".")
    if inner[1] and inner[2].lower() in {"svg", "png", "jpg", "jpeg", "mp4", "mov"}:
        # a double extension: the last one is the real format
        stem = inner[0]
        reason = f"Doppelte Endung .{inner[2].lower()}.{ext}: tatsächliches Format ist {ext}"
    else:
        reason = ""
    if ext == "svg" and is_vector:
        return Normalization(stem, "svg", "", reason)
    target = _CONVERT.get(ext, "")
    return Normalization(stem, ext, target, reason or (f"{ext} wird zu {target}" if target else ""))
