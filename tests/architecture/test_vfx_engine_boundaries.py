"""The VFX Engine plans and renders shot-level footage effects; it owns nothing else.

* It reaches every other module only through that module's public contract, and only the
  modules the Layer 4 graph allows (visual style, plus the stored upstream facts it reads:
  library, inspection, picture analysis, reframing, audio analysis and A/V alignment).
* Its domain imports no other module at all (the application maps upstream results into facts).
* Pixels are touched only in ``infrastructure`` (numpy there; FFmpeg only through the
  ``ProcessRunner`` port, never ``subprocess``); no model, image or database library anywhere.
* It never runs another module's producer: it READS stored results through their catalogs.
* It knows no edit plan and names no timeline backend: it owns its own input DTO.
* Only the pipeline and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
VFX = "media_house.modules.vfx_engine"
DOMAIN = f"{VFX}.domain"
APPLICATION = f"{VFX}.application"
MODULE_DIR = SRC / "media_house" / "modules" / "vfx_engine"

#: The modules whose contracts it may read (contracts sec. 2 + the upstream catalogs).
ALLOWED_MODULES = (
    "media_house.modules.visual_style_engine",
    "media_house.modules.media_library",
    "media_house.modules.media_inspection",
    "media_house.modules.video_intelligence",
    "media_house.modules.reframing_intelligence",
    # stored audio bangs (burst candidates) and the audio/video clock correction
    "media_house.modules.audio_intelligence",
    "media_house.modules.audio_visual_alignment",
)

#: Producers it must never run: it reads their STORED results through catalogs.
PRODUCERS = (
    "AudioAnalyzer",
    "AudioEngine",
    "VideoAnalyzer",
    "ContentAnalyzer",
    "AudioVisualAligner",
    "MediaInspector",
    "VideoImprover",
    "StoryPlanner",
    "PacingAnalyzer",
    "BrollAnalyzer",
    "ContinuityAnalyzer",
    "EditPlanner",
    "ReframingAnalyzer",
    "StyleResolver",
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_vfx_imports_other_modules_only_through_allowed_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(VFX)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(VFX)
        and (".application.contracts" not in i.target or not i.target.startswith(ALLOWED_MODULES))
    ]

    assert offenders == []


def test_vfx_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(VFX)
    ]

    assert offenders == []


def test_pixels_are_touched_only_in_infrastructure_and_never_through_raw_processes() -> None:
    nowhere = {
        "sqlite3", "subprocess", "os", "torch", "transformers", "cv2", "PIL", "av", "rembg",
        "ultralytics", "diffusers",
    }  # fmt: skip
    outside_infrastructure = {"numpy", "scipy", "shutil"}
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(VFX)
        for i in info.imports
        if i.target.split(".")[0] in nowhere
        or (
            i.target.split(".")[0] in outside_infrastructure
            and not name.startswith(f"{VFX}.infrastructure")
        )
    ]

    assert offenders == []


def test_vfx_never_runs_an_upstream_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_vfx_reads_upstream_results_only_through_catalogs() -> None:
    text = (MODULE_DIR / "application" / "plan_vfx.py").read_text(encoding="utf-8")

    for catalog in ("VideoCatalog", "InspectionCatalog", "ReframingCatalog", "VisualStyleCatalog"):
        assert catalog in text


def test_vfx_does_not_depend_on_editing_the_pipeline_or_the_composition_root() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(VFX)
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


def test_only_the_pipeline_and_the_composition_root_depend_on_vfx() -> None:
    allowed = (
        "media_house.modules.media_pipeline",
        "media_house.modules.edit_plan_compiler",  # Layer 5: reads the stored plan
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(VFX)
        and not name.startswith(allowed)
        and any(i.target.startswith(VFX) for i in info.imports)
    ]

    assert offenders == []


def test_vfx_names_no_timeline_backend_and_knows_no_edit_plan() -> None:
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


def test_the_application_layer_never_calls_ffmpeg_itself() -> None:
    offenders = [
        path.name
        for path in sorted((MODULE_DIR / "application").glob("*.py"))
        if re.search(r"ProcessSpec|ProcessRunner|\"ffmpeg\"|'ffmpeg'", path.read_text("utf-8"))
        and path.name != "plan_vfx.py"
    ]
    plan = (MODULE_DIR / "application" / "plan_vfx.py").read_text(encoding="utf-8")

    assert offenders == []
    assert "ProcessSpec" not in plan and "ProcessRunner" not in plan
