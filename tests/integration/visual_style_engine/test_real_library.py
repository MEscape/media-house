"""Resolve, store, pin, reuse, version and validate styles in a real Application."""

from dataclasses import replace
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.visual_style_engine.application.contracts import (
    CURRENT_IDENTITY_VERSION,
    BrandAssetBinding,
    FontFiles,
    InvalidStyleConfig,
    Override,
    PlacementUse,
    ResolveStyleCommand,
    ResolveStyleResult,
    Severity,
    StyleConfig,
    StyleRef,
    StyleRefMismatch,
    StyleResolver,
    StyleUsage,
    StyleValidator,
    UnknownIdentityVersion,
    VisualStyleCatalog,
)
from media_house.modules.visual_style_engine.domain.serialization import style_to_json
from media_house.modules.visual_style_engine.domain.values import ElementKind
from media_house.shared.errors import Err, Ok
from tests.integration.visual_style_engine.conftest import import_image

pytestmark = pytest.mark.integration


def resolve(app: Application, command: ResolveStyleCommand) -> ResolveStyleResult:
    result = app.container.resolve(StyleResolver).execute(command)
    assert isinstance(result, Ok), result
    return result.value


def test_a_style_is_stored_reused_and_pinned(app: Application, tmp_path: Path) -> None:
    anchor = import_image(app, tmp_path, "clip", "black")
    command = ResolveStyleCommand(
        anchor_asset_id=anchor.id,
        project_id="proj-1",
        width=1920,
        height=1080,
        story_format="top_n",
        config=StyleConfig(sponsored=True),
    )

    first = resolve(app, command)
    again = resolve(app, command)

    assert first.created and not again.created
    assert again.document.id == first.document.id
    assert first.style.profile == "ranking" and first.style.style_version == 1
    assert first.ref == StyleRef(
        document_id=first.document.id,
        fingerprint=first.style.fingerprint,
        identity_version=CURRENT_IDENTITY_VERSION,
        profile="ranking",
    )
    assert "visual_style" in first.document.tags
    assert first.document.metadata["processing_type"] == "visual_style"
    assert not [i for i in first.style.checks if i.severity is Severity.ERROR]

    catalog = app.container.resolve(VisualStyleCatalog)
    latest = catalog.latest(anchor.id, project_id="proj-1")
    assert isinstance(latest, Ok) and latest.value is not None
    assert latest.value[0] == first.style
    assert catalog.load_ref(first.ref) == Ok(first.style)


