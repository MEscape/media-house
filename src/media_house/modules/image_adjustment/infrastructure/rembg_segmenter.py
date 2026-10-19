"""Subject segmentation with a local ``rembg`` model (default ISNet general use)."""

import threading
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from media_house.shared.errors import ExternalSystemError


class RembgSegmenter:
    """Returns a soft alpha matte in [0, 1]. Model sessions load lazily, once per model name.

    A model that is not already in rembg's model folder is NOT downloaded during a run unless
    ``allow_download`` is set: the matte fails with the command that fetches it instead.
    """

    def __init__(self, *, allow_download: bool = False) -> None:
        self._allow_download = allow_download
        self._sessions: dict[str, Any] = {}
        self._lock = threading.Lock()

    def matte(self, image: Image.Image, model: str) -> NDArray[np.float32]:
        if not self._allow_download and not _on_disk(model):
            raise ExternalSystemError(
                f"Background removal model {model!r} is not downloaded",
                user_message=(
                    f"The background removal model '{model}' is not downloaded. Run "
                    f"'python scripts/fetch_models.py --background-removal {model}' once."
                ),
            )
        try:
            from rembg import remove  # heavy import, only when segmenting

            mask = remove(
                image, session=self._session(model), only_mask=True, post_process_mask=False
            )
        except Exception as exc:  # rembg/onnxruntime raise many unrelated types
            raise ExternalSystemError(
                f"Background removal model {model!r} failed: {exc}",
                user_message="Background removal failed. Check the model files.",
            ) from exc
        return (np.asarray(mask.convert("L"), dtype=np.float32) / 255.0).astype(np.float32)

    def _session(self, model: str) -> Any:
        with self._lock:
            if model not in self._sessions:
                from rembg import new_session

                self._sessions[model] = new_session(model, providers=["CPUExecutionProvider"])
            return self._sessions[model]


def _on_disk(model: str) -> bool:
    """Whether rembg already has this model's file (its own lookup, including the old layout)."""
    from rembg.sessions import sessions

    session_class = sessions.get(model)
    if session_class is None:
        return False
    return bool(session_class.resolve_existing(f"{model}.onnx") is not None)
