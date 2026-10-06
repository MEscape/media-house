"""Global test configuration."""

import logging
import os
from collections.abc import Iterator

import pytest

# Must be set before any Qt module creates a platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolate_application_logger() -> Iterator[None]:
    """configure_logging() mutates the ``media_house`` logger; undo it after every test."""
    yield
    logger = logging.getLogger("media_house")
    for handler in [h for h in logger.handlers if getattr(h, "_media_house", False)]:
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
    logger.setLevel(logging.NOTSET)
