"""Strict JSON for trees of frozen dataclasses, and canonical digests of them.

Several modules store a document that is a tree of frozen dataclasses (the compiler's
specification, the builder's timeline, the Resolve adapter's binding and report). Each used to
carry its own copy of this codec; this is the one copy.

Reading is strict: an unknown field, a missing field, a wrong type or an unknown enum member is
refused (never reinterpreted). Exact times are ``Rational`` (numerator and denominator), never
floats. ``decode`` raises ``TypeError`` / ``ValueError``; the caller wraps them in its own
document error. The only union a model may use is ``X | None``.
"""

import dataclasses
import hashlib
import json
import types
import typing
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from fractions import Fraction
from typing import Any

_PRIMITIVES = (str, int, float, bool)


def encode(value: object) -> Any:
    """A JSON-safe form of a dataclass tree (enums by value, timestamps as ISO text)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [encode(v) for v in value]
    return value


def dumps(document_type: str, value: object) -> str:
    """The stored text of a document: ``document_type`` first-class, keys sorted, no NaN."""
    return json.dumps(
        {"document_type": document_type, **encode(value)},
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        indent=1,
    )


type _Shape = tuple[dict[str, Any], dict[str, dataclasses.Field[Any]]]


def decode(tp: Any, value: Any, where: str = "document") -> Any:
    """``value`` read as ``tp``, strictly."""
    return _decode(tp, value, where, {})


def _shape(tp: type[Any], shapes: dict[type[Any], _Shape]) -> _Shape:
    """A dataclass's field types and fields, resolved once per read (per instance is slow)."""
    found = shapes.get(tp)
    if found is None:
        found = shapes[tp] = (
            typing.get_type_hints(tp),
            {f.name: f for f in dataclasses.fields(tp)},
        )
    return found


def _decode(tp: Any, value: Any, where: str, shapes: dict[type[Any], _Shape]) -> Any:
    origin = typing.get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
        options = [a for a in typing.get_args(tp) if a is not type(None)]
        if value is None:
            if len(options) == len(typing.get_args(tp)):
                raise ValueError(f"{where}: null is not allowed")
            return None
        return _decode(options[0], value, where, shapes)
    if origin is tuple:
        args = typing.get_args(tp)
        if not isinstance(value, list):
            raise TypeError(f"{where}: expected a list")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v, f"{where}[{i}]", shapes) for i, v in enumerate(value))
        if len(args) != len(value):
            raise ValueError(f"{where}: expected {len(args)} items")
        return tuple(
            _decode(a, v, f"{where}[{i}]", shapes)
            for i, (a, v) in enumerate(zip(args, value, strict=True))
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
        hints, fields = _shape(typing.cast("type[Any]", tp), shapes)
        unknown = set(value) - set(fields)
        if unknown:
            raise ValueError(f"{where}: unknown fields {sorted(unknown)}")
        kwargs: dict[str, Any] = {}
        for name, field in fields.items():
            if name in value:
                kwargs[name] = _decode(hints[name], value[name], f"{where}.{name}", shapes)
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


# --- canonical digests ---------------------------------------------------------------------------


def canon(value: object) -> Any:
    """A JSON-safe, order-stable form of a value (dataclasses, enums, exact times)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: canon(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Fraction):
        return f"{value.numerator}/{value.denominator}"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): canon(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, tuple | list | set | frozenset):
        items = [canon(v) for v in value]
        return sorted(items, key=str) if isinstance(value, set | frozenset) else items
    return value


def digest(value: object) -> str:
    """sha256 of the canonical form: the same content always gives the same hash."""
    text = json.dumps(canon(value), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
