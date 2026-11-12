"""Step 4 of the wizard: the material plan, ingestion, flags and the recording guide."""

from dataclasses import dataclass, replace
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.modules.production_project.application.ports import (
    EvidenceSource,
    ProjectFolder,
    ScannedFile,
)
from media_house.modules.production_project.application.projects import ProjectCatalog
from media_house.modules.production_project.application.views import (
    rows_of,
    rules_of,
    spoken_script,
)
from media_house.modules.production_project.domain.assets import (
    AssetFlag,
    ValidationContext,
    settle,
    usable,
    validate_asset,
)
from media_house.modules.production_project.domain.classify import (
    FileEvidence,
    assign_slot,
    classify,
)
from media_house.modules.production_project.domain.conform import (
    Conformance,
    TranscriptLine,
    conform,
)
from media_house.modules.production_project.domain.documents import Document, recording_guide
from media_house.modules.production_project.domain.manifest import Manifest, ManifestEntry
from media_house.modules.production_project.domain.material import (
    AssetRequirement,
    PlanInputs,
    plan_materials,
)
from media_house.modules.production_project.domain.naming import (
    natural_key,
    normalize,
    target_name,
)
from media_house.modules.production_project.domain.project import Project, clean
from media_house.modules.production_project.domain.reconcile import (
    AssignedFile,
    Reconciliation,
    reconcile,
)
from media_house.modules.production_project.domain.values import (
    Artifact,
    FlagKind,
    MultiFile,
    Resolution,
    Role,
    SlotStatus,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    ConflictError,
    Err,
    NotFoundError,
    Ok,
    Result,
    ValidationError,
)

_Failure = NotFoundError | ConflictError | ValidationError


