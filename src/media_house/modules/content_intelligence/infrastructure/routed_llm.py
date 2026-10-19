"""Chooses between the in-process Hugging Face model and a model served over HTTP."""

from media_house.modules.content_intelligence.application.ports import (
    EngineIdentity,
    LanguageModel,
    LlmReply,
)
from media_house.modules.content_intelligence.domain.config import LlmSettings
from media_house.shared.concurrency import CancellationToken


class RoutedLanguageModel:
    """``endpoint=None`` -> the local engine; an endpoint -> the HTTP engine."""

    def __init__(self, local: LanguageModel, served: LanguageModel) -> None:
        self._local = local
        self._served = served

    def _pick(self, settings: LlmSettings) -> LanguageModel:
        return self._local if settings.endpoint is None else self._served

    def identity(self, settings: LlmSettings) -> EngineIdentity:
        return self._pick(settings).identity(settings)

    def unavailable(self, settings: LlmSettings) -> str | None:
        return self._pick(settings).unavailable(settings)

    def complete(
        self, system: str, user: str, settings: LlmSettings, cancellation: CancellationToken
    ) -> LlmReply:
        return self._pick(settings).complete(system, user, settings, cancellation)
