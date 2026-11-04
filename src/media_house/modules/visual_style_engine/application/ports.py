"""What the style engine needs from the outside world (implemented in ``infrastructure``)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from media_house.modules.visual_style_engine.domain.errors import FontUnavailable
from media_house.modules.visual_style_engine.domain.model import FontFace
from media_house.shared.errors import Result


@dataclass(frozen=True, slots=True)
class FontFile:
    """A verified local font file. Use the path, never store it (tokens keep ``resource``)."""

    face: str
    path: Path
    sha256: str


class FontFiles(Protocol):
    """Bundled font files by face reference."""

    def locate(self, face: FontFace) -> Result[FontFile, FontUnavailable]:
        """The local file of ``face``; ``Err`` if it is missing or its sha256 differs."""
        ...

    def missing_glyphs(self, face: FontFace, text: str) -> Result[str, FontUnavailable]:
        """The characters of ``text`` the font has no glyph for ("" = full coverage)."""
        ...
