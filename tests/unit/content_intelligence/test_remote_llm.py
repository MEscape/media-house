"""The OpenAI-compatible chat adapter against hosted providers, with a fake HTTP transport."""

import json
import logging

import pytest

from media_house.modules.content_intelligence.domain.config import (
    LLM_PROVIDERS,
    ContentConfig,
    LlmSettings,
)
from media_house.modules.content_intelligence.domain.errors import InvalidConfiguration
from media_house.modules.content_intelligence.infrastructure import loopback_llm
from media_house.modules.content_intelligence.infrastructure.loopback_llm import (
    HttpAnswer,
    LoopbackChatModel,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError

KEY = "gsk_very-secret-value"
OK_BODY = json.dumps(
    {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }
).encode()


class FakePost:
    def __init__(self, *answers: HttpAnswer) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, int, str, dict[str, str], float]] = []
        self.bodies: list[dict[str, object]] = []

    def __call__(
        self,
        scheme: str,
        host: str,
        port: int,
        path: str,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpAnswer:
        self.calls.append((host, port, path, headers, timeout))
        self.bodies.append(json.loads(body))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def groq(**changes: object) -> LlmSettings:
    preset = LLM_PROVIDERS["groq"]
    base = {
        "model": "openai/gpt-oss-120b",
        "endpoint": preset.endpoint,
        "api_key_secret": preset.api_key_secret,
        "allow_remote_endpoint": True,
        "timeout_seconds": 30.0,
    }
    return LlmSettings(**{**base, **changes})  # type: ignore[arg-type]


def model(post: FakePost, secrets: dict[str, str] | None = None) -> LoopbackChatModel:
    found = {"groq_api_key": KEY} if secrets is None else secrets
    return LoopbackChatModel(found.get, post)


def test_a_hosted_provider_gets_the_bearer_key_and_usage_is_logged_without_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    post = FakePost((200, None, OK_BODY))
    with caplog.at_level(logging.DEBUG):
        reply = model(post).complete("sys", "user", groq(), CancellationToken())

    assert reply.text == '{"ok": true}'
    assert (reply.prompt_tokens, reply.completion_tokens) == (12, 3)
    host, port, path, headers, timeout = post.calls[0]
    assert (host, port, path, timeout) == ("api.groq.com", 443, "/openai/v1/chat/completions", 30)
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert post.bodies[0]["model"] == "openai/gpt-oss-120b"
    assert post.bodies[0]["temperature"] == 0.0
    usage = [r for r in caplog.records if r.getMessage() == "Language model usage"]
    assert len(usage) == 1
    assert KEY not in caplog.text


def test_a_local_server_gets_no_key_and_keeps_its_old_identity() -> None:
    post = FakePost((200, None, OK_BODY))
    local = LlmSettings(model="qwen", endpoint=LLM_PROVIDERS["ollama"].endpoint)

    model(post).complete("s", "u", local, CancellationToken())

    assert "Authorization" not in post.calls[0][3]
    assert model(post).identity(local).version == "openai-chat-v1"
    assert model(post).identity(groq()).version == "openai-chat-v1@api.groq.com"


def test_a_missing_key_is_a_clear_reason_and_nothing_is_sent() -> None:
    post = FakePost((200, None, OK_BODY))
    engine = model(post, secrets={})

    with pytest.raises(ExternalSystemError) as raised:
        engine.complete("s", "u", groq(), CancellationToken())

    assert "MEDIA_HOUSE_SECRET_GROQ_API_KEY" in raised.value.user_message
    assert "MEDIA_HOUSE_SECRET_GROQ_API_KEY" in (engine.unavailable(groq()) or "")
    assert post.calls == []


def test_a_remote_provider_needs_explicit_consent() -> None:
    post = FakePost((200, None, OK_BODY))

    with pytest.raises(ExternalSystemError, match="Refused"):
        model(post).complete("s", "u", groq(allow_remote_endpoint=False), CancellationToken())
    assert post.calls == []


def test_rate_limits_are_retried_honouring_retry_after() -> None:
    post = FakePost((429, "0", b"{}"), (200, None, OK_BODY))

    reply = model(post).complete("s", "u", groq(), CancellationToken())

    assert reply.text == '{"ok": true}'
    assert len(post.calls) == 2


def test_a_long_retry_after_fails_at_once_with_a_rate_limit_message() -> None:
    post = FakePost((429, "3600", b"{}"))

    with pytest.raises(ExternalSystemError) as raised:
        model(post).complete("s", "u", groq(), CancellationToken())

    assert len(post.calls) == 1
    assert "rate limit" in raised.value.user_message


def test_server_errors_are_retried_a_bounded_number_of_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loopback_llm, "_BACKOFF_SECONDS", 0.0)
    post = FakePost((503, None, b"busy"))

    with pytest.raises(ExternalSystemError, match="HTTP 503"):
        model(post).complete("s", "u", groq(max_attempts=3), CancellationToken())
    assert len(post.calls) == 3


def test_client_errors_are_not_retried() -> None:
    post = FakePost((400, None, b"bad request"))

    with pytest.raises(ExternalSystemError, match="HTTP 400"):
        model(post).complete("s", "u", groq(), CancellationToken())
    assert len(post.calls) == 1


def test_max_attempts_is_validated() -> None:
    with pytest.raises(InvalidConfiguration):
        ContentConfig(llm=groq(max_attempts=0)).validate()
