"""Pacing Intelligence measures rhythm and recommends; it analyses, plans and edits nothing else.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module at all (the application maps upstream results into facts).
* It computes no media result and touches no media: no process, file-system, database or numerical
  library, and no use of the owners' producers (it only READS their stored results).
* It never plans a story and never edits: it does not depend on the editing stage or the pipeline.
* Only the pipeline, the editing stage that consumes it, and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
PACING = "media_house.modules.pacing_intelligence"
DOMAIN = f"{PACING}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "pacing_intelligence"

#: Producers pacing must never run: it reads their STORED results through catalogs.
PRODUCERS = (
    "AudioAnalyzer",
    "AudioEngine",
    "VideoAnalyzer",
    "ContentAnalyzer",
    "AudioVisualAligner",
    "MediaInspector",
    "RelationshipDetection",
    "ProxyManager",
    "StoryPlanner",
    "EditPlanner",
    "ProcessRunner",
    "ProcessSpec",
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_pacing_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(PACING)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(PACING)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_pacing_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(PACING)
    ]

    assert offenders == []


def test_pacing_does_no_analysis_rendering_or_storage_of_its_own() -> None:
    forbidden = {
        "sqlite3", "subprocess", "shutil", "os", "numpy", "scipy", "torch",
        "transformers", "cv2", "PIL", "av",
    }  # fmt: skip
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(PACING)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_pacing_never_runs_an_upstream_producer_or_plans_anything() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_pacing_reads_upstream_results_only_through_catalogs() -> None:
    text = (MODULE_DIR / "application" / "analyze_pacing.py").read_text(encoding="utf-8")

    for catalog in (
        "StoryCatalog",
        "ContentCatalog",
        "AudioCatalog",
        "VideoCatalog",
        "AlignmentCatalog",
        "InspectionCatalog",
    ):
        assert catalog in text


def test_pacing_does_not_depend_on_editing_or_the_pipeline() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(PACING)
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


def test_only_the_pipeline_its_consumers_and_the_composition_root_depend_on_pacing() -> None:
    """The editing stage and B-roll (places that need a change of picture) read it as evidence."""
    allowed = (
        "media_house.modules.media_pipeline",
        "media_house.modules.editing_intelligence",
        "media_house.modules.broll_intelligence",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(PACING)
        and not name.startswith(allowed)
        and any(i.target.startswith(PACING) for i in info.imports)
    ]

    assert offenders == []


def test_pacing_names_no_timeline_backend_and_never_mutates_a_timeline() -> None:
    backends = re.compile(r"davinci|resolve_script|fusionscript|\bPlacement\b|\bEditPlan\b", re.I)
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        offenders += [(path.name, m.group(0)) for m in backends.finditer(text)]

    assert offenders == []


def test_the_domain_has_no_clock_logging_or_global_state() -> None:
    offenders = []
    for path in sorted((MODULE_DIR / "domain").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (r"datetime\.now", "utcnow", "get_logger", "import random", r"\bprint\("):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []
