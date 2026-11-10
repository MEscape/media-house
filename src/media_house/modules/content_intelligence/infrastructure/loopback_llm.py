"""A language model reached over an OpenAI-compatible chat API.

Local servers (Ollama, llama.cpp server, vLLM) and hosted providers (Groq, Cerebras, OpenRouter,
Gemini; presets in ``domain.config.LLM_PROVIDERS``) speak the same ``/chat/completions`` API.

Privacy by construction: the endpoint must be on this machine (``localhost``, ``127.0.0.1`` or
``::1``) unless the settings say ``allow_remote_endpoint``, in which case the user has decided
that transcripts may leave the machine. A bearer key is sent only when the settings name a secret
(``api_key_secret``); it is read from the application's secrets at call time and never logged.
Nothing is downloaded, and only the one configured endpoint is ever contacted.

Rate limits: 429 and 5xx answers are retried up to ``max_attempts`` times, waiting what
``Retry-After`` asks (bounded) or a short exponential backoff. Every answered request logs its
usage (prompt/completion counts) for quota and cost tracking.
"""

import http.client
import json
import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from media_house.modules.content_intelligence.application.ports import (
    EngineIdentity,
    LlmReply,
)
from media_house.modules.content_intelligence.domain.config import LOOPBACK_HOSTS, LlmSettings
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_MAX_REPLY_BYTES = 2_000_000
_PROBE_SECONDS = 2.0
_VERSION = "openai-chat-v1"
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: A longer ``Retry-After`` (a daily quota, typically) fails at once instead of blocking a job.
_MAX_WAIT_SECONDS = 60.0
_BACKOFF_SECONDS = 1.0

#: ``(status, Retry-After header or None, body)``.
type HttpAnswer = tuple[int, str | None, bytes]
#: ``(scheme, host, port, path, body, headers, timeout) -> answer``; raises ``OSError`` /
#: ``http.client.HTTPException`` for transport failures. Swapped by tests (no network).
type HttpPost = Callable[[str, str, int, str, bytes, dict[str, str], float], HttpAnswer]
#: Secret name (``groq_api_key``) -> value, or ``None`` when it is not configured.
type SecretLookup = Callable[[str], str | None]


def http_post(
    scheme: str,
    host: str,
    port: int,
    path: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
) -> HttpAnswer:
    connection_type = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(host, port, timeout=timeout)
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        return (
            response.status,
            response.getheader("Retry-After"),
            response.read(_MAX_REPLY_BYTES + 1),
        )
    finally:
        connection.close()


def _target(settings: LlmSettings) -> tuple[str, str, int, str]:
    """``(scheme, host, port, base path)``; raises for a refused endpoint."""
    parts = urlsplit(settings.endpoint or "")
    host = parts.hostname or ""
    if parts.scheme not in {"http", "https"} or not host:
        raise ExternalSystemError(
            f"Unsupported language-model endpoint {settings.endpoint!r}",
            user_message="The language-model address must be an http(s) URL.",
        )
    if host not in LOOPBACK_HOSTS and not settings.allow_remote_endpoint:
        raise ExternalSystemError(
            f"Refused language-model endpoint on host {host!r}",
            user_message=(
                "The language model must run on this computer. Allow a remote endpoint "
                "explicitly if transcripts may leave this machine."
            ),
        )
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, host, port, parts.path.rstrip("/")


class LoopbackChatModel:
    """Stateless: every call carries its settings."""

    def __init__(
        self,
        secret: SecretLookup = lambda _name: None,
        post: HttpPost = http_post,
    ) -> None:
        self._secret = secret
        self._post = post

    def identity(self, settings: LlmSettings) -> EngineIdentity:
        host = urlsplit(settings.endpoint or "").hostname or ""
        # A hosted provider is part of the identity: the same model id may answer differently.
        version = _VERSION if host in LOOPBACK_HOSTS else f"{_VERSION}@{host}"
        return EngineIdentity("openai-compatible-chat", version, settings.model)

    def unavailable(self, settings: LlmSettings) -> str | None:
        try:
            _, host, port, _ = _target(settings)
            self._headers(settings)
        except ExternalSystemError as exc:
            return exc.user_message
        try:
            with socket.create_connection((host, port), timeout=_PROBE_SECONDS):
                return None
        except OSError as exc:
            return f"nothing is listening at {host}:{port} ({exc.__class__.__name__})"

    def _headers(self, settings: LlmSettings) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if (name := settings.api_key_secret) is not None:
            key = self._secret(name)
            if not key:
                variable = f"MEDIA_HOUSE_SECRET_{name.upper()}"
                raise ExternalSystemError(
                    f"API key secret {name!r} is not configured",
                    user_message=f"Set the environment variable {variable} to use this provider.",
                )
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def complete(
        self,
        system: str,
        user: str,
        settings: LlmSettings,
        cancellation: CancellationToken,
    ) -> LlmReply:
        cancellation.raise_if_cancelled()
        scheme, host, port, base = _target(settings)
        headers = self._headers(settings)
        body = json.dumps(
            {
                "model": settings.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": settings.temperature,
                "max_tokens": settings.max_output_tokens,
                "seed": settings.seed,
                "stream": False,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        started = time.perf_counter()
        attempts = max(1, settings.max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                status, retry_after, raw = self._post(
                    scheme,
                    host,
                    port,
                    f"{base}/chat/completions",
                    body,
                    headers,
                    settings.timeout_seconds,
                )
            except (OSError, http.client.HTTPException) as exc:
                raise ExternalSystemError(
                    f"Language model request failed: {exc.__class__.__name__}",
                    user_message="The language model did not answer.",
                    details={"host": host},
                ) from exc
            if status not in _RETRY_STATUSES or attempt == attempts:
                break
            wait = _wait_seconds(retry_after, attempt)
            if wait is None:
                break
            _log.warning(
                "Language model busy, retrying",
                host=host,
                status=status,
                attempt=attempt,
                wait_seconds=wait,
            )
            if cancellation.wait(wait):
                cancellation.raise_if_cancelled()
        if status != 200 or len(raw) > _MAX_REPLY_BYTES:
            _log.warning("Language model returned an error", status=status, host=host)
            raise ExternalSystemError(
                f"Language model answered HTTP {status}",
                user_message=(
                    "The language-model provider's rate limit is reached; try again later."
                    if status == 429
                    else "The language model returned an error."
                ),
                details={"status": status, "retry_after": retry_after},
            )
        try:
            data = json.loads(raw)
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ExternalSystemError(
                "Language model reply has an unexpected shape",
                user_message="The language model returned an unreadable answer.",
            ) from exc
        if not isinstance(text, str):
            raise ExternalSystemError(
                "Language model reply has no text",
                user_message="The language model returned no text.",
            )
        reply = LlmReply(
            text, _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))
        )
        # Field names avoid "token": the log redactor masks any key containing it.
        _log.info(
            "Language model usage",
            host=host,
            model=settings.model,
            usage_prompt=reply.prompt_tokens,
            usage_completion=reply.completion_tokens,
            attempts=attempt,
            seconds=round(time.perf_counter() - started, 2),
        )
        return reply


def _wait_seconds(retry_after: str | None, attempt: int) -> float | None:
    """Seconds to wait before the next attempt, or ``None`` when the wait is too long."""
    try:
        wait = float(retry_after) if retry_after is not None else None
    except ValueError:
        wait = None  # an HTTP date: fall back to the backoff
    if wait is None:
        wait = _BACKOFF_SECONDS * 2 ** (attempt - 1)
    return max(0.0, wait) if wait <= _MAX_WAIT_SECONDS else None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
