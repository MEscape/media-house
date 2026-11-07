"""Quality control looks, measures and reports. It changes no timeline, decides nothing creative,
approves nothing, renders nothing and carries no analysis of its own that another module owns.

* It reaches other modules only through their public contract, and only the ones it reads: the
  library, the inspection, the timeline builder and compiler (what was built and what was meant),
  the audio mix, continuity and story results, the Resolve binding, and the review.
* Its domain imports no other module at all (``application/views.py`` maps what it reads).
* It never runs a producer or an editor: no build, no human edit, no render, no mix, no execution
  in Resolve. The one Resolve call (a live look that records nothing) is in the infrastructure.
* Files are measured through the shared ``MediaAnalyzer`` port; FFmpeg is named nowhere here.
* Reports are library documents: no database of its own.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
QC = "media_house.modules.quality_control"
DOMAIN = f"{QC}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "quality_control"

READS = (
    "media_house.modules.media_library",
    "media_house.modules.media_inspection",
    "media_house.modules.timeline_builder",
    "media_house.modules.edit_plan_compiler",
    "media_house.modules.audio_mix_engine",
    "media_house.modules.continuity_intelligence",
    "media_house.modules.story_architecture",
    "media_house.modules.resolve_adapter",
    "media_house.modules.review_orchestrator",
    QC,
)
PRODUCERS = (
    "TimelineBuilder",
    "BuildTimeline",
    "TimelineEditor",
    "RecordEditsCommand",
    "HumanEdit",
    "TimelineCompiler",
    "AudioMixer",
    "RenderMixCommand",
    "ResolveExecutor",
    "ResolveRenderer",
    "RenderCommand",
    "ExecuteTimelineCommand",
    "ContinuityAnalyzer",
    "StoryPlanner",
    "EditPlanner",
    "ProxyManager",
)
CONSUMERS = (
    "media_house.modules.revision_engine",
    "media_house.modules.final_polish",
    "media_house.modules.resolve_export",
    "media_house.modules.export_validation",
    "media_house.modules.media_pipeline",
    "media_house.bootstrap",
    QC,
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_quality_control_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(QC)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(QC)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_quality_control_reads_only_what_it_needs() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(QC)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(READS)
    ]

    assert offenders == []


def test_quality_control_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(QC)
    ]

    assert offenders == []


def test_quality_control_never_runs_a_producer_or_edits_a_timeline() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_the_only_resolve_call_is_a_look_that_records_nothing() -> None:
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        if "ResolveSynchronizer" in text or "SyncCommand" in text:
            if path.parent.name != "infrastructure" and path.name != "module.py":
                offenders.append((path.name, "outside the infrastructure"))
            elif "SyncCommand" in text and "record=False" not in text:
                offenders.append((path.name, "records"))

    assert offenders == []


def test_files_are_measured_through_the_shared_port_and_ffmpeg_is_named_nowhere() -> None:
    offenders = sorted(
        str(path.relative_to(MODULE_DIR))
        for path in module_files()
        if re.search(
            r"ffmpeg|ffprobe|ProcessSpec|ProcessRunner",
            path.read_text(encoding="utf-8"),
            re.IGNORECASE,
        )
    )

    assert offenders == []


def test_quality_control_has_no_storage_of_its_own() -> None:
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        for needle in ("sqlite3", r"\.db[\"']", "shelve", "pickle"):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []


def test_only_later_layer6_modules_the_pipeline_and_the_composition_root_depend_on_it() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(CONSUMERS) and any(i.target.startswith(QC) for i in info.imports)
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
    text = (MODULE_DIR / "domain" / "model.py").read_text(encoding="utf-8")

    assert 'approval: str = "not_approved"' in text
    assert not re.search(
        r"APPROVED|approved", (MODULE_DIR / "domain" / "values.py").read_text(encoding="utf-8")
    )
