"""The production project's adapters: the SQLite store behaves like the fake, the folder never
touches originals, documents are real Word and PDF files, the brief reader reads Word and PDF."""

import sqlite3
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from media_house.modules.production_project.application.ports import AuditEntry, ProjectStore
from media_house.modules.production_project.domain.documents import (
    Block,
    BlockKind,
    Document,
)
from media_house.modules.production_project.domain.project import Project, ProjectSettings
from media_house.modules.production_project.infrastructure.brief_reader import DocumentBriefReader
from media_house.modules.production_project.infrastructure.documents import FileDocumentExporter
from media_house.modules.production_project.infrastructure.folder import LocalProjectFolder
from media_house.modules.production_project.infrastructure.sqlite_store import SqliteProjectStore
from media_house.shared.errors import ConflictError, Err, Ok, PersistenceError
from tests.support.fakes import T0
from tests.unit.production_project.fakes import InMemoryProjectStore

pytestmark = pytest.mark.integration

Factory = Callable[[], ProjectStore]


@pytest.fixture(params=["in_memory", "sqlite"])
def make_store(request: pytest.FixtureRequest, tmp_path: Path) -> Factory:
    if request.param == "in_memory":
        shared = InMemoryProjectStore()
        return lambda: shared
    return lambda: SqliteProjectStore(tmp_path / "db" / "projects.db")


def new(project_id: str = "p1") -> Project:
    return Project(
        project_id, ProjectSettings("lidlnico", "Lidl", "Lidl", "ranking", 2026), 1, T0, T0
    )


def test_create_get_save_list_delete(make_store: Factory) -> None:
    store = make_store()
    project = new()
    store.create(project)

    assert store.get("p1") == project
    assert store.save(replace(project, ideas="neu"), expected_revision=1) == 2
    loaded = store.get("p1")
    assert loaded is not None and loaded.ideas == "neu" and loaded.revision == 2
    assert [s.project_id for s in store.list()] == ["p1"]
    store.delete("p1")
    assert store.get("p1") is None


def test_a_stale_write_is_a_conflict_and_a_duplicate_too(make_store: Factory) -> None:
    store = make_store()
    store.create(new())
    store.save(new(), expected_revision=1)

    with pytest.raises(ConflictError):
        store.save(new(), expected_revision=1)
    with pytest.raises(ConflictError):
        store.create(new())


def test_the_audit_log_is_append_only_and_ordered(make_store: Factory) -> None:
    store = make_store()
    store.create(new())
    store.append_audit("p1", AuditEntry(T0, "a", "eins"))
    store.append_audit("p1", AuditEntry(T0, "b", "zwei"))

    assert [e.action for e in store.audit("p1")] == ["a", "b"]


