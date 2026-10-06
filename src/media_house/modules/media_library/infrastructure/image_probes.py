"""Content-based format probes for common image formats (PNG, JPEG, WebP).

Pure standard library: a probe reads only headers/trailers, so ingesting a 8K image never
decodes pixels. Probes decide by *magic bytes*, never by file extension, and a probe that
recognises its format but finds it damaged raises ``InvalidMedia``.

To support a new format (audio, video, ...) implement :class:`FormatProbe` and add it to the
list given to ``FileMediaInspector``; nothing else changes.
"""

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol

from media_house.modules.media_library.domain.errors import InvalidMedia
from media_house.modules.media_library.domain.values import JsonObject, MediaType


@dataclass(frozen=True, slots=True)
class ProbeResult:
    media_type: MediaType
    mime_type: str
    extension: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    technical: JsonObject = field(default_factory=dict)


class FormatProbe(Protocol):
    def probe(self, path: Path, file_size: int) -> ProbeResult | None:
        """``None`` when the file is not this format; ``InvalidMedia`` when it is but is damaged."""
        ...


def _damaged(fmt: str, why: str) -> InvalidMedia:
    return InvalidMedia(
        f"Damaged {fmt} file: {why}",
        user_message=f"This {fmt} file looks damaged or incomplete.",
    )


# --------------------------------------------------------------------------- PNG
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_IEND_TAIL = b"\x00\x00\x00\x00IEND\xaeB`\x82"
_PNG_MODES = {0: "L", 2: "RGB", 3: "P", 4: "LA", 6: "RGBA"}


class PngProbe:
    def probe(self, path: Path, file_size: int) -> ProbeResult | None:
        try:
            with path.open("rb") as f:
                if f.read(8) != _PNG_SIGNATURE:
                    return None
                return self._parse(f, file_size)
        except (OSError, struct.error) as exc:
            raise _damaged("PNG", str(exc)) from exc

    @staticmethod
    def _parse(f: BinaryIO, file_size: int) -> ProbeResult:
        header = f.read(8)
        if len(header) < 8 or header[4:] != b"IHDR" or struct.unpack(">I", header[:4])[0] != 13:
            raise _damaged("PNG", "missing header chunk")
        data, crc = f.read(13), f.read(4)
        if (
            len(data) != 13
            or len(crc) != 4
            or zlib.crc32(b"IHDR" + data) != struct.unpack(">I", crc)[0]
        ):
            raise _damaged("PNG", "header checksum mismatch")
        width, height, bit_depth, color_type = struct.unpack(">IIBB", data[:10])
        if width == 0 or height == 0 or color_type not in _PNG_MODES:
            raise _damaged("PNG", "invalid header values")
        has_alpha = color_type in {4, 6}
        while True:  # walk chunk headers up to the pixel data; only tRNS matters here
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                raise _damaged("PNG", "truncated")
            length, chunk_type = struct.unpack(">I4s", chunk_header)
            if chunk_type == b"tRNS":
                has_alpha = True
            if chunk_type in {b"IDAT", b"IEND"}:
                break
            f.seek(length + 4, 1)
        if file_size < 45:
            raise _damaged("PNG", "too small")
        f.seek(-12, 2)
        if f.read(12) != _PNG_IEND_TAIL:
            raise _damaged("PNG", "truncated (missing end marker)")
        return ProbeResult(
            MediaType.IMAGE,
            "image/png",
            ".png",
            width,
            height,
            technical={
                "format": "PNG",
                "color_mode": _PNG_MODES[color_type],
                "has_alpha": has_alpha,
                "bit_depth": bit_depth,
            },
        )


# --------------------------------------------------------------------------- JPEG
_JPEG_MODES = {1: "L", 3: "RGB", 4: "CMYK"}
_NON_SOF = {0xC4, 0xC8, 0xCC}


