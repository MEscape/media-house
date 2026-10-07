"""The ``MediaProber`` adapter: ffprobe for facts and packet timing, ffmpeg for decoding.

Everything goes through the ``ProcessRunner`` port. Nothing here writes, repairs or converts
media: the decode check discards its output.
"""

import json
import re
from pathlib import Path
from typing import Any

from media_house.core.application.ports import (
    OutputLine,
    OutputStream,
    ProcessResult,
    ProcessRunner,
    ProcessSpec,
)
from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.model import (
    DecodeMessage,
    Integrity,
    ObservedMedia,
)
from media_house.modules.media_inspection.domain.timing import Packet
from media_house.modules.media_inspection.domain.values import Depth
from media_house.modules.media_inspection.infrastructure.ffprobe_normalizer import (
    normalize,
    unreadable,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Revision of how this adapter builds its commands and reads their output. Part of the identity.
_REVISION = "1"
_PROBE_TIMEOUT = 120.0
_PACKET_TIMEOUT = 1800.0
_DECODE_TIMEOUT = 7200.0
_VERSION = re.compile(r"version\s+(\S+)")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_PACKET_FIELDS = 6


class FfprobePacketReader:
    """Collects the packet table that ffprobe streams as CSV, one line per packet."""

    def __init__(self) -> None:
        self.packets: dict[int, list[Packet]] = {}
        #: What ffprobe complained about while reading (damaged or truncated data).
        self.errors: list[str] = []

    def __call__(self, line: OutputLine) -> None:
        if line.stream is OutputStream.STDERR:
            if line.text.strip():
                self.errors.append(line.text.strip())
            return
        parts = line.text.strip().split(",")
        if len(parts) < _PACKET_FIELDS:
            return
        stream, pts, dts, duration, size, flags = parts[:_PACKET_FIELDS]
        try:
            index = int(stream)
            packet = Packet(
                pts=_seconds(pts),
                dts=_seconds(dts),
                duration=_seconds(duration),
                size=int(size) if size.isdigit() else 0,
                keyframe="K" in flags,
            )
        except ValueError:
            return
        self.packets.setdefault(index, []).append(packet)


def _seconds(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None  # "N/A": the container stores no such timestamp


class FfprobeProber:
    """Structurally implements ``application.ports.MediaProber``."""

    def __init__(
        self,
        runner: ProcessRunner,
        *,
        ffprobe: str = "ffprobe",
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self._runner = runner
        self._ffprobe = ffprobe
        self._ffmpeg = ffmpeg
        self._identity: str | None = None

    @property
    def identity(self) -> str:
        if self._identity is None:
            self._identity = (
                f"ffprobe {self._version(self._ffprobe)}"
                f"+ffmpeg {self._version(self._ffmpeg)}+r{_REVISION}"
            )
        return self._identity

    def probe(
        self,
        path: Path,
        config: InspectionConfig,
        cancellation: CancellationToken,
    ) -> ObservedMedia:
        result = self._runner.run(
            ProcessSpec(
                self._ffprobe,
                ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                timeout_seconds=_PROBE_TIMEOUT,
            ),
            cancellation=cancellation,
        )
        info = _parse(result)
        if info is None:
            last = (result.stderr.strip().splitlines() or ["ffprobe could not read the file"])[-1]
            reason = last.removeprefix(f"{path}: ")  # the caller knows the file; keep paths out
            _log.warning("Media could not be read", reason=reason)
            return unreadable(reason)

        reader = self._read_packets(path, cancellation)
        packets = reader.packets
        read_messages = [line for line in result.stderr.splitlines() if line.strip()]
        read_messages += reader.errors
        decode_checked = config.depth is Depth.FULL
        decode_messages = self._decode(path, cancellation) if decode_checked else []
        integrity = _integrity(
            read_messages, decode_messages, decode_checked, config.decode_message_limit
        )
        side_data = self._first_frame_side_data(path, info, cancellation)
        return normalize(info, packets, integrity, config, side_data)

    # ------------------------------------------------------------------------------------------
    def _read_packets(
        self,
        path: Path,
        cancellation: CancellationToken,
    ) -> FfprobePacketReader:
        reader = FfprobePacketReader()
        self._runner.run(
            ProcessSpec(
                self._ffprobe,
                [
                    "-v",
                    "error",
                    "-show_entries",
                    "packet=stream_index,pts_time,dts_time,duration_time,size,flags",
                    "-of",
                    "csv=p=0",
                    str(path),
                ],
                timeout_seconds=_PACKET_TIMEOUT,
                max_captured_lines=1,
            ),
            cancellation=cancellation,
            on_output=reader,
        )
        return reader

    def _first_frame_side_data(
        self,
        path: Path,
        info: dict[str, Any],
        cancellation: CancellationToken,
    ) -> dict[int, list[dict[str, Any]]]:
        """Side data (HDR mastering display, light level, Dolby Vision) of the first video frame."""
        video = next(
            (
                s
                for s in info.get("streams", [])
                if s.get("codec_type") == "video"
                and not s.get("disposition", {}).get("attached_pic")
            ),
            None,
        )
        if video is None:
            return {}
        result = self._runner.run(
            ProcessSpec(
                self._ffprobe,
                [
                    "-v",
                    "error",
                    "-select_streams",
                    str(video.get("index", 0)),
                    "-show_frames",
                    "-read_intervals",
                    "%+#1",
                    "-show_entries",
                    "frame=side_data_list",
                    "-of",
                    "json",
                    str(path),
                ],
                timeout_seconds=_PROBE_TIMEOUT,
            ),
            cancellation=cancellation,
        )
        parsed = _parse_frames(result)
        return {int(video.get("index", 0)): parsed} if parsed else {}

    def _decode(self, path: Path, cancellation: CancellationToken) -> list[str]:
        """Decode every audio and video stream into nowhere; what the decoder complains about."""
        lines: list[str] = []

        def collect(line: OutputLine) -> None:
            if line.stream is OutputStream.STDERR and line.text.strip():
                lines.append(line.text.strip())

        result = self._runner.run(
            ProcessSpec(
                self._ffmpeg,
                [
                    "-hide_banner",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-map",
                    "0:v?",
                    "-map",
                    "0:a?",
                    "-f",
                    "null",
                    "-",
                ],
                timeout_seconds=_DECODE_TIMEOUT,
                max_captured_lines=1,
            ),
            cancellation=cancellation,
            on_output=collect,
        )
        if result.exit_code != 0 and not lines:
            lines.append(f"ffmpeg stopped decoding with exit code {result.exit_code}")
        return lines

    def _version(self, executable: str) -> str:
        result = self._runner.run(
            ProcessSpec(executable, ["-version"], timeout_seconds=_PROBE_TIMEOUT, check=True)
        )
        match = _VERSION.search(result.stdout.splitlines()[0] if result.stdout else "")
        return match.group(1) if match else "unknown"


def _parse(result: ProcessResult) -> dict[str, Any] | None:
    if not result.succeeded:
        return None
    try:
        info = json.loads(result.stdout)
    except ValueError:
        return None
    return info if isinstance(info, dict) and info.get("format") else None


def _parse_frames(result: ProcessResult) -> list[dict[str, Any]]:
    if not result.succeeded:
        return []
    try:
        frames = json.loads(result.stdout).get("frames") or []
    except (ValueError, AttributeError):
        return []
    side_data = frames[0].get("side_data_list") if frames and isinstance(frames[0], dict) else None
    return [d for d in side_data or () if isinstance(d, dict)]


def _integrity(
    read: list[str],
    decode: list[str],
    decode_checked: bool,
    limit: int,
) -> Integrity:
    """Distinct messages (addresses stripped so repeats merge), each with how often it occurred."""
    counts: dict[tuple[str, str], int] = {}
    for stage, messages in (("read", read), ("decode", decode)):
        for message in messages:
            key = (stage, _ADDRESS.sub("0x*", message))
            counts[key] = counts.get(key, 0) + 1
    kept = tuple(
        DecodeMessage(text, count, stage) for (stage, text), count in list(counts.items())[:limit]
    )
    return Integrity(
        readable=True,
        decode_checked=decode_checked,
        decode_messages=kept,
        decode_message_count=len(decode),
        read_message_count=len(read),
    )
