"""A slow local model cannot occupy a job indefinitely or hold cancelled requests in its queue."""

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from media_house.modules.content_intelligence.domain.config import LlmSettings
from media_house.modules.content_intelligence.infrastructure.transformers_llm import (
    TransformersLanguageModel,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError, OperationCancelledError


def test_waiting_for_another_generation_uses_the_request_time_budget() -> None:
    engine = TransformersLanguageModel()
    engine._lock.acquire()
    try:
        with pytest.raises(ExternalSystemError, match="time budget"):
            engine.complete(
                "system", "brief", LlmSettings(timeout_seconds=0.01), CancellationToken()
            )
    finally:
        engine._lock.release()


def test_waiting_for_another_generation_can_be_cancelled() -> None:
    engine = TransformersLanguageModel()
    token = CancellationToken()
    entered = threading.Event()
    errors: list[Exception] = []

    def request() -> None:
        entered.set()
        try:
            engine.complete("system", "brief", LlmSettings(timeout_seconds=10), token)
        except Exception as exc:  # noqa: BLE001 - collect the worker's failure for assertion
            errors.append(exc)

    engine._lock.acquire()
    worker = threading.Thread(target=request)
    worker.start()
    try:
        assert entered.wait(1)
        token.cancel()
        worker.join(1)
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], OperationCancelledError)
    finally:
        token.cancel()
        engine._lock.release()
        worker.join(2)


def test_loading_consumes_the_same_budget_as_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    engine = TransformersLanguageModel()

    def load(_name: str) -> tuple[None, None, str]:
        clock[0] = 2.0
        return None, None, "cpu"

    monkeypatch.setattr(engine, "_load", load)
    with pytest.raises(ExternalSystemError, match="time budget"):
        engine.complete("system", "brief", LlmSettings(timeout_seconds=1), CancellationToken())
    assert not engine._lock.locked()


@pytest.mark.parametrize("interrupt", ["timeout", "cancelled", "none"])
def test_generation_stops_at_the_deadline_or_cancellation_and_discards_partial_text(
    monkeypatch: pytest.MonkeyPatch, interrupt: str
) -> None:
    import torch

    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    token = CancellationToken()
    checks: list[bool] = []

    class Tokenizer:
        pad_token_id = 0
        eos_token_id = 9

        def apply_chat_template(self, *args: Any, **kwargs: Any) -> str:
            return "brief"

        def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"input_ids": torch.tensor([[1, 2]])}

        def decode(self, *args: Any, **kwargs: Any) -> str:
            return '"ok":true}'

    class Model:
        config = SimpleNamespace(max_position_embeddings=64)

        def generate(self, **kwargs: Any) -> Any:
            assert 0 < kwargs["max_time"] <= 1
            if interrupt == "timeout":
                clock[0] = 2.0
            elif interrupt == "cancelled":
                token.cancel()
            stopped = kwargs["stopping_criteria"](kwargs["input_ids"], None)
            checks.append(bool(stopped.all()))
            return torch.tensor([[1, 2, 3]])

    engine = TransformersLanguageModel()
    monkeypatch.setattr(engine, "_load", lambda _name: (Tokenizer(), Model(), "cpu"))
    settings = LlmSettings(timeout_seconds=1, max_output_tokens=4)
    if interrupt == "timeout":
        with pytest.raises(ExternalSystemError, match="time budget"):
            engine.complete("system", "brief", settings, token)
    elif interrupt == "cancelled":
        with pytest.raises(OperationCancelledError):
            engine.complete("system", "brief", settings, token)
    else:
        reply = engine.complete("system", "brief", settings, token)
        assert reply.text == '{"ok":true}'
        assert reply.completion_tokens == 1
    assert checks == [interrupt != "none"]
    assert not engine._lock.locked()
