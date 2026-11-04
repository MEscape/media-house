"""The bundled font files (SIL OFL 1.1, see each folder's OFL.txt) and their glyph coverage.

Files live next to this module in ``fonts/<family>/``. A face is located by its ``resource`` and
accepted only when the bytes match the identity's recorded sha256. Glyph coverage is read from
the font's ``cmap`` table with the standard library (format 4 and 12), so no font library is
needed at run time.
"""

import hashlib
import struct
from pathlib import Path

from media_house.modules.visual_style_engine.application.ports import FontFile
from media_house.modules.visual_style_engine.domain.errors import FontUnavailable
from media_house.modules.visual_style_engine.domain.model import FontFace
from media_house.shared.errors import Err, Ok, Result

FONT_DIR = Path(__file__).resolve().parent / "fonts"
#: Emoji presentation selectors and the joiner select or join glyphs; they have none of their own.
_NOT_GLYPHS = frozenset("︎️‍")


class PackagedFontFiles:
    def __init__(self, root: Path = FONT_DIR) -> None:
        self._root = root
        self._verified: dict[str, FontFile] = {}

    def locate(self, face: FontFace) -> Result[FontFile, FontUnavailable]:
        known = self._verified.get(face.key)
        if known is not None:
            return Ok(known)
        path = self._root / face.resource
        try:
            data = path.read_bytes()
        except OSError as exc:
            return Err(FontUnavailable(face.key, f"{face.resource} cannot be read ({exc})"))
        sha = hashlib.sha256(data).hexdigest()
        if sha != face.sha256:
            return Err(FontUnavailable(face.key, f"{face.resource} has sha256 {sha[:12]}..."))
        found = self._verified[face.key] = FontFile(face=face.key, path=path, sha256=sha)
        return Ok(found)

    def missing_glyphs(self, face: FontFace, text: str) -> Result[str, FontUnavailable]:
        located = self.locate(face)
        if isinstance(located, Err):
            return located
        try:
            covered = covered_codepoints(located.value.path.read_bytes(), {ord(c) for c in text})
        except (OSError, ValueError, struct.error) as exc:
            return Err(FontUnavailable(face.key, f"unreadable cmap ({exc})"))
        return Ok(
            "".join(
                sorted({c for c in text if ord(c) not in covered and not c.isspace()} - _NOT_GLYPHS)
            )
        )


def covered_codepoints(font: bytes, wanted: set[int]) -> set[int]:
    """The members of ``wanted`` that map to a real glyph in a TrueType/OpenType font."""
    tables = _tables(font)
    if b"cmap" not in tables:
        raise ValueError("no cmap table")
    cmap = tables[b"cmap"]
    count = struct.unpack_from(">H", font, cmap + 2)[0]
    subtables: dict[tuple[int, int], int] = {}
    for i in range(count):
        platform, encoding, offset = struct.unpack_from(">HHI", font, cmap + 4 + 8 * i)
        subtables[(platform, encoding)] = cmap + offset
    for key in ((3, 10), (0, 4), (3, 1), (0, 3)):
        if key in subtables:
            start = subtables[key]
            fmt = struct.unpack_from(">H", font, start)[0]
            if fmt == 12:
                return _format12(font, start, wanted)
            if fmt == 4:
                return _format4(font, start, wanted)
    raise ValueError("no Unicode cmap of format 4 or 12")


def _tables(font: bytes) -> dict[bytes, int]:
    count = struct.unpack_from(">H", font, 4)[0]
    found: dict[bytes, int] = {}
    for i in range(count):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", font, 12 + 16 * i)
        found[tag] = offset
    return found


def _format12(font: bytes, start: int, wanted: set[int]) -> set[int]:
    groups = struct.unpack_from(">I", font, start + 12)[0]
    covered: set[int] = set()
    for i in range(groups):
        first, last, glyph = struct.unpack_from(">III", font, start + 16 + 12 * i)
        covered |= {c for c in wanted if first <= c <= last and glyph + c - first != 0}
    return covered


def _format4(font: bytes, start: int, wanted: set[int]) -> set[int]:
    segments = struct.unpack_from(">H", font, start + 6)[0] // 2
    ends = start + 14
    starts = ends + 2 * segments + 2
    deltas = starts + 2 * segments
    ranges = deltas + 2 * segments
    covered: set[int] = set()
    for c in wanted:
        if c > 0xFFFF:
            continue
        for i in range(segments):
            end = struct.unpack_from(">H", font, ends + 2 * i)[0]
            if c > end:
                continue
            first = struct.unpack_from(">H", font, starts + 2 * i)[0]
            if c < first:
                break
            delta = struct.unpack_from(">h", font, deltas + 2 * i)[0]
            range_offset = struct.unpack_from(">H", font, ranges + 2 * i)[0]
            if range_offset == 0:
                glyph = (c + delta) & 0xFFFF
            else:
                address = ranges + 2 * i + range_offset + 2 * (c - first)
                glyph = struct.unpack_from(">H", font, address)[0]
                glyph = (glyph + delta) & 0xFFFF if glyph else 0
            if glyph:
                covered.add(c)
            break
    return covered
