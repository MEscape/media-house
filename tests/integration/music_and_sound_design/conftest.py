"""The real application (with this module registered) and a synthesised sound library.

No file of the owner's library is used here: the library is written by the test (click
tracks, tones, bursts). The opt-in real-library test lives in ``test_real_library.py``.
"""

import hashlib
import json
import math
import shutil
import struct
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.music_and_sound_design.module import MusicAndSoundDesignModule
from media_house.shared.configuration import Environment
from tests.support.sound_fakes import write_tone_wav

pytestmark = pytest.mark.integration

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required",
)


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    application = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    # Until the orchestrator adds the module to bootstrap/modules.py.
    if "music_and_sound_design" not in {m.name for m in application.modules}:
        MusicAndSoundDesignModule().register(application.container)
    yield application
    application.shutdown()


def write_burst(path: Path, *, seconds: float = 0.6, rate: int = 48_000) -> Path:
    """A short noise-like burst: instant attack, exponential decay (an 'impact')."""
    frames = []
    seed = 12345
    for i in range(int(seconds * rate)):
        seed = (1103515245 * seed + 12345) % (1 << 31)
        noise = (seed / (1 << 30)) - 1.0
        value = 0.8 * noise * math.exp(-i / rate * 12)
        frames.append(struct.pack("<h", int(32767 * value)) * 2)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(frames))
    return path


@dataclass(frozen=True)
class SynthLibrary:
    root: Path
    manifest: Path


def _entry(root: Path, rel: str, kind: str, category: str, **extra: object) -> dict[str, object]:
    data = (root / rel).read_bytes()
    with wave.open(str(root / rel)) as wav:
        seconds = wav.getnframes() / wav.getframerate()
    return {
        "path": rel,
        "kind": kind,
        "source": "synth",
        "source_page_url": "https://example.invalid/synth",
        "licence_name": "CC0",
        "licence_url": "https://example.invalid/cc0",
        "commercial_use": True,
        "attribution_required": False,
        "attribution_text": None,
        "author": "test",
        "original_title": Path(rel).stem,
        "tags": ["synth"],
        "categories": [category],
        "duration_seconds": seconds,
        "sample_rate": 48000,
        "channels": 2,
        "sha256": hashlib.sha256(data).hexdigest(),
        **extra,
    }


def build_library(root: Path) -> SynthLibrary:
    (root / "music").mkdir(parents=True)
    (root / "sfx").mkdir(parents=True)
    write_tone_wav(root / "music" / "clicks120.wav", seconds=24.0, clicks_bpm=120.0)
    write_tone_wav(root / "music" / "clicks100.wav", seconds=24.0, clicks_bpm=100.0)
    write_burst(root / "sfx" / "impact.wav")
    write_burst(root / "sfx" / "whoosh.wav", seconds=0.9)
    files = [
        _entry(
            root,
            "music/clicks120.wav",
            "music",
            "hype_energetic",
            attribution_required=True,
            attribution_text='"Clicks 120" test (CC BY)',
            bpm_source_stated="120",
            mood={"value": ["Driving"], "provenance": "source_stated"},
        ),
        _entry(
            root,
            "music/clicks100.wav",
            "music",
            "upbeat_positive",
            commercial_use=None,
            commercial_use_note="needs confirmation",
        ),
        _entry(root, "sfx/impact.wav", "sfx", "impact"),
        _entry(root, "sfx/whoosh.wav", "sfx", "whoosh", ai_training_allowed=False),
    ]
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "generated": "2026-11-02",
                "description": "synthetic test library",
                "category_vocabulary": ["hype_energetic", "upbeat_positive", "impact", "whoosh"],
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return SynthLibrary(root, manifest)


@pytest.fixture
def synth_library(tmp_path: Path) -> SynthLibrary:
    return build_library(tmp_path / "sounds")
