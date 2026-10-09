"""Keyframe descriptions by a small local vision-language model (deep profile only).

SmolVLM-256M-Instruct (Apache-2.0) through ``transformers``, greedy decoding (deterministic), on
sparse keyframes only (every ``description_every``-th planned colour frame). The prompt asks for
what is VISIBLE; the description is stored as text and never interpreted: speech, intent and story
are out of scope for this module. The model loads once per run; the GPU is used when present.
"""

from pathlib import Path
from typing import Any

import numpy as np

from media_house.modules.video_intelligence.application.ports import DecodedVideo, MeasureRequest
from media_house.modules.video_intelligence.domain.signals import DescriptionSignals
from media_house.modules.video_intelligence.domain.values import AnalyzerId, DeviceKind
from media_house.modules.video_intelligence.infrastructure.hf_models import (
    cached_snapshot,
    ensure_snapshot,
    revision_of,
    snapshot_reason,
)
from media_house.modules.video_intelligence.infrastructure.numpy_picture import (
    rgb_frame,
    usable_rgb_frames,
)
from media_house.modules.video_intelligence.infrastructure.numpy_signals import guarded, own
from media_house.modules.video_intelligence.infrastructure.torch_support import (
    OnceLoaded,
    device_label,
    package_version,
    resolve_device,
)
from media_house.shared.concurrency import CancellationToken

REVISION = "1"
REPO = "HuggingFaceTB/SmolVLM-256M-Instruct"
LICENSE = "Apache-2.0"
PROMPT = (
    "Describe in one or two short sentences what is visible in this image: the place, the people "
    "or objects and what they are doing. Describe only what you can see."
)


class TransformersVlm:
    """Structurally implements ``application.ports.SignalAnalyzer`` for ``DESCRIPTIONS``."""

    analyzer = AnalyzerId.DESCRIPTIONS

    def __init__(
        self, hub_cache: Path | None = None, repo: str = REPO, directory: Path | None = None
    ) -> None:
        """``directory``: a model folder to use as it is (no hub lookup, no download)."""
        self._cache = hub_cache
        self._repo = repo
        self._directory = directory
        self._loaded: OnceLoaded[tuple[Any, Any]] = OnceLoaded()

    def identity(self) -> dict[str, str]:
        snapshot = self._directory or cached_snapshot(self._repo, self._cache)
        return {
            "vlm": f"{self._repo}@{revision_of(snapshot) if snapshot else 'not-cached'}",
            "transformers": package_version("transformers"),
            "torch": package_version("torch"),
            "vlm_revision": REVISION,
        }

    def unavailable(self, allow_downloads: bool) -> str | None:
        if self._directory is not None:
            return (
                None
                if self._directory.is_dir()
                else f"the model folder {self._directory} is missing"
            )
        return snapshot_reason(self._repo, LICENSE, self._cache, allow_downloads)

    def device(self, requested: DeviceKind) -> str:
        return device_label(requested)

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> DescriptionSignals:
        frames = own(video)
        chosen = usable_rgb_frames(frames, request.rgb_plan)[:: request.settings.description_every]
        device = resolve_device(request.device)

        def work() -> DescriptionSignals:
            import torch
            from PIL import Image

            model, processor = self._load(request.allow_downloads, device)
            texts: list[str] = []
            for frame in chosen:
                cancellation.raise_if_cancelled()
                image = Image.fromarray(np.ascontiguousarray(rgb_frame(frames, frame)))
                messages = [
                    {
                        "role": "user",
                        "content": [{"type": "image"}, {"type": "text", "text": PROMPT}],
                    }
                ]
                prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
                inputs = processor(text=prompt, images=[image], return_tensors="pt").to(device)
                with torch.inference_mode():
                    generated = model.generate(
                        **inputs,
                        max_new_tokens=request.settings.description_max_tokens,
                        do_sample=False,
                    )
                answer = processor.batch_decode(
                    generated[:, inputs["input_ids"].shape[1] :], skip_special_tokens=True
                )[0]
                texts.append(" ".join(answer.split()))
            return DescriptionSignals(
                model=self.identity()["vlm"], frames=tuple(chosen), texts=tuple(texts)
            )

        return guarded("Describing keyframes", work)

    def _load(self, allow_downloads: bool, device: str) -> tuple[Any, Any]:
        def load() -> tuple[Any, Any]:
            from transformers import AutoModelForImageTextToText, AutoProcessor

            snapshot = str(
                self._directory or ensure_snapshot(self._repo, self._cache, allow_downloads)
            )
            processor = AutoProcessor.from_pretrained(snapshot, local_files_only=True)
            model = AutoModelForImageTextToText.from_pretrained(snapshot, local_files_only=True)
            return model.eval(), processor

        model, processor = self._loaded.get(self._repo, load)
        return model.to(device), processor
