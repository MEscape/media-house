"""Image embeddings with CLIP (OpenAI ViT-B/32 weights, MIT licence) through ``transformers``.

One shared model instance serves two analyzers:

* ``EMBEDDINGS``: one embedding per analysed colour frame, plus the embeddings of the zero-shot
  vocabulary (``vocabulary.label_prompts``), so classification is a dot product in the domain;
* ``APPEARANCE``: the embedding of the picture inside each detected PERSON box, the evidence for
  anonymous identity clusters. This is appearance similarity, not face recognition: nobody is
  identified, and strong face-recognition weights are not used (their licences are
  non-commercial). A permissively licensed face embedder can be added as one more engine
  producing ``AppearanceSignals``.

The model is loaded once per run on the GPU when there is one (CPU otherwise); GPU memory
exhaustion moves it to the CPU.
"""

from pathlib import Path
from typing import Any

import numpy as np

from media_house.modules.video_intelligence.application.ports import DecodedVideo, MeasureRequest
from media_house.modules.video_intelligence.domain.signals import (
    AppearanceRow,
    AppearanceSignals,
    DetectionSignals,
    EmbeddingSignals,
)
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    DeviceKind,
    EntityKind,
)
from media_house.modules.video_intelligence.domain.vocabulary import (
    VOCABULARY_VERSION,
    label_prompts,
)
from media_house.modules.video_intelligence.infrastructure.hf_models import (
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
    is_out_of_memory,
    package_version,
    resolve_device,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

REVISION = "1"
REPO = "openai/clip-vit-base-patch32"
LICENSE = "MIT (OpenAI CLIP weights)"
_BATCH = 16
_DECIMALS = 4
_MIN_CROP = 16


class ClipRuntime:
    """The CLIP model and processor, loaded once and shared by the analyzers that need them."""

    def __init__(self, hub_cache: Path | None = None, repo: str = REPO) -> None:
        self._cache = hub_cache
        self._repo = repo
        self._loaded: OnceLoaded[tuple[Any, Any, str]] = OnceLoaded()
        self._cpu_only = False

    @property
    def repo(self) -> str:
        return self._repo

    def unavailable(self, allow_downloads: bool) -> str | None:
        return snapshot_reason(self._repo, LICENSE, self._cache, allow_downloads)

    def identity(self) -> dict[str, str]:
        from media_house.modules.video_intelligence.infrastructure.hf_models import cached_snapshot

        snapshot = cached_snapshot(self._repo, self._cache)
        return {
            "clip": f"{self._repo}@{revision_of(snapshot) if snapshot else 'not-cached'}",
            "transformers": package_version("transformers"),
            "torch": package_version("torch"),
            "clip_revision": REVISION,
        }

    def device(self, requested: DeviceKind) -> str:
        return "cpu" if self._cpu_only else device_label(requested)

    def _model(self, allow_downloads: bool) -> tuple[Any, Any, str]:
        def load() -> tuple[Any, Any, str]:
            from transformers import CLIPModel, CLIPProcessor

            snapshot = ensure_snapshot(self._repo, self._cache, allow_downloads)
            model = CLIPModel.from_pretrained(str(snapshot), local_files_only=True).eval()
            processor = CLIPProcessor.from_pretrained(str(snapshot), local_files_only=True)
            return model, processor, revision_of(snapshot)

        return self._loaded.get(self._repo, load)

    def embed_images(
        self, pictures: list[Any], request: MeasureRequest, cancellation: CancellationToken
    ) -> list[tuple[float, ...]]:
        """L2-normalised embeddings, in the order of ``pictures`` (uint8 RGB arrays)."""
        import torch
        from PIL import Image

        model, processor, _ = self._model(request.allow_downloads)
        device = "cpu" if self._cpu_only else resolve_device(request.device)
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(pictures), _BATCH):
            cancellation.raise_if_cancelled()
            images = [
                Image.fromarray(np.ascontiguousarray(p)) for p in pictures[start : start + _BATCH]
            ]
            inputs = processor(images=images, return_tensors="pt")
            vectors.extend(self._run(model, "image", inputs, device, torch))
        return vectors

    def embed_texts(self, texts: list[str], request: MeasureRequest) -> list[tuple[float, ...]]:
        import torch

        model, processor, _ = self._model(request.allow_downloads)
        device = "cpu" if self._cpu_only else resolve_device(request.device)
        inputs = processor(text=texts, return_tensors="pt", padding=True)
        return self._run(model, "text", inputs, device, torch)

    def _run(
        self,
        model: Any,
        kind: str,
        inputs: Any,
        device: str,
        torch: Any,
    ) -> list[tuple[float, ...]]:
        def encode(target: str) -> Any:
            model.to(target)
            moved = {k: v.to(target) for k, v in inputs.items()}
            with torch.inference_mode():
                features = (
                    model.get_image_features(**moved)
                    if kind == "image"
                    else model.get_text_features(**moved)
                )
            return torch.nn.functional.normalize(features.float(), dim=-1).cpu().numpy()

        try:
            result = encode(device)
        except RuntimeError as exc:
            if device == "cpu" or not is_out_of_memory(exc):
                raise
            _log.warning("GPU out of memory, CLIP continues on the CPU")
            self._cpu_only = True
            torch.cuda.empty_cache()
            result = encode("cpu")
        return [tuple(round(float(v), _DECIMALS) for v in row) for row in result]


