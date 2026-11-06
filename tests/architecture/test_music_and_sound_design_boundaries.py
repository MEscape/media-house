"""Music and Sound Design plans music and effects from the owner's library; it masters nothing.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module and no third-party package; numpy, scipy and pydantic
  appear only in its infrastructure (measurement, manifest validation, FFmpeg).
* It never downloads, generates or models audio: no network client, no model library.
* It never runs another module's producer: it READS stored results through their catalogs.
* It never mixes or masters (audio_improvement owns that) and knows no edit plan or visual style.
* Only the pipeline and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
MUSIC = "media_house.modules.music_and_sound_design"
DOMAIN = f"{MUSIC}.domain"
APPLICATION = f"{MUSIC}.application"
MODULE_DIR = SRC / "media_house" / "modules" / "music_and_sound_design"

#: Producers it must never run: it reads their STORED results through catalogs.
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
    "EditPlanner",
    "ReframingAnalyzer",
    "AudioImprover",
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def _imports(prefix: str) -> list[tuple[str, str]]:
    return [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(prefix)
        for i in info.imports
    ]


def test_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, target)
        for name, target in _imports(MUSIC)
        if target.startswith("media_house.modules.")
        and not target.startswith(MUSIC)
        and ".application.contracts" not in target
    ]

    assert offenders == []


def test_domain_depends_on_no_other_module_and_no_third_party_package() -> None:
    offenders = [
        (name, target)
        for name, target in _imports(DOMAIN)
        if (target.startswith("media_house.modules.") and not target.startswith(MUSIC))
        or target.split(".")[0] in {"numpy", "scipy", "pydantic", "torch"}
    ]

    assert offenders == []


def test_signal_libraries_live_only_in_the_infrastructure() -> None:
    offenders = [
        (name, target)
        for name, target in _imports(MUSIC)
        if target.split(".")[0] in {"numpy", "scipy", "pydantic"} and ".infrastructure." not in name
    ]

    assert offenders == []


def test_never_downloads_generates_or_runs_a_model() -> None:
    forbidden = {
        "sqlite3", "subprocess", "os", "urllib", "http", "requests", "httpx", "aiohttp",
        "socket", "huggingface_hub", "torch", "torchaudio", "transformers", "diffusers",
        "librosa", "soundfile", "cv2", "PIL",
    }  # fmt: skip
    offenders = [
        (name, target) for name, target in _imports(MUSIC) if target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_never_runs_an_upstream_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_reads_upstream_results_only_through_catalogs() -> None:
    text = (MODULE_DIR / "application" / "plan_sound_design.py").read_text(encoding="utf-8")

    for catalog in ("AudioCatalog", "StoryCatalog", "PacingCatalog"):
        assert catalog in text


def test_never_mixes_masters_or_reads_the_visual_identity() -> None:
    offenders = [
        (name, target)
        for name, target in _imports(MUSIC)
        if target.startswith(
            (
                "media_house.modules.audio_improvement",
                "media_house.modules.visual_style_engine",
                "media_house.modules.editing_intelligence",
                "media_house.modules.media_pipeline",
                "media_house.bootstrap",
            )
        )
    ]

    assert offenders == []


def test_only_the_pipeline_and_the_composition_root_depend_on_it() -> None:
    allowed = (
        "media_house.modules.media_pipeline",
        "media_house.modules.edit_plan_compiler",  # Layer 5: reads the stored plan
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(MUSIC)
        and not name.startswith(allowed)
        and any(i.target.startswith(MUSIC) for i in info.imports)
    ]

    assert offenders == []


def test_names_no_timeline_backend_and_knows_no_edit_plan() -> None:
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


def test_application_has_no_third_party_or_process_code() -> None:
    offenders = [
        (name, target)
        for name, target in _imports(APPLICATION)
        if target.split(".")[0] in {"numpy", "scipy", "pydantic", "shutil", "subprocess"}
    ]

    assert offenders == []
