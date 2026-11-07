"""Cuts a stretch out of a finished render with FFmpeg (through the ``ProcessRunner`` port).

The stretch is re-encoded (H.264 / AAC, fast settings) so it starts on the requested frame instead
of the nearest keyframe: a section preview is for looking at, not for delivery.
"""

from pathlib import Path

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.shared.concurrency import JobContext


class FfmpegSectionCutter:
    """Structurally implements ``SectionCutter``."""

    def __init__(self, runner: ProcessRunner, executable: str = "ffmpeg") -> None:
        self._runner = runner
        self._exe = executable

    def cut(
        self,
        source: Path,
        *,
        start_seconds: float,
        end_seconds: float,
        destination: Path,
        ctx: JobContext,
    ) -> None:
        self._runner.run(
            ProcessSpec(
                self._exe,
                [
                    "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{start_seconds:.6f}", "-i", str(source),
                    "-t", f"{end_seconds - start_seconds:.6f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(destination),
                ],
                timeout_seconds=3600.0,
                check=True,
            ),
            cancellation=ctx.cancellation,
        )  # fmt: skip
