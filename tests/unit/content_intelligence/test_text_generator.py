"""The language model Content Intelligence owns, offered to other modules as ``TextGenerator``."""

import pytest

from media_house.modules.content_intelligence.application.generation import (
    DEFAULT_GENERATION_MODEL,
    ConfiguredTextGenerator,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError, OperationCancelledError
from tests.support.ci_engines import FakeLanguageModel


def test_it_forwards_the_prompt_and_returns_only_the_text() -> None:
    model = FakeLanguageModel(reply=lambda prompt: f"echo: {prompt}")
    generator = ConfiguredTextGenerator(model)

    text = generator.generate("be brief", "hello", CancellationToken())

    assert text == "echo: hello"
    assert model.systems == ["be brief"] and model.prompts == ["hello"]
    assert model.settings[0].model == DEFAULT_GENERATION_MODEL
    assert model.settings[0].temperature == 0.0  # deterministic decoding


def test_identity_and_availability_come_from_the_engine() -> None:
    model = FakeLanguageModel(available="model files are missing")
    generator = ConfiguredTextGenerator(model)

    assert DEFAULT_GENERATION_MODEL in generator.identity()
    assert generator.unavailable() == "model files are missing"
    assert ConfiguredTextGenerator(FakeLanguageModel()).unavailable() is None


def test_engine_failures_and_cancellation_pass_through() -> None:
    failing = ConfiguredTextGenerator(FakeLanguageModel(fail=True))
    token = CancellationToken()
    token.cancel()

    with pytest.raises(ExternalSystemError):
        failing.generate("s", "u", CancellationToken())
    with pytest.raises(OperationCancelledError):
        ConfiguredTextGenerator(FakeLanguageModel()).generate("s", "u", token)
