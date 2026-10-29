"""B-roll Intelligence finds and ranks footage; it analyses, edits and renders nothing.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module at all (the application maps upstream results into facts).
* It computes no media result and touches no media: no process, file-system, database or
  model library. The one numerical dependency (NumPy) lives in its infrastructure adapter.
* It never runs another module's producer: it READS stored results through their catalogs, and
  the shot index (video_intelligence) and the library are the searched footage.
* It never plans a story, decides an edit or names a timeline backend.
* Only the pipeline, the editing stage that consumes it and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
BROLL = "media_house.modules.broll_intelligence"
DOMAIN = f"{BROLL}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "broll_intelligence"

#: Producers B-roll must never run: it reads their STORED results through catalogs.
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
    "StoryPlanner",
    "PacingAnalyzer",
    "EditPlanner",
    "ProcessRunner",
    "ProcessSpec",
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_broll_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(BROLL)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(BROLL)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_broll_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(BROLL)
    ]

    assert offenders == []


def test_broll_does_no_analysis_rendering_or_storage_of_its_own() -> None:
    forbidden = {
        "sqlite3", "subprocess", "shutil", "os", "scipy", "torch", "transformers", "cv2",
        "PIL", "av", "sentence_transformers", "faiss", "chromadb",
    }  # fmt: skip
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(BROLL)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_numpy_is_confined_to_the_vector_adapter() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if name.startswith(BROLL)
        and not name.startswith(f"{BROLL}.infrastructure")
        and any(i.target.split(".")[0] == "numpy" for i in info.imports)
    ]

    assert offenders == []


def test_broll_never_runs_an_upstream_producer_or_plans_anything() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_broll_reads_upstream_results_only_through_catalogs() -> None:
    text = (MODULE_DIR / "application" / "analyze_broll.py").read_text(encoding="utf-8")
    pool = (MODULE_DIR / "application" / "pool.py").read_text(encoding="utf-8")

    for catalog in (
        "StoryCatalog",
        "ContentCatalog",
        "AudioCatalog",
        "VideoCatalog",
        "AlignmentCatalog",
        "PacingCatalog",
    ):
        assert catalog in text
    for catalog in ("ShotIndexCatalog", "RelationshipCatalog"):
        assert catalog in pool


def test_broll_does_not_depend_on_editing_or_the_pipeline() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(BROLL)
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


def test_only_the_pipeline_its_consumers_and_the_composition_root_depend_on_broll() -> None:
    """Editing places its cover; continuity asks whether a cutaway exists for a cut."""
    allowed = (
        "media_house.modules.media_pipeline",
        "media_house.modules.editing_intelligence",
        "media_house.modules.continuity_intelligence",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(BROLL)
        and not name.startswith(allowed)
        and any(i.target.startswith(BROLL) for i in info.imports)
    ]

    assert offenders == []


def test_broll_names_no_timeline_backend_and_never_builds_a_timeline() -> None:
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
