"""The final polish looks at the finished presentation and reports. It changes nothing, fixes
nothing, renders nothing, exports nothing and approves nothing.

* It reaches other modules only through their public contract, and only the ones it reads: the
  library, quality control, the audio mix, the revisions, the review and the timeline builder.
* Its domain imports no other module at all (``application/views.py`` maps what it reads).
* It never runs a producer or an editor and never plans a revision: a fix goes through the
  revision engine, a render through the render manager.
* Pictures are measured through the shared ``MediaAnalyzer`` port; FFmpeg is named nowhere here.
* Reports are library documents: no database of its own.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
POLISH = "media_house.modules.final_polish"
DOMAIN = f"{POLISH}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "final_polish"

READS = (
    "media_house.modules.media_library",
    "media_house.modules.quality_control",
    "media_house.modules.audio_mix_engine",
    "media_house.modules.revision_engine",
    "media_house.modules.review_orchestrator",
    "media_house.modules.timeline_builder",
    POLISH,
)
PRODUCERS = (
    "TimelineBuilder",
    "BuildTimeline",
    "TimelineEditor",
    "HumanEdit",
    "AudioMixer",
    "RenderMixCommand",
    "RevisionPlanner",
    "RevisionDecider",
    "RevisionExecutorService",
    "RenderManager",
    "ExportValidator",
    "ResolveExecutor",
    "ResolveRenderer",
    "FinalExporter",
)
CONSUMERS = (
    "media_house.modules.resolve_export",
    "media_house.modules.media_pipeline",
    "media_house.bootstrap",
    POLISH,
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_final_polish_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(POLISH)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(POLISH)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_final_polish_reads_only_what_it_needs() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(POLISH)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(READS)
    ]

    assert offenders == []


def test_final_polish_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(POLISH)
    ]

    assert offenders == []


def test_final_polish_never_changes_anything_it_only_reports() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_files_are_measured_through_the_shared_port_and_no_tool_or_storage_is_named() -> None:
    pattern = re.compile(
        r"ffmpeg|ffprobe|ProcessSpec|ProcessRunner|davinci|fusionscript|sqlite3|shelve|pickle",
        re.IGNORECASE,
    )
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_nothing_here_authorizes_an_export_or_approves_for_a_person() -> None:
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        for needle in (
            "authorize_export",
            "AuthorizeExportCommand",
            "ApprovalKind.FINAL",
            "mark_delivery_ready",
            "decide(",
        ):
            if needle in text:
                offenders.append((path.name, needle))

    assert offenders == []


def test_only_resolve_export_the_pipeline_and_the_composition_root_depend_on_it() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(CONSUMERS) and any(i.target.startswith(POLISH) for i in info.imports)
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


def test_a_report_never_says_it_is_an_approval() -> None:
    model = (MODULE_DIR / "domain" / "model.py").read_text(encoding="utf-8")
    values = (MODULE_DIR / "domain" / "values.py").read_text(encoding="utf-8")

    assert 'approval: str = "not_approved"' in model
    assert not re.search(r"APPROVED|approved", values)
