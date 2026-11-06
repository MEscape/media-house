"""Stored form of a timeline specification: one JSON document, read strictly.

The strict reader is shared (``core.domain.json_codec``): an unknown field, a missing field, a
wrong type or an unknown enum member is refused (never reinterpreted), and so is a document from
another schema version. Exact times are stored as ``Rational`` (numerator and denominator), never
as floats. Vocabulary additions bump ``VOCABULARY_VERSION`` and are additive.
"""

import json

from media_house.core.domain import json_codec
from media_house.modules.edit_plan_compiler.domain.errors import InvalidTimelineDocument
from media_house.modules.edit_plan_compiler.domain.model import TimelineSpecification
from media_house.modules.edit_plan_compiler.domain.values import DOCUMENT_TYPE, SCHEMA_VERSION


def spec_to_json(spec: TimelineSpecification) -> str:
    return json_codec.dumps(DOCUMENT_TYPE, spec)


def spec_from_json(text: str) -> TimelineSpecification:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidTimelineDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidTimelineDocument("not a timeline specification document")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidTimelineDocument(
            f"schema version {raw.get('schema_version')!r} is not readable"
        )
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(TimelineSpecification, body, "specification")
    except (TypeError, ValueError) as exc:
        raise InvalidTimelineDocument(str(exc)) from exc
    if not isinstance(decoded, TimelineSpecification):
        raise InvalidTimelineDocument("not a timeline specification document")
    return decoded
