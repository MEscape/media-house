"""Subject segmentation with a local ``rembg`` model (default ISNet general use)."""

import threading
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from media_house.shared.errors import ExternalSystemError


class RembgSegmenter:
    """Returns a soft alpha matte in [0, 1]. Model sessions load lazily, once per model name.

    The first use of a model downloads its weights (rembg's own cache); later runs are offline.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}
        self._lock = threading.Lock()

    def matte(self, image: Image.Image, model: str) -> NDArray[np.float32]:
        try:
            from rembg import remove  # heavy import, only when segmenting

            mask = remove(
                image, session=self._session(model), only_mask=True, post_process_mask=False
            )
        except Exception as exc:  # rembg/onnxruntime raise many unrelated types
            raise ExternalSystemError(
                f"Background removal model {model!r} failed: {exc}",
                user_message="Background removal is not available. Check the model and network.",
            ) from exc
        return (np.asarray(mask.convert("L"), dtype=np.float32) / 255.0).astype(np.float32)

    def _session(self, model: str) -> Any:
        with self._lock:
            if model not in self._sessions:
                from rembg import new_session

                self._sessions[model] = new_session(model, providers=["CPUExecutionProvider"])
            return self._sessions[model]
