"""Export validation looks at the delivered file and reports. It changes nothing, re-edits and
re-renders nothing, advances no review, and measures through the shared analyzer only.

* It reaches other modules only through their public contract, and only the ones it needs.
* Its domain imports no other module at all (the application maps what it reads).
* It may depend on the render manager's contract (profiles, the render report of a file); the render
  manager never depends on it.
* It names no media tool and parses no measurement: the shared ``MediaAnalyzer`` port is the only
  way it looks at a file. It has no infrastructure layer.
* It never writes or deletes the file it validates.
* It never touches Resolve and runs no producer.
* Only ``resolve_export``, the pipeline and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
VALIDATION = "media_house.modules.export_validation"
DOMAIN = f"{VALIDATION}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "export_validation"
UPSTREAM = (
    "media_house.modules.media_library",
    "media_house.modules.media_inspection",
    "media_house.modules.timeline_builder",
    "media_house.modules.audio_mix_engine",
    "media_house.modules.render_manager",
    "media_house.modules.review_orchestrator",
    VALIDATION,
)
PRODUCERS = (
    "TimelineBuilder",
    "BuildTimeline",
    "TimelineEditor",
    "AudioMixer",
    "RenderManager",
    "RenderService",
    "RenderQueueing",
    "ResolveExecutor",
    "ResolveRenderer",
    "QualityAnalyzer",
    "RevisionPlanner",
)
CONSUMERS = (
    "media_house.modules.resolve_export",
    "media_house.modules.media_pipeline",
    "media_house.bootstrap",
    VALIDATION,
)
MEASURING = re.compile(
    r"blackdetect|freezedetect|silencedetect|ebur128|astats|signalstats|ffmpeg|ffprobe|"
    r"ProcessRunner|ProcessSpec|subprocess",
    re.I,
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def relative(path: Path) -> str:
    return path.relative_to(MODULE_DIR).as_posix()


def test_validation_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(VALIDATION)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(VALIDATION)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_validation_reads_only_the_modules_it_needs() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(VALIDATION)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(UPSTREAM)
    ]

    assert offenders == []


def test_validation_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(VALIDATION)
    ]

    assert offenders == []


def test_only_resolve_export_the_pipeline_and_the_composition_root_depend_on_validation() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(CONSUMERS)
        and any(i.target.startswith(VALIDATION) for i in info.imports)
    ]

    assert offenders == []


def test_the_render_manager_never_depends_on_validation() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if name.startswith("media_house.modules.render_manager")
        and any(i.target.startswith(VALIDATION) for i in info.imports)
    ]

    assert offenders == []


def test_validation_runs_no_producer_and_touches_no_editing_application() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    resolve = re.compile(r"modules\.resolve_adapter|fusionscript|DaVinci", re.I)
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        offenders += [(relative(path), hit.group(0)) for hit in pattern.finditer(text)]
        offenders += [(relative(path), hit.group(0)) for hit in resolve.finditer(text)]

    assert offenders == []


def test_validation_names_no_media_tool_and_parses_no_measurement() -> None:
    offenders = [
        (relative(p), hit.group(0))
        for p in module_files()
        for hit in MEASURING.finditer(p.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_validation_has_no_infrastructure_layer_and_measures_through_the_shared_port() -> None:
    assert not (MODULE_DIR / "infrastructure").exists()
    text = (MODULE_DIR / "application" / "validate.py").read_text(encoding="utf-8")
    assert "MediaAnalyzer" in text


def test_validation_never_writes_or_deletes_files() -> None:
    writes = re.compile(
        r"\.write_bytes\(|\.write_text\(|\bunlink\(|rmtree|shutil|\.rename\(|\.replace\(\s*[a-z_.]*path"
    )
    offenders = [
        relative(p) for p in module_files() if writes.search(p.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_validation_does_not_advance_a_review_state() -> None:
    """It records its report (``attach``); moving the review is ``resolve_export``'s decision."""
    moves = re.compile(r"\.(advance|decide|authorize_export|record_export_validation|cancel)\(")
    offenders = [relative(p) for p in module_files() if moves.search(p.read_text(encoding="utf-8"))]

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


def test_validation_has_no_storage_of_its_own() -> None:
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        for needle in ("sqlite3", r"\.db[\"']", "shelve", "pickle"):
            if re.search(needle, text):
                offenders.append((relative(path), needle))

    assert offenders == []
