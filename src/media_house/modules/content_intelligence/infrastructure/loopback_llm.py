"""A language model reached over an OpenAI-compatible chat API (Ollama, llama.cpp server, vLLM...).

Privacy by construction: the endpoint must be on this machine (``localhost``, ``127.0.0.1`` or
``::1``) unless the settings say ``allow_remote_endpoint``, in which case the user has decided
that transcripts may leave the machine. No API key is read or sent, nothing is downloaded, and
only the one configured endpoint is ever contacted.
"""

import http.client
import json
import socket
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

    def identity(self, settings: LlmSettings) -> EngineIdentity:
        return EngineIdentity("openai-compatible-chat", _VERSION, settings.model)

    def unavailable(self, settings: LlmSettings) -> str | None:
        try:
            _, host, port, _ = _target(settings)
        except ExternalSystemError as exc:
            return exc.user_message
        try:
            with socket.create_connection((host, port), timeout=_PROBE_SECONDS):
                return None
        except OSError as exc:
            return f"nothing is listening at {host}:{port} ({exc.__class__.__name__})"

    def complete(
        self,
        system: str,
        user: str,
        settings: LlmSettings,
        cancellation: CancellationToken,
    ) -> LlmReply:
        cancellation.raise_if_cancelled()
        scheme, host, port, base = _target(settings)
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
        connection_type = (
            http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_type(host, port, timeout=settings.timeout_seconds)
        try:
            connection.request(
                "POST",
                f"{base}/chat/completions",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read(_MAX_REPLY_BYTES + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise ExternalSystemError(
                f"Language model request failed: {exc.__class__.__name__}",
                user_message="The language model did not answer.",
                details={"host": host},
            ) from exc
        finally:
            connection.close()
        if response.status != 200 or len(raw) > _MAX_REPLY_BYTES:
            _log.warning("Language model returned an error", status=response.status)
            raise ExternalSystemError(
                f"Language model answered HTTP {response.status}",
                user_message="The language model returned an error.",
                details={"status": response.status},
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
        return LlmReply(
            text, _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))
        )


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
