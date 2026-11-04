"""Stored form of this module's documents: one JSON document each, read strictly.

Every document is a tree of frozen dataclasses, so one small codec walks the declared field
types instead of a hand-written reader per class (copied from reframing_intelligence). Reading
is strict: an unknown field, a missing field, a wrong type or an unknown enum member is refused
(never reinterpreted), and so is a document from another schema version. Exact times are stored
as ``Rational`` (numerator and denominator), never as floats.
"""

import dataclasses
import json
import types
import typing
from datetime import datetime
from enum import Enum
from typing import Any

from media_house.modules.music_and_sound_design.domain.analysis import MusicAnalysis, SfxProfile
from media_house.modules.music_and_sound_design.domain.errors import InvalidSoundDocument
from media_house.modules.music_and_sound_design.domain.model import SoundDesignPlan
from media_house.modules.music_and_sound_design.domain.values import (
    MUSIC_ANALYSIS,
    SCHEMA_VERSION,
    SFX_PROFILE,
    SOUND_DESIGN_PLAN,
)

_PRIMITIVES = (str, int, float, bool)


def plan_to_json(plan: SoundDesignPlan) -> str:
    return _write(plan, SOUND_DESIGN_PLAN)


def plan_from_json(text: str) -> SoundDesignPlan:
    return _read(SoundDesignPlan, text, SOUND_DESIGN_PLAN)


def music_analysis_to_json(analysis: MusicAnalysis) -> str:
    return _write(analysis, MUSIC_ANALYSIS)


def music_analysis_from_json(text: str) -> MusicAnalysis:
    return _read(MusicAnalysis, text, MUSIC_ANALYSIS)


def sfx_profile_to_json(profile: SfxProfile) -> str:
    return _write(profile, SFX_PROFILE)


def sfx_profile_from_json(text: str) -> SfxProfile:
    return _read(SfxProfile, text, SFX_PROFILE)


def canonical(value: object) -> str:
    """The compact canonical JSON of a dataclass tree (for hashes)."""
    return json.dumps(_encode(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write(value: object, document_type: str) -> str:
    document = {"document_type": document_type, **_encode(value)}
    return json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=1)


def _read[T](tp: type[T], text: str, document_type: str) -> T:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidSoundDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != document_type:
        raise InvalidSoundDocument(f"not a {document_type} document")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidSoundDocument(f"schema version {raw.get('schema_version')!r} is not readable")
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = _decode(tp, body, document_type, {})
    except (TypeError, ValueError) as exc:
        raise InvalidSoundDocument(str(exc)) from exc
    if not isinstance(decoded, tp):
        raise InvalidSoundDocument(f"not a {document_type} document")
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
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    return value


type _Shape = tuple[dict[str, Any], dict[str, dataclasses.Field[Any]]]


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
