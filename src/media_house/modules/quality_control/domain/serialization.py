"""Stored form of a quality report: one JSON document, read strictly (shared codec)."""

import json

from media_house.core.domain import json_codec
from media_house.modules.quality_control.domain.errors import InvalidQualityDocument
from media_house.modules.quality_control.domain.model import QualityReport
from media_house.modules.quality_control.domain.values import DOCUMENT_TYPE, SCHEMA_VERSION


def report_to_json(report: QualityReport) -> str:
    return json_codec.dumps(DOCUMENT_TYPE, report)


def report_from_json(text: str) -> QualityReport:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidQualityDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidQualityDocument("not a quality report")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidQualityDocument(
            f"schema version {raw.get('schema_version')!r} is not readable"
        )
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(QualityReport, body, "report")
    except (TypeError, ValueError) as exc:
        raise InvalidQualityDocument(str(exc)) from exc
    if not isinstance(decoded, QualityReport):
        raise InvalidQualityDocument("not a quality report")
    return decoded
