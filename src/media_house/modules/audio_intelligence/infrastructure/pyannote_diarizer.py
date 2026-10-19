"""Speaker diarization with pyannote, through WhisperX's wrapper of it.

Everything pyannote/WhisperX-diarization specific stays in this file, and nothing in it is imported
unless a diarization is really requested. The pipeline is loaded lazily, kept per (model, device)
and used under a lock (the shared model is not thread-safe). A run never downloads: the model must
already be on disk (``scripts/fetch_models.py --diarization``) unless the config allows it.

The pyannote models are gated on Hugging Face (an account token and accepted terms), so
availability is a fact about this machine and is reported with the command that fixes it. This
adapter has not been run against a real model in this repository's tests (none is cached and no
token is configured); its behaviour is covered with fakes at the port, and by the availability
checks below.
"""

import importlib.metadata
import importlib.util
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from media_house.core.infrastructure.models import local_model_path
from media_house.modules.audio_intelligence.domain.diarization import (
    DiarizationConfig,
    SpeakerTurn,
)
from media_house.modules.audio_intelligence.infrastructure.wav_io import read_mono_pcm16
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

FETCH_COMMAND = "uv run python scripts/fetch_models.py --diarization"
_SAMPLE_RATE = 16_000


class PyannoteDiarizer:
    """Structurally implements ``SpeakerDiarizer``."""

    def __init__(
        self,
        model_dir: Path | None = None,
        token: Callable[[], str | None] = lambda: None,
    ) -> None:
        self._model_dir = model_dir
        self._token = token
        self._lock = threading.Lock()
        self._pipelines: dict[tuple[str, str], Any] = {}

    # --- port ------------------------------------------------------------------------------
    def identity(self, config: DiarizationConfig) -> dict[str, str]:
        try:
            version = importlib.metadata.version("pyannote.audio")
        except importlib.metadata.PackageNotFoundError:
            version = "not-installed"
        return {"engine": "pyannote", "pyannote.audio": version, "model": config.model}

    def unavailable(self, config: DiarizationConfig) -> str | None:
        if importlib.util.find_spec("whisperx") is None:
            return "WhisperX (which provides pyannote) is not installed."
        if self._local(config) is None and not config.allow_model_download:
            return (
                f"the model {config.model!r} is not on disk. It is gated on Hugging Face: create "
                f"an account token, accept the model's terms there, then run `{FETCH_COMMAND}`."
            )
        return None

    def diarize(
        self,
        audio: Path,
        config: DiarizationConfig,
        cancellation: CancellationToken,
    ) -> tuple[SpeakerTurn, ...]:
        samples, rate = read_mono_pcm16(audio)
        if rate != _SAMPLE_RATE:
            raise ExternalSystemError(
                f"Diarization needs {_SAMPLE_RATE} Hz audio, the prepared file is {rate} Hz",
                user_message="The prepared audio has an unexpected sample rate.",
            )
        cancellation.raise_if_cancelled()
        try:
            with self._lock:
                pipeline = self._pipeline(config)
                frame = pipeline(
                    samples,
                    num_speakers=config.num_speakers,
                    min_speakers=config.min_speakers,
                    max_speakers=config.max_speakers,
                )
        except (RuntimeError, OSError, ValueError, ImportError) as exc:
            raise ExternalSystemError(
                f"Speaker diarization failed: {exc}",
                user_message="Speaker diarization failed. See the log for details.",
                details={"model": config.model},
            ) from exc
        return tuple(
            SpeakerTurn(float(row.start), float(row.end), str(row.speaker))
            for row in frame.itertuples()
            if float(row.end) >= float(row.start)
        )

    # --- internals -------------------------------------------------------------------------
    def _local(self, config: DiarizationConfig) -> Path | None:
        return local_model_path(config.model, self._model_dir)

    def _pipeline(self, config: DiarizationConfig) -> Any:
        import torch
        from whisperx.diarize import DiarizationPipeline

        use_cuda = config.device == "cuda" or (
            config.device == "auto" and torch.cuda.is_available()
        )
        device = "cuda" if use_cuda else "cpu"
        key = (config.model, device)
        if key not in self._pipelines:
            local = self._local(config)
            _log.info("Loading the diarization model", model=config.model, device=device)
            self._pipelines[key] = DiarizationPipeline(
                model_name=str(local) if local is not None else config.model,
                token=None if local is not None else self._token(),
                device=device,
            )
        return self._pipelines[key]
