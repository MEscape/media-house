"""Text of a brief document: plain text, Word, PDF, or a picture through OCR when available.

Pages are joined with a form feed so the brief model can name the page of every item. A PDF is
read with ``pypdf`` when installed, else with a small reader for text PDFs; a scanned PDF or a
picture needs the optional OCR extra, and the answer says so instead of returning nothing.
"""

import contextlib
import importlib
import re
import zipfile
import zlib
from pathlib import Path

from media_house.shared.errors import Err, Ok, Result, ValidationError

_TEXT = frozenset({".txt", ".md", ".text"})
_IMAGES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})
_PAGE = "\f"
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_TEXT_OPS = re.compile(rb"\((?:\\.|[^\\()])*\)\s*Tj|\[(?:\\.|[^\]])*\]\s*TJ|T\*|ET")
_STRING = re.compile(rb"\(((?:\\.|[^\\()])*)\)")


class DocumentBriefReader:
    """Structurally implements ``BriefReader``."""

    def read(self, path: Path) -> Result[str, ValidationError]:
        suffix = path.suffix.casefold()
        try:
            if suffix in _TEXT:
                text = path.read_text(encoding="utf-8", errors="replace")
            elif suffix == ".docx":
                text = _docx(path)
            elif suffix == ".pdf":
                text = _pdf(path)
            elif suffix in _IMAGES:
                text = _ocr(path)
            else:
                return Err(
                    ValidationError(
                        f"Unsupported brief file {suffix}",
                        user_message="Dieses Dateiformat kann ich nicht lesen (PDF, Word, Text).",
                    )
                )
        except (OSError, zipfile.BadZipFile, zlib.error) as exc:
            return Err(
                ValidationError(
                    f"Could not read {path.name}: {exc}",
                    user_message=f"Die Datei {path.name} lässt sich nicht lesen.",
                )
            )
        except _Unreadable as exc:
            return Err(ValidationError(str(exc), user_message=str(exc)))
        if not text.strip():
            return Err(
                ValidationError(
                    "no text found",
                    user_message="In der Datei steht kein lesbarer Text. Füge ihn als Text ein.",
                )
            )
        return Ok(text)


class _Unreadable(Exception):
    pass


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.DOTALL)
    out = []
    for p in paragraphs:
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.DOTALL)
        out.append(_unescape("".join(runs)))
    return "\n".join(out)


def _unescape(text: str) -> str:
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )


def _pdf(path: Path) -> str:
    try:
        pypdf = importlib.import_module("pypdf")
    except ImportError:
        pypdf = None
    if pypdf is not None:
        try:
            reader = pypdf.PdfReader(str(path))
            return _PAGE.join(
                str(page.extract_text(extraction_mode="layout") or "") for page in reader.pages
            )
        except Exception as exc:
            # PDF libraries expose several unrelated parser/encryption error classes.
            raise _Unreadable(
                "Dieses PDF ist beschädigt oder geschützt. Exportiere eine lesbare Kopie "
                "oder füge den Briefingtext ein."
            ) from exc
    text = _simple_pdf(path.read_bytes())
    if not text.strip():
        raise _Unreadable(
            "Dieses PDF lässt sich ohne Zusatzpaket nicht lesen. Installiere „pypdf“ "
            "(uv sync --extra documents) oder füge den Text ein."
        )
    return text


def _simple_pdf(data: bytes) -> str:
    """Text of simple PDFs: Flate or plain content streams with Tj/TJ operators. Pages are
    separated by the ``/Type /Page`` order of the streams; embedded-font PDFs need pypdf."""
    pages: list[str] = []
    for match in _STREAM.finditer(data):
        raw = match.group(1)
        with contextlib.suppress(zlib.error):
            raw = zlib.decompress(raw)
        if b"BT" not in raw:
            continue
        lines: list[str] = []
        current: list[str] = []
        for op in _TEXT_OPS.finditer(raw):
            token = op.group(0)
            if token in (b"T*", b"ET"):
                if current:
                    lines.append("".join(current))
                    current = []
                continue
            for part in _STRING.findall(token):
                current.append(_pdf_string(part))
        if current:
            lines.append("".join(current))
        if lines:
            pages.append("\n".join(lines))
    return _PAGE.join(pages)


def _pdf_string(raw: bytes) -> str:
    text = re.sub(
        rb"\\([nrtbf()\\])",
        lambda m: {b"n": b"\n", b"r": b"", b"t": b" "}.get(m.group(1), m.group(1)),
        raw,
    )
    text = re.sub(rb"\\(\d{3})", lambda m: bytes([int(m.group(1), 8)]), text)
    return text.decode("cp1252", errors="replace")


def _ocr(path: Path) -> str:
    try:
        module = importlib.import_module("rapidocr")
    except ImportError as exc:
        raise _Unreadable(
            "Für Bilder braucht es die OCR-Erweiterung (uv sync --extra vision). "
            "Alternativ den Text einfügen."
        ) from exc
    engine = module.RapidOCR()
    result = engine(str(path))
    texts = getattr(result, "txts", None) or ()
    return "\n".join(str(t) for t in texts)
