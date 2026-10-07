"""Colour vocabulary: which transfer function and primaries a signal uses.

This module only NAMES colour spaces; the maths lives in ``infrastructure/color_science.py`` (a
test guarantees every name here has an implementation there). ``UNKNOWN`` is a valid state: it is
never silently replaced by a guess, and the colour stage does not run on it.

Pipeline (the same for every camera; only the input differs):

    source encoding -> [input transform] -> LINEAR REC.709 working space -> corrections and
    rendering -> [output transform] -> Rec.709 delivery encoding

Linear Rec.709 is the working space because delivery is Rec.709: grading operations (exposure,
white balance, saturation) are physically meaningful on linear light, and a wider space such as
ACEScg would add a gamut conversion without a benefit until wide-gamut delivery exists. The
working space is a single named constant so it can evolve without touching the stages.
"""

from dataclasses import dataclass
from enum import StrEnum

WORKING_SPACE = "linear_rec709"


class Transfer(StrEnum):
    UNKNOWN = "unknown"
    BT709 = "bt709"  # display-referred Rec.709 video, decoded with the BT.1886 power 2.4
    SRGB = "srgb"
    GOPRO_PROTUNE = "gopro_protune"  # GoPro Protune "Flat": y = log(112 x + 1) / log(113)
    SLOG3 = "slog3"  # Sony S-Log3
    VLOG = "vlog"  # Panasonic V-Log
    HLG = "hlg"  # HDR: not graded
    PQ = "pq"  # HDR: not graded


class Primaries(StrEnum):
    UNKNOWN = "unknown"
    BT709 = "bt709"
    BT2020 = "bt2020"
    SGAMUT3_CINE = "sgamut3_cine"
    VGAMUT = "vgamut"


_SCENE_REFERRED = frozenset({Transfer.GOPRO_PROTUNE, Transfer.SLOG3, Transfer.VLOG})
_HDR = frozenset({Transfer.HLG, Transfer.PQ})


@dataclass(frozen=True, slots=True)
class ColorSpec:
    """The colour encoding of a video signal."""

    transfer: Transfer = Transfer.UNKNOWN
    primaries: Primaries = Primaries.UNKNOWN

    @property
    def known(self) -> bool:
        return self.transfer is not Transfer.UNKNOWN and self.primaries is not Primaries.UNKNOWN

    @property
    def is_hdr(self) -> bool:
        return self.transfer in _HDR

    @property
    def scene_referred(self) -> bool:
        """Log or flat footage that needs a rendering to look right on a display."""
        return self.transfer in _SCENE_REFERRED

    def __str__(self) -> str:
        return f"{self.transfer.value}/{self.primaries.value}"


#: What every improved video is delivered as.
OUTPUT_COLOR = ColorSpec(Transfer.BT709, Primaries.BT709)

_TRANSFER_TAGS = {
    "bt709": Transfer.BT709,
    "iec61966-2-1": Transfer.SRGB,
    "smpte2084": Transfer.PQ,
    "arib-std-b67": Transfer.HLG,
}
_PRIMARIES_TAGS = {"bt709": Primaries.BT709, "bt2020": Primaries.BT2020}


def color_from_tags(transfer: str | None, primaries: str | None) -> ColorSpec:
    """The colour space a file DECLARES with its transfer and primaries tags (``unknown`` if
    a tag is missing or not one this module knows)."""
    return ColorSpec(
        _TRANSFER_TAGS.get((transfer or "").lower(), Transfer.UNKNOWN),
        _PRIMARIES_TAGS.get((primaries or "").lower(), Primaries.UNKNOWN),
    )
