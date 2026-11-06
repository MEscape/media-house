"""The execution report: what happened in Resolve, as a versioned document.

It never uses one generic success. Every operation says whether it was executed, whether it was
verified by reading Resolve back, and how (a native call, a project setting, a marker that tells a
person what to do). Open instructions, unsupported capabilities, unverifiable facts and unresolved
conflicts are listed, and the outcome is derived from them, not asserted.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from media_house.core.domain import json_codec
from media_house.modules.resolve_adapter.domain.binding import ConflictRecord
from media_house.modules.resolve_adapter.domain.capabilities import DetectedCapability
from media_house.modules.resolve_adapter.domain.errors import InvalidBindingDocument
from media_house.modules.resolve_adapter.domain.values import (
    ADAPTER_VERSION,
    REPORT_DOCUMENT_TYPE,
    SCHEMA_VERSION,
    Area,
    ExecutionPolicy,
    ExecutionState,
    OperationStatus,
    Outcome,
    Strategy,
    Verification,
)


@dataclass(frozen=True, slots=True)
class OperationRecord:
    op_id: str
    #: The timeline element (or ``timeline`` / an asset id) this operation carries out.
    element_id: str
    area: Area
    action: str
    status: OperationStatus
    strategy: Strategy
    verification: Verification = Verification.NOT_CHECKED
    detail: str = ""
    resolve_ref: str = ""


@dataclass(frozen=True, slots=True)
class SettingRecord:
    name: str
    expected: str
    actual: str
    applied: bool
    critical: bool


@dataclass(frozen=True, slots=True)
class MediaRecord:
    asset_id: str
    name: str
    pool_item_id: str
    path: str
    #: imported | reused | offline | missing | unavailable
    status: str
    folder: tuple[str, ...]
    #: linked | none | offline | unknown | not_requested
    proxy: str
    role: str = ""


@dataclass(frozen=True, slots=True)
class CheckRecord:
    area: Area
    name: str
    state: Verification
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AlternativeRecord:
    """An instruction Resolve's scripting cannot carry out, and what was done about it."""

    element_id: str
    operation: str
    strategy: Strategy
    detail: str
    alternatives: tuple[str, ...] = ()
    owner: str = ""


@dataclass(frozen=True, slots=True)
class RenderRecord:
    profile: str
    job_id: str
    status: str
    output_path: str
    output_asset_id: str | None
    percent: float | None
    seconds: float | None
    error: str
    checks: tuple[CheckRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class SyncRecord:
    #: first_build | in_sync | changes_found | conflicts_open
    status: str
    human_modified: tuple[str, ...] = ()
    human_deleted: tuple[str, ...] = ()
    human_created: tuple[str, ...] = ()
    conflicts: tuple[ConflictRecord, ...] = ()
    #: The changes handed to the timeline builder as a person's edits (element ids).
    recorded: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ConnectionRecord:
    state: str
    product: str
    version: str
    edition: str
    operating_system: str
    database: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    execution_id: str
    anchor_asset_id: str
    timeline_id: str
    build_version: int
    build_hash: str
    policy: ExecutionPolicy
    state: ExecutionState
    outcome: Outcome
    started_at: datetime
    finished_at: datetime
    config_hash: str
    connection: ConnectionRecord
    project_name: str
    project_resolve_id: str
    database: str
    timeline_name: str
    timeline_resolve_id: str
    capabilities: tuple[DetectedCapability, ...]
    media: tuple[MediaRecord, ...]
    operations: tuple[OperationRecord, ...]
    settings: tuple[SettingRecord, ...]
    verification: tuple[CheckRecord, ...]
    alternatives: tuple[AlternativeRecord, ...]
    render: RenderRecord | None
    sync: SyncRecord
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    log: tuple[str, ...]
    #: Whether this Resolve was reachable, and the timeline input this report is about.
    timeline_input_schema: int = 1
    adapter_version: int = ADAPTER_VERSION
    schema_version: int = SCHEMA_VERSION
    project_id: str | None = None

    # --- counts the pipeline and a person read first ---
    def count(self, status: OperationStatus) -> int:
        return sum(1 for o in self.operations if o.status is status)

    def verified(self) -> int:
        return sum(1 for o in self.operations if o.verification is Verification.VERIFIED)

    def failed_checks(self) -> tuple[CheckRecord, ...]:
        return tuple(c for c in self.verification if c.state is Verification.FAILED)

    def not_verifiable(self) -> tuple[CheckRecord, ...]:
        return tuple(c for c in self.verification if c.state is Verification.NOT_VERIFIABLE)


def derive_outcome(
    state: ExecutionState,
    operations: tuple[OperationRecord, ...],
    verification: tuple[CheckRecord, ...],
    conflicts: tuple[ConflictRecord, ...],
    warnings: tuple[str, ...],
) -> Outcome:
    """The pipeline's answer, derived from what happened (never a flat success)."""
    if state is ExecutionState.CANCELLED:
        return Outcome.CANCELLED
    if state is ExecutionState.INTERRUPTED:
        return Outcome.INTERRUPTED
    if state is ExecutionState.FAILED:
        return Outcome.FAILED
    open_conflicts = any(c.resolution.value == "open" for c in conflicts)
    incomplete = any(
        o.status in {OperationStatus.FAILED, OperationStatus.UNSUPPORTED, OperationStatus.PARTIAL}
        for o in operations
    )
    broken = any(c.state is Verification.FAILED for c in verification)
    if incomplete or broken or open_conflicts:
        return Outcome.PARTIAL
    unverified = any(
        c.state in {Verification.NOT_VERIFIABLE, Verification.PARTIALLY_VERIFIED}
        for c in verification
    )
    if warnings or unverified:
        return Outcome.COMPLETED_WITH_WARNINGS
    return Outcome.COMPLETED


def report_to_json(report: ExecutionReport) -> str:
    return json_codec.dumps(REPORT_DOCUMENT_TYPE, report)


def report_from_json(text: str) -> ExecutionReport:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidBindingDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != REPORT_DOCUMENT_TYPE:
        raise InvalidBindingDocument("not a Resolve execution report")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidBindingDocument(
            f"schema version {raw.get('schema_version')!r} is not readable"
        )
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(ExecutionReport, body, "report")
    except (TypeError, ValueError) as exc:
        raise InvalidBindingDocument(str(exc)) from exc
    if not isinstance(decoded, ExecutionReport):
        raise InvalidBindingDocument("not a Resolve execution report")
    return decoded
