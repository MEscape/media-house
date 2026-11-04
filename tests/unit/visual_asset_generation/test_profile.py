"""Knobs: metadata and checks agree, overrides by dotted path, effect identity collapses."""

import dataclasses
from typing import Any

import pytest

from media_house.modules.visual_asset_generation.domain.errors import InvalidVisualAssetConfig
from media_house.modules.visual_asset_generation.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    Contrast,
    Enhancement,
    Outline,
    QcThresholds,
    RimLight,
    SelectionSettings,
    Shadow,
    Tilt,
    VariantName,
    Variants,
    VisualAssetConfig,
    VisualAssetProfile,
    fingerprint,
)
from media_house.modules.visual_asset_generation.domain.requests import (
    ExportSettings,
    ImageFormat,
)
from media_house.modules.visual_asset_generation.domain.values import (
    Canvas,
    Glow,
    HexColor,
    MaskLimits,
)
from media_house.shared.errors import InvariantViolation

#: Every knob group a settings UI can show, with a valid instance to vary.
GROUPS: list[Any] = [
    Glow(),
    Canvas(),
    MaskLimits(),
    Shadow(),
    Outline(),
    RimLight(),
    Contrast(),
    Tilt(),
    Enhancement(),
    QcThresholds(),
    SelectionSettings(),
    ExportSettings(),
]
#: Knobs whose edges are also limited by another field (mask limits need min < max).
CROSS_FIELD = {("MaskLimits", "min_foreground_ratio"), ("MaskLimits", "max_foreground_ratio")}


def _numeric_knobs() -> list[tuple[Any, dataclasses.Field[Any]]]:
    return [
        (group, f) for group in GROUPS for f in dataclasses.fields(group) if "min" in f.metadata
    ]


@pytest.mark.parametrize(
    ("group", "knob"),
    _numeric_knobs(),
    ids=lambda v: v.name if isinstance(v, dataclasses.Field) else type(v).__name__,
)
def test_metadata_ranges_and_checks_agree(group: Any, knob: dataclasses.Field[Any]) -> None:
    meta = knob.metadata
    assert meta["label"] and "max" in meta and "step" in meta
    for outside in (meta["min"] - meta["step"], meta["max"] + meta["step"]):
        with pytest.raises(InvariantViolation):
            dataclasses.replace(group, **{knob.name: type(getattr(group, knob.name))(outside)})
    if (type(group).__name__, knob.name) in CROSS_FIELD:
        return
    for edge in (meta["min"], meta["max"]):
        dataclasses.replace(group, **{knob.name: type(getattr(group, knob.name))(edge)})


def test_every_visible_knob_has_a_label_and_colours_say_so() -> None:
    for group in GROUPS:
        for f in dataclasses.fields(group):
            if f.metadata:
                assert f.metadata.get("label"), (type(group).__name__, f.name)
            if f.type in {HexColor, "HexColor"} or isinstance(getattr(group, f.name), HexColor):
                assert f.metadata.get("kind") == "color", (type(group).__name__, f.name)


def test_effects_are_disabled_by_default_and_the_clean_identity_is_empty() -> None:
    plain = Enhancement()
    assert plain.applied() == ()
    assert plain.to_config() == {}
    assert plain.extent == plain.border


def test_disabled_effect_leftovers_never_change_the_identity() -> None:
    a = Enhancement(shadow=Shadow(enabled=False, blur=3.0), glow=Glow(enabled=True))
    b = Enhancement(shadow=Shadow(enabled=False, blur=99.0), glow=Glow(enabled=True))
    assert a.to_config() == b.to_config()
    assert set(a.to_config()) == {"glow", "border"}


def test_each_preset_enables_only_what_its_name_says() -> None:
    variants = Variants()
    assert variants.of(VariantName.CLEAN) is None
    assert variants.sticker.applied() == ("tilt", "glow")
    assert variants.silhouette.applied() == ("silhouette", "glow")
    assert variants.glow.applied() == ("glow",)
    assert variants.shadow.applied() == ("shadow",)
    assert variants.outline.applied() == ("outline",)


def test_the_sticker_preset_follows_the_channel_discovery_ranges() -> None:
    sticker = PROFILES[DEFAULT_PROFILE].variants.sticker
    assert sticker.glow.color == HexColor("#FFFFFF")
    assert 6.0 <= sticker.glow.radius <= 10.0
    assert 5.0 <= abs(sticker.tilt.degrees) <= 10.0


def test_overrides_by_dotted_path_touch_only_their_key() -> None:
    profile = VisualAssetConfig(
        overrides={
            "variants.sticker.glow.radius": 10,
            "variants.sticker.glow.color": "#ff0000",
            "cutout.canvas.width": 1920,
        }
    ).resolve()
    base = PROFILES[DEFAULT_PROFILE]
    assert profile.variants.sticker.glow.radius == 10.0
    assert profile.variants.sticker.glow.color == HexColor("#FF0000")
    assert profile.cutout.canvas.width == 1920
    assert profile.variants.silhouette == base.variants.silhouette


def test_the_identity_layer_comes_before_the_callers_overrides() -> None:
    profile = VisualAssetConfig(overrides={"variants.sticker.tilt.degrees": 4.0}).resolve(
        {"variants.sticker.tilt.degrees": -9.0, "variants.sticker.glow.opacity": 0.5}
    )
    assert profile.variants.sticker.tilt.degrees == 4.0
    assert profile.variants.sticker.glow.opacity == 0.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"variants.sticker.nope": 1},
        {"variants.sticker.glow.radius": "wide"},
        {"variants.sticker.glow.radius": 9999},
        {"variants.sticker.glow.color": "red"},
        {"variants.sticker": 1},
        {"name": "other"},
        {"cutout.canvas.centering": "left"},
    ],
)
def test_bad_overrides_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(InvalidVisualAssetConfig):
        VisualAssetConfig(overrides=overrides).resolve()


def test_unknown_profile_is_refused() -> None:
    with pytest.raises(InvalidVisualAssetConfig):
        VisualAssetConfig(profile="nope").resolve()


def test_effects_that_leave_no_room_on_the_canvas_are_refused() -> None:
    with pytest.raises(InvalidVisualAssetConfig):
        VisualAssetConfig(
            overrides={"cutout.canvas.width": 64, "variants.glow.glow.radius": 200.0}
        ).resolve()


def test_the_fingerprint_is_plain_json_and_changes_with_any_value() -> None:
    base = fingerprint(PROFILES[DEFAULT_PROFILE])
    tuned = fingerprint(VisualAssetConfig(overrides={"qc.upscale_warning": 2.0}).resolve())
    assert base != tuned
    assert base["variants"]["sticker"]["glow"]["color"] == "#FFFFFF"  # type: ignore[index,call-overload]


def test_export_identity_names_only_what_changes_the_bytes() -> None:
    png = ExportSettings(format=ImageFormat.PNG, quality=10)
    assert "quality" not in png.to_config()
    lossy = ExportSettings(format=ImageFormat.WEBP, lossless=False, quality=80).to_config()
    assert lossy["quality"] == 80
    flat = ExportSettings(format=ImageFormat.JPEG, flatten_onto=HexColor("#000000")).to_config()
    assert flat["flatten_onto"] == "#000000"


def test_every_builtin_profile_is_valid() -> None:
    for profile in PROFILES.values():
        assert isinstance(profile, VisualAssetProfile)
        assert profile.problems() == []