def test_a_corrupt_document_is_a_persistence_error(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    store = SqliteProjectStore(path)
    store.create(new())
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE projects SET document = '{\"settings\": 1}'")
        conn.commit()

    with pytest.raises(PersistenceError):
        store.get("p1")


def test_scanning_ignores_thumbnails_and_junk_and_never_loads_files_into_memory(
    tmp_path: Path,
) -> None:
    (tmp_path / "01_cam").mkdir()
    (tmp_path / "01_cam" / "a.mp4").write_bytes(b"a" * 10)
    (tmp_path / "01_cam" / "Thumbs.db").write_bytes(b"x")
    (tmp_path / "07_thumb").mkdir()
    (tmp_path / "07_thumb" / "t.png").write_bytes(b"x")
    (tmp_path / "02_scr").mkdir()
    (tmp_path / "loose.txt").write_text("hi", encoding="utf-8")
    folder = LocalProjectFolder()

    scan = folder.scan(tmp_path)

    assert isinstance(scan, Ok)
    assert sorted((f.name, f.folder) for f in scan.value.files) == [
        ("a.mp4", "01_cam"),
        ("loose.txt", ""),
    ]
    assert any("07_thumb" in i for i in scan.value.ignored) and any(
        "02_scr (leer)" in i for i in scan.value.ignored
    )
    assert isinstance(folder.scan(tmp_path / "missing"), Err)
    import hashlib

    assert folder.sha256(tmp_path / "01_cam" / "a.mp4") == hashlib.sha256(b"a" * 10).hexdigest()


def test_placing_links_or_copies_converts_and_leaves_the_original_alone(tmp_path: Path) -> None:
    from PIL import Image

    src = tmp_path / "src"
    src.mkdir()
    (src / "clip.mp4").write_bytes(b"video")
    Image.new("RGBA", (4, 4), (1, 2, 3, 100)).save(src / "logo.svg.webp", format="WEBP")
    folder = LocalProjectFolder()
    root = tmp_path / "project"

    video = folder.place(src / "clip.mp4", root, "01_cam/x_talking_01.mp4", "")
    logo = folder.place(src / "logo.svg.webp", root, "06_gfx/x_logo.png", "png")

    assert video.read_bytes() == b"video" and (src / "clip.mp4").read_bytes() == b"video"
    assert logo.read_bytes()[:4] == b"\x89PNG"
    with Image.open(logo) as image:
        assert image.mode == "RGBA"  # transparency kept
    assert (src / "logo.svg.webp").exists()
    with pytest.raises(Exception, match=r"outside|escape|traversal|root"):
        folder.place(src / "clip.mp4", root, "../escape.mp4", "")


def test_documents_are_real_markdown_word_and_pdf_files(tmp_path: Path) -> None:
    doc = Document(
        "Kurzkonzept",
        (
            Block(BlockKind.H1, "Kurzkonzept: Test"),
            Block(BlockKind.PARAGRAPH, "Ärger über (Klammern) & Umlaute: äöüß €"),
            Block(BlockKind.BULLETS, items=("eins", "zwei")),
            Block(BlockKind.TABLE, header=("A", "B"), rows=(("1", "2"), ("3", "x | y"))),
        ),
    )
    exporter = FileDocumentExporter()

    md = exporter.export(doc, tmp_path / "k.md", "md")
    docx = exporter.export(doc, tmp_path / "k.docx", "docx")
    pdf = exporter.export(doc, tmp_path / "k.pdf", "pdf")

    assert "| 3 | x / y |" in md.read_text(encoding="utf-8")
    with zipfile.ZipFile(docx) as z:
        assert z.testzip() is None
        xml = z.read("word/document.xml").decode("utf-8")
        assert (
            "Ärger über (Klammern) &amp; Umlaute" in xml and "<w:tbl>" in xml and "Heading1" in xml
        )
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF") and b"/Count 1" in data
    assert b"Kurzkonzept: Test" in data


def test_the_brief_reader_reads_text_word_and_simple_pdf(tmp_path: Path) -> None:
    reader = DocumentBriefReader()
    (tmp_path / "b.txt").write_text("Das Video muss Werbung sein.", encoding="utf-8")
    exporter = FileDocumentExporter()
    doc = Document(
        "B",
        (Block(BlockKind.H1, "Briefing"), Block(BlockKind.PARAGRAPH, "Es gibt keine Vergleiche.")),
    )
    exporter.export(doc, tmp_path / "b.docx", "docx")
    exporter.export(doc, tmp_path / "b.pdf", "pdf")

    assert reader.read(tmp_path / "b.txt") == Ok("Das Video muss Werbung sein.")
    word = reader.read(tmp_path / "b.docx")
    assert isinstance(word, Ok) and "Es gibt keine Vergleiche." in word.value
    pdf = reader.read(tmp_path / "b.pdf")
    assert isinstance(pdf, Ok) and "Es gibt keine Vergleiche." in pdf.value
    assert isinstance(reader.read(tmp_path / "missing.pdf"), Err)
    (tmp_path / "x.xyz").write_text("x", encoding="utf-8")
    assert isinstance(reader.read(tmp_path / "x.xyz"), Err)
    (tmp_path / "empty.txt").write_text("  ", encoding="utf-8")
    assert isinstance(reader.read(tmp_path / "empty.txt"), Err)
