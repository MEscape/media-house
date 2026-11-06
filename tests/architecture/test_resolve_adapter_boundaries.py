"""The Resolve Adapter is the ONLY module that knows DaVinci Resolve. It carries a built timeline
out in Resolve and reads a person's changes back; it makes no creative decision and builds nothing.

* It reaches every other module only through that module's public contract, and only the modules
  it needs: the library (files, documents, derived renders), the inspection (is the render right),
  the proxy manager (which proxy to link) and the timeline builder (its timeline, its record of a
  person's edits).
* Its domain imports no other module at all (the application maps the built timeline into its own
  facts).
* Resolve's objects live in ONE file of the infrastructure; the library that loads Resolve's
  scripting module appears nowhere else in the code base.
* It never runs a producer: no compiler, no builder, no planner, no improver. It carries out what
  was decided.
* Only the pipeline and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
ADAPTER = "media_house.modules.resolve_adapter"
DOMAIN = f"{ADAPTER}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "resolve_adapter"

#: Producers the adapter must never run (it reads and carries out their stored results).
PRODUCERS = (
    "AudioAnalyzer",
    "VideoAnalyzer",
    "ContentAnalyzer",
    "ProxyManager",
    "EnsureProxy",
    "AudioImprover",
    "VideoImprover",
    "EditPlanner",
    "StyleResolver",
    "MotionGraphicsPlanner",
    "CaptionPlanner",
    "TransitionPlanner",
    "VfxPlanner",
    "SoundDesignPlanner",
    "SoundStemRenderer",
    "GraphicsRenderer",
    "TimelineCompiler",
    "CompileTimeline",
    "TimelineBuilder",
    "BuildTimeline",
)
UPSTREAM = (
    "media_house.modules.media_library",
    "media_house.modules.media_inspection",
    "media_house.modules.proxy_manager",
    "media_house.modules.timeline_builder",
    ADAPTER,
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_adapter_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(ADAPTER)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(ADAPTER)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_adapter_reads_only_the_modules_it_needs() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(ADAPTER)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(UPSTREAM)
    ]

    assert offenders == []


def test_adapter_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(ADAPTER)
    ]

    assert offenders == []


def test_adapter_never_runs_a_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_only_the_pipeline_and_the_composition_root_depend_on_the_adapter() -> None:
    allowed = ("media_house.modules.media_pipeline", "media_house.bootstrap")
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(ADAPTER)
        and not name.startswith(allowed)
        and any(i.target.startswith(ADAPTER) for i in info.imports)
    ]

    assert offenders == []


def test_resolves_scripting_module_is_loaded_in_one_file_and_nowhere_else() -> None:
    loader = re.compile(r"fusionscript|scriptapp\(|DaVinciResolveScript")
    offenders = sorted(
        str(path.relative_to(SRC))
        for path in (SRC / "media_house").rglob("*.py")
        if loader.search(path.read_text(encoding="utf-8"))
        and path != MODULE_DIR / "infrastructure" / "install.py"
        and path.parent != MODULE_DIR / "infrastructure"
    )

    assert offenders == []


def test_resolve_objects_are_only_touched_by_the_scripting_adapter() -> None:
    api = re.compile(
        r"\.(GetProjectManager|AppendToTimeline|CreateEmptyTimeline|GetMediaPool|"
        r"GetItemListInTrack|ImportMedia|AddRenderJob|StartRendering)\("
    )
    offenders = sorted(
        str(path.relative_to(SRC))
        for path in (SRC / "media_house").rglob("*.py")
        if api.search(path.read_text(encoding="utf-8"))
        and path != MODULE_DIR / "infrastructure" / "scripting.py"
    )

    assert offenders == []


def test_the_rest_of_media_house_names_no_timeline_application() -> None:
    backends = re.compile(r"davinci|fusionscript|DaVinciResolve", re.IGNORECASE)
    offenders = []
    for path in sorted((SRC / "media_house" / "modules").rglob("*.py")):
        relative = path.relative_to(SRC / "media_house" / "modules")
        owner = relative.parts[0]
        if owner in {"resolve_adapter", "media_pipeline"}:
            continue
        text = path.read_text(encoding="utf-8")
        # a module that merely says it is not the one is fine; code that names it is not
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith(("#", '"', "*", "-"))
        )
        offenders += [(str(relative), m.group(0)) for m in backends.finditer(code)]

    assert offenders == []


def test_the_domain_has_no_clock_logging_or_global_state() -> None:
    offenders = []
    for path in sorted((MODULE_DIR / "domain").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (r"datetime\.now", "utcnow", "get_logger", "import random", r"\bprint\("):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []


def test_the_application_reads_no_clock_directly_and_loads_no_library() -> None:
    offenders = []
    for path in sorted((MODULE_DIR / "application").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (r"datetime\.now", "utcnow", r"\btime\.time\(", "importlib", "ctypes"):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []


def test_the_adapter_has_no_storage_of_its_own() -> None:
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        for needle in ("sqlite3", r"\.db\b", "shelve", "pickle"):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []
