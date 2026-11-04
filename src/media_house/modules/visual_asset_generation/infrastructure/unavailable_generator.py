"""The only ``ImageGenerator`` this installation has: it says honestly that there is none."""

from pathlib import Path

from media_house.modules.visual_asset_generation.application.ports import GeneratedImage
from media_house.modules.visual_asset_generation.domain.errors import GenerationNotAvailable
from media_house.modules.visual_asset_generation.domain.generation import GenerationRequest
from media_house.shared.concurrency import CancellationToken

REASON = (
    "no image-generation model or provider is installed (none exists in this repository and "
    "this machine is CPU-only)"
)


class UnavailableImageGenerator:
    """Implements ``ImageGenerator``. A real adapter replaces this registration in module.py."""

    def identity(self) -> str:
        return "none"

    def unavailable(self) -> str | None:
        return REASON

    def generate(
        self,
        request: GenerationRequest,
        destination: Path,
        cancellation: CancellationToken,
    ) -> GeneratedImage:
        raise GenerationNotAvailable(REASON)
