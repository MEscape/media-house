"""Versioned JSON for acoustic measurements and the Audio Intelligence Timeline.

JSON is persistence/interchange only; internally everything is typed. Both documents carry
``document_type`` and ``schema_version`` (an integer, bumped for breaking changes; readers
refuse newer versions). Dataclasses are encoded generically from their fields and decoded from
their type hints, so adding a field with a default is backward compatible.

Layout of the timeline document (frames stored once, as columns; words reference nothing
redundant): ``metadata``, ``transcript`` (the transcript document), ``acoustic``
(``origin``, ``hop``, ``frames`` columns, ``baseline``), ``events``, ``pauses``, ``words``
(per-index acoustics and signals), ``segments`` and ``editing_signals``.
"""

import dataclasses
import json
import math
import types
from array import array
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints

from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    AcousticMeasurements,
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.baseline import Baseline
from media_house.modules.audio_intelligence.domain.analysis.features import (
    SegmentAcoustics,
    WordAcoustics,
)
from media_house.modules.audio_intelligence.domain.analysis.pauses import Pause
from media_house.modules.audio_intelligence.domain.analysis.scoring import (
    EditingSignal,
    WordSignals,
)
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    SCHEMA_VERSION,
    AnalysisMetadata,
    AudioIntelligenceTimeline,
    TimelineSegment,
    TimelineWord,
)
from media_house.modules.audio_intelligence.domain.errors import InvalidTranscript
from media_house.modules.audio_intelligence.domain.serialization import (
    from_document as transcript_from_document,
)
from media_house.modules.audio_intelligence.domain.serialization import (
    to_document as transcript_to_document,
)

TIMELINE_DOCUMENT = "audio_intelligence"
MEASUREMENTS_DOCUMENT = "acoustic_measurements"
VALUE_DECIMALS = 6
#: Decimal places kept per frame column (also applied by extractors, so fresh == reloaded).
FRAME_PRECISION = {"f0": 3, "pitch_confidence": 3, "rms_db": 2, "loudness": 2, "speech": 0}


# --- generic dataclass codec ---------------------------------------------------------------------
def encode(value: object) -> Any:
    if value is None or isinstance(value, bool | str | int):
        return value
    if isinstance(value, float):
        return round(value, VALUE_DECIMALS) if math.isfinite(value) else None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [encode(v) for v in value]
    raise TypeError(f"cannot encode {type(value).__name__}")


def decode(tp: Any, value: Any) -> Any:
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        options = [a for a in get_args(tp) if a is not type(None)]
        return None if value is None else decode(options[0], value)
    if value is None:
        raise ValueError("unexpected null")
    if tp is Any or tp is object:
        return value
    if origin is tuple:
        return tuple(decode(get_args(tp)[0], v) for v in value)
    if origin in (dict, Mapping) or (origin is not None and issubclass(origin, Mapping)):
        item = get_args(tp)[1]
        return {k: decode(item, v) for k, v in value.items()}
    if tp is float:
        return float(value)
    if tp in (int, str, bool):
        return tp(value)
    if tp is datetime:
        return datetime.fromisoformat(value)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if dataclasses.is_dataclass(tp):
        hints = get_type_hints(tp)
        kwargs = {
            f.name: decode(hints[f.name], value[f.name])
            for f in dataclasses.fields(tp)
            if f.name in value
        }
        factory: Any = tp
        return factory(**kwargs)
    raise TypeError(f"cannot decode {tp}")


# --- frame columns -------------------------------------------------------------------------------
def _frames_to_json(track: AcousticTrack) -> dict[str, list[float | int | None]]:
    out: dict[str, list[float | int | None]] = {}
    for name, column in track.columns().items():
        digits = FRAME_PRECISION[name]
        out[name] = [
            None if not math.isfinite(v) else (int(v) if digits == 0 else round(v, digits))
            for v in column
        ]
    return out


def _track_from_json(origin: float, hop: float, frames: Mapping[str, list[Any]]) -> AcousticTrack:
    def column(name: str) -> array[float]:
        return array("d", [math.nan if v is None else float(v) for v in frames[name]])

    return AcousticTrack(
        origin,
        hop,
        column("f0"),
        column("pitch_confidence"),
        column("rms_db"),
        column("loudness"),
        column("speech"),
    )


