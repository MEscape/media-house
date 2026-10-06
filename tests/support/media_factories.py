"""Real media files for audio tests: tones and test-pattern videos made with FFmpeg."""

import math
import struct
import subprocess
import wave
from pathlib import Path


def make_wav(path: Path, *, seconds: float = 2.0, rate: int = 44_100, channels: int = 2) -> Path:
    """A quiet 440 Hz tone: stereo 44.1 kHz so preparation really has to convert."""
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) * channels
        for i in range(int(seconds * rate))
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return path


def ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],  # noqa: S607
        check=True,
    )


def make_video(path: Path, *, audio_delay: float | None = 0.0, seconds: int = 2) -> Path:
    """MP4 with a test pattern; ``audio_delay=None`` makes it silent (no audio track)."""
    video = ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=25"]
    if audio_delay is None:
        ffmpeg(*video, "-c:v", "mpeg4", "-an", str(path))
        return path
    delay = ["-itsoffset", str(audio_delay)] if audio_delay else []
    audio = [*delay, "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    ffmpeg(*video, *audio, "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(path))
    return path
