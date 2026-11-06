"""Turn a run's ledger into its report: the state it ended in and the outcome derived from it."""

from collections.abc import Sequence
from datetime import datetime

from media_house.modules.resolve_adapter.application.ledger import Ledger
from media_house.modules.resolve_adapter.domain.capabilities import DetectedCapability
from media_house.modules.resolve_adapter.domain.environment import ConnectionStatus
from media_house.modules.resolve_adapter.domain.report import (
    ConnectionRecord,
    ExecutionReport,
    MediaRecord,
    RenderRecord,
    SyncRecord,
    derive_outcome,
)
from media_house.modules.resolve_adapter.domain.values import (
    ADAPTER_VERSION,
    ExecutionPolicy,
    ExecutionState,
    Outcome,
)


def connection_record(status: ConnectionStatus) -> ConnectionRecord:
    env = status.environment
    return ConnectionRecord(
        state=status.state.value,
        product=env.product if env else "",
        version=env.version.text if env and env.version else "",
        edition=env.edition if env else "",
        operating_system=env.operating_system if env else "",
        database=(env.database or "") if env else "",
        detail=status.detail,
    )


def final_state(ledger: Ledger, sync: SyncRecord) -> tuple[ExecutionState, Outcome]:
    """The terminal state and the outcome. An error recorded on the way is never a success."""
    state = ledger.state
    operations = tuple(ledger.operations)
    verification = tuple(ledger.checks)
    warnings = tuple(ledger.warnings)
    if state.terminal:
        return state, derive_outcome(state, operations, verification, sync.conflicts, warnings)
    outcome = derive_outcome(
        ExecutionState.COMPLETED, operations, verification, sync.conflicts, warnings
    )
    if ledger.errors and outcome in {Outcome.COMPLETED, Outcome.COMPLETED_WITH_WARNINGS}:
        outcome = Outcome.PARTIAL
    if outcome is Outcome.PARTIAL:
        return ExecutionState.PARTIALLY_COMPLETED, outcome
    return ExecutionState.COMPLETED, outcome


def make_report(
    *,
    ledger: Ledger,
    status: ConnectionStatus,
    execution_id: str,
    started: datetime,
    finished: datetime,
    anchor_asset_id: str,
    timeline_id: str,
    project_id: str | None,
    build_version: int,
    build_hash: str,
    schema: int,
    policy: ExecutionPolicy,
    config_hash: str,
    project_name: str,
    project_resolve_id: str,
    database: str,
    timeline_name: str,
    timeline_resolve_id: str,
    capabilities: Sequence[DetectedCapability],
    media: Sequence[MediaRecord],
    sync: SyncRecord,
    render: RenderRecord | None = None,
) -> ExecutionReport:
    state, outcome = final_state(ledger, sync)
    return ExecutionReport(
        execution_id=execution_id,
        anchor_asset_id=anchor_asset_id,
        timeline_id=timeline_id,
        build_version=build_version,
        build_hash=build_hash,
        policy=policy,
        state=state,
        outcome=outcome,
        started_at=started,
        finished_at=finished,
        config_hash=config_hash,
        connection=connection_record(status),
        project_name=project_name,
        project_resolve_id=project_resolve_id,
        database=database,
        timeline_name=timeline_name,
        timeline_resolve_id=timeline_resolve_id,
        capabilities=tuple(capabilities),
        media=tuple(media),
        operations=tuple(ledger.operations),
        settings=tuple(ledger.settings),
        verification=tuple(ledger.checks),
        alternatives=tuple(ledger.alternatives),
        render=render,
        sync=sync,
        warnings=tuple(ledger.warnings),
        errors=tuple(ledger.errors),
        log=tuple(ledger.log),
        timeline_input_schema=schema,
        adapter_version=ADAPTER_VERSION,
        project_id=project_id,
    )
