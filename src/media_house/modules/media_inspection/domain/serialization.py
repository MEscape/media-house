"""The stored inspection document: versioned JSON with the layout

``document_type, schema_version, inspection_version, asset_id, created_at, depth`` then the
observed facts (``container, video_streams, audio_streams, other_streams, timecode,
production, integrity, raw_metadata``), then ``synchronization, findings, status``.

Encoding and decoding follow the dataclass definitions of ``model.py`` (field names are the
keys), so the document cannot drift from the model. Decoding is strict about types and runs
every value object's own validation; any problem is an ``InvalidInspectionDocument`` (the caller
then re-inspects instead of trusting damaged data). Unknown keys are ignored, so a newer writer
of the SAME schema version may add fields; a newer schema version is refused.
"""

import json
import math
import types
from collections.abc import Mapping
from dataclasses import MISSING, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar, Union, cast, get_args, get_origin, get_type_hints

from media_house.modules.media_inspection.domain.errors import InvalidInspectionDocument
from media_house.modules.media_inspection.domain.model import (
    InspectionStatus,
    MediaInspection,
    ObservedMedia,
    Synchronization,
)
from media_house.modules.media_inspection.domain.values import (
    DOCUMENT_TYPE,
    SCHEMA_VERSION,
    Depth,
    JsonValue,
    Rational,
)
from media_house.shared.errors import InvariantViolation

type _Bindings = Mapping[TypeVar, Any]
_FAILURES = (KeyError, TypeError, ValueError, AttributeError, InvariantViolation)


# ------------------------------------------------------------------------------------------
# encoding
# ------------------------------------------------------------------------------------------
def _encode(value: object) -> JsonValue:
    match value:
        case None | bool() | int() | str():
            return value
        case float():
            if not math.isfinite(value):
                raise InvalidInspectionDocument("a measurement is not a finite number")
            return value
        case Enum():
            return cast("str", value.value)
        case datetime():
            return value.isoformat()
        case Rational():
            return str(value)
        case tuple() | list():
            return [_encode(item) for item in value]
        case Mapping():
            return {str(key): _encode(item) for key, item in value.items()}
        case _ if is_dataclass(value) and not isinstance(value, type):
            return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    raise InvalidInspectionDocument(f"cannot store a {type(value).__name__}")


def to_json(inspection: MediaInspection) -> str:
    observed = _object(_encode(inspection.observed))
    document: dict[str, JsonValue] = {
        "document_type": DOCUMENT_TYPE,
        "schema_version": inspection.schema_version,
        "inspection_version": inspection.inspection_version,
        "asset_id": inspection.asset_id,
        "created_at": inspection.created_at.isoformat(),
        "depth": inspection.depth.value,
        **observed,
        "synchronization": _encode(inspection.synchronization),
        "findings": _encode(inspection.findings),
        "status": _encode(inspection.status),
    }
    return json.dumps(document, indent=1, ensure_ascii=False, allow_nan=False)


# ------------------------------------------------------------------------------------------
# decoding
# ------------------------------------------------------------------------------------------
def _decode(tp: Any, value: object, bindings: _Bindings) -> Any:
    if isinstance(tp, TypeVar):
        return _decode(bindings[tp], value, bindings)
    origin = get_origin(tp)
    args = get_args(tp)

    if origin in {Union, types.UnionType}:
        if value is None and type(None) in args:
            return None
        options = [a for a in args if a is not type(None)]
        if len(options) != 1:
            raise TypeError(f"ambiguous union {tp}")
        return _decode(options[0], value, bindings)
    if tp is JsonValue:
        return _json(value)
    if tp is bool:
        return _typed(value, bool)
    if tp is int:
        if isinstance(value, bool):
            raise TypeError("expected an integer")
        return _typed(value, int)
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError("expected a number")
        return float(value)
    if tp is str:
        return _typed(value, str)
    if tp is datetime:
        return datetime.fromisoformat(_typed(value, str))
    if tp is Rational:
        parsed = Rational.parse(value)
        if parsed is None:
            raise ValueError("not a rational")
        return parsed
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if origin is tuple:
        return tuple(_decode(args[0], item, bindings) for item in _list(value))
    if origin in {Mapping, dict}:
        return {
            _typed(key, str): _decode(args[1], item, bindings)
            for key, item in _object(value).items()
        }
    cls = origin or tp
    if isinstance(cls, type) and is_dataclass(cls):
        return _decode_dataclass(cls, args, _object(value), bindings)
    raise TypeError(f"unsupported type {tp}")


def _decode_dataclass(
    cls: type, args: tuple[Any, ...], data: dict[str, Any], outer: _Bindings
) -> Any:
    own = dict(
        zip(getattr(cls, "__type_params__", ()), (_resolve(a, outer) for a in args), strict=False)
    )
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name in data:
            kwargs[f.name] = _decode(hints[f.name], data[f.name], own)
        elif f.default is MISSING and f.default_factory is MISSING:
            raise KeyError(f.name)
    return cls(**kwargs)


def _resolve(arg: Any, bindings: _Bindings) -> Any:
    return bindings[arg] if isinstance(arg, TypeVar) else arg


def _typed[T](value: object, expected: type[T]) -> T:
    if not isinstance(value, expected):
        raise TypeError(f"expected {expected.__name__}, got {type(value).__name__}")
    return value


def _list(value: object) -> list[Any]:
    return _typed(value, list)


def _object(value: object) -> dict[str, Any]:
    return _typed(value, dict)


def _json(value: object) -> JsonValue:
    match value:
        case None | bool() | int() | float() | str():
            return value
        case list():
            return [_json(item) for item in value]
        case dict():
            return {_typed(k, str): _json(v) for k, v in value.items()}
    raise TypeError("not JSON data")


def from_json(text: str) -> MediaInspection:
    """The stored inspection; ``InvalidInspectionDocument`` if it is damaged or too new."""
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise InvalidInspectionDocument(f"not valid JSON ({exc})") from exc
    if not isinstance(document, dict) or document.get("document_type") != DOCUMENT_TYPE:
        raise InvalidInspectionDocument("not an inspection document")
    version = document.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version > SCHEMA_VERSION:
        raise InvalidInspectionDocument(f"unsupported schema version {version!r}")
    try:
        return MediaInspection(
            asset_id=_typed(document["asset_id"], str),
            created_at=_decode(datetime, document["created_at"], {}),
            depth=Depth(document["depth"]),
            observed=_decode(ObservedMedia, document, {}),
            synchronization=_decode(Synchronization, document["synchronization"], {}),
            findings=_decode(get_type_hints(MediaInspection)["findings"], document["findings"], {}),
            status=_decode(InspectionStatus, document["status"], {}),
            inspection_version=_decode(int, document["inspection_version"], {}),
            schema_version=version,
        )
    except _FAILURES as exc:
        raise InvalidInspectionDocument(f"{type(exc).__name__}: {exc}") from exc
