"""Short texts to multilingual sentence vectors, for other modules.

Content Intelligence already owns a sentence-embedding engine and its model; this exposes that one
engine through the public contract so no other module loads a second copy or picks another model.
It computes nothing on its own and never downloads: a missing model is ``unavailable`` and says
what to fetch.
"""

from collections.abc import Sequence

from media_house.modules.content_intelligence.application.ports import SemanticEngine
from media_house.modules.content_intelligence.domain.config import EmbeddingSettings
from media_house.shared.concurrency import CancellationToken

#: Apache-2.0, 118 M parameters, 50+ languages (German and English included), ~0.5 GB.
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class SemanticTextEncoder:
    """Structurally implements the ``TextEncoder`` contract."""

    def __init__(self, engine: SemanticEngine, settings: EmbeddingSettings | None = None) -> None:
        self._engine = engine
        self._settings = settings or EmbeddingSettings(model=DEFAULT_EMBEDDING_MODEL)

    def identity(self) -> str:
        found = self._engine.identity(self._settings)
        return f"{found.name}/{found.version}/{found.model}"

    def unavailable(self) -> str | None:
        return self._engine.unavailable(self._settings)

    def embed(self, texts: Sequence[str], cancellation: CancellationToken) -> list[list[float]]:
        return self._engine.embed(texts, self._settings, cancellation)
