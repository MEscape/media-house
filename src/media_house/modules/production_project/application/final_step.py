"""Step 5 of the wizard: metadata, the final compliance gate and the export package.

Nothing is exported while a blocker stands. The package holds everything the edit engine and the
people around the video need; the edit plan and the finished timeline come from the pipeline stages
that already own them (the package says how to start them, it does not duplicate them).
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.core.domain import json_codec
from media_house.modules.production_project.application.brief_step import BuildBriefDocuments
from media_house.modules.production_project.application.context_step import pipeline_context
from media_house.modules.production_project.application.material_step import BuildRecordingGuide
from media_house.modules.production_project.application.ports import (
    DocumentExporter,
    ProjectFolder,
    TimelineSource,
)
from media_house.modules.production_project.application.projects import ProjectCatalog
from media_house.modules.production_project.application.views import (
    lengths_of,
    scan_context,
    script_report,
    spoken_script,
    style_facts_of,
    timeline_findings,
)
from media_house.modules.production_project.domain.compliance import (
    Finding,
    Stage,
    apply_overrides,
    blockers,
    lint_metadata,
)
from media_house.modules.production_project.domain.documents import (
    Block,
    BlockKind,
    Document,
    compliance_report,
    to_markdown,
)
from media_house.modules.production_project.domain.material import AssetRequirement
from media_house.modules.production_project.domain.metadata import (
    MetadataInputs,
    YoutubeMetadata,
    build_metadata,
    clock,
)
from media_house.modules.production_project.domain.project import Project, clean
from media_house.modules.production_project.domain.script_tools import render_script
from media_house.modules.production_project.domain.values import (
    Artifact,
    ElementKind,
    Severity,
)
from media_house.modules.style_memory.application.contracts import (
    GuidanceQuery,
    StyleGuidance,
    Task,
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


class BuildMetadata:
    """Title options, description, tags, hashtags and chapters. Mandatory items by rule."""

    def __init__(self, projects: ProjectCatalog, style: StyleGuidance, clock_: Clock) -> None:
        self._projects = projects
        self._style = style
        self._clock = clock_

    def execute(self, project_id: str, *, tracking_link: str = "") -> Result[Project, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if project.brief is None or not project.script:
            return Err(ValidationError("no script", user_message="Erst das Skript erzeugen."))
        guidance = self._style.guidance(
            GuidanceQuery(
                Task.METADATA,
                project.settings.format,
                client=project.settings.client,
                project_id=project_id,
            )
        )
        style = style_facts_of(guidance)
        lengths = lengths_of(project, style)
        starts: dict[str, float] = {}
        at = 0.0
        for item in lengths:
            starts[item.scene_id] = at
            at += item.seconds
        link = tracking_link or project.tracking_link
        meta = build_metadata(
            MetadataInputs(
                project.brief,
                project.facts,
                spoken_script(project),
                (chosen,) if (chosen := project.concept()) else project.concepts,
                starts,
                scan_context(project, style),
                tracking_link=link,
                catchphrase=guidance.profile.catchphrases[0]
                if guidance.profile.catchphrases
                else "",
            )
        )
        now = self._clock.now()

        def apply(p: Project) -> Project:
            return clean(replace(p, metadata=meta, tracking_link=link), Artifact.METADATA, now=now)

        return self._projects.update(
            project_id, apply, audit=("build_metadata", meta.titles[0][:60])
        )


class AttachTimeline:
    """Reads what the built timeline does into the project, so the gate can check it against the
    brief (label for the whole video, approved transitions, flagged assets)."""

    def __init__(self, projects: ProjectCatalog, source: TimelineSource) -> None:
        self._projects = projects
        self._source = source

    def execute(self, project_id: str, anchor_asset_id: str = "") -> Result[Project, _Failure]:
        opened = self._projects.get(project_id)
        if isinstance(opened, Err):
            return opened
        anchor = anchor_asset_id or opened.value.timeline_anchor
        if not anchor:
            return Err(
                ValidationError(
                    "no anchor",
                    user_message="Erst einen Testlauf machen oder die Timeline angeben.",
                )
            )
        facts = self._source.facts(anchor)
        if isinstance(facts, Err):
            return facts
        found = facts.value
        return self._projects.update(
            project_id,
            lambda p: replace(p, timeline=found, timeline_anchor=anchor),
            audit=("attach_timeline", anchor[:60]),
        )


@dataclass(frozen=True, slots=True)
class GateReport:
    findings: tuple[Finding, ...]
    blocking: tuple[Finding, ...]

    @property
    def can_export(self) -> bool:
        return not self.blocking


class CheckGate:
    """Every check in one list: script, files, metadata, timeline. A blocker stops the export."""

    def __init__(self, projects: ProjectCatalog, style: StyleGuidance) -> None:
        self._projects = projects
        self._style = style

    def execute(self, project_id: str) -> Result[GateReport, NotFoundError]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        return Ok(self.report(found.value))

    def report(self, project: Project) -> GateReport:
        guidance = self._style.guidance(
            GuidanceQuery(
                Task.EDIT_PLAN,
                project.settings.format,
                client=project.settings.client,
                project_id=project.project_id,
            )
        )
        style = style_facts_of(guidance)
        findings: list[Finding] = list(script_report(project, style).findings)
        for key, missing, message in (
            ("brief", project.brief is None, "Das Briefing fehlt noch."),
            ("script", not project.script.strip(), "Das Script fehlt noch."),
            (
                "material",
                project.manifest is None or not project.manifest.usable_entries(),
                "Es fehlt geprüftes Material. Verknüpfe deinen Materialordner "
                "und kläre die Markierungen.",
            ),
        ):
            if missing:
                findings.append(
                    Finding(
                        f"project.missing_{key}",
                        f"project.missing_{key}",
                        Stage.ASSET,
                        Severity.BLOCKER,
                        key,
                        "",
                        message,
                    )
                )
        findings += _asset_findings(project)
        brief = project.brief
        if brief is not None and project.metadata is not None:
            meta = project.metadata
            findings += lint_metadata(
                title=meta.titles[0],
                description=meta.description,
                tags=meta.tags,
                brief=brief,
                tracking_link_resolved=bool(project.tracking_link)
                or not any(e.kind is ElementKind.LINK for e in brief.mandatory),
            )
        elif brief is not None:
            findings.append(
                Finding(
                    "metadata.missing",
                    "metadata.missing",
                    Stage.METADATA,
                    Severity.BLOCKER,
                    "metadata",
                    "",
                    "Die Metadaten (Titel, Beschreibung) fehlen noch.",
                    "Metadaten erzeugen.",
                )
            )
        if project.timeline is not None:
            findings += timeline_findings(project, project.timeline, guidance)
        final = apply_overrides(findings, project.overrides)
        return GateReport(final, blockers(final))


def _asset_findings(project: Project) -> list[Finding]:
    if project.manifest is None:
        return []
    out: list[Finding] = []
    for entry in project.manifest.entries:
        for flag in entry.flags:
            if not flag.open:
                continue
            out.append(
                Finding(
                    f"asset:{entry.file}:{flag.kind.value}",
                    f"asset.{flag.kind.value}",
                    Stage.ASSET,
                    flag.severity,
                    entry.file,
                    flag.evidence,
                    flag.message,
                    ", ".join(o.value for o in flag.options),
                )
            )
    return out


@dataclass(frozen=True, slots=True)
class ExportReport:
    root: Path
    files: tuple[str, ...]
    handoff: str


class ExportPackage:
    """Write the project folder and every document. Refused while a blocker stands."""

    def __init__(
        self,
        projects: ProjectCatalog,
        gate: CheckGate,
        briefs: BuildBriefDocuments,
        guide: BuildRecordingGuide,
        folder: ProjectFolder,
        exporter: DocumentExporter,
    ) -> None:
        self._projects = projects
        self._gate = gate
        self._briefs = briefs
        self._guide = guide
        self._folder = folder
        self._exporter = exporter

    def execute(
        self, project_id: str, root: Path, ctx: JobContext
    ) -> Result[ExportReport, _Failure]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        report = self._gate.report(project)
        if not report.can_export:
            listing = "; ".join(f"{f.where}: {f.message}" for f in report.blocking[:5])
            return Err(
                ConflictError(
                    f"{len(report.blocking)} blockers",
                    user_message=f"Export gesperrt: {len(report.blocking)} Punkte offen. {listing}",
                )
            )
        written: list[str] = []

        def text(relative: str, content: str) -> None:
            self._folder.write_text(root, relative, content)
            written.append(relative)

        def doc(relative: str, document: Document) -> None:
            for fmt in ("md", "docx", "pdf"):
                target = self._exporter.export(document, root / f"{relative}.{fmt}", fmt)
                written.append(str(target.relative_to(root)))

        slug = project.settings.slug
        spoken = spoken_script(project)
        ctx.raise_if_cancelled()
        text(
            f"00_kontext/{slug}-transcript.md",
            render_script(
                spoken,
                title=project.settings.title,
                facts=project.facts,
                slot_rows=[(s.key, s.label, s.value) for s in project.slots],
            ),
        )
        text("pipeline-context.md", pipeline_context(project))
        for variant in project.contexts:
            text(f"00_kontext/context/{variant.level}.md", variant.text)
        briefs = self._briefs.execute(project_id)
        if isinstance(briefs, Ok):
            doc("kurzkonzept", briefs.value.kurzkonzept)
            doc("rueckfragen", briefs.value.rueckfragen)
        guide = self._guide.execute(project_id)
        if isinstance(guide, Ok):
            doc("aufnahme-leitfaden", guide.value)
        doc("compliance-bericht", compliance_report(report.findings))
        text("asset-plan.json", json_codec.dumps("asset_plan", _Plan(project.requirements)))
        if project.manifest is not None:
            text("manifest.json", json_codec.dumps("manifest", project.manifest))
            ctx.progress.report(0, max(1, len(project.manifest.usable_entries())), "Dateien")
            for i, e in enumerate(project.manifest.usable_entries(), 1):
                ctx.raise_if_cancelled()
                if e.source_path and not e.file.startswith("unsorted/"):
                    convert = Path(e.file).suffix.lstrip(".")
                    self._folder.place(
                        Path(e.source_path),
                        root,
                        e.file,
                        convert
                        if Path(e.original_name).suffix.lstrip(".").lower() != convert
                        else "",
                    )
                    written.append(e.file)
                ctx.progress.report(i, len(project.manifest.usable_entries()), e.file)
        if project.metadata is not None:
            text("metadaten.md", to_markdown(_metadata_doc(project.metadata)))
            text(
                "metadaten.json",
                json.dumps(_metadata_json(project.metadata), ensure_ascii=False, indent=1),
            )
        handoff = _handoff(root, slug, sponsored=project.settings.sponsored)
        text("README-handoff.md", handoff)
        return Ok(ExportReport(root, tuple(written), handoff))


@dataclass(frozen=True, slots=True)
class _Plan:
    requirements: tuple[AssetRequirement, ...]


def _metadata_doc(meta: YoutubeMetadata) -> Document:
    blocks = [
        Block(BlockKind.H1, "YouTube-Metadaten"),
        Block(BlockKind.H2, "Titel"),
        Block(BlockKind.BULLETS, items=meta.titles),
        Block(BlockKind.H2, "Beschreibung"),
        Block(BlockKind.PARAGRAPH, meta.description),
        Block(BlockKind.H2, "Schlagwörter"),
        Block(BlockKind.PARAGRAPH, ", ".join(meta.tags)),
        Block(BlockKind.H2, "Kapitel"),
        Block(
            BlockKind.BULLETS,
            items=tuple(f"{clock(c.at_seconds)} {c.title}" for c in meta.chapters),
        ),
    ]
    return Document("Metadaten", tuple(blocks))


def _metadata_json(meta: YoutubeMetadata) -> dict[str, object]:
    return {
        "titles": list(meta.titles),
        "description": meta.description,
        "tags": list(meta.tags),
        "hashtags": list(meta.hashtags),
        "chapters": [{"at_seconds": c.at_seconds, "title": c.title} for c in meta.chapters],
    }


def _handoff(root: Path, slug: str, *, sponsored: bool) -> str:
    sponsorship = " --sponsored" if sponsored else ""
    return (
        "# Übergabe an die Pipeline\n\n"
        "Dieser Ordner ist das Projekt. Der Schnitt, der Edit-Plan und das Timeline-Projekt "
        "entstehen in der Pipeline:\n\n"
        "```\n"
        f'uv run python scratch/run_pipeline.py --project "{root / "01_cam"}" '
        f'--script "{root / "00_kontext" / (slug + "-transcript.md")}" '
        f'--context "{root / "pipeline-context.md"}"{sponsorship} --language de\n'
        "```\n\n"
        "`manifest.json` sagt, was jede Datei ist. Markierte Dateien stehen nicht im Ordner.\n"
    )
