"""The Transition Engine decides transitions; it edits, analyses and renders nothing itself.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module at all (the application maps upstream results into facts).
* It touches no media: no process, file-system, database, image or model library.
* It never runs another module's producer: it READS stored results through their catalogs and
  asks motion_graphics_engine's executor contract (``GraphicsRenderer``) to render overlays.
* It knows no edit plan type (its own ``TransitionSequence`` is mapped by the pipeline) and
  names no timeline backend.
* Only music_and_sound_design (events), the pipeline and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
TRANSITION = "media_house.modules.transition_engine"
DOMAIN = f"{TRANSITION}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "transition_engine"

#: Producers the engine must never run: it reads their STORED results through catalogs.
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
    "ReframingAnalyzer",
    "EditPlanner",
    "StyleResolver",
    "MotionGraphicsPlanner",
    "ProcessRunner",
    "ProcessSpec",
)
#: The modules whose contracts it may read (contracts sec. 2 + the opened allowlists).
UPSTREAM = (
    "media_house.modules.media_library",
    "media_house.modules.media_inspection",
    "media_house.modules.audio_intelligence",
    "media_house.modules.video_intelligence",
    "media_house.modules.story_architecture",
    "media_house.modules.pacing_intelligence",
    "media_house.modules.continuity_intelligence",
    "media_house.modules.visual_style_engine",
    "media_house.modules.motion_graphics_engine",
    TRANSITION,
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_transition_engine_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(TRANSITION)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(TRANSITION)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_transition_engine_reads_only_its_upstream_modules() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(TRANSITION)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(UPSTREAM)
    ]

    assert offenders == []


def test_transition_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(TRANSITION)
    ]

    assert offenders == []


def test_transition_engine_does_no_media_work_of_its_own() -> None:
    forbidden = {
        "sqlite3", "subprocess", "shutil", "os", "numpy", "scipy", "torch", "transformers", "cv2",
        "PIL", "av", "ultralytics", "sentence_transformers", "faiss", "chromadb",
    }  # fmt: skip
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(TRANSITION)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_transition_engine_never_runs_an_upstream_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_transition_engine_reads_upstream_results_only_through_catalogs() -> None:
    text = (MODULE_DIR / "application" / "plan_transitions.py").read_text(encoding="utf-8")

    for catalog in (
        "InspectionCatalog",
        "AudioCatalog",
        "VideoCatalog",
        "ContinuityCatalog",
        "PacingCatalog",
        "VisualStyleCatalog",
        "StyleValidator",
        "GraphicsRenderer",
    ):
        assert catalog in text


def test_transition_engine_does_not_depend_on_editing_or_the_pipeline() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(TRANSITION)
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


def test_only_music_the_pipeline_and_the_composition_root_depend_on_transitions() -> None:
    allowed = (
        "media_house.modules.music_and_sound_design",  # syncs sound to transition events
        "media_house.modules.edit_plan_compiler",  # Layer 5: reads the stored plan
        "media_house.modules.media_pipeline",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(TRANSITION)
        and not name.startswith(allowed)
        and any(i.target.startswith(TRANSITION) for i in info.imports)
    ]

    assert offenders == []


def test_transition_engine_names_no_timeline_backend_and_knows_no_edit_plan() -> None:
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
