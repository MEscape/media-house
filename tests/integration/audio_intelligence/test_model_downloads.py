"""Models are never fetched during a run unless the config allows it.

These tests run the REAL WhisperX loading code but never touch the network: a missing model must
fail fast with the command that fetches it, and a cached one must load from disk only.
"""

import time
from pathlib import Path

import pytest

from media_house.modules.audio_intelligence.domain.values import TranscriptionConfig
from media_house.modules.audio_intelligence.infrastructure.whisperx_engine import (
    WhisperXEngine,
    _alignment_model_on_disk,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError
from tests.support.media_factories import make_wav

pytestmark = [
    pytest.mark.integration,
    pytest.mark.filterwarnings("ignore"),  # torch/pyannote/lightning emit import-time warnings
]


def cached(repo: str) -> bool:
    from huggingface_hub import snapshot_download

    try:
        snapshot_download(repo_id=repo, local_files_only=True)
    except Exception:  # noqa: BLE001 - "not cached" surfaces as several unrelated types
        return False
    return True


def test_downloads_are_off_unless_the_config_allows_them() -> None:
    assert TranscriptionConfig().allow_model_download is False


def test_allowing_downloads_does_not_change_a_results_identity() -> None:
    off = TranscriptionConfig().fingerprint_config("1")
    on = TranscriptionConfig(allow_model_download=True).fingerprint_config("1")

    assert off == on


def test_a_missing_speech_model_fails_fast_offline_and_names_the_fetch_command(
    tmp_path: Path,
) -> None:
    wav = make_wav(tmp_path / "tone.wav", seconds=1.0, rate=16_000, channels=1)
    config = TranscriptionConfig(model="definitely/not-a-real-whisper-model")
    started = time.perf_counter()

    with pytest.raises(ExternalSystemError) as raised:
        WhisperXEngine().transcribe(wav, config, CancellationToken(), lambda _stage: None)

    assert "scripts/fetch_models.py --speech" in raised.value.user_message
    assert time.perf_counter() - started < 60  # no download attempt


def test_a_missing_alignment_model_is_reported_not_downloaded() -> None:
    assert not _alignment_model_on_disk("nobody/not-an-alignment-model")


@pytest.mark.skipif(
    not cached("Systran/faster-whisper-base"), reason="the base model is not cached"
)
def test_a_cached_speech_model_loads_from_disk_only(tmp_path: Path) -> None:
    wav = make_wav(tmp_path / "tone.wav", seconds=1.0, rate=16_000, channels=1)
    config = TranscriptionConfig(model="base", language="en")

    raw = WhisperXEngine().transcribe(wav, config, CancellationToken(), lambda _stage: None)

    assert raw.model == "base" and raw.language == "en"
