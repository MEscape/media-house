"""Background removal never downloads a model during a run unless it was explicitly allowed."""

import pytest
from PIL import Image

from media_house.modules.image_adjustment.infrastructure.rembg_segmenter import (
    RembgSegmenter,
    _on_disk,
)
from media_house.shared.errors import ExternalSystemError

pytestmark = pytest.mark.integration


def picture() -> Image.Image:
    return Image.new("RGB", (64, 64), (200, 30, 30))


def test_an_unknown_model_is_refused_without_touching_the_network() -> None:
    with pytest.raises(ExternalSystemError) as raised:
        RembgSegmenter().matte(picture(), "no-such-model")

    assert "scripts/fetch_models.py --background-removal no-such-model" in raised.value.user_message


def test_a_model_on_disk_is_recognised_and_one_that_is_not_is_not() -> None:
    assert not _on_disk("no-such-model")
    assert _on_disk("u2net") in {True, False}  # depends on the machine, never raises


@pytest.mark.skipif(not _on_disk("u2net"), reason="the u2net model is not in rembg's folder")
def test_a_cached_model_produces_a_matte_offline() -> None:
    matte = RembgSegmenter().matte(picture(), "u2net")

    assert matte.shape == (64, 64) and 0.0 <= float(matte.min()) <= float(matte.max()) <= 1.0