def test_a_changed_request_becomes_a_new_version_that_supersedes(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_image(app, tmp_path, "clip", "black")
    base = ResolveStyleCommand(anchor_asset_id=anchor.id, project_id="p", width=1920, height=1080)
    v1 = resolve(app, base)
    tweak = Override("motion.stagger", 0.1, "owner wants a slower cascade")
    v2 = resolve(
        app,
        ResolveStyleCommand(
            anchor_asset_id=anchor.id,
            project_id="p",
            width=1920,
            height=1080,
            config=StyleConfig(overrides=(tweak,)),
        ),
    )

    assert (v2.style.style_version, v2.style.supersedes) == (2, v1.document.id)
    assert v2.style.fingerprint != v1.style.fingerprint
    history = app.container.resolve(VisualStyleCatalog).history(anchor.id)
    assert [a.id for a in history] == [v2.document.id, v1.document.id]
    stale = StyleRef(
        document_id=v2.document.id,
        fingerprint=v1.style.fingerprint,
        identity_version=1,
        profile="generic",
    )
    loaded = app.container.resolve(VisualStyleCatalog).load_ref(stale)
    assert isinstance(loaded, Err) and isinstance(loaded.error, StyleRefMismatch)


def test_a_project_resolved_under_v1_keeps_v1_and_gets_v2_only_on_request(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_image(app, tmp_path, "clip", "black")
    base = ResolveStyleCommand(anchor_asset_id=anchor.id, project_id="old", width=1080, height=1920)
    old = resolve(app, replace(base, identity_version=1))
    pinned = resolve(app, base)  # no version named: the project's stored version
    upgraded = resolve(app, replace(base, identity_version=2))

    assert not pinned.created and pinned.style.identity_version == 1
    assert old.style.tokens.emoji is None and old.style.tokens.transitions is None
    assert '"transitions"' not in style_to_json(old.style).split('"tokens"', 1)[1]
    assert upgraded.style.identity_version == 2 and upgraded.created
    assert upgraded.style.tokens.emoji is not None
    assert upgraded.style.face(upgraded.style.tokens.emoji.face).family == "Noto Color Emoji"
    assert not [i for i in upgraded.style.checks if i.code.startswith("font.")]
    tr = upgraded.style.tokens.transitions
    assert tr is not None and "whip_pan" not in tr.approved  # shorts_meme: no movement


def test_the_no_speech_path_needs_only_an_anchor(app: Application, tmp_path: Path) -> None:
    anchor = import_image(app, tmp_path, "meme", "white")

    plain = resolve(app, ResolveStyleCommand(anchor_asset_id=anchor.id))
    vertical = resolve(app, ResolveStyleCommand(anchor_asset_id=anchor.id, width=1080, height=1920))

    assert (plain.style.profile, plain.style.output.width) == ("generic", 1920)
    assert plain.style.project_id is None
    assert vertical.style.profile == "shorts_meme"


def test_brand_assets_are_library_assets(app: Application, tmp_path: Path) -> None:
    anchor = import_image(app, tmp_path, "clip", "black")
    plate = import_image(app, tmp_path, "fire_plate", "orange")

    bound = resolve(
        app,
        ResolveStyleCommand(
            anchor_asset_id=anchor.id, brand_assets=(BrandAssetBinding("fire_plate", plate.id),)
        ),
    )

    asset = next(a for a in bound.style.brand_assets if a.slot == "fire_plate")
    assert (asset.asset_id, asset.checksum) == (plate.id, plate.checksum)
    missing = app.container.resolve(StyleResolver).execute(
        ResolveStyleCommand(
            anchor_asset_id=anchor.id, brand_assets=(BrandAssetBinding("logo", "no-such-asset"),)
        )
    )
    assert isinstance(missing, Err)


def test_invalid_requests_are_refused_and_nothing_is_stored(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_image(app, tmp_path, "clip", "black")
    resolver = app.container.resolve(StyleResolver)
    locked = StyleConfig(overrides=(Override("palette.brand_red", "#FF0000", "brighter"),))

    refused = resolver.execute(ResolveStyleCommand(anchor_asset_id=anchor.id, config=locked))
    unknown = resolver.execute(ResolveStyleCommand(anchor_asset_id=anchor.id, identity_version=9))

    assert isinstance(refused, Err) and isinstance(refused.error, InvalidStyleConfig)
    assert isinstance(unknown, Err) and isinstance(unknown.error, UnknownIdentityVersion)
    assert app.container.resolve(VisualStyleCatalog).history(anchor.id) == []


def test_the_current_identity_affects_no_stored_style_of_itself(
    app: Application, tmp_path: Path
) -> None:
    anchor = import_image(app, tmp_path, "clip", "black")
    resolve(app, ResolveStyleCommand(anchor_asset_id=anchor.id))
    catalog = app.container.resolve(VisualStyleCatalog)

    assert catalog.affected_by(CURRENT_IDENTITY_VERSION) == Ok([])  # same version: nothing
    unknown = catalog.affected_by(42)
    assert isinstance(unknown, Err)


def test_consumers_validate_against_the_stored_style(app: Application, tmp_path: Path) -> None:
    anchor = import_image(app, tmp_path, "clip", "black")
    style = resolve(
        app, ResolveStyleCommand(anchor_asset_id=anchor.id, width=1080, height=1920)
    ).style
    zone = style.zone("platform_bottom")
    usage = StyleUsage(
        module="motion_graphics_engine",
        fingerprint=style.fingerprint,
        width=1080,
        height=1920,
        placements=(
            PlacementUse(
                element_id="cta",
                kind=ElementKind.CTA,
                zone="platform_bottom",
                left=zone.left,
                top=zone.top,
                width=zone.width,
                height=zone.height,
            ),
        ),
    )

    issues = app.container.resolve(StyleValidator).validate(style, usage)

    assert [i.code for i in issues] == ["zone.keep_out"]


def test_font_files_resolve_through_the_container(app: Application) -> None:
    fonts = app.container.resolve(FontFiles)
    library = app.container.resolve(MediaLibrary)  # the engine stores no font in the library

    from media_house.modules.visual_style_engine.application.contracts import MESC_IDENTITY_V1

    for face in MESC_IDENTITY_V1.faces:
        located = fonts.locate(face)
        assert isinstance(located, Ok) and located.value.path.is_file()
    assert library.search().total == 0
