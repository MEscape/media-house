"""Media-House: a modular desktop platform for media automation."""

import logging
from importlib import metadata

try:
    __version__ = metadata.version("media-house")
except metadata.PackageNotFoundError:  # pragma: no cover - only when not installed
    __version__ = "0.0.0+unknown"

# Library convention: until the bootstrap configures logging, stay silent
# (otherwise Python's "last resort" handler prints raw records to stderr).
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["__version__"]