class JpegProbe:
    def probe(self, path: Path, file_size: int) -> ProbeResult | None:
        try:
            with path.open("rb") as f:
                if f.read(2) != b"\xff\xd8":
                    return None
                return self._parse(f, file_size)
        except (OSError, struct.error) as exc:
            raise _damaged("JPEG", str(exc)) from exc

    @staticmethod
    def _parse(f: BinaryIO, file_size: int) -> ProbeResult:
        while True:
            byte = f.read(1)
            if byte != b"\xff":
                raise _damaged("JPEG", "marker expected")
            while byte == b"\xff":  # fill bytes
                byte = f.read(1)
            if not byte:
                raise _damaged("JPEG", "truncated")
            marker = byte[0]
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:
                continue  # standalone markers carry no length
            if marker in {0xD9, 0xDA}:
                raise _damaged("JPEG", "no frame header")
            length = struct.unpack(">H", f.read(2))[0]
            if length < 2:
                raise _damaged("JPEG", "invalid segment length")
            if 0xC0 <= marker <= 0xCF and marker not in _NON_SOF:
                _precision, height, width, components = struct.unpack(">BHHB", f.read(6))
                break
            f.seek(length - 2, 1)
        if width == 0 or height == 0:
            raise _damaged("JPEG", "invalid dimensions")
        f.seek(max(0, file_size - 4096))
        if b"\xff\xd9" not in f.read():
            raise _damaged("JPEG", "truncated (missing end marker)")
        return ProbeResult(
            MediaType.IMAGE,
            "image/jpeg",
            ".jpg",
            width,
            height,
            technical={
                "format": "JPEG",
                "color_mode": _JPEG_MODES.get(components, f"{components}ch"),
                "has_alpha": False,
                "progressive": marker in {0xC2, 0xC6, 0xCA, 0xCE},
            },
        )


# --------------------------------------------------------------------------- WebP
class WebpProbe:
    def probe(self, path: Path, file_size: int) -> ProbeResult | None:
        try:
            with path.open("rb") as f:
                head = f.read(12)
                if head[:4] != b"RIFF" or head[8:12] != b"WEBP":
                    return None
                if struct.unpack("<I", head[4:8])[0] + 8 > file_size:
                    raise _damaged("WebP", "truncated")
                return self._parse(f)
        except (OSError, struct.error) as exc:
            raise _damaged("WebP", str(exc)) from exc

    @staticmethod
    def _parse(f: BinaryIO) -> ProbeResult:
        fourcc = f.read(8)[:4]
        technical: dict[str, object]
        if fourcc == b"VP8 ":
            data = f.read(10)
            if len(data) < 10 or data[3:6] != b"\x9d\x01\x2a":
                raise _damaged("WebP", "invalid lossy header")
            width = struct.unpack("<H", data[6:8])[0] & 0x3FFF
            height = struct.unpack("<H", data[8:10])[0] & 0x3FFF
            technical = {"color_mode": "RGB", "has_alpha": False, "lossless": False}
        elif fourcc == b"VP8L":
            data = f.read(5)
            if len(data) < 5 or data[0] != 0x2F:
                raise _damaged("WebP", "invalid lossless header")
            bits = struct.unpack("<I", data[1:5])[0]
            width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            alpha = bool((bits >> 28) & 1)
            technical = {
                "color_mode": "RGBA" if alpha else "RGB",
                "has_alpha": alpha,
                "lossless": True,
            }
        elif fourcc == b"VP8X":
            data = f.read(10)
            if len(data) < 10:
                raise _damaged("WebP", "invalid extended header")
            alpha = bool(data[0] & 0x10)
            width = int.from_bytes(data[4:7], "little") + 1
            height = int.from_bytes(data[7:10], "little") + 1
            technical = {
                "color_mode": "RGBA" if alpha else "RGB",
                "has_alpha": alpha,
                "animated": bool(data[0] & 0x02),
            }
        else:
            raise _damaged("WebP", "unknown encoding")
        if width == 0 or height == 0:
            raise _damaged("WebP", "invalid dimensions")
        return ProbeResult(
            MediaType.IMAGE,
            "image/webp",
            ".webp",
            width,
            height,
            technical={"format": "WEBP", **technical},  # type: ignore[arg-type]
        )


def default_image_probes() -> tuple[FormatProbe, ...]:
    return (PngProbe(), JpegProbe(), WebpProbe())
