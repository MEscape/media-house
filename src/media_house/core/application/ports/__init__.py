"""Ports: capabilities the application needs from the outside world."""

from media_house.core.application.ports.clock import Clock
from media_house.core.application.ports.media_analysis import (
    AnalyzerCapabilities,
    DecodeReport,
    FrameComparison,
    FrameSample,
    LoudnessReading,
    MediaAnalyzer,
    TimeSpan,
)
from media_house.core.application.ports.process_runner import (
    OutputLine,
    OutputStream,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)

__all__ = [
    "AnalyzerCapabilities",
    "Clock",
    "DecodeReport",
    "FrameComparison",
    "FrameSample",
    "LoudnessReading",
    "MediaAnalyzer",
    "OutputLine",
    "OutputStream",
    "ProcessResult",
    "ProcessRunner",
    "ProcessSpec",
    "TimeSpan",
]
