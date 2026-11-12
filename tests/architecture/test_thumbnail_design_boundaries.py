"""Thumbnail Design composes still pictures; it analyses, plans and segments nothing itself.

* It reaches other modules only through their public contracts, and only the ones it reads: the
  library, visual asset generation (cut-outs, frames), the visual style and stored video analyses.
* Its domain imports no other module and no pixel library; pixels live in infrastructure only.
* It never runs another module's producer: styles and analyses are READ through their catalogs.
* Only the pipeline, the Control Center and the composition root depend on it.
"""

import re
from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
MODULE = "media_house.modules.thumbnail_design"
DOMAIN = f"{MODULE}.domain"
MODULE_DIR = SRC / "media_house" / "modules" / "thumbnail_design"

ALLOWED_UPSTREAM = (
    "media_house.modules.media_library.application.contracts",
    "media_house.modules.visual_asset_generation.application.contracts",
    "media_house.modules.visual_style_engine.application.contracts",
    "media_house.modules.video_intelligence.application.contracts",
)
PRODUCERS = (
    "VideoAnalyzer",
    "AudioAnalyzer",
    "ContentAnalyzer",
    "MediaInspector",
    "StyleResolver",
    "EditPlanner",
    "StoryPlanner",
)
PIXEL_LIBRARIES = frozenset({"PIL", "numpy", "scipy", "rembg", "cv2", "torch", "skimage"})
CONSUMERS = (
    "media_house.modules.media_pipeline",
    "media_house.modules.control_center",
    "media_house.bootstrap",
)


def test_it_imports_other_modules_only_through_allowed_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MODULE)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(MODULE)
        and not i.target.startswith(ALLOWED_UPSTREAM)
    ]

    assert offenders == []


def test_the_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(MODULE)
    ]

    assert offenders == []


def test_pixels_live_in_infrastructure_only() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(MODULE) and not name.startswith(f"{MODULE}.infrastructure")
        for i in info.imports
        if i.target.split(".")[0] in PIXEL_LIBRARIES | {"subprocess", "shutil"}
    ]

    assert offenders == []


def test_it_never_runs_an_upstream_producer() -> None:
    pattern = re.compile(r"\b(" + "|".join(PRODUCERS) + r")\b")
    offenders = [
        (path.name, hit.group(0))
        for path in sorted(MODULE_DIR.rglob("*.py"))
        for hit in pattern.finditer(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_only_the_pipeline_the_control_center_and_the_composition_root_depend_on_it() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(MODULE)
        and not name.startswith(CONSUMERS)
        and any(i.target.startswith(MODULE) for i in info.imports)
    ]

    assert offenders == []


def test_consumers_use_the_contracts_only() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if not name.startswith(MODULE) and not name.startswith("media_house.bootstrap")
        for i in info.imports
        if i.target.startswith(MODULE)
        and not i.target.startswith(f"{MODULE}.application.contracts")
    ]

    assert offenders == []
