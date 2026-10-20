"""The one place in this module that starts FFmpeg and ffprobe (through the ``ProcessRunner`` port).

Command lines are argument lists, never shell strings. Reading packet timestamps lists every
packet of the file without decoding it; that is how both the source and the output are measured.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_house.core.application.ports import (
    OutputLine,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)
from media_house.shared.concurrency import CancellationToken

_ENCODE_TIMEOUT_SECONDS = 7200.0
_PROBE_TIMEOUT_SECONDS = 600.0
_MAX_PACKET_LINES = 10_000_000
_VERSION = re.compile(r"version\s+(\S+)")


@dataclass(frozen=True, slots=True)
class PacketTime:
    """One compressed packet as the container stores it (seconds; ``None`` when not stated)."""

    pts: float | None
    duration: float | None


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:  # "N/A"
        return None


class ProxyFfmpeg:
    """Runs FFmpeg and ffprobe; a missing tool surfaces as ``ToolNotFoundError`` from the runner."""

    def __init__(
        self, runner: ProcessRunner, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"
    ) -> None:
        self._runner = runner
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._version: str | None = None

    @property
    def version(self) -> str:
        """FFmpeg's own version; recorded with every proxy it makes."""
        if self._version is None:
            result = self._runner.run(
                ProcessSpec(
                    self._ffmpeg, ["-version"], timeout_seconds=_PROBE_TIMEOUT_SECONDS, check=True
                )
            )
            match = _VERSION.search(result.stdout.splitlines()[0] if result.stdout else "")
            self._version = match.group(1) if match else "unknown"
        return self._version

    def run(
        self,
        args: list[str],
        cancellation: CancellationToken,
        *,
        check: bool = True,
        on_output: Callable[[OutputLine], None] | None = None,
    ) -> ProcessResult:
        return self._runner.run(
            ProcessSpec(
                self._ffmpeg,
                ["-hide_banner", "-nostdin", "-y", *args],
                timeout_seconds=_ENCODE_TIMEOUT_SECONDS,
                check=check,
                max_captured_lines=200,
            ),
            cancellation=cancellation,
            on_output=on_output,
        )

    def streams_json(self, path: Path, cancellation: CancellationToken) -> dict[str, Any]:
        """``ffprobe -show_format -show_streams`` as a dict; raises ``ProcessFailedError``."""
        result = self._runner.run(
            ProcessSpec(
                self._ffprobe,
                ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                timeout_seconds=_PROBE_TIMEOUT_SECONDS,
                check=True,
            ),
            cancellation=cancellation,
        )
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed

    def packets(self, path: Path, cancellation: CancellationToken) -> dict[int, list[PacketTime]]:
        """Every packet's timestamps by stream index. Reads the container, decodes nothing."""
        result = self._runner.run(
            ProcessSpec(
                self._ffprobe,
                [
                    "-v", "error", "-show_entries", "packet=stream_index,pts_time,duration_time",
                    "-of", "compact=p=0", str(path),
                ],
                timeout_seconds=_PROBE_TIMEOUT_SECONDS,
                check=True,
                max_captured_lines=_MAX_PACKET_LINES,
            ),
            cancellation=cancellation,
        )  # fmt: skip
        by_stream: dict[int, list[PacketTime]] = {}
        for line in result.stdout.splitlines():
            fields = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
            index = fields.get("stream_index")
            if index is None or not index.isdigit():
                continue
            by_stream.setdefault(int(index), []).append(
                PacketTime(_number(fields.get("pts_time")), _number(fields.get("duration_time")))
            )
        return by_stream
