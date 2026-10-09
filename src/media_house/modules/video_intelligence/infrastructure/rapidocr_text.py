"""On-screen text with RapidOCR (Apache-2.0, ONNX Runtime, models shipped in the wheel).

CPU. Frames are read at the profile's ``text_every`` rhythm; every recognised line becomes a row
with its box normalised to the display-oriented picture. Reading text says nothing about its
meaning: what is said or meant is out of scope for this module.
"""

import threading
from typing import Any

import numpy as np

from media_house.modules.video_intelligence.application.ports import DecodedVideo, MeasureRequest
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.signals import TextRow, TextSignals
from media_house.modules.video_intelligence.domain.values import AnalyzerId, DeviceKind
from media_house.modules.video_intelligence.infrastructure.model_files import library_missing
from media_house.modules.video_intelligence.infrastructure.numpy_picture import (
    rgb_frame,
    usable_rgb_frames,
)
from media_house.modules.video_intelligence.infrastructure.numpy_signals import guarded, own
from media_house.modules.video_intelligence.infrastructure.torch_support import (
    OnceLoaded,
    package_version,
)
from media_house.shared.concurrency import CancellationToken

REVISION = "1"
#: Models that ship inside the rapidocr wheel of the pinned major version.
_MODELS = "PP-OCRv6-small-det+rec"


class RapidOcrAnalyzer:
    """Structurally implements ``application.ports.SignalAnalyzer`` for ``TEXT``."""

    analyzer = AnalyzerId.TEXT

    def __init__(self) -> None:
        self._engine: OnceLoaded[Any] = OnceLoaded()
        self._lock = threading.Lock()

    def identity(self) -> dict[str, str]:
        return {
            "rapidocr": package_version("rapidocr"),
            "onnxruntime": package_version("onnxruntime"),
            "ocr_models": _MODELS,
            "ocr_revision": REVISION,
        }

    def unavailable(self, allow_downloads: bool) -> str | None:
        return library_missing("rapidocr", "onnxruntime")

    def device(self, requested: DeviceKind) -> str:
        return "cpu"

    def measure(
        self,
        video: DecodedVideo,
        request: MeasureRequest,
        cancellation: CancellationToken,
    ) -> TextSignals:
        frames = own(video)
        chosen = usable_rgb_frames(frames, request.rgb_plan)[:: request.settings.text_every]
        minimum = request.settings.text_min_score

        def work() -> TextSignals:
            engine = self._engine.get("rapidocr", _create)
            rows: list[TextRow] = []
            for frame in chosen:
                cancellation.raise_if_cancelled()
                picture = rgb_frame(frames, frame)
                with self._lock:
                    output = engine(np.ascontiguousarray(picture))
                rows.extend(_rows(frame, picture, output, minimum))
            return TextSignals(model=_MODELS, frames=tuple(chosen), rows=tuple(rows))

        return guarded("Reading on-screen text", work)


def _create() -> Any:
    from rapidocr import RapidOCR

    return RapidOCR()


def _rows(frame: int, picture: Any, output: Any, minimum: float) -> list[TextRow]:
    if output is None or output.boxes is None or output.txts is None:
        return []
    height, width = picture.shape[:2]
    rows: list[TextRow] = []
    for quad, text, score in zip(output.boxes, output.txts, output.scores, strict=True):
        points = np.asarray(quad, dtype=np.float64)
        box = BBox.clamped(
            points[:, 0].min() / width,
            points[:, 1].min() / height,
            points[:, 0].max() / width,
            points[:, 1].max() / height,
        )
        if box is not None and float(score) >= minimum and str(text).strip():
            rows.append(TextRow(frame, str(text).strip(), round(float(score), 4), box))
    return sorted(rows, key=lambda r: (r.box.y0, r.box.x0, r.text))
