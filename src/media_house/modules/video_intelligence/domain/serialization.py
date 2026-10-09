"""JSON storage of the result and of every analyzer's signals.

JSON is the storage form only; the typed models are the truth. One reflective codec handles every
frozen dataclass of this module, so a new field can never be forgotten by a hand-written
converter. Readers are strict: an unknown schema, a missing or unexpected field, or a value of
the wrong type is an ``InvalidAnalysisDocument`` and the caller recomputes instead of guessing.
"""

import json
import math
import types
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from numbers import Integral, Real
from typing import Any, Union, get_args, get_origin, get_type_hints

from media_house.modules.video_intelligence.domain.errors import InvalidAnalysisDocument
from media_house.modules.video_intelligence.domain.result import VideoAnalysis
from media_house.modules.video_intelligence.domain.signals import (
    AppearanceSignals,
    BodySignals,
    DescriptionSignals,
    DetectionSignals,
    EmbeddingSignals,
    FaceSignals,
    GeometrySignals,
    MotionSignals,
    QualitySignals,
    SaliencySignals,
    ShotSignals,
    TextSignals,
)
from media_house.modules.video_intelligence.domain.values import SCHEMA_VERSION, AnalyzerId
from media_house.shared.errors import InvariantViolation

#: Document type of the final result; each analyzer's signals use ``...signals.<analyzer id>``.
#: The Media Library accepts a JSON file as an asset only when it declares ``document_type``.
RESULT_DOCUMENT = "video_intelligence.analysis"

SIGNAL_TYPES: dict[AnalyzerId, type] = {
    AnalyzerId.SHOTS: ShotSignals,
    AnalyzerId.MOTION: MotionSignals,
    AnalyzerId.QUALITY: QualitySignals,
    AnalyzerId.SALIENCY: SaliencySignals,
    AnalyzerId.GEOMETRY: GeometrySignals,
    AnalyzerId.ENTITIES: DetectionSignals,
    AnalyzerId.FACES: FaceSignals,
    AnalyzerId.BODY: BodySignals,
    AnalyzerId.TEXT: TextSignals,
    AnalyzerId.EMBEDDINGS: EmbeddingSignals,
    AnalyzerId.APPEARANCE: AppearanceSignals,
    AnalyzerId.DESCRIPTIONS: DescriptionSignals,
}


def encode(value: object) -> Any:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, Integral):  # also NumPy integers coming from an engine
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            raise InvalidAnalysisDocument("a non-finite number cannot be stored")
        return number
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in sorted(value.items())}
    if isinstance(value, tuple | list):
        return [encode(v) for v in value]
    raise InvalidAnalysisDocument(f"cannot store a value of type {type(value).__name__}")


def decode(tp: Any, data: Any) -> Any:
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        options = [a for a in get_args(tp) if a is not type(None)]
        if data is None:
            if len(options) < len(get_args(tp)):
                return None
            raise InvalidAnalysisDocument("a required value is missing")
        return decode(options[0], data)
    if origin is tuple:
        if not isinstance(data, list):
            raise InvalidAnalysisDocument("expected a list")
        return tuple(decode(get_args(tp)[0], item) for item in data)
    if origin in (dict, Mapping) or (origin is not None and issubclass(origin, Mapping)):
        if not isinstance(data, dict):
            raise InvalidAnalysisDocument("expected an object")
        value_type = get_args(tp)[1]
        return {str(k): decode(value_type, v) for k, v in data.items()}
    if isinstance(tp, type):
        if issubclass(tp, Enum):
            try:
                return tp(data)
            except ValueError as exc:
                raise InvalidAnalysisDocument(f"unknown {tp.__name__} {data!r}") from exc
        if tp is bool:
            return _only(data, bool)
        if tp is int:
            return _only(data, int)
        if tp is float:
            return float(_only(data, int | float))
        if tp is str:
            return _only(data, str)
        if is_dataclass(tp):
            return _build(tp, data)
    raise InvalidAnalysisDocument(f"unsupported type {tp!r}")


def _only(data: Any, expected: Any) -> Any:
    if isinstance(data, bool) and expected is not bool:
        raise InvalidAnalysisDocument("expected a number, found a boolean")
    if not isinstance(data, expected):
        raise InvalidAnalysisDocument(f"expected {expected}, found {type(data).__name__}")
    return data


def _build(cls: type, data: Any) -> Any:
    if not isinstance(data, dict):
        raise InvalidAnalysisDocument(f"expected an object for {cls.__name__}")
    hints = get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    extra = set(data) - names
    if extra:
        raise InvalidAnalysisDocument(f"{cls.__name__} has unexpected fields {sorted(extra)}")
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            continue  # a default applies; otherwise the constructor reports it
        kwargs[f.name] = decode(hints[f.name], data[f.name])
    try:
        return cls(**kwargs)
    except (TypeError, InvariantViolation) as exc:
        raise InvalidAnalysisDocument(f"{cls.__name__}: {exc}") from exc


# --- documents ------------------------------------------------------------------------------------
def document_to_json(kind: str, value: object) -> str:
    return json.dumps(
        {"document_type": kind, "schema_version": SCHEMA_VERSION, "payload": encode(value)},
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def document_from_json[T](kind: str, tp: type[T], text: str) -> T:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise InvalidAnalysisDocument("the document is not valid JSON") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != kind:
        raise InvalidAnalysisDocument(f"not a {kind} document")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidAnalysisDocument(f"unsupported schema version {raw.get('schema_version')!r}")
    result: T = decode(tp, raw.get("payload"))
    return result


def analysis_to_json(analysis: VideoAnalysis) -> str:
    return document_to_json(RESULT_DOCUMENT, analysis)


def analysis_from_json(text: str) -> VideoAnalysis:
    return document_from_json(RESULT_DOCUMENT, VideoAnalysis, text)


def signals_kind(analyzer: AnalyzerId) -> str:
    return f"video_intelligence.signals.{analyzer.value}"
