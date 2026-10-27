"""Short text generation for other modules.

Content Intelligence already owns the language-model engine and its model folders; this exposes that
one engine through the public contract so no other module loads a second copy or picks another
model. It computes nothing on its own, never downloads, and a missing model is ``unavailable`` and
says what to fetch. Decoding is greedy, so the same prompt gives the same reply.
"""

from media_house.modules.content_intelligence.application.ports import LanguageModel
from media_house.modules.content_intelligence.domain.config import LlmSettings
from media_house.shared.concurrency import CancellationToken

#: Same default as ``scripts/fetch_models.py``: Apache-2.0, ~3 GB.
DEFAULT_GENERATION_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"


class ConfiguredTextGenerator:
    """Structurally implements the ``TextGenerator`` contract."""

    def __init__(self, model: LanguageModel, settings: LlmSettings | None = None) -> None:
        self._model = model
        self._settings = settings or LlmSettings(model=DEFAULT_GENERATION_MODEL)

    def identity(self) -> str:
        found = self._model.identity(self._settings)
        return f"{found.name}/{found.version}/{found.model}"

    def unavailable(self) -> str | None:
        return self._model.unavailable(self._settings)

    def generate(self, system: str, user: str, cancellation: CancellationToken) -> str:
        return self._model.complete(system, user, self._settings, cancellation).text
