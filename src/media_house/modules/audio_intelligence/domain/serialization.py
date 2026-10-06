"""Deterministic, versioned JSON interchange. Internal code uses ``Transcript``, not this.

Layout: ``{"document_type", "schema_version", "metadata", "segments", "words"}``. Words are
stored once, flat; segments reference them by ``first_word`` / ``word_count``. Times are rounded
to microseconds, far finer than any frame rate. Breaking changes bump ``SCHEMA_VERSION`` and add
a step to ``_MIGRATIONS``; readers refuse versions newer than they know.
"""

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from media_house.modules.audio_intelligence.domain.errors import InvalidTranscript
from media_house.modules.audio_intelligence.domain.transcript import (
    Transcript,
    TranscriptMetadata,
    TranscriptSegment,
    TranscriptWord,
    WordTiming,
)

DOCUMENT_TYPE = "transcript"
SCHEMA_VERSION = 1
TIME_DECIMALS = 6

#: ``{from_version: step}`` where a step upgrades a document dict by exactly one version.
_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _t(value: float) -> float:
    return round(value, TIME_DECIMALS)


def to_document(transcript: Transcript) -> dict[str, Any]:
    m = transcript.metadata
    return {
        "document_type": DOCUMENT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "source_asset_id": m.source_asset_id,
            "audio_asset_id": m.audio_asset_id,
            "duration": _t(m.duration),
            "language": m.language,
            "language_detected": m.language_detected,
            "transcription_engine": m.transcription_engine,
            "engine_version": m.engine_version,
            "model": m.model,
            "model_source": m.model_source,
            "alignment_engine": m.alignment_engine,
            "alignment_model": m.alignment_model,
            "alignment_method": m.alignment_method,
            "processing_version": m.processing_version,
            "created_at": m.created_at.isoformat(),
            "sample_rate": m.sample_rate,
            "channels": m.channels,
            "device": m.device,
            "compute_type": m.compute_type,
            "audio_offset": _t(m.audio_offset),
            "warnings": list(m.warnings),
        },
        "segments": [
            {
                "id": s.id,
                "start": _t(s.start),
                "end": _t(s.end),
                "text": s.text,
                "speaker": s.speaker,
                "first_word": s.words[0].index if s.words else None,
                "word_count": len(s.words),
            }
            for s in transcript.segments
        ],
        "words": [
            {
                "index": w.index,
                "segment_id": w.segment_id,
                "raw_word": w.raw_word,
                "normalized_word": w.normalized_word,
                "start": _t(w.start),
                "end": _t(w.end),
                "duration": _t(w.duration),
                "confidence": None if w.confidence is None else round(w.confidence, 4),
                "speaker": w.speaker,
                "timing": w.timing.value,
                "emphasis_hints": list(w.emphasis_hints),
            }
            for w in transcript.words
        ],
    }


def to_json(transcript: Transcript) -> str:
    return json.dumps(to_document(transcript), ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def from_json(text: str) -> Transcript:
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise InvalidTranscript(f"not valid JSON ({exc})") from exc
    return from_document(document)


def from_document(document: object) -> Transcript:
    if not isinstance(document, dict) or document.get("document_type") != DOCUMENT_TYPE:
        raise InvalidTranscript("not a transcript document")
    version = document.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise InvalidTranscript("missing schema_version")
    if version > SCHEMA_VERSION:
        raise InvalidTranscript(
            f"schema_version {version} is newer than supported {SCHEMA_VERSION}"
        )
    while version < SCHEMA_VERSION:
        document = _MIGRATIONS[version](document)
        version += 1
    try:
        return _parse(document)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTranscript(f"malformed content ({type(exc).__name__}: {exc})") from exc


def _parse(document: Mapping[str, Any]) -> Transcript:
    m = document["metadata"]
    metadata = TranscriptMetadata(
        source_asset_id=m["source_asset_id"],
        audio_asset_id=m["audio_asset_id"],
        duration=float(m["duration"]),
        language=m["language"],
        language_detected=bool(m["language_detected"]),
        transcription_engine=m["transcription_engine"],
        engine_version=m["engine_version"],
        model=m["model"],
        model_source=m["model_source"],
        alignment_engine=m["alignment_engine"],
        alignment_model=m["alignment_model"],
        alignment_method=m["alignment_method"],
        processing_version=int(m["processing_version"]),
        created_at=datetime.fromisoformat(m["created_at"]),
        sample_rate=int(m["sample_rate"]),
        channels=int(m["channels"]),
        device=m["device"],
        compute_type=m["compute_type"],
        audio_offset=float(m["audio_offset"]),
        warnings=tuple(m["warnings"]),
    )
    words = [
        TranscriptWord(
            index=int(w["index"]),
            segment_id=int(w["segment_id"]),
            raw_word=w["raw_word"],
            normalized_word=w["normalized_word"],
            start=float(w["start"]),
            end=float(w["end"]),
            confidence=None if w["confidence"] is None else float(w["confidence"]),
            speaker=w["speaker"],
            timing=WordTiming(w["timing"]),
            emphasis_hints=tuple(w["emphasis_hints"]),
        )
        for w in document["words"]
    ]
    segments = []
    for s in document["segments"]:
        first, count = s["first_word"], int(s["word_count"])
        members = tuple(words[first : first + count]) if count else ()
        if len(members) != count:
            raise ValueError(f"segment {s['id']} references missing words")
        segments.append(
            TranscriptSegment(
                id=int(s["id"]),
                start=float(s["start"]),
                end=float(s["end"]),
                text=s["text"],
                words=members,
                speaker=s["speaker"],
            ),
        )
    return Transcript(metadata, tuple(segments))
