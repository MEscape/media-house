"""Editing Intelligence decides what to edit; it analyses, renders and compiles nothing.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module at all (the application maps upstream results into facts).
* It computes no media result and touches no media: no process, file-system, database or numerical
  library, and no use of the owners' analysers (it only READS their stored results).
* It names no timeline backend and depends on no module that does not exist yet. The pacing
  analysis is a real input, read through its catalog.
* Only the pipeline (and the composition root) depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
EDITING = "media_house.modules.editing_intelligence"
DOMAIN = f"{EDITING}.domain"
MODULES_DIR = SRC / "media_house" / "modules"

#: Responsibilities of modules that are planned but not built. Nothing may depend on them, and
#: no empty package may reserve their names.
FUTURE_MODULES = (
    "continuity_intelligence",
    "reframing_intelligence",
    "music_and_sound_design",
    "transition_engine",
    "motion_graphics_engine",
    "visual_style_engine",
    "edit_plan_compiler",
    "timeline_builder",
    "resolve_adapter",
    "quality_control",
    "revision_engine",
)
#: Upstream producers: the editor may read their stored results (catalogs) but never run them.
PRODUCERS = (
    "AudioAnalyzer",
    "AudioEngine",
    "VideoAnalyzer",
    "ContentAnalyzer",
    "AudioVisualAligner",
    "MediaInspector",
    "RelationshipDetection",
    "ProxyManager",
    "VideoImprover",
    "AudioImprover",
    "BrollAnalyzer",
    "ProcessRunner",
    "ProcessSpec",
)


def module_files() -> list[Path]:
    return sorted((MODULES_DIR / "editing_intelligence").rglob("*.py"))


def test_editing_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(EDITING)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(EDITING)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_editing_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(EDITING)
    ]

    assert offenders == []


def test_editing_does_no_analysis_rendering_or_storage_of_its_own() -> None:
    forbidden = {
        "sqlite3",
        "subprocess",
        "shutil",
        "os",
        "numpy",
        "torch",
        "transformers",
        "cv2",
        "PIL",
        "scipy",
        "av",
    }
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(EDITING)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_editing_never_runs_an_upstream_producer_or_a_process() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_editing_only_reads_upstream_results_through_catalogs() -> None:
    """Reading is by ``*Catalog`` (stored results); planning the story is the one exception and
    belongs to the story's own owner, asked only when the caller requests it."""
    text = (MODULES_DIR / "editing_intelligence" / "application" / "plan_edit.py").read_text(
        encoding="utf-8"
    )

    for catalog in (
        "ContentCatalog",
        "AudioCatalog",
        "VideoCatalog",
        "AlignmentCatalog",
        "InspectionCatalog",
        "StoryCatalog",
        "BrollCatalog",
    ):
        assert catalog in text
    assert "StoryPlanner" in text and "Acquire.REQUEST" in text


def test_editing_names_no_timeline_backend_and_depends_on_no_future_module() -> None:
    backends = re.compile(
        r"davinci|resolve_script|DaVinciResolveScript|fusionscript", re.IGNORECASE
    )
    future = re.compile("|".join(FUTURE_MODULES))
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        offenders += [(path.name, m.group(0)) for m in backends.finditer(text)]
        offenders += [
            (path.name, m.group(0))
            for m in future.finditer(
                "\n".join(
                    line
                    for line in text.splitlines()
                    if line.lstrip().startswith(("import", "from"))
                )
            )
        ]

    assert offenders == []


def test_no_empty_package_reserves_the_name_of_a_module_that_does_not_exist_yet() -> None:
    present = {p.name for p in MODULES_DIR.iterdir() if p.is_dir()}

    assert present.isdisjoint(FUTURE_MODULES)


def test_only_the_pipeline_and_the_composition_root_depend_on_editing() -> None:
    allowed = ("media_house.modules.media_pipeline", "media_house.bootstrap")
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(EDITING)
        and not name.startswith(allowed)
        and any(i.target.startswith(EDITING) for i in info.imports)
    ]

    assert offenders == []


def test_the_domain_has_no_clock_logging_or_global_state() -> None:
    offenders = []
    for path in sorted((MODULES_DIR / "editing_intelligence" / "domain").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (r"datetime\.now", "utcnow", "get_logger", "import random", r"print\("):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []
