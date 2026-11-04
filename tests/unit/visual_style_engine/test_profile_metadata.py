"""Settings-UI metadata and ``problems()`` must agree (docs/layer4/contracts.md section 7)."""

from dataclasses import Field
from typing import Any

import pytest

from media_house.modules.visual_style_engine.domain.errors import InvalidStyleConfig
from media_house.modules.visual_style_engine.domain.profile import (
    Emoji,
    StyleTokens,
    Transitions,
    knob_fields,
    with_value,
)

KNOBS = knob_fields(StyleTokens)
ALLOWED_KEYS = {"label", "help", "unit", "min", "max", "step", "choices", "kind", "advanced"}
#: Every group present (``emoji`` and ``transitions`` exist from identity v2 on).
FULL = StyleTokens(emoji=Emoji(), transitions=Transitions())


def _set(path: str, value: Any) -> StyleTokens:
    return with_value(FULL, path, value)


def test_every_knob_is_described_with_the_shared_keys() -> None:
    assert len(KNOBS) > 100
    for path, f in KNOBS:
        assert f.metadata.get("label"), path
        assert set(f.metadata) <= ALLOWED_KEYS, path


def test_the_defaults_have_no_problem() -> None:
    assert StyleTokens().problems() == []
    assert FULL.problems() == []


@pytest.mark.parametrize(("path", "field"), [k for k in KNOBS if "min" in k[1].metadata])
def test_problems_reject_one_step_beyond_each_range(path: str, field: Field[Any]) -> None:
    meta = field.metadata
    step = meta.get("step", 1)
    kind = int if isinstance(field.default, int) and not isinstance(field.default, bool) else float

    assert not any(path in p for p in _set(path, kind(meta["min"])).problems())
    assert any(path in p for p in _set(path, kind(meta["min"] - step)).problems())
    assert any(path in p for p in _set(path, kind(meta["max"] + step)).problems())


@pytest.mark.parametrize(("path", "field"), [k for k in KNOBS if "choices" in k[1].metadata])
def test_problems_reject_a_value_outside_the_choices(path: str, field: Field[Any]) -> None:
    wrong: Any = ["not-a-choice"] if isinstance(field.default, tuple) else "not-a-choice"
    assert any(path in p for p in _set(path, wrong).problems())


@pytest.mark.parametrize("path", [p for p, f in KNOBS if f.metadata.get("kind") == "color"])
def test_problems_reject_a_malformed_colour(path: str) -> None:
    assert any(path in p for p in _set(path, "red").problems())


def test_a_wrong_value_type_is_refused() -> None:
    with pytest.raises(InvalidStyleConfig):
        _set("motion.intensity_ceiling", "high")
    with pytest.raises(InvalidStyleConfig):
        _set("motion", 3)
    with pytest.raises(InvalidStyleConfig):
        _set("motion.nope", 3)


def test_an_int_is_accepted_for_a_float_knob_and_palette_hex_is_normalised() -> None:
    assert _set("graphics.sticker_glow", 9).graphics.sticker_glow == 9.0
    assert _set("palette.brand_red", "#c4131a").palette.brand_red == "#C4131A"
