"""The final export joins the modules that own each part, in the one order that keeps a person in
control. It renders nothing itself, measures nothing, decides no creative question and talks to
Resolve only through the Resolve adapter's contract.

* It reaches other modules only through their public contract.
* Its domain imports no other module at all.
* It never renders, validates or mixes by itself: it asks the render manager, the validator and the
  review; it names no media tool and no editing application API.
* Only ``resolve_export`` may start a FINAL render: that call carries the authorization, and no
  other module in the codebase requests ``RenderType.FINAL``.
* It never publishes or uploads: no network, no platform.
* Its one disk adapter (the delivery folder) lives in the infrastructure and replaces nothing.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
EXPORT = "media_house.modules.resolve_export"
DOMAIN = f"{EXPORT}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "resolve_export"

READS = (
    "media_house.modules.media_library",
    "media_house.modules.audio_mix_engine",
    "media_house.modules.export_validation",
    "media_house.modules.final_polish",
    "media_house.modules.quality_control",
    "media_house.modules.render_manager",
    "media_house.modules.resolve_adapter",
    "media_house.modules.review_orchestrator",
    "media_house.modules.timeline_builder",
    EXPORT,
)
CONSUMERS = ("media_house.modules.media_pipeline", "media_house.bootstrap", EXPORT)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_resolve_export_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(EXPORT)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(EXPORT)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_resolve_export_reads_only_what_it_needs() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(EXPORT)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(READS)
    ]

    assert offenders == []


def test_resolve_export_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(EXPORT)
    ]

    assert offenders == []


def test_it_renders_validates_and_mixes_nothing_itself() -> None:
    pattern = re.compile(
        r"ffmpeg|ffprobe|ProcessSpec|ProcessRunner|MediaAnalyzer|MediaInspector|AudioMixer|"
        r"RenderMixCommand|ResolveRenderer|davinci|fusionscript",
        re.IGNORECASE,
    )
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_only_resolve_export_asks_for_a_final_render() -> None:
    allowed = (EXPORT, "media_house.modules.render_manager")
    offenders = []
    for path in sorted((SRC / "media_house" / "modules").rglob("*.py")):
        name = ".".join(path.relative_to(SRC).with_suffix("").parts)
        if name.startswith(allowed):
            continue
        if re.search(r"RenderType\.FINAL", path.read_text(encoding="utf-8")):
            offenders.append(name)

    assert offenders == []


def test_the_final_render_always_carries_the_authorization() -> None:
    text = (MODULE_DIR / "application" / "run_export.py").read_text(encoding="utf-8")

    assert "RenderType.FINAL" in text and "authorization=RenderAuthorization(" in text
    assert (
        text.index("verify_export(")
        < text.index("begin_export(")
        < text.index("self._renders.render(")
    )


def test_nothing_is_published_or_uploaded() -> None:
    pattern = re.compile(
        r"\b(requests|urllib|http\.client|socket|ftplib|smtplib|boto3|googleapiclient)\b"
    )
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_no_storage_of_its_own_and_the_delivery_disk_adapter_is_infrastructure() -> None:
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        for needle in ("sqlite3", "shelve", "pickle"):
            if needle in text:
                offenders.append((path.name, needle))
        if "shutil" in text and path.parent.name != "infrastructure":
            offenders.append((path.name, "shutil outside the infrastructure"))

    assert offenders == []


def test_only_the_pipeline_and_the_composition_root_depend_on_it() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(CONSUMERS) and any(i.target.startswith(EXPORT) for i in info.imports)
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
