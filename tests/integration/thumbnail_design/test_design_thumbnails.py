"""Thumbnail design on the real application: library, style, fonts, renderer, cut-outs.

Only background removal is replaced (``ColourSegmenter``: no model download in tests).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from media_house.bootstrap.application import Application, StartupOptions
from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.thumbnail_design.application.contracts import (
    OPERATION,
    DesignThumbnailsCommand,
    DeviceCard,
    Headline,
    StoryBadge,
    ThumbnailBrief,
    ThumbnailExporter,
)
from media_house.modules.thumbnail_design.application.design_thumbnails import DesignThumbnails
from media_house.modules.thumbnail_design.application.ports import ThumbnailRenderer
from media_house.modules.visual_asset_generation.application.adjust_image import AdjustImage
from media_house.modules.visual_asset_generation.application.contracts import FrameGrabber
from media_house.modules.visual_asset_generation.infrastructure.pillow_renderer import (
    PillowImageRenderer,
)
from media_house.modules.visual_style_engine.application.contracts import (
    FontFiles,
    VisualStyleCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.support.vag_fakes import ColourSegmenter, product_png

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    application = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    yield application
    application.shutdown()


@pytest.fixture
def designer(app: Application) -> DesignThumbnails:
    c = app.container
    library = c.resolve(MediaLibrary)
    return DesignThumbnails(
        library=library,
        paths=c.resolve(AppPaths),
        clock=c.resolve(Clock),
        adjuster=AdjustImage(library, PillowImageRenderer(ColourSegmenter()), c.resolve(AppPaths)),
        frames=c.resolve(FrameGrabber),
        styles=c.resolve(VisualStyleCatalog),
        fonts=c.resolve(FontFiles),
        renderer=c.resolve(ThumbnailRenderer),
    )


def _import(app: Application, path: Path) -> str:
    imported = app.container.resolve(MediaLibrary).import_file(path)
    assert isinstance(imported, Ok), imported
    return imported.value.asset.id


@pytest.fixture
def pictures(app: Application, tmp_path: Path) -> dict[str, str]:
    ids = {
        name: _import(app, product_png(tmp_path / f"{name}.png", box=box, size=(800, 600)))
        for name, box in (
            ("reaction", (200, 60, 640, 600)),
            ("seven", (100, 150, 700, 450)),
            ("arma", (120, 160, 680, 440)),
            ("pure", (140, 170, 660, 430)),
        )
    }
    logo = tmp_path / "logo.png"
    Image.new("RGB", (400, 400), (255, 220, 0)).save(logo)
    ids["logo"] = _import(app, logo)
    return ids


def _brief(p: dict[str, str]) -> ThumbnailBrief:
    return ThumbnailBrief(
        headline=Headline(("Erstmals", "online!")),
        reaction_asset_id=p["reaction"],
        products=(p["seven"], p["arma"]),
        logo_asset_id=p["logo"],
        device=DeviceCard(product_asset_id=p["pure"], product_name="Pure Impact", price="79,99 €"),
        badge=StoryBadge("04:30"),
    )


def test_designs_a_and_b_as_derived_pictures_and_reuses_them(
    app: Application, designer: DesignThumbnails, pictures: dict[str, str], tmp_path: Path
) -> None:
    command = DesignThumbnailsCommand(brief=_brief(pictures))
    first = designer.execute(command, JobContext.detached())
    assert isinstance(first, Ok), first
    variants = first.value.variants
    assert [v.name for v in variants] == ["a", "b"]
    library = app.container.resolve(MediaLibrary)
    for v in variants:
        assert v.created and v.asset is not None
        assert (v.asset.width, v.asset.height) == (1920, 1080)
        assert v.asset.derivation is not None
        assert v.asset.derivation.operation == OPERATION
        assert v.asset.derivation.source_asset_id == pictures["reaction"]
    assert variants[0].asset is not None and variants[1].asset is not None
    assert variants[0].asset.checksum != variants[1].asset.checksum  # the badge changed pixels

    again = designer.execute(command, JobContext.detached())
    assert isinstance(again, Ok)
    assert [v.created for v in again.value.variants] == [False, False]
    assert [v.asset.id for v in again.value.variants if v.asset] == [
        v.asset.id for v in variants if v.asset
    ]

    exported = app.container.resolve(ThumbnailExporter).execute(
        variants[1].asset.id, tmp_path / "out" / "thumb"
    )
    assert isinstance(exported, Ok)
    with Image.open(exported.value) as jpeg:
        assert (jpeg.format, jpeg.size) == ("JPEG", (1280, 720))
    assert exported.value.stat().st_size <= 2_000_000
    source = library.local_path(variants[1].asset.id)
    assert isinstance(source, Ok) and source.value.exists()  # the stored picture is untouched


def test_a_minimal_brief_gives_one_clean_variant(
    designer: DesignThumbnails, pictures: dict[str, str]
) -> None:
    brief = ThumbnailBrief(headline=Headline(("Online!",)), reaction_asset_id=pictures["reaction"])
    result = designer.execute(DesignThumbnailsCommand(brief=brief), JobContext.detached())
    assert isinstance(result, Ok), result
    (only,) = result.value.variants
    assert only.name == "a" and only.asset is not None
    assert [i.code for i in only.issues] == []