def _check(document: object, expected: str) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("document_type") != expected:
        raise InvalidTranscript(f"not a {expected} document")
    version = document.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise InvalidTranscript("missing schema_version")
    if version > SCHEMA_VERSION:
        raise InvalidTranscript(
            f"schema_version {version} is newer than supported {SCHEMA_VERSION}"
        )
    return document


# --- measurements --------------------------------------------------------------------------------
def measurements_to_json(measurements: AcousticMeasurements) -> str:
    document = {
        "document_type": MEASUREMENTS_DOCUMENT,
        "schema_version": SCHEMA_VERSION,
        "identity": dict(measurements.identity),
        "parameters": encode(measurements.parameters),
        "warnings": list(measurements.warnings),
        "events": encode(measurements.events),
        "acoustic": {
            "origin": measurements.track.origin,
            "hop": measurements.track.hop,
            "frames": _frames_to_json(measurements.track),
        },
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def measurements_from_json(text: str) -> AcousticMeasurements:
    try:
        document = _check(json.loads(text), MEASUREMENTS_DOCUMENT)
        acoustic = document["acoustic"]
        return AcousticMeasurements(
            track=_track_from_json(
                float(acoustic["origin"]),
                float(acoustic["hop"]),
                acoustic["frames"],
            ),
            identity=dict(document["identity"]),
            parameters={k: float(v) for k, v in document["parameters"].items()},
            events=tuple(decode(AudioEvent, e) for e in document["events"]),
            warnings=tuple(document["warnings"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTranscript(f"malformed measurements ({type(exc).__name__}: {exc})") from exc


# --- timeline ------------------------------------------------------------------------------------
def timeline_to_document(timeline: AudioIntelligenceTimeline) -> dict[str, Any]:
    return {
        "document_type": TIMELINE_DOCUMENT,
        "schema_version": SCHEMA_VERSION,
        "metadata": encode(timeline.metadata),
        "transcript": transcript_to_document(timeline.transcript),
        "acoustic": {
            "origin": timeline.track.origin,
            "hop": timeline.track.hop,
            "frames": _frames_to_json(timeline.track),
            "baseline": encode(timeline.baseline),
        },
        "events": encode(timeline.events),
        "pauses": encode(timeline.pauses),
        "words": [
            {"index": w.index, "acoustics": encode(w.acoustics), "signals": encode(w.signals)}
            for w in timeline.words
        ],
        "segments": [{"id": s.id, "acoustics": encode(s.acoustics)} for s in timeline.segments],
        "editing_signals": encode(timeline.editing_signals),
    }


def timeline_to_json(timeline: AudioIntelligenceTimeline) -> str:
    return (
        json.dumps(
            timeline_to_document(timeline),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def timeline_from_json(text: str) -> AudioIntelligenceTimeline:
    try:
        document = _check(json.loads(text), TIMELINE_DOCUMENT)
        transcript = transcript_from_document(document["transcript"])
        acoustic = document["acoustic"]
        words = tuple(
            TimelineWord(
                transcript.words[int(entry["index"])],
                decode(WordAcoustics | None, entry["acoustics"]),
                decode(WordSignals, entry["signals"]),
            )
            for entry in document["words"]
        )
        segments = tuple(
            TimelineSegment(
                transcript.segments[int(entry["id"])],
                decode(SegmentAcoustics, entry["acoustics"]),
            )
            for entry in document["segments"]
        )
        return AudioIntelligenceTimeline(
            metadata=decode(AnalysisMetadata, document["metadata"]),
            transcript=transcript,
            track=_track_from_json(
                float(acoustic["origin"]), float(acoustic["hop"]), acoustic["frames"]
            ),
            baseline=decode(Baseline, acoustic["baseline"]),
            events=tuple(decode(AudioEvent, e) for e in document["events"]),
            pauses=tuple(decode(Pause, p) for p in document["pauses"]),
            words=words,
            segments=segments,
            editing_signals=tuple(decode(EditingSignal, s) for s in document["editing_signals"]),
        )
    except InvalidTranscript:
        raise
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise InvalidTranscript(f"malformed timeline ({type(exc).__name__}: {exc})") from exc
