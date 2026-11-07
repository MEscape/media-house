"""The review orchestrator keeps the state of a review and nothing else. It owns no timeline,
produces no report, renders nothing, edits nothing and knows no editing application.

* It reaches other modules only through their public contract, and only the two it needs: the
  timeline builder (which version of the timeline exists) and the library's error type.
* Its domain imports no other module at all (the application maps the timeline into its own facts).
* Its one database is behind one port; ``sqlite3`` appears only in the infrastructure store.
* It runs no producer: no builder, no quality control, no revision, no render, no export.
* Layer 6 modules depend on it; it depends on none of them.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
ORCHESTRATOR = "media_house.modules.review_orchestrator"
DOMAIN = f"{ORCHESTRATOR}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "review_orchestrator"
UPSTREAM = (
    "media_house.modules.media_library",  # the error type its catalog calls return
    "media_house.modules.timeline_builder",
    ORCHESTRATOR,
)

PRODUCERS = (
    "TimelineBuilder",
    "BuildTimeline",
    "TimelineEditor",
    "QualityAnalyzer",
    "RevisionPlanner",
    "FinalPolisher",
    "RenderManager",
    "ExportValidator",
    "ResolveExecutor",
    "ResolveRenderer",
)
LAYER6_CONSUMERS = (
    "media_house.modules.quality_control",
    "media_house.modules.revision_engine",
    "media_house.modules.final_polish",
    "media_house.modules.render_manager",
    "media_house.modules.export_validation",
    "media_house.modules.resolve_export",
    "media_house.modules.media_pipeline",
    "media_house.bootstrap",
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_orchestrator_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(ORCHESTRATOR)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(ORCHESTRATOR)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_orchestrator_reads_only_the_builder_and_the_librarys_errors() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(ORCHESTRATOR)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(UPSTREAM)
    ]

    assert offenders == []


def test_orchestrator_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(ORCHESTRATOR)
    ]

    assert offenders == []


def test_orchestrator_runs_no_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_only_layer6_and_the_composition_root_depend_on_the_orchestrator() -> None:
    allowed = (*LAYER6_CONSUMERS, ORCHESTRATOR)
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(allowed)
        and any(i.target.startswith(ORCHESTRATOR) for i in info.imports)
    ]

    assert offenders == []


def test_the_orchestrator_depends_on_no_layer6_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(ORCHESTRATOR)
        for i in info.imports
        if i.target.startswith(LAYER6_CONSUMERS[:6])
    ]

    assert offenders == []


def test_sqlite_is_named_in_the_infrastructure_store_only() -> None:
    offenders = sorted(
        str(path.relative_to(MODULE_DIR))
        for path in module_files()
        if re.search(r"sqlite3", path.read_text(encoding="utf-8"))
        and path.parent.name != "infrastructure"
    )

    assert offenders == []


def test_the_orchestrator_names_no_media_tool_or_editing_application() -> None:
    pattern = re.compile(
        r"davinci|resolve_adapter|fusionscript|ffmpeg|ProcessRunner", re.IGNORECASE
    )
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_the_domain_has_no_clock_logging_or_global_state() -> None:
    offenders = []
    for path in sorted((MODULE_DIR / "domain").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (r"datetime\.now", "utcnow", "get_logger", "import random", r"\bprint\("):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []


def test_the_application_reads_no_clock_directly() -> None:
    offenders = []
    for path in sorted((MODULE_DIR / "application").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (r"datetime\.now", "utcnow", r"\btime\.time\("):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []
