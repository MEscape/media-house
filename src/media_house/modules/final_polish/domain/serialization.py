"""Stored form of a final polish report: one JSON document, read strictly (shared codec)."""

import json

from media_house.core.domain import json_codec
from media_house.modules.final_polish.domain.errors import InvalidPolishDocument
from media_house.modules.final_polish.domain.model import FinalPolishReport
from media_house.modules.final_polish.domain.values import DOCUMENT_TYPE, SCHEMA_VERSION


def report_to_json(report: FinalPolishReport) -> str:
    return json_codec.dumps(DOCUMENT_TYPE, report)


def report_from_json(text: str) -> FinalPolishReport:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidPolishDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidPolishDocument("not a final polish report")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidPolishDocument(f"schema version {raw.get('schema_version')!r} is not readable")
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(FinalPolishReport, body, "report")
    except (TypeError, ValueError) as exc:
        raise InvalidPolishDocument(str(exc)) from exc
    if not isinstance(decoded, FinalPolishReport):
        raise InvalidPolishDocument("not a final polish report")
    return decoded
