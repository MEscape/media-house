"""Small real media files for proxy tests, made with FFmpeg at test time (nothing is committed)."""

import json
import subprocess
from pathlib import Path
from typing import Any

from tests.support.media_factories import ffmpeg

SIZE = "320x240"


def _video(seconds: float, *, size: str = SIZE, rate: int = 25) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={seconds}"]


def _audio(seconds: float, *, rate: int = 48_000, freq: int = 440) -> list[str]:
    return ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate={rate}:duration={seconds}"]


def cfr_with_audio(path: Path, *, seconds: float = 2, size: str = SIZE) -> Path:
    ffmpeg(*_video(seconds, size=size), *_audio(seconds), "-c:v", "libx264", "-c:a", "aac",
           "-shortest", str(path))  # fmt: skip
    return path


def video_only(path: Path, *, seconds: float = 2) -> Path:
    ffmpeg(*_video(seconds), "-c:v", "libx264", "-an", str(path))
    return path


def audio_only(path: Path, *, seconds: float = 2) -> Path:
    ffmpeg(*_audio(seconds), "-c:a", "aac", str(path))
    return path


def two_audio_tracks(path: Path, *, seconds: float = 2) -> Path:
    ffmpeg(*_video(seconds), *_audio(seconds, freq=440), *_audio(seconds, freq=880),
           "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac",
           "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=deu",
           "-shortest", str(path))  # fmt: skip
    return path


def rotated(path: Path, *, seconds: float = 2, degrees: int = 90) -> Path:
    plain = cfr_with_audio(path.with_name("plain-" + path.name), seconds=seconds)
    ffmpeg("-display_rotation", str(degrees), "-i", str(plain), "-c", "copy", str(path))
    return path


def anamorphic(path: Path, *, seconds: float = 2) -> Path:
    """720x576 with 16:15 pixels: displayed 4:3."""
    ffmpeg(*_video(seconds, size="720x576"), "-vf", "setsar=16/15", "-c:v", "libx264", "-an",
           str(path))  # fmt: skip
    return path


def variable_frame_rate(path: Path, *, seconds: float = 4) -> Path:
    ffmpeg(*_video(seconds), *_audio(seconds), "-vf", "setpts='(N/25+0.012*sin(N*1.7))/TB'",
           "-fps_mode", "passthrough", "-c:v", "libx264", "-c:a", "aac", "-shortest",
           str(path))  # fmt: skip
    return path


def late_start(path: Path, *, seconds: float = 2, offset: float = 1.5) -> Path:
    """Every stream starts ``offset`` seconds after zero."""
    plain = cfr_with_audio(path.with_name("plain-" + path.name), seconds=seconds)
    ffmpeg("-itsoffset", str(offset), "-i", str(plain), "-c", "copy", "-map", "0", str(path))
    return path


def cut_without_reencoding(path: Path, *, seconds: float = 4, start: float = 0.55) -> Path:
    """Starts mid-GOP: hidden pre-roll packets before time zero that no player shows."""
    plain = cfr_with_audio(path.with_name("plain-" + path.name), seconds=seconds)
    ffmpeg("-ss", str(start), "-i", str(plain), "-c", "copy", str(path))
    return path


def with_timecode(path: Path, *, seconds: float = 2) -> Path:
    ffmpeg(*_video(seconds), "-timecode", "01:00:00:00", "-c:v", "libx264", "-an", str(path))
    return path


# ---------------------------------------------------------------------- independent inspection
def probe(path: Path) -> dict[str, Any]:
    """ffprobe's own view of a file, deliberately not going through the module under test."""
    result = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],  # noqa: S607
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def streams(path: Path, kind: str) -> list[dict[str, Any]]:
    return [
        s
        for s in probe(path)["streams"]
        if s["codec_type"] == kind and not s.get("disposition", {}).get("attached_pic")
    ]


def rotation(stream: dict[str, Any]) -> int:
    """ffprobe's own number, comparable between a source and its proxy."""
    return next(
        (int(d["rotation"]) for d in stream.get("side_data_list", []) if "rotation" in d), 0
    )


def video_pts(path: Path) -> list[float]:
    """Presentation times of the frames a player shows (from time zero on), ascending."""
    result = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",  # noqa: S607
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    times = [float(t) for t in result.stdout.split() if t.strip(",") not in ("", "N/A")]
    return sorted(t for t in times if t >= -1e-6)
