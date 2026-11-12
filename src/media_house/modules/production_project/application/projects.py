"""The project catalogue: create, read and change a project with optimistic concurrency.

Every step of the wizard changes a project through ``update``: it reads the newest revision,
applies a pure function and saves; if somebody saved in between, the step is applied again to
the newer project (so two panels never overwrite each other).
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import replace

from media_house.core.application.ports import Clock
from media_house.modules.production_project.application.ports import (
    AuditEntry,
    ProjectStore,
    ProjectSummary,
)
from media_house.modules.production_project.domain.numbers import slugify
from media_house.modules.production_project.domain.project import Project, ProjectSettings
from media_house.shared.errors import (
    ConflictError,
    Err,
    NotFoundError,
    Ok,
    Result,
    ValidationError,
)
from media_house.shared.types import new_id

_RETRIES = 3
type Change = Callable[[Project], Project]


class ProjectCatalog:
    def __init__(self, store: ProjectStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def create(self, settings: ProjectSettings) -> Result[Project, ValidationError]:
        slug = slugify(settings.slug or settings.title)
        if not settings.title.strip() or not slug or not re.fullmatch(r"[a-z0-9-]+", slug):
            return Err(
                ValidationError(
                    "A project needs a title",
                    field="title",
                    user_message="Gib dem Projekt einen Namen.",
                )
            )
        now = self._clock.now()
        project = Project(
            project_id=new_id(),
            settings=replace(settings, slug=slug),
            revision=1,
            created_at=now,
            updated_at=now,
        )
        self._store.create(project)
        self._store.append_audit(project.project_id, AuditEntry(now, "create", settings.title))
        return Ok(project)

    def get(self, project_id: str) -> Result[Project, NotFoundError]:
        project = self._store.get(project_id)
        if project is None:
            return Err(
                NotFoundError(
                    f"No project {project_id}", user_message="Dieses Projekt gibt es nicht (mehr)."
                )
            )
        return Ok(project)

    def list(self) -> Sequence[ProjectSummary]:
        return self._store.list()

    def update(
        self, project_id: str, change: Change, *, audit: tuple[str, str] | None = None
    ) -> Result[Project, NotFoundError | ConflictError]:
        for _ in range(_RETRIES):
            found = self.get(project_id)
            if isinstance(found, Err):
                return found
            current = found.value
            changed = change(current)
            try:
                revision = self._store.save(changed, expected_revision=current.revision)
            except ConflictError:
                continue
            if audit:
                self._store.append_audit(project_id, AuditEntry(self._clock.now(), *audit))
            return Ok(replace(changed, revision=revision))
        return Err(
            ConflictError(
                "project changed while saving",
                user_message="Das Projekt wurde gleichzeitig geändert. Bitte nochmal versuchen.",
            )
        )

    def delete(self, project_id: str) -> None:
        self._store.delete(project_id)

    def audit(self, project_id: str) -> Sequence[AuditEntry]:
        return self._store.audit(project_id)

    def note(self, project_id: str, action: str, detail: str) -> None:
        self._store.append_audit(project_id, AuditEntry(self._clock.now(), action, detail))
