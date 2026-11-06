"""Stored form of a built timeline: one JSON document, read strictly.

The strict reader is shared (``core.domain.json_codec``): an unknown field, a missing field, a
wrong type or an unknown enum member is refused (never reinterpreted), and so is a document from
another schema version. Exact times are stored as ``Rational`` (numerator and denominator), never
as floats. Vocabulary additions bump ``VOCABULARY_VERSION`` and are additive.
"""

import json

from media_house.core.domain import json_codec
from media_house.modules.timeline_builder.domain.errors import InvalidBuiltDocument
from media_house.modules.timeline_builder.domain.model import BuiltTimeline
from media_house.modules.timeline_builder.domain.values import DOCUMENT_TYPE, SCHEMA_VERSION


def timeline_to_json(timeline: BuiltTimeline) -> str:
    return json_codec.dumps(DOCUMENT_TYPE, timeline)


def timeline_from_json(text: str) -> BuiltTimeline:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidBuiltDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidBuiltDocument("not a built timeline document")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidBuiltDocument(f"schema version {raw.get('schema_version')!r} is not readable")
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(BuiltTimeline, body, "timeline")
    except (TypeError, ValueError) as exc:
        raise InvalidBuiltDocument(str(exc)) from exc
    if not isinstance(decoded, BuiltTimeline):
        raise InvalidBuiltDocument("not a built timeline document")
    return decoded
