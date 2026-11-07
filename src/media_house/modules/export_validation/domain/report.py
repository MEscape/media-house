"""The export validation report: one stored document per validation, read strictly. It is never
edited; validating again makes a new report."""

import json
from dataclasses import dataclass, field
from datetime import datetime

from media_house.core.domain import json_codec
from media_house.modules.export_validation.domain.values import (
    SCHEMA_VERSION,
    VALIDATOR_VERSION,
    Category,
    Certainty,
    CheckState,
    NextAction,
    Route,
    Severity,
    Status,
)
from media_house.shared.errors import DomainError

DOCUMENT_TYPE = "export_validation_report"


class InvalidValidationDocument(DomainError):
    code = "export_validation.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Stored validation report is unreadable: {detail}",
            user_message="A stored export validation report could not be read.",
        )


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    category: Category
    severity: Severity
    certainty: Certainty
    message: str
    #: Who owns the fix, and what to do next (never "re-edit" for a render setting or the reverse).
    route_to: Route
    next_action: NextAction
    recommended_action: str
    evidence: tuple[tuple[str, str], ...] = ()
    start_seconds: float | None = None
    end_seconds: float | None = None
    #: The automated check cannot settle it; only a person's look can.
    needs_review: bool = False


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    category: Category
    name: str
    state: CheckState
    detail: str = ""


@dataclass(frozen=True, slots=True)
class NotChecked:
    """Something the validation should have checked and could not."""

    category: Category
    name: str
    reason: str
    #: A mandatory check that did not run keeps the verdict from being a PASS.
    mandatory: bool = True


@dataclass(frozen=True, slots=True)
class AreaStatus:
    category: Category
    status: CheckState


@dataclass(frozen=True, slots=True)
class ApprovedReference:
    timeline_id: str
    timeline_version: int
    content_hash: str
    build_asset_id: str
    review_session_id: str


@dataclass(frozen=True, slots=True)
class ExportValidationReport:
    export_validation_id: str
    export_id: str
    render_id: str
    anchor_asset_id: str
    project_id: str | None
    timeline_id: str
    timeline_version: int
    approved_version_reference: ApprovedReference
    #: The output asset and its file name (never an absolute path).
    output_asset_id: str
    file_path: str
    file_checksum: str
    delivery_profile_id: str
    delivery_profile_version: int
    validation_status: Status
    #: The worst severity among the findings (``info`` when there are none).
    severity: Severity
    recommended_next_action: NextAction
    created_at: datetime
    analyzer_identity: str
    #: Per area: passed / warning / failed / not checked.
    area_status: tuple[AreaStatus, ...]
    checks: tuple[ValidationCheck, ...]
    detected_problems: tuple[Finding, ...]
    not_checked: tuple[NotChecked, ...]
    measurements: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...] = ()
    cache_key: str = ""
    review_session_id: str = ""
    module: str = "export_validation"
    module_version: int = VALIDATOR_VERSION
    inputs: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    content_hash: str = ""
    schema_version: int = SCHEMA_VERSION

    def area(self, category: Category) -> CheckState:
        found = next((a.status for a in self.area_status if a.category is category), None)
        return found if found is not None else CheckState.NOT_CHECKED


def report_to_json(report: ExportValidationReport) -> str:
    return json_codec.dumps(DOCUMENT_TYPE, report)


def report_from_json(text: str) -> ExportValidationReport:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidValidationDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidValidationDocument("not an export validation report")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidValidationDocument(
            f"schema version {raw.get('schema_version')!r} is not readable"
        )
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(ExportValidationReport, body, "report")
    except (TypeError, ValueError) as exc:
        raise InvalidValidationDocument(str(exc)) from exc
    if not isinstance(decoded, ExportValidationReport):
        raise InvalidValidationDocument("not an export validation report")
    return decoded
