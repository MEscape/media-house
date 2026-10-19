"""Ports for the two optional model engines. Both are adapters in ``infrastructure``.

Neither is required: without an embedding engine the analysis measures similarity by word
overlap, and without a language model it keeps its deterministic result. Neither adapter may
download anything unless its settings allow it, and neither may send data off this machine
unless the user configured that explicitly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from media_house.modules.content_intelligence.domain.config import EmbeddingSettings, LlmSettings
from media_house.modules.content_intelligence.domain.similarity import SimilarityProvider
from media_house.shared.concurrency import CancellationToken


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    """Cheap to obtain (loads no model); part of every result's processing identity."""

    name: str
    version: str
    model: str


@dataclass(frozen=True, slots=True)
class LlmReply:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class SemanticEngine(Protocol):
    """Sentence embeddings turned into a similarity provider."""

    def identity(self, settings: EmbeddingSettings) -> EngineIdentity: ...

    def unavailable(self, settings: EmbeddingSettings) -> str | None:
        """Why the engine cannot run now (library, model files, device), else ``None``."""
        ...

    def embed(
        self,
        texts: Sequence[str],
        settings: EmbeddingSettings,
        cancellation: CancellationToken,
    ) -> list[list[float]]:
        """Blocking. One vector per text (batched; repeated texts are reused).

        Raises ``ExternalSystemError`` for a failure of the model runtime; falls back from GPU
        to CPU on its own when the device fails.
        """
        ...

    def index(
        self, vectors: Sequence[Sequence[float]], settings: EmbeddingSettings
    ) -> SimilarityProvider:
        """Similarity over vectors made earlier (possibly by an earlier run and stored)."""
        ...

    @property
    def device(self) -> str:
        """The device the last call ran on (``cpu``, ``cuda``) or ``none`` before the first."""
        ...


class LanguageModel(Protocol):
    """A chat-completion model on this machine (or one the user explicitly authorised)."""

    def identity(self, settings: LlmSettings) -> EngineIdentity: ...

    def unavailable(self, settings: LlmSettings) -> str | None: ...

    def complete(
        self,
        system: str,
        user: str,
        settings: LlmSettings,
        cancellation: CancellationToken,
    ) -> LlmReply:
        """Blocking. Deterministic settings (temperature, seed) come from ``settings``.

        Raises ``ExternalSystemError`` for transport or server failures.
        """
        ...
