"""JSON storage of the alignment document.

JSON is the storage form only; the typed model is the truth. One reflective codec handles every
frozen dataclass of this module, so a new field cannot be forgotten by a hand-written converter.
The reader is strict: an unknown schema, a missing or unexpected field, or a value of the wrong
type is an ``InvalidAlignmentDocument`` and the caller recomputes instead of guessing.
"""

import json
import math
import types
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from numbers import Integral, Real
from typing import Any, Union, get_args, get_origin, get_type_hints

from media_house.modules.audio_visual_alignment.domain.errors import InvalidAlignmentDocument
from media_house.modules.audio_visual_alignment.domain.model import AlignmentDocument
from media_house.modules.audio_visual_alignment.domain.values import (
    RESULT_DOCUMENT,
    SCHEMA_VERSION,
)
from media_house.shared.errors import InvariantViolation


def encode(value: object) -> Any:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            raise InvalidAlignmentDocument("a non-finite number cannot be stored")
        return number
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in sorted(value.items())}
    if isinstance(value, tuple | list):
        return [encode(v) for v in value]
    raise InvalidAlignmentDocument(f"cannot store a value of type {type(value).__name__}")


def decode(tp: Any, data: Any) -> Any:
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        options = [a for a in get_args(tp) if a is not type(None)]
        if data is None:
            if len(options) < len(get_args(tp)):
                return None
            raise InvalidAlignmentDocument("a required value is missing")
        return decode(options[0], data)
    if origin is tuple:
        if not isinstance(data, list):
            raise InvalidAlignmentDocument("expected a list")
        return tuple(decode(get_args(tp)[0], item) for item in data)
    if origin in (dict, Mapping) or (origin is not None and issubclass(origin, Mapping)):
        if not isinstance(data, dict):
            raise InvalidAlignmentDocument("expected an object")
        value_type = get_args(tp)[1]
        return {str(k): decode(value_type, v) for k, v in data.items()}
    if isinstance(tp, type):
        if issubclass(tp, Enum):
            try:
                return tp(data)
            except ValueError as exc:
                raise InvalidAlignmentDocument(f"unknown {tp.__name__} {data!r}") from exc
        if tp is bool:
            return _only(data, bool)
        if tp is int:
            return _only(data, int)
        if tp is float:
            return float(_only(data, int | float))
        if tp is str:
            return _only(data, str)
        if tp is datetime:
            try:
                return datetime.fromisoformat(_only(data, str))
            except ValueError as exc:
                raise InvalidAlignmentDocument("a timestamp is not valid") from exc
        if is_dataclass(tp):
            return _build(tp, data)
    raise InvalidAlignmentDocument(f"unsupported type {tp!r}")


def _only(data: Any, expected: Any) -> Any:
    if isinstance(data, bool) and expected is not bool:
        raise InvalidAlignmentDocument("expected a number, found a boolean")
    if not isinstance(data, expected):
        raise InvalidAlignmentDocument(f"expected {expected}, found {type(data).__name__}")
    return data


def _build(cls: type, data: Any) -> Any:
    if not isinstance(data, dict):
        raise InvalidAlignmentDocument(f"expected an object for {cls.__name__}")
    hints = get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    extra = set(data) - names
    if extra:
        raise InvalidAlignmentDocument(f"{cls.__name__} has unexpected fields {sorted(extra)}")
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name in data:
            kwargs[f.name] = decode(hints[f.name], data[f.name])
    try:
        return cls(**kwargs)
    except (TypeError, InvariantViolation) as exc:
        raise InvalidAlignmentDocument(f"{cls.__name__}: {exc}") from exc


def alignment_to_json(document: AlignmentDocument) -> str:
    return json.dumps(
        {
            "document_type": RESULT_DOCUMENT,
            "schema_version": SCHEMA_VERSION,
            "payload": encode(document),
        },
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def alignment_from_json(text: str) -> AlignmentDocument:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise InvalidAlignmentDocument("the document is not valid JSON") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != RESULT_DOCUMENT:
        raise InvalidAlignmentDocument(f"not a {RESULT_DOCUMENT} document")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidAlignmentDocument(f"unsupported schema version {raw.get('schema_version')!r}")
    result: AlignmentDocument = decode(AlignmentDocument, raw.get("payload"))
    return result
