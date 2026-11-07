"""The revision engine plans and coordinates changes; it does not own what it coordinates.

* It reaches other modules only through their public contract, and only the ones it works with:
  the library, quality control (what is wrong), the review (what is decided), the timeline builder,
  the audio mix engine and the Resolve adapter (the owners whose changes it carries out).
* Its domain imports no other module at all (``application/views.py`` maps what it reads).
* It does not duplicate any owner's intelligence: it imports no pacing, caption, B-roll,
  transition, graphics, reframing, continuity, sound design or story module. A change for those
  is a hand-off.
* The only places that change anything are the executors (and the rollback executor): the planner,
  the feedback reader and the decision step never call an owner.
* It names no editing application and no media tool, and has no storage of its own.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
REVISION = "media_house.modules.revision_engine"
DOMAIN = f"{REVISION}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "revision_engine"

READS = (
    "media_house.modules.media_library",
    "media_house.modules.quality_control",
    "media_house.modules.review_orchestrator",
    "media_house.modules.timeline_builder",
    "media_house.modules.audio_mix_engine",
    "media_house.modules.resolve_adapter",
    REVISION,
)
CONSUMERS = (
    "media_house.modules.final_polish",
    "media_house.modules.resolve_export",
    "media_house.modules.media_pipeline",
    "media_house.bootstrap",
    REVISION,
)
#: The files that may call an owner to change something.
ACTING = {"executors.py", "rollback.py", "module.py"}
OWNER_CALLS = (
    "TimelineEditor",
    "TimelineBuilder",
    "AudioMixer",
    "ResolveExecutor",
    "RenderMixCommand",
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_revision_engine_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(REVISION)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(REVISION)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_revision_engine_works_with_only_the_owners_it_carries_changes_out_through() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(REVISION)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(READS)
    ]

    assert offenders == []


def test_revision_engine_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(REVISION)
    ]

    assert offenders == []


def test_only_the_executors_call_an_owner_to_change_something() -> None:
    pattern = re.compile(r"\b(" + "|".join(OWNER_CALLS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        if path.name not in ACTING
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_the_planner_and_the_feedback_reader_are_pure() -> None:
    other_modules = re.compile(r"(from|import) media_house\.modules\.(?!revision_engine)")
    offenders = []
    for name in ("planner.py", "feedback.py", "plan.py", "invalidation.py"):
        text = (MODULE_DIR / "domain" / name).read_text(encoding="utf-8")
        if other_modules.search(text):
            offenders.append((name, "another module"))
        for needle in ("ProcessRunner", "sqlite3"):
            if needle in text:
                offenders.append((name, needle))

    assert offenders == []


def test_revision_engine_names_no_editing_application_media_tool_or_storage() -> None:
    pattern = re.compile(
        r"davinci|fusionscript|ffmpeg|ffprobe|ProcessRunner|sqlite3|shelve|pickle", re.IGNORECASE
    )
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_only_later_modules_the_pipeline_and_the_composition_root_depend_on_it() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(CONSUMERS)
        and any(i.target.startswith(REVISION) for i in info.imports)
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


def test_nothing_here_authorizes_an_export_or_approves_for_a_person() -> None:
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        for needle in (
            "authorize_export",
            "AuthorizeExportCommand",
            "ApprovalKind.FINAL",
            "mark_delivery_ready",
        ):
            if needle in text:
                offenders.append((path.name, needle))

    assert offenders == []
