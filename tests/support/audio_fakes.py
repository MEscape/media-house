"""Test doubles and builders for audio intelligence."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from media_house.modules.audio_intelligence.application.ports import EngineIdentity
from media_house.modules.audio_intelligence.domain.builder import BuildContext, build_transcript
from media_house.modules.audio_intelligence.domain.errors import AlignmentUnavailable
from media_house.modules.audio_intelligence.domain.raw import (
    ALIGNMENT_FORCED,
    RawSegment,
    RawTranscription,
    RawWord,
)
from media_house.modules.audio_intelligence.domain.transcript import Transcript
from media_house.modules.audio_intelligence.domain.values import TranscriptionConfig
from media_house.shared.concurrency import CancellationToken

CREATED = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: Prepared-audio times of the default script: (text, start, end).
SCRIPT = (
    ("Hello,", 0.42, 0.81),
    ("world.", 0.835, 1.24),
    ("AMAZING", 3.5, 4.4),
    ("Feuerwerk,", 4.5, 5.1),
)


def raw_transcription(
    *,
    language: str = "en",
    detected: bool = True,
    words: tuple[tuple[str, float | None, float | None], ...] = SCRIPT,
    split_after: int = 2,
    method: str = ALIGNMENT_FORCED,
) -> RawTranscription:
    """Two segments: the first ``split_after`` words, then the rest."""

    def segment(items: tuple[tuple[str, float | None, float | None], ...]) -> RawSegment:
        timed = [t for _, s, e in items for t in (s, e) if t is not None]
        return RawSegment(
            min(timed),
            max(timed),
            " ".join(w for w, _, _ in items),
            tuple(RawWord(w, s, e, 0.9) for w, s, e in items),
        )

    return RawTranscription(
        segments=tuple(
            segment(chunk) for chunk in (words[:split_after], words[split_after:]) if chunk
        ),
        language=language,
        language_detected=detected,
        engine="fake",
        engine_version="1",
        model="large-v3",
        model_source=None,
        alignment_engine="fake",
        alignment_model="fake-aligner",
        alignment_method=method,
        device="cpu",
        compute_type="int8",
    )


def build(
    raw: RawTranscription | None = None,
    *,
    duration: float = 10.0,
    offset: float = 0.0,
) -> Transcript:
    return build_transcript(
        raw or raw_transcription(),
        BuildContext(
            source_asset_id="src",
            audio_asset_id="aud",
            duration=duration,
            audio_offset=offset,
            sample_rate=16_000,
            channels=1,
            processing_version=1,
            created_at=CREATED,
        ),
    )


class FakeEngine:
    """Scripted ``TranscriptionEngine`` that counts how often it really ran."""

    def __init__(
        self,
        script: Callable[[TranscriptionConfig], RawTranscription] | None = None,
        *,
        version: str = "test-1",
    ) -> None:
        self.calls = 0
        self.configs: list[TranscriptionConfig] = []
        self.audio_paths: list[Path] = []
        self._version = version
        self._script = script or (
            lambda cfg: raw_transcription(
                language=cfg.language or "en",
                detected=cfg.language is None,
            )
        )

    def identity(self, config: TranscriptionConfig) -> EngineIdentity:
        _ = config
        return EngineIdentity("fake", self._version)

    def transcribe(
        self,
        audio: Path,
        config: TranscriptionConfig,
        cancellation: CancellationToken,
        on_stage: Callable[[str], None],
    ) -> RawTranscription:
        _ = cancellation
        self.calls += 1
        self.configs.append(config)
        self.audio_paths.append(audio)
        on_stage("fake stage")
        return self._script(config)


def refusing_alignment(config: TranscriptionConfig) -> RawTranscription:
    raise AlignmentUnavailable(config.language or "xx", "no aligner")
