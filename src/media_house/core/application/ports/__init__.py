"""Ports: capabilities the application needs from the outside world."""

from media_house.core.application.ports.clock import Clock
from media_house.core.application.ports.process_runner import (
    OutputLine,
    OutputStream,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)

__all__ = [
    "Clock",
    "OutputLine",
    "OutputStream",
    "ProcessResult",
    "ProcessRunner",
    "ProcessSpec",
]
