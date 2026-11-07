"""The render manager renders and checks what it rendered. It decides nothing creative, approves
nothing, exports nothing without a person's authorization, and judges no delivered file.

* It reaches other modules only through their public contract, and only the ones it needs: the
  library, inspection (to look at a render), the timeline builder (what the timeline is), the
  review orchestrator (the export authorization) and ``resolve_adapter`` (the render backend).
* Its domain imports no other module at all.
* ``resolve_adapter`` is imported by ONE adapter file: there is no second Resolve integration here.
* FFmpeg and ``ProcessRunner`` are named only in infrastructure; no media measurement is parsed here
  (the shared ``MediaAnalyzer`` owns that), and nothing here writes a file except through them.
* It never runs a producer and does not depend on export validation (the reverse is allowed).
* Only Layer 6 modules, the pipeline and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
MANAGER = "media_house.modules.render_manager"
DOMAIN = f"{MANAGER}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "render_manager"
UPSTREAM = (
    "media_house.modules.media_library",
    "media_house.modules.media_inspection",
    "media_house.modules.timeline_builder",
    "media_house.modules.review_orchestrator",
    "media_house.modules.resolve_adapter",
    MANAGER,
)
PRODUCERS = (
    "TimelineBuilder",
    "BuildTimeline",
    "TimelineEditor",
    "TimelineCompiler",
    "ResolveExecutor",
    "ResolveSynchronizer",
    "AudioMixer",
    "QualityAnalyzer",
    "RevisionPlanner",
    "ExportValidator",
)
CONSUMERS = (
    "media_house.modules.export_validation",
    "media_house.modules.resolve_export",
    "media_house.modules.quality_control",
    "media_house.modules.revision_engine",
    "media_house.modules.final_polish",
    "media_house.modules.media_pipeline",
    "media_house.bootstrap",
    MANAGER,
)
RESOLVE_ADAPTER_FILE = "infrastructure/resolve_backend.py"
MEASURING = re.compile(r"blackdetect|freezedetect|silencedetect|ebur128|astats|signalstats", re.I)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def relative(path: Path) -> str:
    return path.relative_to(MODULE_DIR).as_posix()


def test_manager_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MANAGER)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(MANAGER)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_manager_reads_only_the_modules_it_needs() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MANAGER)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(UPSTREAM)
    ]

    assert offenders == []


def test_manager_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(MANAGER)
    ]

    assert offenders == []


def test_there_is_one_resolve_integration_and_it_is_the_adapters_contract() -> None:
    importers = sorted(
        {
            relative(path)
            for path in module_files()
            if re.search(
                r"^\s*(from|import) media_house\.modules\.resolve_adapter",
                path.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        }
    )
    api = re.compile(r"fusionscript|DaVinciResolveScript|GetResolve\(|scriptapp", re.I)
    second = [relative(p) for p in module_files() if api.search(p.read_text(encoding="utf-8"))]

    assert importers == [RESOLVE_ADAPTER_FILE, "module.py"]
    assert second == []


def test_manager_runs_no_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (relative(path), hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_manager_does_not_depend_on_export_validation_or_later_layer6_modules() -> None:
    later = (
        "media_house.modules.export_validation",
        "media_house.modules.resolve_export",
        "media_house.modules.quality_control",
        "media_house.modules.revision_engine",
        "media_house.modules.final_polish",
    )
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MANAGER)
        for i in info.imports
        if i.target.startswith(later)
    ]

    assert offenders == []


def test_only_layer6_the_pipeline_and_the_composition_root_depend_on_the_manager() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(CONSUMERS)
        and any(i.target.startswith(MANAGER) for i in info.imports)
    ]

    assert offenders == []


def test_ffmpeg_and_the_process_runner_are_named_in_infrastructure_only() -> None:
    offenders = sorted(
        relative(path)
        for path in module_files()
        if re.search(
            r"ffmpeg|ProcessSpec|ProcessRunner|subprocess",
            path.read_text(encoding="utf-8"),
            re.IGNORECASE,
        )
        and path.parent.name != "infrastructure"
        and path.name != "module.py"
    )

    assert offenders == []


def test_no_media_measurement_is_parsed_here() -> None:
    offenders = [
        relative(p) for p in module_files() if MEASURING.search(p.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_files_are_written_only_by_infrastructure() -> None:
    writes = re.compile(r"\.write_bytes\(|\.write_text\(|\bunlink\(|rmtree|shutil\.copy|\.rename\(")
    offenders = [
        relative(p)
        for p in module_files()
        if writes.search(p.read_text(encoding="utf-8")) and p.parent.name != "infrastructure"
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


def test_the_manager_has_no_storage_of_its_own() -> None:
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        for needle in ("sqlite3", r"\.db[\"']", "shelve", "pickle"):
            if re.search(needle, text):
                offenders.append((relative(path), needle))

    assert offenders == []


def test_the_final_render_is_guarded_at_its_one_entry_point() -> None:
    """Every way a FINAL render starts goes through ``execute``, which asks the review orchestrator."""
    text = (MODULE_DIR / "application" / "render.py").read_text(encoding="utf-8")

    assert text.count("verify_export(") == 1
    assert "def _authorize" in text and text.count("self._authorize(") == 2
