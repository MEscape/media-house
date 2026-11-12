"""Produce reviewable local drafts and safety evidence from a real brief, with no model calls.

Example:
    uv run python scripts/check_brief_to_cut.py --brief <brief.pdf> \
        --reference-dir <manual-project> --output scratch/brief-review

This exercises the existing application services and real SQLite adapters. It does not ingest
or copy footage, fetch web pages, manufacture ratings, select an A/B winner, or render a video.
The final compliance gate remains authoritative; these are review drafts, not an export package.
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from media_house.core.domain import json_codec
from media_house.core.infrastructure.system_clock import SystemClock
from media_house.modules.production_project.application.brief_step import (
    BuildBriefDocuments,
    ExtractBrief,
    RunRiskPanel,
)
from media_house.modules.production_project.application.context_step import BuildContext
from media_house.modules.production_project.application.final_step import BuildMetadata, CheckGate
from media_house.modules.production_project.application.material_step import (
    BuildRecordingGuide,
    PlanMaterial,
)
from media_house.modules.production_project.application.projects import ProjectCatalog
from media_house.modules.production_project.application.story_step import (
    Brainstorm,
    ChooseConcept,
    GenerateScript,
    LintScript,
)
from media_house.modules.production_project.domain.compliance import tag_length
from media_house.modules.production_project.domain.facts import products, referenced_keys
from media_house.modules.production_project.domain.project import Project, ProjectSettings
from media_house.modules.production_project.domain.script import parse_script
from media_house.modules.production_project.infrastructure.brief_reader import DocumentBriefReader
from media_house.modules.production_project.infrastructure.documents import FileDocumentExporter
from media_house.modules.production_project.infrastructure.sqlite_store import SqliteProjectStore
from media_house.modules.style_memory.application.guide import GuideStyle
from media_house.modules.style_memory.application.library import SeedStyleMemory
from media_house.modules.style_memory.application.memory import RecordPreference
from media_house.modules.style_memory.infrastructure.sqlite_store import SqliteStyleStore
from media_house.shared.errors import Err, Ok


def value(result: Any) -> Any:
    if isinstance(result, Err):
        raise RuntimeError(result.error.user_message)
    if not isinstance(result, Ok):
        raise TypeError("Expected an application Result")
    return result.value


def write_json(path: Path, document: object) -> None:
    path.write_text(
        json.dumps(json_codec.encode(document), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def metrics(text: str) -> dict[str, int]:
    script = parse_script(text)
    spoken = " ".join(sentence.text for scene in script.scenes for sentence in scene.sentences)
    return {
        "scenes": len(script.scenes),
        "spoken_words": len(spoken.split()),
        "unfilled_markers": len(re.findall(r"(?<!\{)\{[A-Z_][A-Z_0-9]*\}(?!\})", spoken)),
        "spoken_characters": len(spoken),
        "usable_spoken_words": sum(
            len(sentence.text.split()) for sentence in script.sentences if sentence.usable
        ),
        "repeated_sentence_occurrences": sum(
            count - 1
            for count in Counter(sentence.text for sentence in script.sentences).values()
            if count > 1
        ),
    }


def quality_checks(project: Project) -> dict[str, bool]:
    """Deterministic safety gates only. Creative quality requires a creator's review."""
    rows = products(project.facts)
    keys = {fact.key for fact in project.facts}
    generated = "\n".join([project.script, *(variant.text for variant in project.contexts)])
    no_price = rows and all(row.price is None for row in rows)
    checks = {
        "all_script_fact_references_exist": set(referenced_keys(project.script)) <= keys,
        "no_unsupported_basket_total": not no_price or "price_total" not in keys,
        "no_unverified_fact_marked_verified": not any(f.verified for f in project.facts),
        "three_context_variants": {v.level for v in project.contexts}
        == {"minimal", "normal", "detailed"},
        "all_context_sections_survive_budget": all(
            all(
                f"{section}:" in variant.text
                for section in ("Thema", "Idee", "Look", "Regeln", "Material", "Freiheit")
            )
            for variant in project.contexts
        ),
        "no_thumbnail_deliverable": "thumbnail" not in generated.casefold(),
        "single_consolidated_talking_requirement": sum(
            requirement.role.value == "talking" for requirement in project.requirements
        )
        == 1,
        "every_requirement_has_scene_consumers": all(r.scene_ids for r in project.requirements),
        "metadata_tag_limit": project.metadata is not None
        and tag_length(project.metadata.tags) <= 500,
    }
    if project.brief and project.brief.client.casefold() == "aldi":
        current = {row.name for row in rows}
        checks["aldi_current_products_found"] = current >= {
            "Plasma Pulse",
            "Wonderland",
            "Power Circus",
            "Golden Phoenix",
            "Colour Beast",
        }
        checks["aldi_current_prices_not_invented"] = all(row.price is None for row in rows)
        checks["reference_video_dates_not_project_deadlines"] = not any(
            deadline.kind == "publish" for deadline in project.brief.deadlines
        )
        checks["no_lidl_campaign_leakage"] = not any(
            phrase in generated.casefold()
            for phrase in ("lidl plus", "lidl und nico", "lidlnico", "494,95", "2.474,75")
        )
    return checks


def run(args: argparse.Namespace) -> int:
    started = perf_counter()
    brief_path: Path = args.brief.resolve()
    output: Path = args.output.resolve()
    reference: Path | None = args.reference_dir.resolve() if args.reference_dir else None
    if reference is not None and not reference.is_dir():
        raise ValueError("The reference project directory does not exist")
    if reference is not None and (output == reference or reference in output.parents):
        raise ValueError("Choose an output directory outside the original reference project")
    output.mkdir(parents=True, exist_ok=True)
    text = value(DocumentBriefReader().read(brief_path))
    clock = SystemClock()
    projects = ProjectCatalog(SqliteProjectStore(output / "production_project.db"), clock)
    style_store = SqliteStyleStore(output / "style_memory.db")
    SeedStyleMemory(style_store, clock).execute()
    style = GuideStyle(style_store)
    recorder = RecordPreference(style_store, clock)
    exporter = FileDocumentExporter()
    project = value(
        projects.create(
            ProjectSettings(
                "",
                args.title or brief_path.stem,
                args.client,
                args.format,
                args.year,
                sponsored=args.sponsored,
            )
        )
    )
    pid = project.project_id
    report = value(ExtractBrief(projects, style, clock, None).execute(pid, text, ideas=args.ideas))
    project = value(Brainstorm(projects, style, clock, None).execute(pid))
    value(
        ChooseConcept(projects, recorder, clock).execute(
            pid, project.concepts[args.concept - 1].concept_id
        )
    )
    value(GenerateScript(projects, style, clock, None).execute(pid, use_model=False))
    value(PlanMaterial(projects, clock).execute(pid))
    value(BuildContext(projects, style, clock).execute(pid))
    value(BuildMetadata(projects, style, clock).execute(pid))
    value(RunRiskPanel(projects, style, clock).execute(pid))
    project = value(projects.get(pid))
    lint = value(LintScript(projects, style).execute(pid))
    gate = value(CheckGate(projects, style).execute(pid))
    brief_docs = BuildBriefDocuments(projects, style, clock, exporter)
    value(brief_docs.export(pid, output, formats=("md", "docx", "pdf")))
    guide = value(BuildRecordingGuide(projects).execute(pid))
    exporter.export(guide, output / "aufnahmeplan.md", "md")
    (output / "transcript-entwurf.md").write_text(project.script, encoding="utf-8")
    for variant in project.contexts:
        (output / f"kontext-{variant.level}.md").write_text(variant.text, encoding="utf-8")
    write_json(output / "brief-model.json", report.project.brief)
    write_json(output / "asset-plan.json", project.requirements)
    write_json(output / "metadaten.json", project.metadata)
    write_json(output / "compliance.json", gate)
    checks = quality_checks(project)
    comparison: dict[str, object] = {"draft": metrics(project.script)}
    if reference is not None:
        scripts = sorted((reference / "00_kontext").glob("*-transcript.md"))
        if scripts:
            comparison["manual_reference"] = metrics(scripts[0].read_text(encoding="utf-8"))
            comparison["reference_file"] = str(scripts[0])
    quality = {
        "source": str(brief_path),
        "project_id": pid,
        "seconds": round(perf_counter() - started, 3),
        "model_calls": 0,
        "safety_checks": checks,
        "safety_passed": all(checks.values()),
        "can_export": gate.can_export,
        "blocking_findings": len(gate.blocking),
        "estimated_minutes": round(lint.seconds / 60, 2),
        "requirements_without_scenes": [
            requirement.requirement_id
            for requirement in project.requirements
            if not requirement.scene_ids
        ],
        "brief_duration_target_minutes": (
            [int(duration.group(1)), int(duration.group(2))]
            if (duration := re.search(r"Länge:\s*(\d+)\s*[\u2013-]\s*(\d+)\s*Minuten", text))
            else None
        ),
        "manual_quality_equivalence": (
            "Nicht nachgewiesen; eine Strukturprüfung ersetzt keine kreative Bewertung."
        ),
        "draft_comparison": comparison,
        "creative_review": (
            "Offen: Stimme, Dramaturgie, technische Tiefe und Länge am Referenzskript prüfen."
        ),
        "render_review": (
            "Nicht ausgeführt: Es wurden keine ALDI-Aufnahmen oder fertigen Schnitte bewertet."
        ),
        "findings": json_codec.encode(lint.findings),
    }
    write_json(output / "quality-report.json", quality)
    print(
        json.dumps(
            {key: item for key, item in quality.items() if key != "findings"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(checks.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--client", default="")
    parser.add_argument("--format", default="brochure_review")
    parser.add_argument("--year", type=int, default=datetime.now(UTC).year)
    parser.add_argument("--concept", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--ideas", default="")
    parser.add_argument("--sponsored", action="store_true")
    try:
        return run(parser.parse_args())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Brief review failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
