"""Reframing Intelligence recommends framing; it detects, edits, renders and compiles nothing.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module at all (the application maps upstream results into facts).
* It computes no media result and touches no media: no process, file-system, database, image or
  model library.
* It never runs another module's producer: it READS stored results through their catalogs.
* It never decides an edit, plans a story or names a timeline backend: it knows no edit plan.
* Only the pipeline, the editing stage that consumes it and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
REFRAMING = "media_house.modules.reframing_intelligence"
DOMAIN = f"{REFRAMING}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "reframing_intelligence"

#: Producers reframing must never run: it reads their STORED results through catalogs.
PRODUCERS = (
    "AudioAnalyzer",
    "AudioEngine",
    "VideoAnalyzer",
    "ContentAnalyzer",
    "AudioVisualAligner",
    "MediaInspector",
    "RelationshipDetection",
    "RelationshipEditor",
    "ProxyManager",
    "VideoImprover",
    "StoryPlanner",
    "PacingAnalyzer",
    "BrollAnalyzer",
    "ContinuityAnalyzer",
    "EditPlanner",
    "ProcessRunner",
    "ProcessSpec",
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_reframing_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(REFRAMING)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(REFRAMING)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_reframing_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(REFRAMING)
    ]

    assert offenders == []


def test_reframing_does_no_detection_rendering_or_storage_of_its_own() -> None:
    forbidden = {
        "sqlite3", "subprocess", "shutil", "os", "numpy", "scipy", "torch", "transformers", "cv2",
        "PIL", "av", "ultralytics", "sentence_transformers", "faiss", "chromadb",
    }  # fmt: skip
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(REFRAMING)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_reframing_never_runs_an_upstream_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_reframing_reads_upstream_results_only_through_catalogs() -> None:
    text = (MODULE_DIR / "application" / "analyze_reframing.py").read_text(encoding="utf-8")

    for catalog in (
        "VideoCatalog",
        "InspectionCatalog",
        "AlignmentCatalog",
        "StoryCatalog",
        "ContinuityCatalog",
    ):
        assert catalog in text


def test_reframing_does_not_depend_on_editing_or_the_pipeline() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(REFRAMING)
        for i in info.imports
        if i.target.startswith(
            (
                "media_house.modules.editing_intelligence",
                "media_house.modules.media_pipeline",
                "media_house.bootstrap",
            )
        )
    ]

    assert offenders == []


def test_only_the_pipeline_the_editing_stage_and_the_composition_root_depend_on_reframing() -> None:
    allowed = (
        "media_house.modules.media_pipeline",
        "media_house.modules.editing_intelligence",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(REFRAMING)
        and not name.startswith(allowed)
        and any(i.target.startswith(REFRAMING) for i in info.imports)
    ]

    assert offenders == []


def test_reframing_names_no_timeline_backend_and_knows_no_edit_plan() -> None:
    backends = re.compile(r"davinci|resolve_script|fusionscript", re.IGNORECASE)
    edit_types = re.compile(r"\bPlacement\b|\bEditPlan\b")
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        offenders += [(path.name, m.group(0)) for m in backends.finditer(text)]
        offenders += [(path.name, m.group(0)) for m in edit_types.finditer(text)]

    assert offenders == []


def test_the_domain_has_no_clock_logging_or_global_state() -> None:
    offenders = []
    for path in sorted((MODULE_DIR / "domain").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (r"datetime\.now", "utcnow", "get_logger", "import random", r"\bprint\("):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []
