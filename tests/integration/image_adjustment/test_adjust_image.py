"""Image adjustment end to end: real library (SQLite + blob store) and real pixel pipeline.

Only the AI model is replaced: ``ColourSegmenter`` finds the red subject by colour and counts
its calls, which is how the tests prove that reuse never re-segments.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from PIL import Image, ImageDraw
from scipy import ndimage

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.image_adjustment.application.adjust_image import (
    AdjustImage,
    AdjustImageCommand,
)
from media_house.modules.image_adjustment.domain.errors import ImageUnreadable, SegmentationRejected
from media_house.modules.image_adjustment.domain.values import (
    ADJUSTMENT_OPERATION,
    AdjustmentSettings,
    BackgroundRemoval,
    Canvas,
    Centering,
    Glow,
    HexColor,
)
from media_house.modules.image_adjustment.infrastructure.pillow_renderer import PillowImageRenderer
from media_house.modules.media_library.application.contracts import (
    DerivedAssetResultDto,
    MediaLibrary,
    MediaType,
)
from media_house.modules.media_library.module import MediaLibraryModule
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from tests.support.fakes import FixedClock, RecordingPublisher

pytestmark = pytest.mark.integration

BACKGROUND = (80, 120, 160)
RED = (200, 30, 40)


class ColourSegmenter:
    """Stand-in for the AI model. ``mode`` can simulate the failure cases."""

    def __init__(self, mode: str = "subject") -> None:
        self.mode = mode
        self.calls = 0

    def matte(self, image: Image.Image, model: str) -> NDArray[np.float32]:
        self.calls += 1
        pixels = np.asarray(image, dtype=np.float32)
        if self.mode == "empty":
            return np.zeros(pixels.shape[:2], dtype=np.float32)
        if self.mode == "full":
            return np.ones(pixels.shape[:2], dtype=np.float32)
        distance = np.abs(pixels - np.asarray(RED, dtype=np.float32)).sum(axis=-1)
        return (distance < 40).astype(np.float32)


@dataclass
class Env:
    library: MediaLibrary
    segmenter: ColourSegmenter
    adjuster: AdjustImage
    tmp: Path

    def write_source(self, name: str, box: tuple[int, int, int, int]) -> Path:
        path = self.tmp / f"{name}.png"
        image = Image.new("RGB", (400, 300), BACKGROUND)
        ImageDraw.Draw(image).ellipse(box, fill=RED)
        image.save(path)
        return path

    def make_source(
        self,
        name: str,
        *,
        box: tuple[int, int, int, int] = (120, 80, 280, 220),
        group_ids: tuple[str, ...] = (),
    ) -> str:
        imported = self.library.import_file(
            self.write_source(name, box),
            display_name=name,
            group_ids=group_ids,
        )
        assert isinstance(imported, Ok)
        return imported.value.asset.id

    def adjust(
        self,
        source_id: str,
        config: AdjustmentSettings | None = None,
        group_ids: tuple[str, ...] = (),
    ) -> DerivedAssetResultDto:
        command = AdjustImageCommand(source_id, config or AdjustmentSettings(), group_ids)
        result = self.adjuster.execute(command, JobContext.detached())
        assert isinstance(result, Ok), result
        return result.value

    def pixels(self, result: DerivedAssetResultDto) -> NDArray[np.uint8]:
        path = self.library.local_path(result.asset.id)
        assert isinstance(path, Ok)
        with Image.open(path.value) as image:
            assert image.mode == "RGBA"
            return np.asarray(image).copy()


class _NoProcesses:
    """Only the thumbnail generator would use a ProcessRunner; these tests never ask for one."""


@pytest.fixture
def env(tmp_path: Path) -> Env:
    container = Container()
    paths = AppPaths.under_root(tmp_path / "app")
    container.register_instance(AppPaths, paths)
    container.register_instance(Clock, FixedClock())
    container.register_instance(EventPublisher, RecordingPublisher())
    container.register_instance(ProcessRunner, _NoProcesses())
    MediaLibraryModule().register(container)
    segmenter = ColourSegmenter()
    library = container.resolve(MediaLibrary)
    sources = tmp_path / "src"
    sources.mkdir()
    return Env(
        library, segmenter, AdjustImage(library, PillowImageRenderer(segmenter), paths), sources
    )


PINK = Glow(enabled=True, color=HexColor("#FF2A5F"), opacity=0.85, radius=30, spread=12)
BLUE = Glow(enabled=True, color=HexColor("#2A5FFF"), opacity=0.85, radius=30, spread=12)


def settings(
    glow: Glow | None = None,
    *,
    width: int = 1080,
    height: int = 1080,
    padding: int = 60,
    centering: Centering = Centering.CENTER,
) -> AdjustmentSettings:
    return AdjustmentSettings(
        glow=glow or Glow(enabled=False),
        canvas=Canvas(width=width, height=height, padding=padding, centering=centering),
    )


def border_alpha_max(out: NDArray[np.uint8]) -> int:
    alpha = out[..., 3]
    return int(max(alpha[0].max(), alpha[-1].max(), alpha[:, 0].max(), alpha[:, -1].max()))


# A ---------------------------------------------------------------------------------------
def test_new_image_is_processed_and_stored_as_a_derived_library_asset(env: Env) -> None:
    source_id = env.make_source("product")

    result = env.adjust(source_id, settings(PINK))

    asset = result.asset
    assert result.created
    assert (asset.media_type, asset.mime_type) == (MediaType.IMAGE, "image/png")
    assert (asset.width, asset.height) == (1080, 1080)
    assert asset.derivation is not None
    assert asset.derivation.source_asset_id == source_id
    assert asset.derivation.operation == ADJUSTMENT_OPERATION
    assert asset.metadata["source_asset_id"] == source_id
    assert asset.metadata["glow_color"] == "#FF2A5F"
    assert [a.id for a in env.library.list_derived(source_id)] == [asset.id]
    assert asset.id in [a.id for a in env.library.search().items]


# B ---------------------------------------------------------------------------------------
def test_without_glow_only_the_clean_subject_remains(env: Env) -> None:
    out = env.pixels(env.adjust(env.make_source("product"), settings()))

    alpha = out[..., 3]
    assert border_alpha_max(out) == 0
    visible = out[alpha > 10][:, :3].astype(int)
    # No background-coloured or white/dark fringe: every visible pixel is the subject's red.
    assert np.abs(visible - np.asarray(RED)).max() <= 12
    assert (alpha == 255).sum() > 0.1 * alpha.size


# C ---------------------------------------------------------------------------------------
def test_glow_sits_behind_an_unchanged_subject_and_is_not_clipped(env: Env) -> None:
    source_id = env.make_source("product")
    # Same total margin, so subject size and position are identical with and without glow.
    plain = env.pixels(env.adjust(source_id, settings(padding=20 + PINK.extent)))
    glowing = env.pixels(env.adjust(source_id, settings(PINK, padding=20)))

    solid = plain[..., 3] == 255
    assert solid.any()
    assert np.array_equal(glowing[solid], plain[solid])  # glow never touches opaque subject pixels

    edge = ndimage.binary_dilation(solid, iterations=20)  # anti-aliased rim of the subject itself
    halo = glowing[~edge & (glowing[..., 3] > 20)]
    assert len(halo) > 1000
    assert np.abs(halo[:, :3].astype(int) - np.asarray(HexColor("#FF2A5F").rgb)).max() <= 12
    assert border_alpha_max(glowing) == 0


# D ---------------------------------------------------------------------------------------
def test_each_configuration_is_its_own_asset(env: Env) -> None:
    source_id = env.make_source("product")

    results = [
        env.adjust(source_id, settings()),
        env.adjust(source_id, settings(PINK)),
        env.adjust(source_id, settings(BLUE)),
        env.adjust(source_id, settings(width=1920, height=1080)),
    ]

    assert len({r.asset.id for r in results}) == 4
    assert all(r.created for r in results)
    assert len(env.library.list_derived(source_id)) == 4
    assert (results[3].asset.width, results[3].asset.height) == (1920, 1080)


# E ---------------------------------------------------------------------------------------
def test_identical_request_reuses_the_stored_asset_without_processing(env: Env) -> None:
    source_id = env.make_source("product")
    first = env.adjust(source_id, settings(PINK))
    assert env.segmenter.calls == 1

    second = env.adjust(source_id, settings(PINK))

    assert (first.created, second.created) == (True, False)
    assert second.asset.id == first.asset.id
    assert env.segmenter.calls == 1
    assert len(env.library.list_derived(source_id)) == 1


def test_disabled_glow_details_do_not_create_a_second_variant(env: Env) -> None:
    source_id = env.make_source("product")
    first = env.adjust(source_id, settings(Glow(enabled=False, color=HexColor("#00FF00"))))
    second = env.adjust(source_id, settings(Glow(enabled=False, radius=77)))

    assert second.asset.id == first.asset.id
    assert env.segmenter.calls == 1


# F ---------------------------------------------------------------------------------------
def test_a_different_source_gets_its_own_asset(env: Env) -> None:
    a = env.make_source("a")
    b = env.make_source("b", box=(60, 60, 200, 240))

    first = env.adjust(a, settings(PINK))
    second = env.adjust(b, settings(PINK))

    assert first.asset.id != second.asset.id
    assert second.created
    assert env.segmenter.calls == 2


# G ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["empty", "full"])
def test_an_invalid_mask_is_rejected_and_nothing_is_stored(env: Env, mode: str) -> None:
    source_id = env.make_source("product")
    env.segmenter.mode = mode

    result = env.adjuster.execute(
        AdjustImageCommand(source_id, settings(PINK)),
        JobContext.detached(),
    )

    assert isinstance(result, Err)
    assert isinstance(result.error, SegmentationRejected)
    assert env.library.list_derived(source_id) == []


def test_a_rejected_mask_can_be_retried_after_the_cause_is_fixed(env: Env) -> None:
    source_id = env.make_source("product")
    env.segmenter.mode = "empty"
    failed = env.adjuster.execute(AdjustImageCommand(source_id), JobContext.detached())
    assert isinstance(failed, Err)
    env.segmenter.mode = "subject"

    assert env.adjust(source_id).created


def test_unreadable_source_is_an_expected_error(env: Env) -> None:
    imported = env.library.import_file(env.write_source("broken", (10, 10, 50, 50)))
    assert isinstance(imported, Ok)
    stored = env.library.local_path(imported.value.asset.id)
    assert isinstance(stored, Ok)
    stored.value.write_bytes(b"not an image")  # corrupt the blob after import

    result = env.adjuster.execute(
        AdjustImageCommand(imported.value.asset.id),
        JobContext.detached(),
    )

    assert isinstance(result, Err)
    assert isinstance(result.error, ImageUnreadable)


# H ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("centering", [Centering.CENTER, Centering.CENTER_OF_MASS])
@pytest.mark.parametrize("size", [(300, 200), (1080, 1920), (1920, 1080)])
def test_subject_and_glow_stay_inside_the_canvas(
    env: Env,
    centering: Centering,
    size: tuple[int, int],
) -> None:
    source_id = env.make_source("product", box=(10, 150, 390, 290))  # wide, near the edge
    glow = Glow(enabled=True, radius=24, spread=10)

    out = env.pixels(
        env.adjust(
            source_id,
            settings(glow, width=size[0], height=size[1], padding=0, centering=centering),
        ),
    )

    assert out.shape[:2] == (size[1], size[0])
    assert border_alpha_max(out) == 0
    ys, xs = np.nonzero(out[..., 3] == 255)
    assert xs.min() >= glow.extent - 1
    assert xs.max() <= size[0] - glow.extent
    assert ys.min() >= glow.extent - 1
    assert ys.max() <= size[1] - glow.extent


def test_background_removal_can_be_turned_off(env: Env) -> None:
    source_id = env.make_source("product")

    result = env.adjust(
        source_id,
        AdjustmentSettings(background_removal=BackgroundRemoval(enabled=False)),
    )

    assert env.segmenter.calls == 0
    assert (env.pixels(result)[..., 3] == 255).sum() > 0.25 * 1080 * 1080  # whole picture kept


# groups ----------------------------------------------------------------------------------
def test_result_joins_the_source_groups_and_requested_groups(env: Env) -> None:
    products = env.library.create_group("Products")
    extra = env.library.create_group("Processed")
    assert isinstance(products, Ok)
    assert isinstance(extra, Ok)
    source_id = env.make_source("grouped", group_ids=(products.value.id,))

    first = env.adjust(source_id, group_ids=(extra.value.id,))
    again = env.adjust(source_id, group_ids=(extra.value.id,))

    assert set(first.asset.group_ids) == {products.value.id, extra.value.id}
    assert again.asset.id == first.asset.id
    assert set(again.asset.group_ids) == set(first.asset.group_ids)