class PlanMaterial:
    """The approved script as the fewest files that still work, in a recording order."""

    def __init__(
        self, projects: ProjectCatalog, clock: Clock, brand_known: frozenset[str] = frozenset()
    ) -> None:
        self._projects = projects
        self._clock = clock
        self._brand = brand_known

    def execute(self, project_id: str) -> Result[Project, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if project.brief is None or not project.script:
            return Err(
                ValidationError(
                    "no script", user_message="Zuerst Briefing einlesen und das Skript erzeugen."
                )
            )
        plan = plan_materials(
            PlanInputs(
                project.settings.slug,
                spoken_script(project),
                project.brief,
                rules_of(project),
                rows_of(project),
                off_blocks=frozenset(project.off_blocks),
                approvals=frozenset(project.approvals),
                known_brand_assets=self._brand,
            )
        )
        now = self._clock.now()

        def apply(p: Project) -> Project:
            return clean(replace(p, requirements=plan), Artifact.REQUIREMENTS, now=now)

        return self._projects.update(project_id, apply, audit=("plan_material", str(len(plan))))


@dataclass(frozen=True, slots=True)
class IngestReport:
    project: Project
    reconciliation: Reconciliation
    ignored: tuple[str, ...]
    flagged: int


class IngestFiles:
    """Scan a folder (nothing is moved), classify every file by content, name it, check it, lay
    the recordings onto the script's scenes, and say what is still missing."""

    def __init__(
        self,
        projects: ProjectCatalog,
        folder: ProjectFolder,
        evidence: EvidenceSource,
        clock: Clock,
    ) -> None:
        self._projects = projects
        self._folder = folder
        self._evidence = evidence
        self._clock = clock

    def execute(
        self, project_id: str, root: Path, ctx: JobContext
    ) -> Result[IngestReport, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if not project.requirements or project.brief is None:
            return Err(ValidationError("no plan", user_message="Erst den Materialplan erstellen."))
        scan = self._folder.scan(root)
        if isinstance(scan, Err):
            return scan
        script = spoken_script(project)
        vctx = ValidationContext(
            rules_of(project),
            project.brief,
            allowed_brands=frozenset(b.lower() for b in project.settings.allowed_brands),
            approvals=frozenset(project.approvals),
        )
        files = sorted(scan.value.files, key=lambda f: (natural_key(f.name), f.name))
        built: list[
            tuple[ScannedFile, FileEvidence, Role | None, str, str, tuple[AssetFlag, ...]]
        ] = []
        for i, f in enumerate(files, 1):
            ctx.raise_if_cancelled()
            ctx.progress.report(i, len(files), f.name)
            sha = self._folder.sha256(f.path)
            ev = self._evidence.evidence(f, sha, ctx)
            cls = classify(ev)
            slot, why = ("", "")
            flags: tuple[AssetFlag, ...] = ()
            if cls.role is not None:
                slot, why = assign_slot(ev, cls.role, project.requirements)
                req = next((r for r in project.requirements if r.requirement_id == slot), None)
                flags = validate_asset(ev, cls.role, req, vctx)
            built.append((f, ev, cls.role, slot, why, flags))
        talking = [f for f, _, role, slot, _, _ in built if slot == "talking"]
        lines: list[TranscriptLine] = []
        for f in talking:
            lines.extend(self._evidence.transcript(f))
        order = [f.name for f in talking]
        conformance = conform(script, lines, order) if lines else None
        entries, assigned = self._entries(project, built, conformance)
        rec = reconcile(project.requirements, assigned, script, conformance, order)
        manifest = Manifest(project_id, project.settings.slug, tuple(entries), scan.value.ignored)
        now = self._clock.now()

        def apply(p: Project) -> Project:
            return replace(p, manifest=manifest, requirements=rec.requirements, updated_at=now)

        saved = self._projects.update(
            project_id,
            apply,
            audit=("ingest", f"{len(entries)} Dateien, {len(manifest.flagged())} markiert"),
        )
        if isinstance(saved, Err):
            return saved
        return Ok(IngestReport(saved.value, rec, scan.value.ignored, len(manifest.flagged())))

    def _entries(
        self,
        project: Project,
        built: list[tuple[ScannedFile, FileEvidence, Role | None, str, str, tuple[AssetFlag, ...]]],
        conformance: Conformance | None,
    ) -> tuple[list[ManifestEntry], list[AssignedFile]]:
        counters: dict[str, int] = {}
        entries: list[ManifestEntry] = []
        assigned: list[AssignedFile] = []
        by_id = {r.requirement_id: r for r in project.requirements}
        scenes_of: dict[str, set[str]] = {}
        if conformance:
            for m in conformance.matches:
                if m.scene_id:
                    scenes_of.setdefault(m.line.asset_id, set()).add(m.scene_id)
        for f, ev, role, slot, _why, flags in built:
            req = by_id.get(slot)
            norm = normalize(f.name, is_vector=ev.is_vector)
            ext = norm.convert_to or norm.ext
            if role is None or req is None:
                name = f"unsorted/{f.name}"
            else:
                counters[slot] = counters.get(slot, 0) + 1
                name = (
                    f"{req.folder}/{_project_name(project.settings.slug, req, counters[slot], ext)}"
                )
            scenes = tuple(sorted(scenes_of.get(f.name, set()))) or (req.scene_ids if req else ())
            entries.append(
                ManifestEntry(
                    file=name,
                    original_name=f.name,
                    sha256=ev.sha256,
                    role=role.value if role else "",
                    slot=slot,
                    scenes=scenes,
                    probe=_probe(ev),
                    asr_excerpt=ev.transcript_excerpt[:200],
                    flags=flags,
                    license_state=_license(flags, role),
                    verification_state="checked" if usable(flags) else "unchecked",
                    asset_id=self._evidence.asset_id(f),
                    normalization=norm.reason,
                    source_path=str(f.path),
                )
            )
            if role is not None:
                assigned.append(
                    AssignedFile(
                        name, slot, role, self._evidence.asset_id(f), flagged=not usable(flags)
                    )
                )
        return entries, assigned


def _project_name(slug: str, req: AssetRequirement, n: int, ext: str) -> str:
    if "{nn}" in req.target_name_pattern or req.multi_file is MultiFile.ORDERED_CHUNKS:
        return target_name(slug, req.role, index=n, label="", ext=ext)
    parts = req.target_name_pattern.rsplit(".", 1)[0].split("_", 2)
    label = parts[2] if len(parts) == 3 else ""
    suffix = "" if n == 1 else f"-{n}"
    return target_name(slug, req.role, index=None, label=label + suffix, ext=ext)


def _probe(ev: FileEvidence) -> tuple[tuple[str, str], ...]:
    pairs = [
        ("width", ev.width),
        ("height", ev.height),
        ("fps", ev.fps),
        ("duration", ev.duration),
        ("has_audio", ev.has_audio),
    ]
    return tuple((k, str(v)) for k, v in pairs if v not in (0, 0.0, None))


def _license(flags: tuple[AssetFlag, ...], role: Role | None) -> str:
    if any(f.kind is FlagKind.LICENSE_UNKNOWN for f in flags):
        approved = all(
            f.resolution is not Resolution.OPEN for f in flags if f.kind is FlagKind.LICENSE_UNKNOWN
        )
        return "licensed" if approved else "unknown"
    return "own" if role is not None else "unknown"


class SettleFlag:
    """ "Freigabe liegt vor", "verpixeln" or "ersetzen": a person settles a flag; only then may
    the edit engine use the file."""

    def __init__(self, projects: ProjectCatalog, clock: Clock) -> None:
        self._projects = projects
        self._clock = clock

    def execute(
        self, project_id: str, file: str, kind: FlagKind, how: Resolution, note: str
    ) -> Result[Project, _Failure]:
        if how is Resolution.OPEN:
            return Err(ValidationError("open is not a decision", user_message="Wähle eine Lösung."))
        if how is Resolution.APPROVED and not note.strip():
            return Err(
                ValidationError(
                    "an approval needs a note",
                    user_message="Schreib kurz dazu, woher die Freigabe kommt.",
                )
            )
        now = self._clock.now()

        def apply(p: Project) -> Project:
            if p.manifest is None:
                return p
            entries = tuple(
                replace(e, flags=settle(e.flags, kind, how, note)) if e.file == file else e
                for e in p.manifest.entries
            )
            manifest = replace(p.manifest, entries=entries)
            return replace(
                p, manifest=manifest, requirements=_statuses(p, manifest), updated_at=now
            )

        return self._projects.update(
            project_id, apply, audit=("settle_flag", f"{file}: {kind.value}={how.value}")
        )


def _statuses(project: Project, manifest: Manifest) -> tuple[AssetRequirement, ...]:
    out: list[AssetRequirement] = []
    for r in project.requirements:
        mine = manifest.by_slot(r.requirement_id)
        if not mine:
            out.append(r)
        elif all(not e.usable for e in mine):
            out.append(replace(r, status=SlotStatus.FLAGGED))
        elif r.status is SlotStatus.FLAGGED:
            out.append(replace(r, status=SlotStatus.FILLED))
        else:
            out.append(r)
    return tuple(out)


class BuildRecordingGuide:
    def __init__(self, projects: ProjectCatalog) -> None:
        self._projects = projects

    def execute(self, project_id: str) -> Result[Document, NotFoundError | ValidationError]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if not project.requirements:
            return Err(ValidationError("no plan", user_message="Erst den Materialplan erstellen."))
        return Ok(
            recording_guide(project.requirements, spoken_script(project), project.settings.slug)
        )
