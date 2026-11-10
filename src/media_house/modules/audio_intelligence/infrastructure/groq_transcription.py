"""Hosted speech recognition over an OpenAI-compatible ``/audio/transcriptions`` API (Groq).

An opt-in alternative to WhisperX for machines without a usable GPU. THE AUDIO LEAVES THIS
MACHINE: the module registers this engine only when the application is composed with
``RemoteTranscriptionSettings`` (default: local WhisperX), so nothing is ever uploaded by accident.

How it works: the prepared 16 kHz mono WAV (made once by the ``AudioPreparer``; the source is not
probed again) is cut into overlapping windows and each is encoded to lossless FLAC with FFmpeg
through the ``ProcessRunner``. A window stays under the provider's upload limit (Groq free tier:
25 MB per file). Each window is sent with ``response_format=verbose_json`` and word + segment
timestamp granularities at temperature 0. A segment is kept from the window whose share of the
overlap holds the segment's midpoint, so the stitched transcript has no duplicates; the language
found in the first window is pinned for the rest, so every window decodes the same language.

Limits (recorded honestly): word times come from the recogniser itself, not a forced aligner, so
``alignment_method`` is ``whisper_segment`` (consumers such as captions then warn that word times
are estimated). Words carry no confidence. ``device``/``compute_type`` are ``remote``. The engine,
endpoint host, model and window sizes are part of ``identity().version``, so results never share
a cache key with local WhisperX transcripts.
"""

import bisect
import http.client
import json
import math
import tempfile
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.modules.audio_intelligence.application.ports import EngineIdentity
from media_house.modules.audio_intelligence.domain.errors import UnreadableAudio
from media_house.modules.audio_intelligence.domain.raw import (
    ALIGNMENT_NONE,
    ALIGNMENT_SEGMENT,
    RawSegment,
    RawTranscription,
    RawWord,
)
from media_house.modules.audio_intelligence.domain.values import TranscriptionConfig
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ConfigurationError, ExternalSystemError, ProcessFailedError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

ENGINE_GROQ = "groq"
_VERSION = "openai-audio-v1"
_MAX_REPLY_BYTES = 50_000_000
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: Groq's audio limits are per hour; a longer ``Retry-After`` fails instead of blocking a job.
_MAX_WAIT_SECONDS = 120.0
_BACKOFF_SECONDS = 2.0
_ENCODE_TIMEOUT = 120.0

#: ``(status, Retry-After header or None, body)``.
type HttpAnswer = tuple[int, str | None, bytes]
#: ``(scheme, host, port, path, body, headers, timeout) -> answer``. Swapped by tests.
type HttpPost = Callable[[str, str, int, str, bytes, dict[str, str], float], HttpAnswer]


@dataclass(frozen=True, slots=True)
class RemoteTranscriptionSettings:
    endpoint: str = "https://api.groq.com/openai/v1"
    model: str = "whisper-large-v3-turbo"
    #: ``MEDIA_HOUSE_SECRET_GROQ_API_KEY``.
    api_key_secret: str = "groq_api_key"  # noqa: S105 - a secret name, not a credential
    #: 10 min of 16 kHz mono FLAC is well under 19 MB (raw PCM size), below the 25 MB limit.
    chunk_seconds: float = 600.0
    overlap_seconds: float = 10.0
    max_upload_bytes: int = 25_000_000
    timeout_seconds: float = 300.0
    max_attempts: int = 4

    def __post_init__(self) -> None:
        if not 30.0 <= self.chunk_seconds <= 1800.0:
            raise ConfigurationError("chunk_seconds must be 30-1800")
        if not 0.0 <= self.overlap_seconds <= self.chunk_seconds / 4:
            raise ConfigurationError("overlap_seconds must be 0 to a quarter of chunk_seconds")
        if not self.model.strip() or self.max_attempts < 1:
            raise ConfigurationError("model must be named and max_attempts at least 1")


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


