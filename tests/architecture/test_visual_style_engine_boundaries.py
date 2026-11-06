"""The Visual Style Engine defines and validates the channel look; it renders and decides nothing.

* It reaches other modules only through their public contracts (only the Media Library).
* Its domain imports no other module at all.
* It has no Layer 4 dependency (it is the root of the Layer 4 graph) and runs no producer.
* Only the Layer 4 consumers, the pipeline and the composition root depend on it.
* Fonts are read with the standard library only (no font, image or model library).
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
STYLE = "media_house.modules.visual_style_engine"
DOMAIN = f"{STYLE}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "visual_style_engine"

#: Producers the style engine must never run.
PRODUCERS = (
    "AudioAnalyzer",
    "VideoAnalyzer",
    "ContentAnalyzer",
    "MediaInspector",
    "StoryPlanner",
    "EditPlanner",
    "ReframingAnalyzer",
    "ProcessRunner",
    "ProcessSpec",
)

#: Allowlist from docs/layer4/contracts.md section 2.
CONSUMERS = (
    "media_house.modules.motion_graphics_engine",
    "media_house.modules.caption_engine",
    "media_house.modules.transition_engine",
    "media_house.modules.vfx_engine",
    "media_house.modules.visual_asset_generation",
    "media_house.modules.edit_plan_compiler",  # Layer 5: binds the identity, runs its validator
    "media_house.modules.media_pipeline",
    "media_house.bootstrap",
)


def module_files() -> list[Path]:
    return sorted(MODULE_DIR.rglob("*.py"))


def test_style_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(STYLE)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(STYLE)
        and not i.target.startswith("media_house.modules.media_library.application.contracts")
    ]

    assert offenders == []


def test_style_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(STYLE)
    ]

    assert offenders == []


def test_style_uses_no_process_image_font_or_model_library() -> None:
    forbidden = {
        "sqlite3", "subprocess", "shutil", "os", "numpy", "scipy", "torch", "cv2", "PIL", "av",
        "fontTools", "freetype", "transformers",
    }  # fmt: skip
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(STYLE)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_style_never_runs_a_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in module_files()
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_only_layer4_consumers_the_pipeline_and_the_composition_root_depend_on_style() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(STYLE)
        and not name.startswith(CONSUMERS)
        and any(i.target.startswith(STYLE) for i in info.imports)
    ]

    assert offenders == []


def test_style_names_no_timeline_backend_and_knows_no_edit_plan() -> None:
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


def test_bundled_fonts_ship_with_their_licence() -> None:
    fonts = MODULE_DIR / "infrastructure" / "fonts"
    families = [d for d in fonts.iterdir() if d.is_dir()]

    assert families
    for family in families:
        assert (family / "OFL.txt").is_file(), family.name
