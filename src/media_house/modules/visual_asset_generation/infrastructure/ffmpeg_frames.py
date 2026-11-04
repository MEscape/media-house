"""One picture out of a video with FFmpeg (through the ``ProcessRunner`` port), as RGB48 PNG.

Accurate input seek (``-ss`` before ``-i`` on a transcode decodes from the previous keyframe and
drops pictures before the target), first video stream, display orientation (FFmpeg applies the
rotation tag), and one conversion straight from the decoder's YUV to 16-bit RGB with accurate
rounding and full chroma: no 8-bit step in between.
"""

import re
import struct
from pathlib import Path

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.modules.visual_asset_generation.application.ports import ExtractedFrame
from media_house.modules.visual_asset_generation.domain.errors import FrameGrabFailed
from media_house.shared.concurrency import CancellationToken

PIXEL_FORMAT = "rgb48be"
_TIMEOUT_SECONDS = 300.0
_VERSION_PATTERN = re.compile(r"version\s+(\S+)")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class FfmpegFrameExtractor:
    """Implements ``FrameExtractor``. A picture FFmpeg cannot deliver is ``FrameGrabFailed``."""

    def __init__(self, runner: ProcessRunner, *, ffmpeg: str = "ffmpeg") -> None:
        self._runner = runner
        self._ffmpeg = ffmpeg
        self._version: str | None = None

    def extract(
        self,
        video: Path,
        seek_seconds: str,
        destination: Path,
        cancellation: CancellationToken,
    ) -> ExtractedFrame:
        args = [
            "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-ss", seek_seconds, "-i", str(video),
            "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1",
            "-vf", "scale=flags=accurate_rnd+full_chroma_int+full_chroma_inp",
            "-pix_fmt", PIXEL_FORMAT, "-f", "image2", "-c:v", "png", "-update", "1",
            str(destination),
        ]  # fmt: skip
        # A missing tool or a timeout is the environment, not this picture: it propagates as
        # ToolNotFoundError / ProcessTimeoutError and nothing is recorded as a result.
        result = self._runner.run(
            ProcessSpec(self._ffmpeg, args, timeout_seconds=_TIMEOUT_SECONDS),
            cancellation=cancellation,
        )
        if result.exit_code != 0:
            lines = [line for line in result.stderr.splitlines() if line.strip()]
            raise FrameGrabFailed(lines[-1] if lines else f"FFmpeg exit code {result.exit_code}")
        size = _png_size(destination)
        if size is None:
            raise FrameGrabFailed("no picture at that time (past the end of the video?)")
        return ExtractedFrame(
            width=size[0],
            height=size[1],
            pixel_format=PIXEL_FORMAT,
            engine=("ffmpeg", self._tool_version(cancellation)),
        )

    def _tool_version(self, cancellation: CancellationToken) -> str:
        if self._version is None:
            result = self._runner.run(
                ProcessSpec(self._ffmpeg, ["-version"], timeout_seconds=60.0),
                cancellation=cancellation,
            )
            first = result.stdout.splitlines()[0] if result.stdout else ""
            match = _VERSION_PATTERN.search(first)
            self._version = match.group(1) if match else "unknown"
        return self._version


def _png_size(path: Path) -> tuple[int, int] | None:
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or not header.startswith(_PNG_SIGNATURE):
        return None
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)