class ClipEmbedder:
    """Structurally implements ``application.ports.SignalAnalyzer`` for ``EMBEDDINGS``."""

    analyzer = AnalyzerId.EMBEDDINGS

    def __init__(self, runtime: ClipRuntime) -> None:
        self._runtime = runtime

    def identity(self) -> dict[str, str]:
        return {**self._runtime.identity(), "vocabulary": str(VOCABULARY_VERSION)}

    def unavailable(self, allow_downloads: bool) -> str | None:
        return self._runtime.unavailable(allow_downloads)

    def device(self, requested: DeviceKind) -> str:
        return self._runtime.device(requested)

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> EmbeddingSignals:
        frames = own(video)
        chosen = usable_rgb_frames(frames, request.rgb_plan)[:: request.settings.embed_every]

        def work() -> EmbeddingSignals:
            vectors = self._runtime.embed_images(
                [rgb_frame(frames, f) for f in chosen], request, cancellation
            )
            prompts = label_prompts()
            labels = self._runtime.embed_texts([sentence for _, sentence in prompts], request)
            return EmbeddingSignals(
                model=self._runtime.identity()["clip"],
                dim=len(labels[0]),
                vocabulary_version=VOCABULARY_VERSION,
                frames=tuple(chosen),
                vectors=tuple(vectors),
                label_names=tuple(name for name, _ in prompts),
                label_vectors=tuple(labels),
            )

        return guarded("Embedding frames", work)


class ClipAppearanceAnalyzer:
    """Structurally implements ``application.ports.SignalAnalyzer`` for ``APPEARANCE``."""

    analyzer = AnalyzerId.APPEARANCE

    def __init__(self, runtime: ClipRuntime) -> None:
        self._runtime = runtime

    def identity(self) -> dict[str, str]:
        return self._runtime.identity()

    def unavailable(self, allow_downloads: bool) -> str | None:
        return self._runtime.unavailable(allow_downloads)

    def device(self, requested: DeviceKind) -> str:
        return self._runtime.device(requested)

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> AppearanceSignals:
        frames = own(video)
        entities = request.dependencies.get(AnalyzerId.ENTITIES)
        if not isinstance(entities, DetectionSignals):
            return AppearanceSignals(model=self._runtime.identity()["clip"], frames=(), rows=())
        available = set(usable_rgb_frames(frames, tuple(entities.frames)))
        analysed = sorted(available)
        minimum = request.settings.appearance_min_height

        def work() -> AppearanceSignals:
            wanted = [
                r
                for r in entities.rows
                if r.frame in available and r.kind is EntityKind.PERSON and r.box.height >= minimum
            ]
            crops: list[Any] = []
            for row in wanted:
                picture = rgb_frame(frames, row.frame)
                h, w = picture.shape[:2]
                crop = picture[
                    int(row.box.y0 * h) : int(row.box.y1 * h) + 1,
                    int(row.box.x0 * w) : int(row.box.x1 * w) + 1,
                ]
                crops.append(crop)
            keep = [i for i, c in enumerate(crops) if min(c.shape[:2]) >= _MIN_CROP]
            vectors = self._runtime.embed_images([crops[i] for i in keep], request, cancellation)
            rows = [
                AppearanceRow(wanted[i].frame, wanted[i].box, v)
                for i, v in zip(keep, vectors, strict=True)
            ]
            return AppearanceSignals(
                model=self._runtime.identity()["clip"], frames=tuple(analysed), rows=tuple(rows)
            )

        return guarded("Embedding people", work)
