"""FFmpeg-based thumbnail generator (images today, video frames tomorrow, same command)."""

from pathlib import Path

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.shared.errors import ExternalSystemError


class FfmpegThumbnailGenerator:
    """Structurally implements ``ThumbnailGenerator``; runs FFmpeg through the ProcessRunner port.

    A missing FFmpeg surfaces as ``ToolNotFoundError`` with an actionable message.
    """

    extension = ".webp"

    def __init__(
        self,
        runner: ProcessRunner,
        *,
        executable: str = "ffmpeg",
        timeout_seconds: float = 120.0,
    ) -> None:
        self._runner = runner
        self._executable = executable
        self._timeout = timeout_seconds

    def render(self, source: Path, destination: Path, *, max_size: int) -> None:
        box = int(max_size)
        scale = f"scale='min({box},iw)':'min({box},ih)':force_original_aspect_ratio=decrease"
        self._runner.run(
            ProcessSpec(
                self._executable,
                [
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source),
                    "-an",
                    "-sn",
                    "-frames:v",
                    "1",
                    "-vf",
                    scale,
                    "-c:v",
                    "libwebp",
                    "-quality",
                    "80",
                    str(destination),
                ],
                timeout_seconds=self._timeout,
                check=True,
            ),
        )
        if not destination.is_file() or destination.stat().st_size == 0:
            raise ExternalSystemError(
                "FFmpeg finished without producing a thumbnail",
                user_message="The preview could not be generated.",
            )
