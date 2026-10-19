"""Deterministic, versioned JSON for ``ContentAnalysis``.

Layout: ``{"document_type", "schema_version", "payload"}``. The payload mirrors the dataclasses
field by field (enums by value, tuples as lists, floats exactly). Reading is strict:
unknown fields, wrong types, unknown enum members and newer schema versions are refused rather
than guessed at. A breaking change bumps ``SCHEMA_VERSION`` and adds a step to ``_MIGRATIONS``.
"""

import json
import types
from collections.abc import Callable, Mapping
from dataclasses import MISSING, fields, is_dataclass
from datetime import datetime
from enum import Enum
from functools import cache
from typing import Any, Union, get_args, get_origin, get_type_hints

from media_house.modules.content_intelligence.domain.errors import InvalidAnalysisDocument
from media_house.modules.content_intelligence.domain.model import ContentAnalysis
from media_house.modules.content_intelligence.domain.values import DOCUMENT_TYPE, SCHEMA_VERSION
from media_house.shared.errors import InvariantViolation

#: ``{from_version: step}``; a step upgrades a payload by exactly one schema version.
_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _encode(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        return value  # JSON keeps a double exactly, so a stored result reads back equal
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_encode(v) for v in value]
    raise TypeError(f"cannot serialise {type(value).__name__}")


@cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def _decode(tp: Any, value: object, path: str) -> object:
    origin = get_origin(tp)
    if origin in {Union, types.UnionType}:
        options = get_args(tp)
        if value is None and type(None) in options:
            return None
        inner = [o for o in options if o is not type(None)]
        return _decode(inner[0], value, path)
    if origin is tuple:
        args = get_args(tp)
        if not isinstance(value, list):
            raise InvalidAnalysisDocument(f"{path}: expected a list")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v, f"{path}[{i}]") for i, v in enumerate(value))
        if len(args) != len(value):
            raise InvalidAnalysisDocument(f"{path}: expected {len(args)} items")
        return tuple(
            _decode(a, v, f"{path}[{i}]") for i, (a, v) in enumerate(zip(args, value, strict=True))
        )
    if origin in {Mapping, dict} or tp in {Mapping, dict}:
        args = get_args(tp)
        if not isinstance(value, dict):
            raise InvalidAnalysisDocument(f"{path}: expected an object")
        return {k: _decode(args[1], v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(value)
        except ValueError as exc:
            raise InvalidAnalysisDocument(f"{path}: unknown {tp.__name__} {value!r}") from exc
    if tp is datetime:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise InvalidAnalysisDocument(f"{path}: not a timestamp") from exc
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise InvalidAnalysisDocument(f"{path}: expected a number")
        return float(value)
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidAnalysisDocument(f"{path}: expected an integer")
        return value
    if tp is bool:
        if not isinstance(value, bool):
            raise InvalidAnalysisDocument(f"{path}: expected true or false")
        return value
    if tp is str:
        if not isinstance(value, str):
            raise InvalidAnalysisDocument(f"{path}: expected text")
        return value
    if isinstance(tp, type) and is_dataclass(tp):
        return _decode_dataclass(tp, value, path)
    raise InvalidAnalysisDocument(f"{path}: unsupported type {tp!r}")


def _decode_dataclass(cls: type, value: object, path: str) -> object:
    if not isinstance(value, dict):
        raise InvalidAnalysisDocument(f"{path}: expected an object")
    hints = _hints(cls)
    known = {f.name: f for f in fields(cls)}
    extra = set(value) - set(known)
    if extra:
        raise InvalidAnalysisDocument(f"{path}: unexpected field(s) {sorted(extra)}")
    kwargs: dict[str, object] = {}
    for name, field in known.items():
        if name not in value:
            if field.default is not MISSING or field.default_factory is not MISSING:
                continue
            raise InvalidAnalysisDocument(f"{path}: missing field {name!r}")
        kwargs[name] = _decode(hints[name], value[name], f"{path}.{name}")
    try:
        return cls(**kwargs)
    except (InvariantViolation, TypeError, ValueError) as exc:
        raise InvalidAnalysisDocument(f"{path}: {exc}") from exc


def analysis_to_json(analysis: ContentAnalysis) -> str:
    document = {
        "document_type": DOCUMENT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "payload": _encode(analysis),
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def analysis_from_json(text: str) -> ContentAnalysis:
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise InvalidAnalysisDocument("not JSON") from exc
    if not isinstance(document, dict) or document.get("document_type") != DOCUMENT_TYPE:
        raise InvalidAnalysisDocument("not a content analysis document")
    version = document.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise InvalidAnalysisDocument("missing schema version")
    if version > SCHEMA_VERSION:
        raise InvalidAnalysisDocument(f"schema {version} is newer than supported {SCHEMA_VERSION}")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise InvalidAnalysisDocument("missing payload")
    while version < SCHEMA_VERSION:
        step = _MIGRATIONS.get(version)
        if step is None:
            raise InvalidAnalysisDocument(f"no migration from schema {version}")
        payload, version = step(payload), version + 1
    result = _decode_dataclass(ContentAnalysis, payload, "analysis")
    if not isinstance(result, ContentAnalysis):
        raise InvalidAnalysisDocument("payload is not a content analysis")
    return result
