"""Advance widths of a TrueType/OpenType font (``head``, ``hhea``, ``hmtx``, ``cmap`` 4/12),
read with the standard library: the layout measures text with the font the renderer draws."""

import struct
from pathlib import Path

from media_house.modules.motion_graphics_engine.domain.model import FaceMetrics

#: Code points measured: Latin (incl. German) plus typographic punctuation and the euro sign.
_CODEPOINTS = (
    *range(0x20, 0x250),
    0x1E9E, 0x2013, 0x2014, 0x2018, 0x2019, 0x201A, 0x201C, 0x201D, 0x201E, 0x2022, 0x2026,
    0x20AC, 0x2122,
)  # fmt: skip


class SfntFontMetrics:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, Path], FaceMetrics] = {}

    def metrics(self, key: str, path: Path) -> FaceMetrics:
        found = self._cache.get((key, path))
        if found is None:
            found = self._cache[(key, path)] = read_metrics(key, path.read_bytes())
        return found


def read_metrics(key: str, font: bytes) -> FaceMetrics:
    tables = _tables(font)
    head, hhea, hmtx = tables[b"head"], tables[b"hhea"], tables[b"hmtx"]
    units_per_em = struct.unpack_from(">H", font, head + 18)[0]
    ascender, descender = struct.unpack_from(">hh", font, hhea + 4)
    metrics_count = struct.unpack_from(">H", font, hhea + 34)[0]
    colour = b"COLR" in tables or b"CBDT" in tables  # an emoji face: measure every symbol
    glyphs = _glyph_ids(font, tables[b"cmap"], every=colour)

    def advance(glyph: int) -> int:
        index = min(glyph, metrics_count - 1)
        width: int = struct.unpack_from(">H", font, hmtx + 4 * index)[0]
        return width

    advances = tuple((cp, advance(glyphs[cp])) for cp in _CODEPOINTS if glyphs.get(cp))
    default = max((w for _, w in advances), default=units_per_em)
    if colour:
        advances += tuple(
            (cp, advance(glyph)) for cp, glyph in sorted(glyphs.items()) if cp > 0x2122 and glyph
        )
    return FaceMetrics(
        key=key,
        units_per_em=units_per_em,
        ascender=ascender,
        descender=descender,
        advances=advances,
        default_advance=default,
    )


def _tables(font: bytes) -> dict[bytes, int]:
    count = struct.unpack_from(">H", font, 4)[0]
    found: dict[bytes, int] = {}
    for i in range(count):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", font, 12 + 16 * i)
        found[tag] = offset
    return found


def _glyph_ids(font: bytes, cmap: int, *, every: bool = False) -> dict[int, int]:
    count = struct.unpack_from(">H", font, cmap + 2)[0]
    subtables: dict[tuple[int, int], int] = {}
    for i in range(count):
        platform, encoding, offset = struct.unpack_from(">HHI", font, cmap + 4 + 8 * i)
        subtables[(platform, encoding)] = cmap + offset
    for wanted in ((3, 10), (0, 4), (3, 1), (0, 3)):
        start = subtables.get(wanted)
        if start is None:
            continue
        fmt = struct.unpack_from(">H", font, start)[0]
        if fmt == 12:
            return _format12(font, start, every=every)
        if fmt == 4:
            return _format4(font, start)
    raise ValueError("no Unicode cmap of format 4 or 12")


def _format12(font: bytes, start: int, *, every: bool = False) -> dict[int, int]:
    groups = struct.unpack_from(">I", font, start + 12)[0]
    found: dict[int, int] = {}
    wanted = set(_CODEPOINTS)
    for i in range(groups):
        first, last, glyph = struct.unpack_from(">III", font, start + 16 + 12 * i)
        for cp in range(first, last + 1) if every else wanted:
            if first <= cp <= last:
                found[cp] = glyph + cp - first
    return found


def _format4(font: bytes, start: int) -> dict[int, int]:
    segments = struct.unpack_from(">H", font, start + 6)[0] // 2
    ends = start + 14
    starts = ends + 2 * segments + 2
    deltas = starts + 2 * segments
    ranges = deltas + 2 * segments
    found: dict[int, int] = {}
    for cp in _CODEPOINTS:
        for i in range(segments):
            end = struct.unpack_from(">H", font, ends + 2 * i)[0]
            if cp > end:
                continue
            first = struct.unpack_from(">H", font, starts + 2 * i)[0]
            if cp < first:
                break
            delta = struct.unpack_from(">h", font, deltas + 2 * i)[0]
            range_offset = struct.unpack_from(">H", font, ranges + 2 * i)[0]
            if range_offset == 0:
                glyph = (cp + delta) & 0xFFFF
            else:
                address = ranges + 2 * i + range_offset + 2 * (cp - first)
                glyph = struct.unpack_from(">H", font, address)[0]
                glyph = (glyph + delta) & 0xFFFF if glyph else 0
            if glyph:
                found[cp] = glyph
            break
    return found