class GroqTranscriptionEngine:
    """Structurally implements ``TranscriptionEngine``."""

    def __init__(
        self,
        runner: ProcessRunner,
        settings: RemoteTranscriptionSettings,
        secret: Callable[[str], str | None],
        *,
        post: HttpPost = http_post,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self._runner = runner
        self._settings = settings
        self._secret = secret
        self._post = post
        self._ffmpeg = ffmpeg
        parts = urlsplit(settings.endpoint)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ConfigurationError(f"Unsupported transcription endpoint {settings.endpoint!r}")
        self._scheme, self._host = parts.scheme, parts.hostname
        self._port = parts.port or (443 if parts.scheme == "https" else 80)
        self._path = parts.path.rstrip("/") + "/audio/transcriptions"

    def identity(self, config: TranscriptionConfig) -> EngineIdentity:
        _ = config
        s = self._settings
        return EngineIdentity(
            ENGINE_GROQ,
            f"{_VERSION}/{self._host}/{s.model}/w{s.chunk_seconds:g}-o{s.overlap_seconds:g}",
        )

    def transcribe(
        self,
        audio: Path,
        config: TranscriptionConfig,
        cancellation: CancellationToken,
        on_stage: Callable[[str], None],
    ) -> RawTranscription:
        key = self._key()
        duration = _wav_seconds(audio)
        windows = plan_windows(
            duration, self._settings.chunk_seconds, self._settings.overlap_seconds
        )
        language = config.language
        segments: list[RawSegment] = []
        with tempfile.TemporaryDirectory(prefix="remote-asr-", dir=audio.parent) as folder:
            for index, (start, length, keep_from, keep_to) in enumerate(windows):
                cancellation.raise_if_cancelled()
                on_stage(f"Recognising speech remotely ({index + 1}/{len(windows)})")
                encoded = Path(folder) / f"window-{index:04d}.flac"
                self._encode(audio, encoded, start, length, cancellation)
                reply = self._request(encoded, language, config.align, key, cancellation, length)
                found_language = _language_code(reply.get("language"))
                if language is None and found_language is not None:
                    language = found_language  # pin it: every window decodes the same language
                for segment in window_segments(reply, config.align):
                    middle = start + (segment.start + segment.end) / 2
                    if keep_from <= middle < keep_to:
                        segments.append(_shifted(segment, start))
        return RawTranscription(
            segments=tuple(segments),
            language=language or "und",
            language_detected=config.language is None,
            engine=ENGINE_GROQ,
            engine_version=self.identity(config).version,
            model=self._settings.model,
            model_source=self._settings.endpoint,
            alignment_engine=None,
            alignment_model=None,
            alignment_method=ALIGNMENT_SEGMENT if config.align else ALIGNMENT_NONE,
            device="remote",
            compute_type="remote",
        )

    # ------------------------------------------------------------------------------------------
    def _key(self) -> str:
        name = self._settings.api_key_secret
        key = self._secret(name)
        if not key:
            variable = f"MEDIA_HOUSE_SECRET_{name.upper()}"
            raise ConfigurationError(
                f"API key secret {name!r} is not configured",
                user_message=f"Set {variable} to use remote transcription.",
            )
        return key

    def _encode(
        self,
        audio: Path,
        destination: Path,
        start: float,
        length: float,
        cancellation: CancellationToken,
    ) -> None:
        args = [
            "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(audio),
            "-ac", "1", "-ar", "16000", "-c:a", "flac", "-compression_level", "8",
            str(destination),
        ]  # fmt: skip
        try:
            self._runner.run(
                ProcessSpec(
                    self._ffmpeg, args, timeout_seconds=_ENCODE_TIMEOUT + length, check=True
                ),
                cancellation=cancellation,
            )
        except ProcessFailedError as exc:
            raise UnreadableAudio(f"audio window could not be encoded ({exc})") from exc
        size = destination.stat().st_size
        if size > self._settings.max_upload_bytes:
            raise ConfigurationError(
                f"Encoded window is {size} bytes, above the upload limit",
                user_message="An audio window is too large to upload; lower chunk_seconds.",
            )

    def _request(
        self,
        flac: Path,
        language: str | None,
        words: bool,
        key: str,
        cancellation: CancellationToken,
        seconds: float,
    ) -> dict[str, Any]:
        fields = [
            ("model", self._settings.model),
            ("response_format", "verbose_json"),
            ("temperature", "0"),
            ("timestamp_granularities[]", "segment"),
            *([("timestamp_granularities[]", "word")] if words else []),
            *([("language", language)] if language else []),
        ]
        boundary = f"media-house-{uuid.uuid4().hex}"
        body = multipart(fields, flac.name, flac.read_bytes(), boundary)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {key}",
        }
        attempts = self._settings.max_attempts
        for attempt in range(1, attempts + 1):
            try:
                status, retry_after, raw = self._post(
                    self._scheme, self._host, self._port, self._path, body, headers,
                    self._settings.timeout_seconds,
                )  # fmt: skip
            except (OSError, http.client.HTTPException) as exc:
                raise ExternalSystemError(
                    f"Transcription request failed: {exc.__class__.__name__}",
                    user_message="The transcription service did not answer.",
                    details={"host": self._host},
                ) from exc
            if status not in _RETRY_STATUSES or attempt == attempts:
                break
            wait = retry_wait(retry_after, attempt)
            if wait is None:
                break
            _log.warning(
                "Transcription service busy, retrying",
                host=self._host,
                status=status,
                attempt=attempt,
                wait_seconds=wait,
            )
            if cancellation.wait(wait):
                cancellation.raise_if_cancelled()
        if status != 200 or len(raw) > _MAX_REPLY_BYTES:
            _log.warning("Transcription service returned an error", status=status, host=self._host)
            raise ExternalSystemError(
                f"Transcription service answered HTTP {status}",
                user_message={
                    401: "The transcription service rejected the API key.",
                    413: "An audio window is too large for the service; lower chunk_seconds.",
                    429: "The transcription service's rate limit is reached; try again later.",
                }.get(status, "The transcription service returned an error."),
                details={"status": status, "retry_after": retry_after},
            )
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ExternalSystemError(
                "Transcription reply is not JSON",
                user_message="The transcription service returned an unreadable answer.",
            ) from exc
        if not isinstance(data, dict):
            raise ExternalSystemError(
                "Transcription reply has an unexpected shape",
                user_message="The transcription service returned an unreadable answer.",
            )
        _log.info(
            "Remote transcription usage",
            host=self._host,
            model=self._settings.model,
            audio_seconds=round(seconds, 1),
            upload_bytes=len(body),
            attempts=attempt,
        )
        return data


