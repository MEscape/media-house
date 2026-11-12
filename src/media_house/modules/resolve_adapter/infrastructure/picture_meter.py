"""The average colour of a recording, measured with FFmpeg (a few frames, no file written)."""

import re
from pathlib import Path

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.modules.resolve_adapter.domain.grade import rgb_of_yuv

#: Every 150th frame of the first 750 (about 25 s at 30 fps): enough for a room's colour.
_FILTER = r"select='not(mod(n\,150))',signalstats,metadata=print:file=-"
_VALUE = re.compile(r"lavfi\.signalstats\.(YAVG|UAVG|VAVG)=([\d.]+)")


class FfmpegPictureMeter:
    def __init__(self, runner: ProcessRunner, executable: str = "ffmpeg") -> None:
        self._runner = runner
        self._executable = executable

    def mean_rgb(self, path: Path) -> tuple[float, float, float] | None:
        result = self._runner.run(
            ProcessSpec(
                executable=self._executable,
                args=(
                    "-hide_banner", "-nostats", "-i", str(path), "-vf", _FILTER,
                    "-frames:v", "5", "-an", "-f", "null", "-",
                ),
                timeout_seconds=120,
            )
        )  # fmt: skip
        if not result.succeeded:
            return None
        found: dict[str, list[float]] = {"YAVG": [], "UAVG": [], "VAVG": []}
        for name, value in _VALUE.findall(result.stdout):
            found[name].append(float(value))
        if not all(found.values()):
            return None
        y, u, v = (sum(found[k]) / len(found[k]) for k in ("YAVG", "UAVG", "VAVG"))
        return rgb_of_yuv(y, u, v)
