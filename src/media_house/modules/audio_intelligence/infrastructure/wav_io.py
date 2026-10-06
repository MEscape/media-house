"""Reading the prepared PCM WAV files (our own FFmpeg output) into float arrays."""

import wave
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from media_house.modules.audio_intelligence.domain.errors import UnreadableAudio


def read_mono_pcm16(path: Path) -> tuple[NDArray[np.float32], int]:
    """Samples in [-1, 1) and the sample rate of a mono 16-bit PCM WAV."""
    try:
        with wave.open(str(path), "rb") as wav:
            rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())
    except (OSError, EOFError, wave.Error) as exc:
        raise UnreadableAudio(f"prepared audio cannot be read ({exc})") from exc
    if channels != 1 or width != 2:
        raise UnreadableAudio("prepared audio is not mono 16-bit PCM")
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0, rate
