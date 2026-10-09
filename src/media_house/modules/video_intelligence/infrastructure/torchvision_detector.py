"""Object, person, animal and vehicle detection with a torchvision detector (``ENTITIES``).

The default detector is SSDLite320 with a MobileNetV3 backbone trained on COCO (BSD-3 licensed
code and weights, small and fast on a CPU). AGPL-licensed detectors are deliberately not used.
Boxes are returned normalised to the display-oriented picture. Another torchvision detector is a
new entry in ``DETECTORS`` (with its pinned checksum); nothing else changes.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np

from media_house.modules.video_intelligence.application.ports import DecodedVideo, MeasureRequest
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.signals import DetectionRow, DetectionSignals
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    DeviceKind,
    EntityKind,
)
from media_house.modules.video_intelligence.infrastructure.model_files import (
    ModelSpec,
    ModelStore,
    library_missing,
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
from media_house.shared.errors import ExternalSystemError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Revision of how frames are prepared and results mapped. Part of the cache identity.
REVISION = "1"
_BATCH = 8
_PER_FRAME = 30

_ANIMALS = frozenset(
    ["bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"]
)
_VEHICLES = frozenset(["bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat"])


@dataclass(frozen=True, slots=True)
class DetectorModel:
    spec: ModelSpec
    #: Name of the torchvision constructor and of the weights enum holding the class names.
    constructor: str
    weights_enum: str


DETECTORS: dict[str, DetectorModel] = {
    "ssdlite320_mobilenet_v3_large": DetectorModel(
        ModelSpec(
            filename="ssdlite320_mobilenet_v3_large_coco-a79551df.pth",
            url="https://download.pytorch.org/models/ssdlite320_mobilenet_v3_large_coco-a79551df.pth",
            license="BSD-3-Clause (torchvision weights trained on COCO)",
            sha256_prefix="a79551df90c79834bcd3bb3845ef9d96",
        ),
        "ssdlite320_mobilenet_v3_large",
        "SSDLite320_MobileNet_V3_Large_Weights",
    ),
}


def kind_of(label: str) -> EntityKind:
    if label == "person":
        return EntityKind.PERSON
    if label in _ANIMALS:
        return EntityKind.ANIMAL
    if label in _VEHICLES:
        return EntityKind.VEHICLE
    return EntityKind.OBJECT


class TorchvisionDetector:
    """Structurally implements ``application.ports.SignalAnalyzer`` for ``ENTITIES``."""

    analyzer = AnalyzerId.ENTITIES

    def __init__(self, models: ModelStore, detector: str = "ssdlite320_mobilenet_v3_large") -> None:
        self._models = models
        self._detector = detector
        self._loaded: OnceLoaded[tuple[Any, list[str]]] = OnceLoaded()
        self._fallback_to_cpu = False

    def _model_for(self, name: str) -> DetectorModel:
        try:
            return DETECTORS[name]
        except KeyError as exc:
            raise ExternalSystemError(
                f"unknown detector {name!r}; available: {', '.join(DETECTORS)}"
            ) from exc

    def identity(self) -> dict[str, str]:
        model = DETECTORS.get(self._detector)
        return {
            "torch": package_version("torch"),
            "torchvision": package_version("torchvision"),
            "detector": self._detector,
            "weights": model.spec.identity if model else "unknown",
            "detector_revision": REVISION,
        }

    def unavailable(self, allow_downloads: bool) -> str | None:
        missing = library_missing("torch", "torchvision")
        if missing:
            return missing
        model = DETECTORS.get(self._detector)
        if model is None:
            return f"unknown detector {self._detector!r}"
        return self._models.missing_reason(model.spec, allow_downloads)

    def device(self, requested: DeviceKind) -> str:
        return "cpu" if self._fallback_to_cpu else device_label(requested)

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> DetectionSignals:
        frames = own(video)
        settings = request.settings
        usable = usable_rgb_frames(frames, request.rgb_plan)[:: settings.detect_every]
        device = "cpu" if self._fallback_to_cpu else resolve_device(request.device)

        def work() -> DetectionSignals:
            model, categories = self._model(request, device)
            rows: list[DetectionRow] = []
            for start in range(0, len(usable), _BATCH):
                cancellation.raise_if_cancelled()
                batch = usable[start : start + _BATCH]
                pictures = [rgb_frame(frames, f) for f in batch]
                found = self._detect(model, pictures, device)
                for frame, picture, result in zip(batch, pictures, found, strict=True):
                    rows.extend(
                        _rows(frame, picture, result, categories, settings.detect_min_score)
                    )
            rows.sort(key=lambda r: (r.frame, -r.confidence, r.label, r.box.x0))
            return DetectionSignals(
                model=f"{self._detector}@{self._model_for(self._detector).spec.identity}",
                frames=tuple(usable),
                rows=tuple(rows),
            )

        return guarded("Detecting objects", work)

    def _model(self, request: MeasureRequest, device: str) -> tuple[Any, list[str]]:
        entry = self._model_for(self._detector)

        def load() -> tuple[Any, list[str]]:
            import torch
            from torchvision.models import detection

            path = self._models.ensure(entry.spec, request.allow_downloads)
            weights = getattr(detection, entry.weights_enum).COCO_V1
            network = getattr(detection, entry.constructor)(
                weights=None, weights_backbone=None, num_classes=len(weights.meta["categories"])
            )
            network.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
            return network.eval(), list(weights.meta["categories"])

        model, categories = self._loaded.get(self._detector, load)
        return model.to(device), categories

    def _detect(self, model: Any, pictures: list[Any], device: str) -> list[dict[str, Any]]:
        import torch

        tensors = [
            torch.from_numpy(np.ascontiguousarray(p)).permute(2, 0, 1).float().div(255.0)
            for p in pictures
        ]
        try:
            with torch.inference_mode():
                return list(model([t.to(device) for t in tensors]))
        except RuntimeError as exc:
            if device == "cpu" or not is_out_of_memory(exc):
                raise
            _log.warning("GPU out of memory, the detector continues on the CPU")
            self._fallback_to_cpu = True
            torch.cuda.empty_cache()
            model.to("cpu")
            with torch.inference_mode():
                return list(model(tensors))


def _rows(
    frame: int,
    picture: Any,
    result: dict[str, Any],
    categories: list[str],
    minimum: float,
) -> list[DetectionRow]:
    height, width = picture.shape[:2]
    rows: list[DetectionRow] = []
    boxes, labels, scores = (
        result[k].detach().cpu().numpy() for k in ("boxes", "labels", "scores")
    )
    for (x0, y0, x1, y1), label, score in zip(boxes, labels, scores, strict=True):
        if float(score) < minimum:
            continue
        box = BBox.clamped(
            float(x0) / width, float(y0) / height, float(x1) / width, float(y1) / height
        )
        name = categories[int(label)] if 0 <= int(label) < len(categories) else "unknown"
        if box is not None and name not in {"__background__", "N/A"}:
            rows.append(DetectionRow(frame, kind_of(name), name, round(float(score), 4), box))
    return sorted(rows, key=lambda r: -r.confidence)[:_PER_FRAME]
