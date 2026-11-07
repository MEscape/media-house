"""Stored form of an export report: one JSON document, read strictly (shared codec)."""

import json

from media_house.core.domain import json_codec
from media_house.modules.resolve_export.domain.errors import InvalidExportDocument
from media_house.modules.resolve_export.domain.report import ExportReport
from media_house.modules.resolve_export.domain.values import DOCUMENT_TYPE, SCHEMA_VERSION


def report_to_json(report: ExportReport) -> str:
    return json_codec.dumps(DOCUMENT_TYPE, report)


def report_from_json(text: str) -> ExportReport:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidExportDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidExportDocument("not an export report")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidExportDocument(f"schema version {raw.get('schema_version')!r} is not readable")
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(ExportReport, body, "report")
    except (TypeError, ValueError) as exc:
        raise InvalidExportDocument(str(exc)) from exc
    if not isinstance(decoded, ExportReport):
        raise InvalidExportDocument("not an export report")
    return decoded
