"""Motion Graphics Engine builds, animates and renders graphics; it decides no edit.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module at all (the application maps the style into facts).
* It never runs an upstream producer: it reads stored results and calls only the owners it
  executes for (the style catalog/validator, the visual asset preparer for cut-outs).
* Processes run only through the ProcessRunner port; no subprocess, no model library.
* It never names a timeline backend and knows no edit plan (its input is ``GraphicsBrief``).
* Only the Layer 4 modules that execute through it, the pipeline and the composition root
  depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
MOTION = "media_house.modules.motion_graphics_engine"
DOMAIN = f"{MOTION}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "motion_graphics_engine"

#: Producers motion graphics must never run: it reads their STORED results (or gets them in
#: the brief). The style is read through ``VisualStyleCatalog``, never re-resolved.
PRODUCERS = (
    "AudioAnalyzer",
    "AudioEngine",
    "VideoAnalyzer",
    "ContentAnalyzer",
    "AudioVisualAligner",
    "MediaInspector",
    "StoryPlanner",
    "PacingAnalyzer",
    "BrollAnalyzer",
    "ContinuityAnalyzer",
    "ReframingAnalyzer",
    "EditPlanner",
    "StyleResolver",
    "ImageGenerationService",
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_motion_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MOTION)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(MOTION)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_motion_reads_only_the_modules_the_contract_graph_allows() -> None:
    allowed = (
        "media_house.modules.visual_style_engine",
        "media_house.modules.visual_asset_generation",
        "media_house.modules.media_library",
        "media_house.modules.media_inspection",
        "media_house.modules.content_intelligence",
        "media_house.modules.story_architecture",
        "media_house.modules.reframing_intelligence",
        MOTION,
    )
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MOTION)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(allowed)
    ]

    assert offenders == []


def test_motion_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(MOTION)
    ]

    assert offenders == []


def test_motion_runs_processes_only_through_the_port_and_loads_no_model() -> None:
    forbidden = {
        "subprocess", "sqlite3", "torch", "transformers", "cv2", "rembg", "ultralytics",
        "sentence_transformers", "onnxruntime",
    }  # fmt: skip
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MOTION)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_motion_never_runs_an_upstream_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_motion_does_not_depend_on_editing_or_the_pipeline() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MOTION)
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


def test_only_executing_layer4_modules_the_pipeline_and_the_root_depend_on_motion() -> None:
    allowed = (
        "media_house.modules.caption_engine",
        "media_house.modules.transition_engine",
        "media_house.modules.music_and_sound_design",  # events only
        "media_house.modules.media_pipeline",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(MOTION)
        and not name.startswith(allowed)
        and any(i.target.startswith(MOTION) for i in info.imports)
    ]

    assert offenders == []


def test_motion_names_no_timeline_backend_and_knows_no_edit_plan() -> None:
    backends = re.compile(r"davinci|resolve_script|fusionscript", re.IGNORECASE)
    edit_types = re.compile(r"\bEditPlan\b|\bEditCatalog\b")
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


def test_the_renderer_is_pinned_and_downloads_only_in_setup() -> None:
    runtime = MODULE_DIR / "infrastructure" / "hyperframes_runtime"
    assert (runtime / "package.json").is_file()
    assert (runtime / "package-lock.json").is_file()
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        assert "npx" not in text.replace("``npx``", ""), path.name
        assert "cdn.jsdelivr" not in text and "unpkg.com" not in text, path.name
