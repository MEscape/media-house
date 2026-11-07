"""Stored form of a review session: one JSON document, read strictly (shared codec)."""

import json

from media_house.core.domain import json_codec
from media_house.modules.review_orchestrator.domain.errors import InvalidReviewDocument
from media_house.modules.review_orchestrator.domain.session import AuditEntry, ReviewSession
from media_house.modules.review_orchestrator.domain.values import SCHEMA_VERSION

DOCUMENT_TYPE = "review_session"


def session_to_json(session: ReviewSession) -> str:
    return json_codec.dumps(DOCUMENT_TYPE, session)


def session_from_json(text: str) -> ReviewSession:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidReviewDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidReviewDocument("not a review session")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidReviewDocument(f"schema version {raw.get('schema_version')!r} is not readable")
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(ReviewSession, body, "session")
    except (TypeError, ValueError) as exc:
        raise InvalidReviewDocument(str(exc)) from exc
    if not isinstance(decoded, ReviewSession):
        raise InvalidReviewDocument("not a review session")
    return decoded


def entry_to_json(entry: AuditEntry) -> str:
    return json.dumps(json_codec.encode(entry), sort_keys=True, ensure_ascii=False)


def entry_from_json(text: str) -> AuditEntry:
    try:
        decoded = json_codec.decode(AuditEntry, json.loads(text), "entry")
    except (TypeError, ValueError) as exc:
        raise InvalidReviewDocument(str(exc)) from exc
    if not isinstance(decoded, AuditEntry):
        raise InvalidReviewDocument("not an audit entry")
    return decoded
