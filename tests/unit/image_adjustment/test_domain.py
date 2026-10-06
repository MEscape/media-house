"""Settings, fingerprint config and mask limits: pure domain rules."""

import pytest

from media_house.modules.image_adjustment.domain.errors import SegmentationRejected
from media_house.modules.image_adjustment.domain.values import (
    AdjustmentSettings,
    BackgroundRemoval,
    Canvas,
    Glow,
    HexColor,
    MaskLimits,
    MaskStats,
)
from media_house.shared.errors import InvariantViolation


def test_hex_color_is_normalised() -> None:
    assert HexColor.of(" ff2a5f ").value == "#FF2A5F"
    assert HexColor.of("#ff2a5f").rgb == (255, 42, 95)


@pytest.mark.parametrize("raw", ["", "#12", "#GGGGGG", "red"])
def test_hex_color_rejects_garbage(raw: str) -> None:
    with pytest.raises(InvariantViolation):
        HexColor.of(raw)


@pytest.mark.parametrize("opacity", [0.0, 1.5, float("nan")])
def test_glow_rejects_bad_opacity(opacity: float) -> None:
    with pytest.raises(InvariantViolation):
        Glow(enabled=True, opacity=opacity)


def test_glow_extent_is_zero_when_disabled_and_covers_spread_and_blur_when_enabled() -> None:
    assert Glow(enabled=False, radius=99, spread=99).extent == 0
    glow = Glow(enabled=True, radius=30, spread=12)
    assert glow.extent >= 12 + 3 * glow.sigma


def test_padding_and_glow_must_leave_room_for_the_subject() -> None:
    with pytest.raises(InvariantViolation):
        AdjustmentSettings(
            canvas=Canvas(width=200, height=200, padding=90),
            glow=Glow(enabled=True),
        )


def test_canvas_rejects_unreasonable_sizes() -> None:
    with pytest.raises(InvariantViolation):
        Canvas(width=10, height=1080)


def test_unused_glow_options_do_not_change_the_identity() -> None:
    pink = AdjustmentSettings(glow=Glow(enabled=False, color=HexColor("#FF2A5F")))
    blue = AdjustmentSettings(glow=Glow(enabled=False, color=HexColor("#0000FF"), radius=5))
    assert pink.to_config() == blue.to_config()


def test_every_used_option_changes_the_identity() -> None:
    base = AdjustmentSettings(glow=Glow(enabled=True))
    variants = [
        AdjustmentSettings(glow=Glow(enabled=False)),
        AdjustmentSettings(glow=Glow(enabled=True, color=HexColor("#0000FF"))),
        AdjustmentSettings(glow=Glow(enabled=True, opacity=0.5)),
        AdjustmentSettings(glow=Glow(enabled=True, radius=10)),
        AdjustmentSettings(glow=Glow(enabled=True, spread=2)),
        AdjustmentSettings(glow=Glow(enabled=True), canvas=Canvas(width=1920)),
        AdjustmentSettings(glow=Glow(enabled=True), canvas=Canvas(padding=10)),
        AdjustmentSettings(glow=Glow(enabled=True), background_removal=BackgroundRemoval(False)),
        AdjustmentSettings(
            glow=Glow(enabled=True),
            background_removal=BackgroundRemoval(model="u2net"),
        ),
    ]
    assert all(v.to_config() != base.to_config() for v in variants)


def test_mask_limits_do_not_change_the_identity() -> None:
    assert (
        AdjustmentSettings(mask_limits=MaskLimits(0.1, 0.9)).to_config()
        == AdjustmentSettings().to_config()
    )


@pytest.mark.parametrize(
    "stats",
    [
        MaskStats(0.0, None),
        MaskStats(0.001, (0, 0, 2, 2)),
        MaskStats(0.99, (0, 0, 10, 10)),
    ],
)
def test_mask_limits_reject_missing_tiny_and_unremoved_subjects(stats: MaskStats) -> None:
    with pytest.raises(SegmentationRejected):
        MaskLimits().check(stats)


def test_mask_limits_accept_a_reasonable_subject() -> None:
    MaskLimits().check(MaskStats(0.2, (10, 10, 50, 50)))
