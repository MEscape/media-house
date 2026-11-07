"""Stored form of a revision plan: one JSON document, read strictly (shared codec)."""

import json

from media_house.core.domain import json_codec
from media_house.modules.revision_engine.domain.errors import InvalidRevisionDocument
from media_house.modules.revision_engine.domain.model import RevisionPlan
from media_house.modules.revision_engine.domain.values import DOCUMENT_TYPE, SCHEMA_VERSION


def plan_to_json(plan: RevisionPlan) -> str:
    return json_codec.dumps(DOCUMENT_TYPE, plan)


def plan_from_json(text: str) -> RevisionPlan:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidRevisionDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidRevisionDocument("not a revision plan")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidRevisionDocument(
            f"schema version {raw.get('schema_version')!r} is not readable"
        )
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(RevisionPlan, body, "plan")
    except (TypeError, ValueError) as exc:
        raise InvalidRevisionDocument(str(exc)) from exc
    if not isinstance(decoded, RevisionPlan):
        raise InvalidRevisionDocument("not a revision plan")
    return decoded
