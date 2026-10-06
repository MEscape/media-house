"""Opt-in: runs the REAL WhisperX engine (downloads models, uses the GPU if present).

set MEDIA_HOUSE_SPEECH_SAMPLE=C:\\path\\to\\speech.wav      (spoken English, a few seconds)
set MEDIA_HOUSE_SPEECH_MODEL=base                          (optional, default "base")
uv run pytest tests/integration/audio_intelligence/test_whisperx_real.py
"""

import os
from pathlib import Path

import pytest

from media_house.core.infrastructure.process import SubprocessRunner
from media_house.modules.audio_intelligence.domain.raw import ALIGNMENT_FORCED
from media_house.modules.audio_intelligence.domain.values import (
    PreparationConfig,
    TranscriptionConfig,
)
from media_house.modules.audio_intelligence.infrastructure.ffmpeg_audio import FfmpegAudioPreparer
from media_house.modules.audio_intelligence.infrastructure.whisperx_engine import WhisperXEngine
from media_house.shared.concurrency import CancellationToken

SAMPLE = os.environ.get("MEDIA_HOUSE_SPEECH_SAMPLE")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not SAMPLE, reason="set MEDIA_HOUSE_SPEECH_SAMPLE to run the real engine"),
    pytest.mark.filterwarnings("ignore"),  # torch/pyannote/lightning emit import-time warnings
]


def test_real_engine_produces_ordered_aligned_words(tmp_path: Path) -> None:
    assert SAMPLE is not None
    token = CancellationToken()
    prepared = tmp_path / "prepared.wav"
    FfmpegAudioPreparer(SubprocessRunner()).prepare(
        Path(SAMPLE),
        prepared,
        PreparationConfig(),
        token,
    )
    engine = WhisperXEngine()
    config = TranscriptionConfig(model=os.environ.get("MEDIA_HOUSE_SPEECH_MODEL", "base"))

    raw = engine.transcribe(prepared, config, token, lambda _stage: None)
    again = engine.transcribe(prepared, config, token, lambda _stage: None)  # reuses loaded models

    words = [w for s in raw.segments for w in s.words]
    assert raw.alignment_method == ALIGNMENT_FORCED
    assert raw.language_detected
    assert words
    starts = [w.start for w in words if w.start is not None]
    assert starts == sorted(starts)
    assert all(w.end is not None and w.start is not None and w.end >= w.start for w in words)
    assert [w.text for s in again.segments for w in s.words] == [w.text for w in words]