# --- pure helpers (unit-tested) --------------------------------------------------------------
def plan_windows(
    duration: float, chunk: float, overlap: float
) -> list[tuple[float, float, float, float]]:
    """``(start, length, keep_from, keep_to)`` per window; keep ranges tile ``[0, inf)``."""
    if duration <= chunk:
        return [(0.0, max(duration, 0.0), 0.0, math.inf)]
    step = chunk - overlap
    starts: list[float] = []
    start = 0.0
    while True:
        starts.append(start)
        if start + chunk >= duration:
            break
        start += step
    windows = []
    for i, s in enumerate(starts):
        last = i == len(starts) - 1
        keep_from = 0.0 if i == 0 else s + overlap / 2
        keep_to = math.inf if last else starts[i + 1] + overlap / 2
        windows.append((s, min(chunk, duration - s), keep_from, keep_to))
    return windows


def window_segments(reply: dict[str, Any], with_words: bool) -> list[RawSegment]:
    """The reply's segments (window time), each with the words whose midpoint falls in it."""
    try:
        segments = sorted(
            (
                (float(s["start"]), float(s["end"]), str(s["text"]))
                for s in reply.get("segments") or []
            ),
            key=lambda s: s[0],
        )
        words = [
            (str(w["word"]), float(w["start"]), float(w["end"]))
            for w in (reply.get("words") or [] if with_words else [])
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ExternalSystemError(
            "Transcription reply has malformed segments or words",
            user_message="The transcription service returned an unreadable answer.",
        ) from exc
    if not segments:
        return []
    starts = [s[0] for s in segments]
    assigned: list[list[RawWord]] = [[] for _ in segments]
    for text, word_start, word_end in words:
        middle = (word_start + word_end) / 2
        i = max(0, bisect.bisect_right(starts, middle) - 1)
        if (
            i + 1 < len(segments)
            and middle >= segments[i][1]
            and segments[i + 1][0] - middle < middle - segments[i][1]
        ):
            i += 1
        assigned[i].append(RawWord(text, word_start, word_end))
    return [
        RawSegment(start, end, text, tuple(assigned[i]))
        for i, (start, end, text) in enumerate(segments)
    ]


def multipart(fields: list[tuple[str, str]], file_name: str, data: bytes, boundary: str) -> bytes:
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        "Content-Type: audio/flac\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    return b"".join(parts) + f"--{boundary}--\r\n".encode()


def retry_wait(retry_after: str | None, attempt: int) -> float | None:
    """Seconds before the next attempt, or ``None`` when the requested wait is too long."""
    try:
        wait = float(retry_after) if retry_after is not None else None
    except ValueError:
        wait = None  # an HTTP date: fall back to the backoff
    if wait is None:
        wait = _BACKOFF_SECONDS * 2 ** (attempt - 1)
    return max(0.0, wait) if wait <= _MAX_WAIT_SECONDS else None


# shortcut: common languages only; an unlisted name yields "und" unless a language is pinned.
_LANGUAGE_CODES = {
    "english": "en", "german": "de", "french": "fr", "spanish": "es", "italian": "it",
    "portuguese": "pt", "dutch": "nl", "polish": "pl", "turkish": "tr", "russian": "ru",
    "ukrainian": "uk", "czech": "cs", "swedish": "sv", "danish": "da", "norwegian": "no",
    "finnish": "fi", "greek": "el", "hungarian": "hu", "romanian": "ro", "japanese": "ja",
    "chinese": "zh", "korean": "ko", "arabic": "ar", "hindi": "hi", "hebrew": "he",
}  # fmt: skip


def _language_code(value: object) -> str | None:
    """The reply's language as ISO 639-1 (providers answer a code or Whisper's English name)."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if 2 <= len(text) <= 3 and text.isalpha():
        return text
    return _LANGUAGE_CODES.get(text)


def _shifted(segment: RawSegment, offset: float) -> RawSegment:
    return RawSegment(
        segment.start + offset,
        segment.end + offset,
        segment.text,
        tuple(
            RawWord(
                w.text,
                None if w.start is None else w.start + offset,
                None if w.end is None else w.end + offset,
                w.confidence,
            )
            for w in segment.words
        ),
    )


def _wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except (OSError, EOFError, wave.Error, ZeroDivisionError) as exc:
        raise UnreadableAudio(f"prepared audio cannot be read ({exc})") from exc
