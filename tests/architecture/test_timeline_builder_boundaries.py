"""The Timeline Builder constructs the timeline of a compiled specification; it makes no creative
decision, compiles nothing, renders nothing, probes nothing and speaks to no editing application.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module at all (the application maps the specification into its own
  facts).
* It touches no media: no process, file-system, database, image or model library.
* It never runs another module's producer, not even the compiler: it READS the stored
  specification through ``TimelineCatalog`` (or takes the one the pipeline hands in) and looks
  files up in the library and the proxy catalog.
* It knows no edit plan type and no Layer 4 plan, and names no timeline application: that is
  ``resolve_adapter``'s job.
* Only the pipeline, ``resolve_adapter``, ``audio_mix_engine`` and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
BUILDER = "media_house.modules.timeline_builder"
DOMAIN = f"{BUILDER}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "timeline_builder"

#: Producers the builder must never run: it reads stored results through catalogs.
PRODUCERS = (
    "AudioAnalyzer",
    "VideoAnalyzer",
    "ContentAnalyzer",
    "MediaInspector",
    "ProxyManager",
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
    "ProcessRunner",
    "ProcessSpec",
)
#: The modules whose contracts it may read: the compiled specification, the files, the proxies.
UPSTREAM = (
    "media_house.modules.media_library",
    "media_house.modules.proxy_manager",
    "media_house.modules.edit_plan_compiler",
    BUILDER,
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_builder_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(BUILDER)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(BUILDER)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_builder_reads_only_its_upstream_modules() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(BUILDER)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(UPSTREAM)
    ]

    assert offenders == []


def test_builder_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(BUILDER)
    ]

    assert offenders == []


def test_builder_does_no_media_work_of_its_own() -> None:
    forbidden = {
        "sqlite3", "subprocess", "shutil", "os", "numpy", "scipy", "torch", "transformers", "cv2",
        "PIL", "av", "ultralytics", "sentence_transformers", "faiss", "chromadb",
    }  # fmt: skip
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(BUILDER)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_builder_never_runs_a_producer_not_even_the_compiler() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_builder_reads_the_specification_only_through_the_catalog() -> None:
    text = (MODULE_DIR / "application" / "build_timeline.py").read_text(encoding="utf-8")

    assert "TimelineCatalog" in text
    assert "MediaLibrary" in text


def test_builder_does_not_depend_on_editing_layer4_or_the_pipeline() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(BUILDER)
        for i in info.imports
        if i.target.startswith(
            (
                "media_house.modules.editing_intelligence",
                "media_house.modules.media_pipeline",
                "media_house.modules.visual_style_engine",
                "media_house.modules.motion_graphics_engine",
                "media_house.modules.caption_engine",
                "media_house.modules.transition_engine",
                "media_house.modules.vfx_engine",
                "media_house.modules.music_and_sound_design",
                "media_house.bootstrap",
            )
        )
    ]

    assert offenders == []


def test_only_the_pipeline_its_consumers_and_the_composition_root_depend_on_the_builder() -> None:
    allowed = (
        "media_house.modules.media_pipeline",
        "media_house.modules.resolve_adapter",
        "media_house.modules.audio_mix_engine",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(BUILDER)
        and not name.startswith(allowed)
        and any(i.target.startswith(BUILDER) for i in info.imports)
    ]

    assert offenders == []


def test_builder_names_no_timeline_application_and_knows_no_edit_plan() -> None:
    backends = re.compile(r"davinci|resolve_script|fusionscript|DaVinciResolve", re.IGNORECASE)
    edit_types = re.compile(r"\bPlacement\b|\bEditPlan\b|\bEditCatalog\b|\bGraphicsPlan\b")
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


def test_the_application_reads_no_clock_directly() -> None:
    offenders = []
    for path in sorted((MODULE_DIR / "application").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in (r"datetime\.now", "utcnow", r"\btime\.time\("):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []
