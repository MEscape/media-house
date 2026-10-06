"""Structured logging: ``get_logger`` for modules, ``configure_logging`` for the bootstrap."""

from media_house.shared.logging.api import StructuredLogger, get_logger
from media_house.shared.logging.context import (
    bind_context,
    current_correlation_id,
    current_operation_id,
    new_correlation_id,
)
from media_house.shared.logging.setup import LoggingHandle, configure_logging

__all__ = [
    "LoggingHandle",
    "StructuredLogger",
    "bind_context",
    "configure_logging",
    "current_correlation_id",
    "current_operation_id",
    "get_logger",
    "new_correlation_id",
]
