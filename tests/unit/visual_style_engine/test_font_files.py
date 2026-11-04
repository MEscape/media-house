"""The bundled fonts: pinned by sha256, licensed, and covering German."""

from dataclasses import replace
from pathlib import Path

import pytest

from media_house.modules.visual_style_engine.domain.identity import (
    EMOJI_GLYPHS,
    MESC_IDENTITY_V1,
    MESC_IDENTITY_V2,
)
from media_house.modules.visual_style_engine.infrastructure.font_files import (
    FONT_DIR,
    PackagedFontFiles,
    covered_codepoints,
)
from media_house.shared.errors import Err, Ok

FACES = MESC_IDENTITY_V1.faces
EMOJI = next(f for f in MESC_IDENTITY_V2.faces if f.coverage is not None)


@pytest.mark.parametrize("face", FACES, ids=lambda f: f.key)
def test_every_face_is_bundled_pinned_and_covers_german(face: object) -> None:
    from media_house.modules.visual_style_engine.domain.model import FontFace

    assert isinstance(face, FontFace)
    fonts = PackagedFontFiles()
    located = fonts.locate(face)

    assert isinstance(located, Ok)
    assert located.value.sha256 == face.sha256
    assert (located.value.path.parent / "OFL.txt").is_file()
    assert face.license == "OFL-1.1"
    assert fonts.missing_glyphs(face, MESC_IDENTITY_V1.required_glyphs) == Ok("")


def test_the_emoji_face_is_bundled_pinned_licensed_and_covers_every_planned_emoji() -> None:
    fonts = PackagedFontFiles()
    located = fonts.locate(EMOJI)

    assert isinstance(located, Ok) and located.value.sha256 == EMOJI.sha256
    licence = (located.value.path.parent / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in licence and EMOJI.license == "OFL-1.1"
    # the channel's punchlines (6.5) and caption_engine's vocabulary; FE0F selects, never a glyph
    assert fonts.missing_glyphs(EMOJI, EMOJI_GLYPHS + "😍🤯😱🤔👀⚠️❤️") == Ok("")
    assert fonts.missing_glyphs(EMOJI, "A") == Ok("A")  # emoji only: never a text face


def test_poppins_has_no_capital_sharp_s_which_is_why_it_is_written_ss() -> None:
    poppins = next(f for f in FACES if f.key == "poppins_700")

    assert PackagedFontFiles().missing_glyphs(poppins, "STRAẞE") == Ok("ẞ")


def test_a_changed_or_missing_file_is_unavailable(tmp_path: Path) -> None:
    face = FACES[0]
    (tmp_path / "bangers").mkdir()
    (tmp_path / "bangers" / "Bangers-Regular.ttf").write_bytes(b"not a font")

    changed = PackagedFontFiles(tmp_path).locate(face)
    missing = PackagedFontFiles(tmp_path).locate(replace(face, resource="nope/x.ttf"))

    assert isinstance(changed, Err) and "sha256" in str(changed.error)
    assert isinstance(missing, Err)


def test_the_cmap_reader_agrees_with_fonttools() -> None:
    ttlib = pytest.importorskip("fontTools.ttLib")
    probe = {ord(c) for c in "AZaz09ÄÖÜäöüß€ẞ@#{}~\N{EN DASH}\N{HORIZONTAL ELLIPSIS}"} | {
        0x4E00,
        0x1F600,
        0x1F92F,
        0xFE0F,
    }
    for path in sorted(FONT_DIR.rglob("*.ttf")):
        expected = {c for c in probe if c in ttlib.TTFont(path).getBestCmap()}
        assert covered_codepoints(path.read_bytes(), probe) == expected, path.name
