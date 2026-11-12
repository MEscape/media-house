# ruff: noqa: E501  (the Office XML package parts are single long literals)
"""Write a ``Document`` as Markdown, Word (.docx) or PDF with the standard library only.

Word: a minimal Office Open XML package (headings, paragraphs, bullet lines, tables). PDF: A4
pages in Helvetica with automatic wrapping. Both are plain and printable on purpose; the
content is what matters and it stays editable as Markdown or Word.
"""

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from media_house.modules.production_project.domain.documents import (
    Block,
    BlockKind,
    Document,
    to_markdown,
)
from media_house.shared.errors import PersistenceError, ValidationError

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)
_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _style(style_id: str, name: str, size: int, bold: bool) -> str:
    b = "<w:b/>" if bold else ""
    return (
        f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>'
        f'<w:pPr><w:spacing w:before="200" w:after="80"/></w:pPr>'
        f'<w:rPr>{b}<w:sz w:val="{size}"/></w:rPr></w:style>'
    )


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f"<w:styles {_NS}>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:style>'
    + _style("Heading1", "heading 1", 40, True)
    + _style("Heading2", "heading 2", 30, True)
    + _style("Heading3", "heading 3", 26, True)
    + "</w:styles>"
)


class FileDocumentExporter:
    """Structurally implements ``DocumentExporter``."""

    def export(self, document: Document, target: Path, fmt: str) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            match fmt:
                case "md":
                    target.write_text(to_markdown(document), encoding="utf-8")
                case "docx":
                    _write_docx(document, target)
                case "pdf":
                    target.write_bytes(_pdf(document))
                case _:
                    raise ValidationError(
                        f"Unknown format {fmt}", user_message="Unbekanntes Format."
                    )
        except OSError as exc:
            raise PersistenceError(f"Could not write {target.name}: {exc}") from exc
        return target


# --- Word -------------------------------------------------------------------------------------


def _para(text: str, style: str = "") -> str:
    pr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{pr}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _cell(text: str, bold: bool = False) -> str:
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return (
        '<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr><w:p><w:r>'
        f'{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p></w:tc>'
    )


def _table(block: Block) -> str:
    borders = "".join(
        f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
        for s in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    rows = [
        "<w:tr>" + "".join(_cell(h, True) for h in block.header) + "</w:tr>",
        *("<w:tr>" + "".join(_cell(c) for c in row) + "</w:tr>" for row in block.rows),
    ]
    return (
        f"<w:tbl><w:tblPr><w:tblBorders>{borders}</w:tblBorders></w:tblPr>{''.join(rows)}</w:tbl>"
    )


def _body(document: Document) -> str:
    parts: list[str] = [_para(document.title, "Heading1")] if not _has_title(document) else []
    for b in document.blocks:
        match b.kind:
            case BlockKind.H1:
                parts.append(_para(b.text, "Heading1"))
            case BlockKind.H2:
                parts.append(_para(b.text, "Heading2"))
            case BlockKind.H3:
                parts.append(_para(b.text, "Heading3"))
            case BlockKind.BULLETS:
                parts.extend(_para(f"• {item}") for item in b.items)
            case BlockKind.TABLE:
                parts.append(_table(b))
                parts.append(_para(""))
            case _:
                parts.append(_para(b.text))
    return "".join(parts)


def _has_title(document: Document) -> bool:
    return bool(document.blocks) and document.blocks[0].kind is BlockKind.H1


def _write_docx(document: Document, target: Path) -> None:
    body = _body(document)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_NS}><w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", xml)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/styles.xml", _STYLES)


# --- PDF --------------------------------------------------------------------------------------

_PAGE_W, _PAGE_H, _MARGIN = 595, 842, 56


def _wrap(text: str, size: float, width: float) -> list[str]:
    per_line = max(10, int(width / (size * 0.5)))
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}".strip()
            if len(candidate) > per_line and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


def _pdf_text(text: str) -> str:
    raw = text.encode("cp1252", errors="replace").decode("latin-1")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _layout(document: Document) -> list[list[tuple[float, float, float, bool, str]]]:
    """Pages of (x, y, size, bold, text)."""
    pages: list[list[tuple[float, float, float, bool, str]]] = [[]]
    y: float = _PAGE_H - _MARGIN
    width = _PAGE_W - 2 * _MARGIN

    def put(text: str, size: float, bold: bool = False, indent: float = 0.0) -> None:
        nonlocal y
        for line in _wrap(text, size, width - indent):
            if y < _MARGIN:
                pages.append([])
                y = _PAGE_H - _MARGIN
            pages[-1].append((_MARGIN + indent, y, size, bold, line))
            y -= size * 1.35
        y -= size * 0.25

    if not _has_title(document):
        put(document.title, 20, True)
    for b in document.blocks:
        match b.kind:
            case BlockKind.H1:
                put(b.text, 20, True)
            case BlockKind.H2:
                put(b.text, 15, True)
            case BlockKind.H3:
                put(b.text, 12, True)
            case BlockKind.BULLETS:
                for item in b.items:
                    put(f"- {item}", 10.5, indent=10)
            case BlockKind.TABLE:
                put(" | ".join(b.header), 10, True)
                for row in b.rows:
                    put(" | ".join(row), 10)
            case _:
                put(b.text, 10.5)
    return pages


def _pdf(document: Document) -> bytes:
    pages = _layout(document)
    objects: list[bytes] = []
    n = len(pages)
    # 1 catalog, 2 pages, 3 regular font, 4 bold font, then (content, page) per page.
    kids = " ".join(f"{5 + 2 * i + 1} 0 R" for i in range(n))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    for i, page in enumerate(pages):
        ops = ["BT"]
        for x, y, size, bold, text in page:
            ops.append(
                f"/{'F2' if bold else 'F1'} {size} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm ({_pdf_text(text)}) Tj"
            )
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
            ).encode()
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for k, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)
