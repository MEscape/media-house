"""Fakes for the engines Content Intelligence talks to (audio, language model, embeddings)."""

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from media_house.modules.audio_intelligence.application.contracts import (
    Transcript,
    TranscriptionResult,
)
from media_house.modules.audio_intelligence.domain.builder import BuildContext, build_transcript
from media_house.modules.audio_intelligence.domain.raw import (
    ALIGNMENT_FORCED,
    RawSegment,
    RawTranscription,
    RawWord,
)
from media_house.modules.content_intelligence.application.ports import EngineIdentity, LlmReply
from media_house.modules.content_intelligence.domain.config import EmbeddingSettings, LlmSettings
from media_house.modules.content_intelligence.domain.similarity import SimilarityProvider
from media_house.modules.content_intelligence.infrastructure.numpy_similarity import DenseSimilarity
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, ExternalSystemError, Ok, Result, ValidationError
from tests.support.asset_dto import make_asset
from tests.support.audio_fakes import CREATED


def audio_transcript(
    lines: Sequence[str],
    *,
    asset_id: str = "src",
    seconds_per_word: float = 0.4,
    language: str = "en",
) -> Transcript:
    """A real Audio Intelligence ``Transcript`` with forced-alignment style word times."""
    clock = 0.0
    segments: list[RawSegment] = []
    for line in lines:
        words: list[RawWord] = []
        start = clock
        for token in line.split():
            words.append(RawWord(token, clock, clock + seconds_per_word, 0.9))
            clock += seconds_per_word
        segments.append(RawSegment(start, clock, line, tuple(words)))
        clock += 0.3
    raw = RawTranscription(
        segments=tuple(segments),
        language=language,
        language_detected=True,
        engine="fake",
        engine_version="1",
        model="large-v3",
        model_source=None,
        alignment_engine="fake",
        alignment_model="fake-aligner",
        alignment_method=ALIGNMENT_FORCED,
        device="cpu",
        compute_type="int8",
    )
    return build_transcript(
        raw,
        BuildContext(
            source_asset_id=asset_id,
            audio_asset_id="aud",
            duration=clock + 1.0,
            audio_offset=0.0,
            sample_rate=16_000,
            channels=1,
            processing_version=1,
            created_at=CREATED,
        ),
    )


@dataclass
class FakeAudioEngine:
    """Stands in for Audio Intelligence: counts calls, optionally fails."""

    transcript: Transcript | None = None
    failure: ValidationError | None = None
    calls: int = 0
    seen: list[str] = field(default_factory=list)

    def execute(self, command, ctx: JobContext) -> Result[TranscriptionResult, ValidationError]:  # type: ignore[no-untyped-def]
        _ = ctx
        self.calls += 1
        self.seen.append(command.source_asset_id)
        if self.failure is not None:
            return Err(self.failure)
        assert self.transcript is not None
        asset: MediaAssetDto = make_asset("transcript-doc")
        return Ok(
            TranscriptionResult(self.transcript, asset, make_asset("aud"), created=self.calls == 1)
        )


@dataclass
class FakeLanguageModel:
    """Scripted ``LanguageModel``: answers from a function of the prompt, records every call."""

    reply: Callable[[str], str] | None = None
    fail: bool = False
    available: str | None = None
    calls: int = 0
    prompts: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    settings: list[LlmSettings] = field(default_factory=list)

    def identity(self, settings: LlmSettings) -> EngineIdentity:
        return EngineIdentity("fake-llm", "1", settings.model)

    def unavailable(self, settings: LlmSettings) -> str | None:
        _ = settings
        return self.available

    def complete(
        self, system: str, user: str, settings: LlmSettings, cancellation: CancellationToken
    ) -> LlmReply:
        cancellation.raise_if_cancelled()
        self.calls += 1
        self.prompts.append(user)
        self.systems.append(system)
        self.settings.append(settings)
        if self.fail:
            raise ExternalSystemError("fake outage", user_message="down")
        text = (self.reply or summarise_every_target)(user)
        return LlmReply(text, prompt_tokens=len(user) // 4, completion_tokens=len(text) // 4)


def summarise_every_target(prompt: str) -> str:
    """A well-behaved model: a grounded summary and a role for every TARGET passage."""
    segments = []
    current: dict[str, object] | None = None
    for line in prompt.splitlines():
        if m := re.match(r"(S\d+) \((TARGET|CONTEXT)\)", line):
            current = {"id": m.group(1), "numbers": []} if m.group(2) == "TARGET" else None
            if current is not None:
                segments.append(current)
        elif current is not None and (m := re.match(r"\[(\d+)\] ", line)):
            current["numbers"].append(int(m.group(1)))  # type: ignore[attr-defined]
    return json.dumps(
        {
            "segments": [
                {
                    "id": s["id"],
                    "summary": "A short grounded summary of the passage.",
                    "summary_sentences": s["numbers"],
                    "roles": ["explanation"],
                }
                for s in segments
            ],
            "key_points": [],
            "relationships": [],
            "topics": [],
        }
    )


@dataclass
class FakeSemanticEngine:
    """Deterministic hashed bag-of-words vectors behind the real dense similarity."""

    fail: bool = False
    available: str | None = None
    calls: int = 0
    last_device: str = "none"

    @property
    def device(self) -> str:
        return self.last_device

    def identity(self, settings: EmbeddingSettings) -> EngineIdentity:
        return EngineIdentity("fake-embedder", "1", settings.model or "")

    def unavailable(self, settings: EmbeddingSettings) -> str | None:
        _ = settings
        return self.available

    def embed(
        self, texts: Sequence[str], settings: EmbeddingSettings, cancellation: CancellationToken
    ) -> list[list[float]]:
        cancellation.raise_if_cancelled()
        self.calls += 1
        if self.fail:
            raise ExternalSystemError("fake embedding failure", user_message="no")
        dim = 128
        matrix = np.zeros((len(texts), dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for token in re.findall(r"\w+", text.casefold()):
                matrix[i, int(hashlib.sha256(token.encode()).hexdigest(), 16) % dim] += 1.0
        self.last_device = "cpu"
        return [[float(x) for x in row] for row in matrix.tolist()]

    def index(
        self, vectors: Sequence[Sequence[float]], settings: EmbeddingSettings
    ) -> SimilarityProvider:
        return DenseSimilarity(np.asarray(vectors, dtype=np.float32), f"embedding:{settings.model}")
