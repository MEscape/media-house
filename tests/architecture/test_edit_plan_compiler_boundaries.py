"""The Edit Plan Compiler turns plans into a timeline specification; it makes no creative
decision, plans nothing, renders nothing and probes nothing.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module at all (the application maps plans into its own facts).
* It touches no media: no process, file-system, database, image or model library.
* It never runs another module's producer: it READS stored results through their catalogs (or
  takes the plans the pipeline hands in) and verifies files through the library.
* It knows no edit plan type (its own ``TimelineSource`` is mapped by the pipeline) and names no
  timeline backend: Resolve-specific execution belongs to a later module.
* Only the pipeline and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
COMPILER = "media_house.modules.edit_plan_compiler"
DOMAIN = f"{COMPILER}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "edit_plan_compiler"

#: Producers the compiler must never run: it reads their STORED results through catalogs.
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
    "AudioImprover",
    "VideoImprover",
    "StoryPlanner",
    "PacingAnalyzer",
    "BrollAnalyzer",
    "ContinuityAnalyzer",
    "ReframingAnalyzer",
    "EditPlanner",
    "StyleResolver",
    "MotionGraphicsPlanner",
    "CaptionPlanner",
    "TransitionPlanner",
    "VfxPlanner",
    "SoundDesignPlanner",
    "SoundStemRenderer",
    "GraphicsRenderer",
    "ProcessRunner",
    "ProcessSpec",
)
#: The modules whose contracts it may read: the Layer 4 plans, the style, the files.
UPSTREAM = (
    "media_house.modules.media_library",
    "media_house.modules.media_inspection",
    "media_house.modules.visual_style_engine",
    "media_house.modules.motion_graphics_engine",
    "media_house.modules.caption_engine",
    "media_house.modules.transition_engine",
    "media_house.modules.vfx_engine",
    "media_house.modules.music_and_sound_design",
    "media_house.modules.reframing_intelligence",
    COMPILER,
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_compiler_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(COMPILER)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(COMPILER)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_compiler_reads_only_its_upstream_modules() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(COMPILER)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(UPSTREAM)
    ]

    assert offenders == []


def test_compiler_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(COMPILER)
    ]

    assert offenders == []


def test_compiler_does_no_media_work_of_its_own() -> None:
    forbidden = {
        "sqlite3", "subprocess", "shutil", "os", "numpy", "scipy", "torch", "transformers", "cv2",
        "PIL", "av", "ultralytics", "sentence_transformers", "faiss", "chromadb",
    }  # fmt: skip
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(COMPILER)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_compiler_never_runs_an_upstream_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_compiler_reads_upstream_results_only_through_catalogs() -> None:
    text = (MODULE_DIR / "application" / "compile_timeline.py").read_text(encoding="utf-8")

    for catalog in (
        "InspectionCatalog",
        "VisualStyleCatalog",
        "StyleValidator",
        "GraphicsCatalog",
        "CaptionCatalog",
        "TransitionCatalog",
        "VfxCatalog",
        "SoundDesignCatalog",
        "ReframingCatalog",
    ):
        assert catalog in text


def test_compiler_does_not_depend_on_editing_or_the_pipeline() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(COMPILER)
        for i in info.imports
        if i.target.startswith(
            (
                "media_house.modules.editing_intelligence",
                "media_house.modules.media_pipeline",
                "media_house.modules.audio_improvement",
                "media_house.bootstrap",
            )
        )
    ]

    assert offenders == []


def test_only_the_pipeline_the_builder_and_the_composition_root_depend_on_the_compiler() -> None:
    allowed = (
        "media_house.modules.media_pipeline",
        "media_house.modules.timeline_builder",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(COMPILER)
        and not name.startswith(allowed)
        and any(i.target.startswith(COMPILER) for i in info.imports)
    ]

    assert offenders == []


def test_compiler_names_no_timeline_backend_and_knows_no_edit_plan() -> None:
    backends = re.compile(r"davinci|resolve_script|fusionscript", re.IGNORECASE)
    edit_types = re.compile(r"\bPlacement\b|\bEditPlan\b|\bEditCatalog\b")
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
