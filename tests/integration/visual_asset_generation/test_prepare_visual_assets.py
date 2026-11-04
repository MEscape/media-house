"""Batch preparation end to end on the real application (library, files, pixels, documents)."""

from datetime import UTC, datetime
from typing import Any, cast

import numpy as np
import pytest
from numpy.typing import NDArray
from scipy import ndimage

from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.visual_asset_generation.application.adjust_image import (
    AdjustImageCommand,
)
from media_house.modules.visual_asset_generation.application.contracts import (
    ADJUSTMENT_OPERATION,
    ENHANCEMENT_OPERATION,
    EXPORT_OPERATION,
    SET_OPERATION,
    AssetKind,
    AssetRequest,
    ExportSettings,
    HexColor,
    ImageFormat,
    InvalidVisualAssetRequest,
    ItemStatus,
    PreparedVisualAssets,
    PrepareVisualAssetsCommand,
    ResultState,
    StageState,
    VariantName,
    VisualAssetCatalog,
    VisualAssetConfig,
    VisualAssetPreparer,
)
from media_house.modules.visual_asset_generation.application.prepare_visual_assets import (
    PrepareVisualAssets,
)
from media_house.modules.visual_style_engine.application.contracts import (
    MESC_IDENTITY_V1,
    StyleConfig,
    resolve_style,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, ExternalSystemError, Ok
from tests.integration.visual_asset_generation.conftest import Lab
from tests.support.vag_fakes import RED, border_alpha_max, rgba_of

pytestmark = pytest.mark.integration

ALL = (
    VariantName.STICKER,
    VariantName.SILHOUETTE,
    VariantName.GLOW,
    VariantName.SHADOW,
    VariantName.OUTLINE,
)


def prepare(
    preparer: PrepareVisualAssets, *requests: AssetRequest, **kwargs: object
) -> PreparedVisualAssets:
    result = preparer.execute(
        PrepareVisualAssetsCommand(requests=requests, **kwargs),  # type: ignore[arg-type]
        JobContext.detached(),
    )
    assert isinstance(result, Ok), result
    return result.value


def asset(lab: Lab, asset_id: str | None) -> MediaAssetDto:
    assert asset_id is not None
    found = lab.library.get(asset_id)
    assert isinstance(found, Ok)
    return found.value


def pixels(lab: Lab, asset_id: str | None) -> NDArray[np.uint8]:
    path = lab.library.local_path(asset(lab, asset_id).id)
    assert isinstance(path, Ok)
    return rgba_of(path.value)


def test_clean_cutout_and_every_variant_become_linked_library_assets(lab: Lab) -> None:
    source = lab.product("titan")
    done = prepare(
        lab.preparer(),
        AssetRequest(request_id="r1", source_asset_id=source, variants=ALL, purpose="rank 3"),
    )

    document = done.document
    assert done.created and document.state is ResultState.OK
    clean = next(i for i in document.items if i.variant == "clean")
    assert clean.kind is AssetKind.CLEAN_CUTOUT and clean.status is ItemStatus.OK
    clean_asset = asset(lab, clean.asset_id)
    assert clean_asset.derivation is not None
    assert clean_asset.derivation.source_asset_id == source
    assert clean_asset.derivation.operation == ADJUSTMENT_OPERATION
    for variant in ALL:
        item = next(i for i in document.items if i.variant == variant.value)
        assert item.status is ItemStatus.OK, item
        derived = asset(lab, item.asset_id)
        assert derived.derivation is not None
        assert derived.derivation.source_asset_id == clean_asset.id  # linked to the clean cutout
        assert derived.derivation.operation == ENHANCEMENT_OPERATION
        assert item.source_asset_id == source and item.clean_cutout_asset_id == clean_asset.id
        out = pixels(lab, item.asset_id)
        assert border_alpha_max(out) == 0, variant  # no effect is clipped at the canvas edge
    assert len({i.asset_id for i in document.items}) == 1 + len(ALL)
    assert lab.segmenter.calls == 1  # variants are made from the clean cutout, not re-segmented
    assert done.asset.metadata["processing_type"] == SET_OPERATION


def test_the_clean_cutout_is_the_same_asset_the_image_adjuster_makes(lab: Lab) -> None:
    source = lab.product("titan")
    first = lab.adjuster().execute(AdjustImageCommand(source), JobContext.detached())
    assert isinstance(first, Ok)

    done = prepare(lab.preparer(), AssetRequest(request_id="r1", source_asset_id=source))

    clean = done.document.items[0]
    assert clean.asset_id == first.value.asset.id and clean.reused
    assert lab.segmenter.calls == 1


def test_effects_never_touch_the_clean_cutout_or_the_opaque_subject(lab: Lab) -> None:
    source = lab.product("titan")
    done = prepare(
        lab.preparer(),
        AssetRequest(request_id="r1", source_asset_id=source, variants=(VariantName.GLOW,)),
    )
    clean_item, glow_item = done.document.items
    clean, glow = pixels(lab, clean_item.asset_id), pixels(lab, glow_item.asset_id)

    assert asset(lab, glow_item.asset_id).metadata["subject_scale"] == 1.0
    solid = glow[..., 3] == 255
    reds = glow[solid][:, :3].astype(int)
    rim = ndimage.binary_dilation(glow[..., 3] >= 128, iterations=6)  # the subject's own soft edge
    halo = glow[~rim & (glow[..., 3] > 20)][:, :3].astype(int)
    assert np.abs(reds - np.asarray(RED)).max(axis=1).min() <= 2  # the subject keeps its red
    assert len(halo) > 500 and halo.min() >= 200  # the halo is the white glow
    assert int((clean[..., 3] > 0).sum()) < int((glow[..., 3] > 0).sum())


def test_an_identical_batch_reuses_the_stored_set_and_computes_nothing(lab: Lab) -> None:
    source = lab.product("titan")
    request = AssetRequest(request_id="r1", source_asset_id=source, variants=ALL)
    first = prepare(lab.preparer(), request)
    second = prepare(lab.preparer(), request)

    assert (first.created, second.created) == (True, False)
    assert second.asset.id == first.asset.id and second.document == first.document
    assert lab.segmenter.calls == 1


def test_a_changed_batch_is_a_new_version_that_names_its_predecessor(lab: Lab) -> None:
    source = lab.product("titan")
    first = prepare(lab.preparer(), AssetRequest(request_id="r1", source_asset_id=source))
    second = prepare(
        lab.preparer(),
        AssetRequest(request_id="r1", source_asset_id=source),
        config=VisualAssetConfig(overrides={"variants.sticker.tilt.degrees": 8.0}),
    )

    assert second.document.plan_version == 2
    assert second.document.supersedes == first.asset.id
    catalog = lab.resolve(VisualAssetCatalog)
    latest = catalog.latest(source)
    assert isinstance(latest, Ok) and latest.value is not None
    assert latest.value[0] == second.document
    assert [a.id for a in catalog.history(source)] == [second.asset.id, first.asset.id]


def test_a_failed_segmentation_keeps_the_original_and_reports_it(lab: Lab) -> None:
    source = lab.product("titan")
    lab.segmenter.mode = "empty"
    done = prepare(
        lab.preparer(),
        AssetRequest(request_id="r1", source_asset_id=source, variants=(VariantName.STICKER,)),
    )

    document = done.document
    assert document.state is ResultState.PARTIAL
    assert all(i.status is ItemStatus.FAILED and i.asset_id is None for i in document.items)
    assert document.asset_for("r1", "clean") == source  # the untouched original stands in
    assert document.asset_for("r1", "sticker") == source
    assert "mask_rejected" in {c.code for c in document.checks}
    assert {s.stage: s.state for s in document.stages}["cutout"] is StageState.FAILED
    produced = [
        a
        for a in lab.library.list_derived(source)
        if a.metadata["processing_type"] != SET_OPERATION
    ]
    assert produced == []  # nothing half-made was registered


def test_exports_keep_alpha_refuse_silent_flattening_and_skip_needless_copies(lab: Lab) -> None:
    source = lab.product("titan")
    preparer = lab.preparer()
    webp = prepare(
        preparer,
        AssetRequest(
            request_id="w",
            source_asset_id=source,
            export=ExportSettings(format=ImageFormat.WEBP, max_width=512),
        ),
    ).document
    jpeg = prepare(
        preparer,
        AssetRequest(
            request_id="j", source_asset_id=source, export=ExportSettings(format=ImageFormat.JPEG)
        ),
    ).document
    flat = prepare(
        preparer,
        AssetRequest(
            request_id="f",
            source_asset_id=source,
            export=ExportSettings(format=ImageFormat.JPEG, flatten_onto=HexColor("#000000")),
        ),
    ).document
    png = prepare(
        preparer,
        AssetRequest(request_id="p", source_asset_id=source, export=ExportSettings()),
    ).document

    exported = next(i for i in webp.items if i.kind is AssetKind.EXPORT)
    assert (exported.status, exported.format, exported.has_alpha) == (ItemStatus.OK, "webp", True)
    assert (exported.width, exported.height) == (512, 512)
    assert asset(lab, exported.asset_id).derivation.operation == EXPORT_OPERATION  # type: ignore[union-attr]
    refused = next(i for i in jpeg.items if i.kind is AssetKind.EXPORT)
    assert refused.status is ItemStatus.FAILED and "transparency" in refused.detail
    assert refused.fallback_asset_id == jpeg.items[0].asset_id  # the PNG cutout
    flattened = next(i for i in flat.items if i.kind is AssetKind.EXPORT)
    assert (flattened.status, flattened.format, flattened.has_alpha) == (
        ItemStatus.OK,
        "jpeg",
        False,
    )
    kept = next(i for i in png.items if i.kind is AssetKind.EXPORT)
    assert kept.asset_id == png.items[0].asset_id and kept.reused  # not recompressed


def test_an_invalid_batch_is_refused_and_nothing_is_stored(lab: Lab) -> None:
    source = lab.product("titan")
    request = AssetRequest(request_id="r1", source_asset_id=source)
    result = lab.preparer().execute(
        PrepareVisualAssetsCommand(requests=(request, request)), JobContext.detached()
    )

    assert isinstance(result, Err) and isinstance(result.error, InvalidVisualAssetRequest)
    assert lab.library.list_derived(source) == []


def test_an_unknown_source_fails_its_items_without_failing_the_batch(lab: Lab) -> None:
    source = lab.product("titan")
    done = prepare(
        lab.preparer(),
        AssetRequest(request_id="ok", source_asset_id=source),
        AssetRequest(request_id="gone", source_asset_id="no-such-asset"),
    )

    assert done.document.state is ResultState.PARTIAL
    assert done.document.asset_for("ok", "clean") is not None
    assert done.document.asset_for("gone", "clean") is None


def test_the_channel_style_sets_the_sticker_values_and_is_recorded(lab: Lab) -> None:
    source = lab.product("titan")
    style = resolve_style(
        MESC_IDENTITY_V1,
        StyleConfig(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        width=1080,
        height=1080,
    )
    plain = prepare(
        lab.preparer(),
        AssetRequest(request_id="r1", source_asset_id=source, variants=(VariantName.STICKER,)),
    )
    styled = prepare(
        lab.preparer(),
        AssetRequest(request_id="r1", source_asset_id=source, variants=(VariantName.STICKER,)),
        style=style,
    )

    document = styled.document
    assert document.style is not None and document.style.fingerprint == style.fingerprint
    assert plain.document.style is None
    recorded = next(i for i in document.inputs if i.kind == "visual_style")
    assert recorded.identity == style.fingerprint
    sticker = asset(lab, document.asset_for("r1", "sticker"))
    assert sticker.derivation is not None
    effects = cast("dict[str, Any]", sticker.derivation.config["enhancement"])
    graphics = style.tokens.graphics
    assert effects["glow"]["color"] == style.hex("glow")
    middle = (graphics.sticker_tilt_min + graphics.sticker_tilt_max) / 2.0
    assert effects["tilt"]["degrees"] == -middle


def test_the_wired_application_fails_honestly_without_a_background_model(lab: Lab) -> None:
    from media_house.modules.visual_asset_generation.domain.values import DEFAULT_MODEL
    from media_house.modules.visual_asset_generation.infrastructure.rembg_segmenter import _on_disk

    if _on_disk(DEFAULT_MODEL):  # the premise: this machine has no background model
        pytest.skip(f"{DEFAULT_MODEL} is installed here: there is nothing to fail on")
    source = lab.product("titan")
    preparer = lab.resolve(VisualAssetPreparer)

    result = preparer.execute(
        PrepareVisualAssetsCommand(
            requests=(AssetRequest(request_id="r", source_asset_id=source),)
        ),
        JobContext.detached(),
    )

    assert isinstance(result, Err) and isinstance(result.error, ExternalSystemError)
    assert "fetch_models.py --background-removal" in result.error.user_message
    assert lab.library.list_derived(source) == []  # no set is stored, so a rerun recomputes
