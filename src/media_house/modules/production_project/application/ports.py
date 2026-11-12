"""What the production project needs from the outside: storage, files, documents, evidence."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from media_house.modules.production_project.domain.classify import FileEvidence
from media_house.modules.production_project.domain.compliance import TimelineFacts
from media_house.modules.production_project.domain.conform import TranscriptLine
from media_house.modules.production_project.domain.documents import Document
from media_house.modules.production_project.domain.project import Project
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import NotFoundError, Result, ValidationError


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    project_id: str
    title: str
    client: str
    updated_at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class AuditEntry:
    at: datetime
    action: str
    detail: str


class ProjectStore(Protocol):
    """One document per project, written with the revision the caller read (a stale write is a
    ``ConflictError``: the loser applies its step to the newer project)."""

    def create(self, project: Project) -> None: ...
    def get(self, project_id: str) -> Project | None: ...
    def save(self, project: Project, *, expected_revision: int) -> int: ...
    def list(self) -> Sequence[ProjectSummary]: ...
    def delete(self, project_id: str) -> None: ...
    def append_audit(self, project_id: str, entry: AuditEntry) -> None: ...
    def audit(self, project_id: str) -> Sequence[AuditEntry]: ...


@dataclass(frozen=True, slots=True)
class ScannedFile:
    path: Path
    name: str
    #: The numbered folder it lies in (``01_cam``), "" at the root.
    folder: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class FolderScan:
    files: tuple[ScannedFile, ...]
    #: Folders and files looked at and left alone (an empty ``07_thumb``).
    ignored: tuple[str, ...]


class ProjectFolder(Protocol):
    """The project folder on disk. Originals are never moved or changed: files are placed as
    links or copies under their project name."""

    def scan(self, root: Path) -> Result[FolderScan, ValidationError]: ...
    def sha256(self, path: Path) -> str: ...
    def place(self, source: Path, root: Path, relative: str, convert_to: str) -> Path: ...
    def write_text(self, root: Path, relative: str, text: str) -> Path: ...


class BriefReader(Protocol):
    """The text of a brief document (PDF, Word, text, or a picture through OCR)."""

    def read(self, path: Path) -> Result[str, ValidationError]: ...


class DocumentExporter(Protocol):
    def export(self, document: Document, target: Path, fmt: str) -> Path: ...


class EvidenceSource(Protocol):
    """What the analysis modules already know about a file. Nothing here recomputes: a missing
    analysis is simply absent evidence, and the classifier copes with less."""

    def evidence(self, file: ScannedFile, sha256: str, ctx: JobContext) -> FileEvidence: ...
    def transcript(self, file: ScannedFile) -> tuple[TranscriptLine, ...]: ...
    def asset_id(self, file: ScannedFile) -> str: ...


class TimelineSource(Protocol):
    """What a built timeline does, for the timeline QC. Read from the stored build, never
    rebuilt."""

    def facts(self, anchor_asset_id: str) -> Result[TimelineFacts, NotFoundError]: ...


class VariantRunner(Protocol):
    """Runs the editing pipeline once with one context text as background and returns the anchor
    asset of the built timeline. The run is the pipeline's own: nothing is simulated here."""

    def run(
        self, paths: Sequence[Path], context_text: str, output_format: str, ctx: JobContext
    ) -> Result[str, ValidationError]: ...
