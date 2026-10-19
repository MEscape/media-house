"""Opt-in smoke test: the REAL pyannote diarization adapter. It never runs by default.

It needs authorised access that this repository's tests never use: a Hugging Face account that has
accepted the terms of the gated model, and the model on disk. Steps, once you have that:

    1. huggingface.co: accept the terms of pyannote/speaker-diarization-community-1
    2. set HF_TOKEN=<your read token>
    3. uv run python scripts/fetch_models.py --diarization
    4. set MEDIA_HOUSE_DIARIZATION_SAMPLE=C:\\path\\to\\two_speakers.wav   (a few seconds of at
       least two people taking turns; the more distinct the voices the better)
    5. uv run pytest tests/integration/audio_intelligence/test_pyannote_real.py -p no:cacheprovider

It checks that the adapter loads the on-disk model without any network use, returns well-formed
non-overlapping-in-time-order turns on the prepared audio, labels them with 2+ speakers, that a
second call reuses the loaded pipeline, and that the labels reach the words of a transcript.
It does NOT judge accuracy: that needs labelled footage.
"""

import os
from pathlib import Path

import pytest

from media_house.core.infrastructure.process import SubprocessRunner
from media_house.modules.audio_intelligence.domain.diarization import (
    Diarization,
    DiarizationConfig,
    assign_speakers,
)
from media_house.modules.audio_intelligence.domain.values import PreparationConfig
from media_house.modules.audio_intelligence.infrastructure.ffmpeg_audio import FfmpegAudioPreparer
from media_house.modules.audio_intelligence.infrastructure.pyannote_diarizer import PyannoteDiarizer
from media_house.shared.concurrency import CancellationToken
from media_house.shared.filesystem import AppPaths
from tests.support.analysis_fakes import timeline

SAMPLE = os.environ.get("MEDIA_HOUSE_DIARIZATION_SAMPLE")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not SAMPLE, reason="set MEDIA_HOUSE_DIARIZATION_SAMPLE to run the real diarizer"
    ),
    pytest.mark.filterwarnings("ignore"),  # torch/pyannote/lightning emit import-time warnings
]


def test_the_real_adapter_finds_turns_and_the_labels_reach_the_words(tmp_path: Path) -> None:
    assert SAMPLE is not None
    models = AppPaths.for_user().cache_dir / "audio_intelligence" / "models"
    diarizer = PyannoteDiarizer(models)
    config = DiarizationConfig()
    why = diarizer.unavailable(config)
    if why is not None:
        pytest.skip(why)  # the model is not on disk: follow the steps above
    token = CancellationToken()
    prepared = tmp_path / "prepared.wav"
    info = FfmpegAudioPreparer(SubprocessRunner()).prepare(
        Path(SAMPLE), prepared, PreparationConfig(), token
    )

    turns = diarizer.diarize(prepared, config, token)
    again = diarizer.diarize(prepared, config, token)  # reuses the loaded pipeline

    assert turns, "no speaker turns were found"
    assert all(0.0 <= t.start <= t.end <= info.duration + 1.0 for t in turns)
    assert len({t.speaker for t in turns}) >= 2, "expected at least two speakers in the sample"
    assert [(t.start, t.end, t.speaker) for t in again] == [
        (t.start, t.end, t.speaker) for t in turns
    ]  # deterministic for the same audio and settings
    identity = diarizer.identity(config)
    assert identity["model"] == config.model and identity["pyannote.audio"] != "not-installed"

    words = [("alpha", t.start, t.start + 0.2) for t in turns[:6]]
    labelled = assign_speakers(
        timeline([words], duration=info.duration).transcript,
        Diarization(turns, identity),
        0.0,
    )
    assert any(w.speaker for w in labelled.words)
