"""Caption Engine decides captions; it transcribes nothing and draws nothing.

* It reaches every other module only through that module's public contract, and only the
  modules of the Layer 4 graph (docs/layer4/contracts.md section 2) plus the upstream readers.
* Its domain imports no other module at all (the application maps upstream results into facts).
* It runs no process, model or image library: transcription is audio_intelligence's,
  drawing is motion_graphics_engine's (through ``GraphicsRenderer``).
* It never runs an upstream producer: it READS stored results through their catalogs.
* It never names a timeline backend and knows no edit plan (its input is ``CaptionTimeline``).
* Only music and sound design (events), the pipeline and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
CAPTION = "media_house.modules.caption_engine"
DOMAIN = f"{CAPTION}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "caption_engine"

#: The only modules caption_engine may read (their contracts).
READS = (
    "visual_style_engine",
    "motion_graphics_engine",
    "media_library",
    "media_inspection",
    "audio_intelligence",
    "video_intelligence",
    "content_intelligence",
    "story_architecture",
    "reframing_intelligence",
)
#: Producers caption_engine must never run: it reads their STORED results.
PRODUCERS = (
    "AudioAnalyzer",
    "AudioEngine",
    "TranscribeAudioCommand",
    "VideoAnalyzer",
    "ContentAnalyzer",
    "TextGenerator",
    "AudioVisualAligner",
    "MediaInspector",
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


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_caption_imports_other_modules_only_through_allowed_contracts() -> None:
    allowed = tuple(f"media_house.modules.{m}.application.contracts" for m in READS)
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(CAPTION)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(CAPTION)
        and not i.target.startswith(allowed)
    ]

    assert offenders == []


def test_caption_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(CAPTION)
    ]

    assert offenders == []


def test_caption_does_no_transcription_rendering_or_storage_of_its_own() -> None:
    forbidden = {
        "sqlite3", "subprocess", "shutil", "os", "numpy", "scipy", "torch", "transformers", "cv2",
        "PIL", "av", "whisperx", "faster_whisper", "sentence_transformers", "playwright",
    }  # fmt: skip
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(CAPTION)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_caption_never_runs_an_upstream_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_caption_reads_upstream_results_only_through_catalogs() -> None:
    text = (MODULE_DIR / "application" / "plan_captions.py").read_text(encoding="utf-8")

    for catalog in (
        "AudioCatalog",
        "ContentCatalog",
        "VideoCatalog",
        "ReframingCatalog",
        "InspectionCatalog",
        "GraphicsCatalog",
        "VisualStyleCatalog",
    ):
        assert catalog in text


def test_caption_draws_only_through_the_motion_graphics_renderer() -> None:
    text = (MODULE_DIR / "application" / "plan_captions.py").read_text(encoding="utf-8")

    assert "GraphicsRenderer" in text
    assert not (MODULE_DIR / "infrastructure").exists()


def test_only_music_the_pipeline_and_the_composition_root_depend_on_captions() -> None:
    allowed = (
        "media_house.modules.music_and_sound_design",  # syncs to caption events
        "media_house.modules.media_pipeline",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(CAPTION)
        and not name.startswith(allowed)
        and any(i.target.startswith(CAPTION) for i in info.imports)
    ]

    assert offenders == []


def test_caption_names_no_timeline_backend_and_knows_no_edit_plan() -> None:
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
