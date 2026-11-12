"""Fakes for the production workflow: an in-memory project store, a scripted language model and
an evidence source that serves synthetic evidence (no client material anywhere)."""

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from media_house.core.domain import json_codec
from media_house.modules.production_project.application.ports import (
    AuditEntry,
    ProjectSummary,
    ScannedFile,
)
from media_house.modules.production_project.domain.classify import FileEvidence
from media_house.modules.production_project.domain.compliance import TimelineFacts
from media_house.modules.production_project.domain.conform import TranscriptLine
from media_house.modules.production_project.domain.project import Project
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import ConflictError, Err, NotFoundError, Ok, Result, ValidationError


class InMemoryProjectStore:
    """Round-trips every project through the strict JSON codec, like the real store."""

    def __init__(self) -> None:
        self._docs: dict[str, str] = {}
        self._audit: dict[str, list[AuditEntry]] = {}

    def _dump(self, project: Project) -> str:
        return json_codec.dumps("production_project", project)

    def _load(self, text: str) -> Project:
        raw = json.loads(text)
        raw.pop("document_type")
        project: Project = json_codec.decode(Project, raw)
        return project

    def create(self, project: Project) -> None:
        if project.project_id in self._docs:
            raise ConflictError("exists")
        self._docs[project.project_id] = self._dump(project)

    def get(self, project_id: str) -> Project | None:
        text = self._docs.get(project_id)
        return None if text is None else self._load(text)

    def save(self, project: Project, *, expected_revision: int) -> int:
        current = self.get(project.project_id)
        if current is None or current.revision != expected_revision:
            raise ConflictError("stale revision")
        revision = expected_revision + 1
        self._docs[project.project_id] = self._dump(replace(project, revision=revision))
        return revision

    def list(self) -> Sequence[ProjectSummary]:
        out = []
        for text in self._docs.values():
            p = self._load(text)
            out.append(
                ProjectSummary(
                    p.project_id, p.settings.title, p.settings.client, p.updated_at, p.revision
                )
            )
        return out

    def delete(self, project_id: str) -> None:
        self._docs.pop(project_id, None)

    def append_audit(self, project_id: str, entry: AuditEntry) -> None:
        self._audit.setdefault(project_id, []).append(entry)

    def audit(self, project_id: str) -> Sequence[AuditEntry]:
        return list(self._audit.get(project_id, []))


class ScriptedGenerator:
    """A ``TextGenerator`` that answers from a queue (the last answer repeats)."""

    def __init__(
        self, answers: Sequence[str], *, identity: str = "local-test", why: str | None = None
    ) -> None:
        self._answers = list(answers)
        self._identity = identity
        self._why = why
        self.prompts: list[tuple[str, str]] = []

    def identity(self) -> str:
        return self._identity

    def unavailable(self) -> str | None:
        return self._why

    def generate(self, system: str, user: str, cancellation: CancellationToken) -> str:
        self.prompts.append((system, user))
        return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]


class FakeEvidence:
    """Serves prepared evidence by file name; talking files speak the lines they were given."""

    def __init__(
        self, evidence: dict[str, FileEvidence], lines: dict[str, tuple[TranscriptLine, ...]]
    ) -> None:
        self._evidence = evidence
        self._lines = lines

    def evidence(self, file: ScannedFile, sha256: str, ctx: JobContext) -> FileEvidence:
        found = self._evidence.get(file.name)
        base = found or FileEvidence(file.name, "other")
        return replace(base, sha256=sha256, folder=file.folder, size_bytes=file.size_bytes)

    def transcript(self, file: ScannedFile) -> tuple[TranscriptLine, ...]:
        return self._lines.get(file.name, ())

    def asset_id(self, file: ScannedFile) -> str:
        return f"asset-{file.name}"


class FakeRunner:
    """A pipeline run that only remembers what it was given and answers with an anchor."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[tuple[Path, ...], str, str]] = []
        self._fail = fail

    def run(
        self, paths: Sequence[Path], context_text: str, output_format: str, ctx: JobContext
    ) -> Result[str, ValidationError]:
        self.calls.append((tuple(paths), context_text, output_format))
        if self._fail:
            return Err(ValidationError("boom", user_message="Der Testlauf ist fehlgeschlagen."))
        return Ok(f"anchor-{len(self.calls)}")


class FakeTimelines:
    """Timeline facts per anchor asset; an unknown anchor is not found."""

    def __init__(self, facts: dict[str, TimelineFacts]) -> None:
        self._facts = facts

    def facts(self, anchor_asset_id: str) -> Result[TimelineFacts, NotFoundError]:
        found = self._facts.get(anchor_asset_id)
        if found is None:
            return Err(NotFoundError("none", user_message="Keine Timeline."))
        return Ok(found)
