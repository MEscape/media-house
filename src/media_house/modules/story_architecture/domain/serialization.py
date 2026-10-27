"""Stored form of a plan: one JSON document, read strictly.

The plan is a tree of frozen dataclasses, so one small codec walks the declared field types instead
of a hand-written reader per class. Reading is strict: an unknown field, a missing field, a wrong
type or an unknown enum member is refused (never reinterpreted), and so is a document from another
schema version. Vocabulary additions bump ``VOCABULARY_VERSION`` and are additive.
"""

import dataclasses
import json
import types
import typing
from datetime import datetime
from enum import Enum
from typing import Any

from media_house.modules.story_architecture.domain.errors import InvalidStoryDocument
from media_house.modules.story_architecture.domain.model import StoryPlan
from media_house.modules.story_architecture.domain.values import (
    DOCUMENT_TYPE,
    SCHEMA_VERSION,
)

_PRIMITIVES = (str, int, float, bool)


def plan_to_json(plan: StoryPlan) -> str:
    document = {"document_type": DOCUMENT_TYPE, **_encode(plan)}
    return json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=1)


def plan_from_json(text: str) -> StoryPlan:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidStoryDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidStoryDocument("not a story plan document")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidStoryDocument(f"schema version {raw.get('schema_version')!r} is not readable")
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = _decode(StoryPlan, body, "plan")
    except (TypeError, ValueError) as exc:
        raise InvalidStoryDocument(str(exc)) from exc
    if not isinstance(decoded, StoryPlan):
        raise InvalidStoryDocument("not a story plan document")
    return decoded


def _encode(value: object) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_encode(v) for v in value]
    return value


def _decode(tp: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
        options = [a for a in typing.get_args(tp) if a is not type(None)]
        if value is None:
            if len(options) == len(typing.get_args(tp)):
                raise ValueError(f"{where}: null is not allowed")
            return None
        return _decode(options[0], value, where)
    if origin is tuple:
        args = typing.get_args(tp)
        if not isinstance(value, list):
            raise TypeError(f"{where}: expected a list")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v, f"{where}[{i}]") for i, v in enumerate(value))
        if len(args) != len(value):
            raise ValueError(f"{where}: expected {len(args)} items")
        return tuple(
            _decode(a, v, f"{where}[{i}]") for i, (a, v) in enumerate(zip(args, value, strict=True))
        )
    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(value)
        except ValueError as exc:
            raise ValueError(f"{where}: unknown {tp.__name__} {value!r}") from exc
    if tp is datetime:
        if not isinstance(value, str):
            raise TypeError(f"{where}: expected a timestamp")
        return datetime.fromisoformat(value)
    if dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            raise TypeError(f"{where}: expected an object")
        hints = typing.get_type_hints(tp)
        fields = {f.name: f for f in dataclasses.fields(tp)}
        unknown = set(value) - set(fields)
        if unknown:
            raise ValueError(f"{where}: unknown fields {sorted(unknown)}")
        kwargs: dict[str, Any] = {}
        for name, field in fields.items():
            if name in value:
                kwargs[name] = _decode(hints[name], value[name], f"{where}.{name}")
            elif (
                field.default is dataclasses.MISSING
                and field.default_factory is dataclasses.MISSING
            ):
                raise ValueError(f"{where}: missing field {name}")
        return typing.cast("type[Any]", tp)(**kwargs)
    if tp is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if tp in _PRIMITIVES:
        if not isinstance(value, tp) or (tp is not bool and isinstance(value, bool)):
            raise TypeError(f"{where}: expected {tp.__name__}")
        return value
    raise TypeError(f"{where}: unsupported type {tp!r}")
