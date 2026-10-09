"""PyTorch helpers shared by the model-based engines: device choice and graceful memory limits.

Torch is a dependency of the project already (through WhisperX) and is the one inference runtime
the model engines use, on the GPU when there is one and on the CPU otherwise. GPU use changes
speed, not results beyond numerical tolerance (the tests compare both). A model is loaded once per
engine instance and kept; running out of GPU memory moves the engine to the CPU instead of failing.
"""

import threading
from importlib import metadata

from media_house.modules.video_intelligence.domain.values import DeviceKind
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


def package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "missing"


def cuda_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def resolve_device(requested: DeviceKind) -> str:
    """``cuda`` when the GPU was asked for (or left to us) and exists, else ``cpu``."""
    if requested is DeviceKind.CPU:
        return "cpu"
    return "cuda" if cuda_available() else "cpu"


def device_label(requested: DeviceKind) -> str:
    """The user-facing name of where the engine would run: ``gpu`` or ``cpu``."""
    return "gpu" if resolve_device(requested) == "cuda" else "cpu"


def is_out_of_memory(error: BaseException) -> bool:
    return "out of memory" in str(error).lower()


class OnceLoaded[T]:
    """Loads a value on first use and keeps it, safely under concurrent first calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, T] = {}

    def get(self, key: str, load: "callable[[], T]") -> T:  # type: ignore[valid-type]
        with self._lock:
            if key not in self._values:
                _log.info("Loading a model", model=key)
                self._values[key] = load()  # type: ignore[misc]
            return self._values[key]

    def drop(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)
