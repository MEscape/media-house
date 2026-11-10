"""Hosted (Groq) transcription: windowing, stitching and provenance, with fake HTTP and FFmpeg."""

import itertools
import json
import math
import wave
from collections.abc import Callable
from pathlib import Path

import pytest

from media_house.core.application.ports import OutputLine, ProcessResult, ProcessSpec
from media_house.modules.audio_intelligence.domain.raw import ALIGNMENT_NONE, ALIGNMENT_SEGMENT
from media_house.modules.audio_intelligence.domain.values import TranscriptionConfig
from media_house.modules.audio_intelligence.infrastructure.groq_transcription import (
    ENGINE_GROQ,
    GroqTranscriptionEngine,
    HttpAnswer,
    RemoteTranscriptionSettings,
    multipart,
    plan_windows,
    retry_wait,
    window_segments,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ConfigurationError, ExternalSystemError

KEY = "gsk_secret"


class FakeFfmpeg:
    def __init__(self) -> None:
        self.windows: list[tuple[str, str]] = []

    def run(
        self,
        spec: ProcessSpec,
        *,
        cancellation: CancellationToken | None = None,
        on_output: Callable[[OutputLine], None] | None = None,
    ) -> ProcessResult:
        args = list(spec.args)
        self.windows.append((args[args.index("-ss") + 1], args[args.index("-t") + 1]))
        Path(args[-1]).write_bytes(b"fLaC fake")
        return ProcessResult(0, "", "", 0.0)


class FakeGroq:
    """Answers each window with the replies given, in order; records the form fields."""

    def __init__(self, *replies: dict[str, object] | HttpAnswer) -> None:
        self.replies = list(replies)
        self.requests: list[tuple[str, dict[str, str], bytes]] = []

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
        self.requests.append((path, headers, body))
        reply = self.replies.pop(0)
        if isinstance(reply, tuple):
            return reply
        return 200, None, json.dumps(reply).encode()


def silent_wav(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * int(seconds * 16_000))
    return path


def engine(post: FakeGroq, ffmpeg: FakeFfmpeg, key: str | None = KEY) -> GroqTranscriptionEngine:
    settings = RemoteTranscriptionSettings(chunk_seconds=30.0, overlap_seconds=6.0)
    return GroqTranscriptionEngine(ffmpeg, settings, {"groq_api_key": key}.get, post=post)


def seg(start: float, end: float, text: str) -> dict[str, object]:
    return {"start": start, "end": end, "text": text}


def test_windows_overlap_and_their_keep_ranges_tile_the_timeline() -> None:
    windows = plan_windows(70.0, 30.0, 6.0)

    assert [(w[0], w[1]) for w in windows] == [(0.0, 30.0), (24.0, 30.0), (48.0, 22.0)]
    assert windows[0][2] == 0.0 and windows[-1][3] == math.inf
    for before, after in itertools.pairwise(windows):
        assert before[3] == after[2]  # no gap, no double coverage
    assert plan_windows(12.0, 30.0, 6.0) == [(0.0, 12.0, 0.0, math.inf)]


def test_words_join_the_segment_that_holds_their_midpoint() -> None:
    reply = {
        "segments": [seg(2.0, 4.0, " world"), seg(0.0, 2.0, " Hello")],
        "words": [
            {"word": "Hello", "start": 0.1, "end": 0.9},
            {"word": "world", "start": 2.1, "end": 3.0},
            {"word": "again", "start": 4.1, "end": 4.3},  # past the last segment: nearest
        ],
    }

    segments = window_segments(reply, with_words=True)

    assert [s.text for s in segments] == [" Hello", " world"]
    assert [w.text for w in segments[0].words] == ["Hello"]
    assert [w.text for w in segments[1].words] == ["world", "again"]
    assert all(w.confidence is None for s in segments for w in s.words)
    assert window_segments(reply, with_words=False)[1].words == ()


def test_malformed_replies_are_typed_errors() -> None:
    with pytest.raises(ExternalSystemError):
        window_segments({"segments": [{"start": "x"}]}, with_words=True)


def test_a_long_recording_is_stitched_without_duplicates_and_records_its_provenance(
    tmp_path: Path,
) -> None:
    audio = silent_wav(tmp_path / "prepared.wav", 70.0)
    post = FakeGroq(
        # window 0 [0, 30): keeps midpoints < 27; the segment at 25-29 (mid 27) belongs to window 1
        {"language": "German", "segments": [seg(0, 10, "a"), seg(25, 29, "cut")], "words": []},
        # window 1 starts at 24: local 1-5 = 25-29 (mid 27, kept), local 26-29 = 50-53 (mid 51.5,
        # belongs to window 2)
        {"language": "de", "segments": [seg(1, 5, "b"), seg(26, 29, "late")], "words": []},
        # window 2 starts at 48
        {
            "language": "de",
            "segments": [seg(2, 5, "c")],
            "words": [{"word": "c", "start": 2.5, "end": 3.0}],
        },
    )
    ffmpeg = FakeFfmpeg()
    stages: list[str] = []

    raw = engine(post, ffmpeg).transcribe(
        audio, TranscriptionConfig(), CancellationToken(), stages.append
    )

    assert [(s.start, s.end, s.text) for s in raw.segments] == [
        (0.0, 10.0, "a"),
        (25.0, 29.0, "b"),
        (50.0, 53.0, "c"),
    ]
    assert raw.segments[2].words[0].start == 50.5
    assert ffmpeg.windows == [("0.000", "30.000"), ("24.000", "30.000"), ("48.000", "22.000")]
    assert len(stages) == 3
    # the language found first is pinned for the later windows
    assert b'name="language"' not in post.requests[0][2]
    assert b'name="language"\r\n\r\nde\r\n' in post.requests[1][2]
    assert post.requests[0][0] == "/openai/v1/audio/transcriptions"
    assert post.requests[0][1]["Authorization"] == f"Bearer {KEY}"
    assert raw.language == "de" and raw.language_detected
    assert raw.engine == ENGINE_GROQ and raw.model == "whisper-large-v3-turbo"
    assert raw.model_source == "https://api.groq.com/openai/v1"
    assert raw.alignment_method == ALIGNMENT_SEGMENT and raw.alignment_engine is None
    assert raw.device == "remote"
    assert "api.groq.com/whisper-large-v3-turbo" in raw.engine_version
    assert list(tmp_path.iterdir()) == [audio]  # encoded windows are cleaned up


def test_disabled_alignment_requests_no_words(tmp_path: Path) -> None:
    audio = silent_wav(tmp_path / "prepared.wav", 5.0)
    post = FakeGroq({"language": "en", "segments": [seg(0, 2, "hi")]})

    raw = engine(post, FakeFfmpeg()).transcribe(
        audio, TranscriptionConfig(language="en", align=False), CancellationToken(), print
    )

    assert raw.alignment_method == ALIGNMENT_NONE and not raw.language_detected
    assert b"word" not in post.requests[0][2].split(b"fLaC")[0]


def test_a_missing_key_fails_before_anything_is_encoded_or_sent(tmp_path: Path) -> None:
    audio = silent_wav(tmp_path / "prepared.wav", 5.0)
    post, ffmpeg = FakeGroq(), FakeFfmpeg()

    with pytest.raises(ConfigurationError) as raised:
        engine(post, ffmpeg, key=None).transcribe(
            audio, TranscriptionConfig(), CancellationToken(), print
        )

    assert "MEDIA_HOUSE_SECRET_GROQ_API_KEY" in raised.value.user_message
    assert post.requests == [] and ffmpeg.windows == []


def test_rate_limits_are_retried_and_a_rejected_key_is_explained(tmp_path: Path) -> None:
    audio = silent_wav(tmp_path / "prepared.wav", 5.0)
    post = FakeGroq((429, "0", b"{}"), {"language": "en", "segments": [seg(0, 1, "x")]})

    raw = engine(post, FakeFfmpeg()).transcribe(
        audio, TranscriptionConfig(), CancellationToken(), print
    )

    assert len(post.requests) == 2 and raw.segments[0].text == "x"
    with pytest.raises(ExternalSystemError) as raised:
        engine(FakeGroq((401, None, b"{}")), FakeFfmpeg()).transcribe(
            audio, TranscriptionConfig(), CancellationToken(), print
        )
    assert "API key" in raised.value.user_message


def test_retry_waits_are_bounded() -> None:
    assert retry_wait("3", 1) == 3.0
    assert retry_wait(None, 2) == 4.0
    assert retry_wait("Wed, 21 Oct 2026 07:28:00 GMT", 1) == 2.0
    assert retry_wait("7200", 1) is None


def test_identity_differs_from_whisperx_and_follows_model_and_windows() -> None:
    a = engine(FakeGroq(), FakeFfmpeg()).identity(TranscriptionConfig())
    other = GroqTranscriptionEngine(
        FakeFfmpeg(), RemoteTranscriptionSettings(model="whisper-large-v3"), lambda _name: None
    ).identity(TranscriptionConfig())

    assert a.name == ENGINE_GROQ
    assert a.version != other.version


def test_multipart_carries_every_field_and_the_file() -> None:
    body = multipart([("model", "m"), ("language", "de")], "w.flac", b"DATA", "BOUND")

    assert body.startswith(b"--BOUND\r\n") and body.endswith(b"--BOUND--\r\n")
    assert b'name="model"\r\n\r\nm\r\n' in body
    assert b'filename="w.flac"\r\nContent-Type: audio/flac\r\n\r\nDATA\r\n' in body


def test_settings_are_validated() -> None:
    with pytest.raises(ConfigurationError):
        RemoteTranscriptionSettings(chunk_seconds=10.0)
    with pytest.raises(ConfigurationError):
        RemoteTranscriptionSettings(chunk_seconds=60.0, overlap_seconds=30.0)
