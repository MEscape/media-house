"""Visual Asset Generation prepares still pictures; it analyses, plans and animates nothing.

* It reaches every other module only through that module's public contract, and only the
  modules it is allowed to read: the library, stored inspections, stored b-roll gaps, the visual
  style and video intelligence (contracts.md section 2).
* Its domain imports no other module and no third-party or I/O library; pixels (Pillow, numpy,
  scipy, rembg) and processes (FFmpeg through ``ProcessRunner``) live in infrastructure only.
* It never runs another module's producer: it READS stored results through their catalogs.
* It names no timeline backend and knows no edit plan.
* Only motion graphics, the pipeline and the composition root depend on it.
* Stored cutouts stay cache hits: the operation name survives the rename.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
MODULE = "media_house.modules.visual_asset_generation"
DOMAIN = f"{MODULE}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "visual_asset_generation"

#: What this module may read (contracts.md section 2 + the orchestrator's broll allowlist).
ALLOWED_UPSTREAM = (
    "media_house.modules.media_library.application.contracts",
    "media_house.modules.media_inspection.application.contracts",
    "media_house.modules.broll_intelligence.application.contracts",
    "media_house.modules.visual_style_engine.application.contracts",
    "media_house.modules.video_intelligence.application.contracts",
)
#: Producers it must never run: it reads their STORED results.
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
    "ReframingAnalyzer",
    "StyleResolver",
)
PROCESS_PORTS = frozenset(
    {
        "media_house.core.application.ports.ProcessRunner",
        "media_house.core.application.ports.ProcessSpec",
    }
)
PIXEL_AND_MODEL_LIBRARIES = frozenset(
    {"PIL", "numpy", "scipy", "rembg", "onnxruntime", "cv2", "torch", "skimage"}
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_it_imports_other_modules_only_through_allowed_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MODULE)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(MODULE)
        and not i.target.startswith(ALLOWED_UPSTREAM)
    ]

    assert offenders == []


def test_the_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(MODULE)
    ]

    assert offenders == []


def test_pixels_models_and_processes_live_in_infrastructure_only() -> None:
    offenders = []
    for name, info in scan(SRC).items():
        if not name.startswith(MODULE) or name.startswith(f"{MODULE}.infrastructure"):
            continue
        for i in info.imports:
            root = i.target.split(".")[0]
            if root in PIXEL_AND_MODEL_LIBRARIES or root in {"subprocess", "shutil"}:
                offenders.append((name, i.target))
            if i.target in PROCESS_PORTS and name != f"{MODULE}.module":
                offenders.append((name, i.target))

    assert offenders == []


def test_it_never_runs_an_upstream_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_it_reads_upstream_results_through_catalogs() -> None:
    application = MODULE_DIR / "application"
    assert "InspectionCatalog" in (application / "grab_frame.py").read_text(encoding="utf-8")
    assert "BrollCatalog" in (application / "select_images.py").read_text(encoding="utf-8")
    prepare = (application / "prepare_visual_assets.py").read_text(encoding="utf-8")
    assert "VisualStyleCatalog" in prepare


def test_only_motion_graphics_the_pipeline_and_the_composition_root_depend_on_it() -> None:
    allowed = (
        "media_house.modules.motion_graphics_engine",
        "media_house.modules.media_pipeline",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(MODULE)
        and not name.startswith(allowed)
        and any(i.target.startswith(MODULE) for i in info.imports)
    ]

    assert offenders == []


def test_consumers_reach_it_only_through_its_contract() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if not name.startswith(MODULE) and not name.startswith("media_house.bootstrap")
        for i in info.imports
        if i.target.startswith(MODULE)
        and not i.target.startswith(f"{MODULE}.application.contracts")
    ]

    assert offenders == []


def test_it_names_no_timeline_backend_and_knows_no_edit_plan() -> None:
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


def test_the_old_module_name_is_gone_but_the_cutout_operation_name_is_kept() -> None:
    assert not (SRC / "media_house" / "modules" / "image_adjustment").exists()
    values = (MODULE_DIR / "domain" / "values.py").read_text(encoding="utf-8")
    assert 'ADJUSTMENT_OPERATION = "image_adjustment"' in values
    assert "ADJUSTMENT_VERSION = 1" in values
