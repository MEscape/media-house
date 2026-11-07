"""The Audio Mix Engine mixes the sound of a built timeline. It decides nothing creative, repairs no
recording, analyses nothing and knows no editing application.

* It reaches every other module only through that module's public contract, and only the ones it
  needs: the library (files, documents, derived stems and mixes) and the timeline builder (the
  timeline it mixes).
* Its domain imports no other module at all (the application maps the timeline into its own facts).
* Processing happens behind ONE port; the only file that names FFmpeg is the infrastructure backend.
* It never runs a producer: no sound design planner, no audio improver, no analyser, no builder.
* It names no timeline application (``resolve_adapter`` can import its outputs; the reverse is not so).
* Only the pipeline and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
ENGINE = "media_house.modules.audio_mix_engine"
DOMAIN = f"{ENGINE}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "audio_mix_engine"

PRODUCERS = (
    "AudioImprover",
    "AudioMixing",
    "AudioAnalyzer",
    "AudioCatalog",
    "SoundDesignPlanner",
    "SoundStemRenderer",
    "MusicAndSoundDesign",
    "TimelineCompiler",
    "TimelineBuilder",
    "BuildTimeline",
    "EditPlanner",
    "ProxyManager",
)
UPSTREAM = (
    "media_house.modules.media_library",
    "media_house.modules.timeline_builder",
    ENGINE,
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_engine_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(ENGINE)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(ENGINE)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_engine_reads_only_the_modules_it_needs() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(ENGINE)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(UPSTREAM)
    ]

    assert offenders == []


def test_engine_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(ENGINE)
    ]

    assert offenders == []


def test_engine_never_runs_a_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_only_the_pipeline_and_the_composition_root_depend_on_the_engine() -> None:
    allowed = (
        "media_house.modules.media_pipeline",
        "media_house.modules.quality_control",  # Layer 6 reads the stored result
        "media_house.modules.revision_engine",  # Layer 6 reads the stored result
        "media_house.modules.final_polish",  # Layer 6 reads the stored result
        "media_house.modules.export_validation",  # Layer 6 reads the stored result
        "media_house.modules.resolve_export",  # Layer 6 reads the stored result
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(ENGINE)
        and not name.startswith(allowed)
        and any(i.target.startswith(ENGINE) for i in info.imports)
    ]

    assert offenders == []


def test_ffmpeg_is_named_in_the_infrastructure_backend_only() -> None:
    offenders = sorted(
        str(path.relative_to(MODULE_DIR))
        for path in module_files()
        if re.search(
            r"ffmpeg|ProcessSpec|ProcessRunner", path.read_text(encoding="utf-8"), re.IGNORECASE
        )
        and path.parent.name != "infrastructure"
        and path.name != "module.py"
    )

    assert offenders == []


def test_engine_names_no_timeline_application() -> None:
    backends = re.compile(r"davinci|resolve_adapter|fusionscript", re.IGNORECASE)
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in backends.finditer(path.read_text(encoding="utf-8"))
    ]

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


def test_the_engine_has_no_storage_of_its_own() -> None:
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        for needle in ("sqlite3", r"\.db[\"']", "shelve", "pickle"):
            if re.search(needle, text):
                offenders.append((path.name, needle))

    assert offenders == []
