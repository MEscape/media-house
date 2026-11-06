"""The stored forms: the link to the Resolve project and the execution report. Exact, strict,
versioned (the same rules as every document in the library)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from media_house.modules.resolve_adapter.application.execution import Execution
from media_house.modules.resolve_adapter.application.reporting import make_report
from media_house.modules.resolve_adapter.domain.binding import (
    SyncState,
    state_from_json,
    state_to_json,
)
from media_house.modules.resolve_adapter.domain.environment import ConnectionStatus
from media_house.modules.resolve_adapter.domain.errors import InvalidBindingDocument
from media_house.modules.resolve_adapter.domain.report import (
    CheckRecord,
    ExecutionReport,
    SyncRecord,
    derive_outcome,
    report_from_json,
    report_to_json,
)
from media_house.modules.resolve_adapter.domain.values import (
    Area,
    ConnectionState,
    ExecutionPolicy,
    ExecutionState,
    OperationStatus,
    Outcome,
    Strategy,
    Verification,
)
from tests.unit.resolve_adapter import fixtures as f

T0 = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)


def done(tmp_path: Path) -> tuple[Execution, SyncState]:
    ex, _ = f.execution(tmp_path)
    return ex, f.run(ex)


def report_of(ex: Execution) -> ExecutionReport:
    assert ex.env is not None and ex.matrix is not None
    return make_report(
        ledger=ex.ledger,
        status=ConnectionStatus(ConnectionState.CONNECTED, environment=ex.env),
        execution_id="exec-1",
        started=T0,
        finished=T0,
        anchor_asset_id=ex.facts.anchor_asset_id,
        timeline_id=ex.facts.timeline_id,
        project_id=None,
        build_version=ex.facts.build_version,
        build_hash=ex.facts.build_hash,
        schema=ex.facts.schema_version,
        policy=ExecutionPolicy.REQUIRE_CONFLICT_REVIEW,
        config_hash="c",
        project_name=ex.project_name,
        project_resolve_id=ex.project_resolve_id,
        database=ex.database,
        timeline_name=ex.timeline_name,
        timeline_resolve_id=ex.timeline_resolve_id,
        capabilities=ex.matrix.items,
        media=list(ex.media_records.values()),
        sync=ex.sync_record(),
    )


class TestBinding:
    def test_it_reads_back_equal(self, tmp_path: Path) -> None:
        _, state = done(tmp_path)

        assert state_from_json(state_to_json(state)) == state

    def test_the_text_is_deterministic(self, tmp_path: Path) -> None:
        _, state = done(tmp_path)

        assert state_to_json(state) == state_to_json(state)

    def test_resolves_ids_are_external_references_next_to_the_stable_ones(
        self, tmp_path: Path
    ) -> None:
        _, state = done(tmp_path)

        body = json.loads(state_to_json(state))
        assert body["project"]["timeline_id"] and body["project"]["project_name"] == "Test edit"
        pb = next(e for e in body["elements"] if e["element_id"] == "v:pB")
        assert pb["item_id"].startswith("item-") and ["start", "100"] in pb["fields"]

    def doc(self, tmp_path: Path) -> dict[str, Any]:
        _, state = done(tmp_path)
        body: dict[str, Any] = json.loads(state_to_json(state))
        return body

    def test_not_json_is_refused(self) -> None:
        with pytest.raises(InvalidBindingDocument, match="JSON"):
            state_from_json("{")

    def test_another_document_type_is_refused(self, tmp_path: Path) -> None:
        body = self.doc(tmp_path)
        body["document_type"] = "built_timeline"

        with pytest.raises(InvalidBindingDocument, match="not a Resolve binding"):
            state_from_json(json.dumps(body))

    def test_another_schema_is_refused_not_reinterpreted(self, tmp_path: Path) -> None:
        body = self.doc(tmp_path)
        body["schema_version"] = 99

        with pytest.raises(InvalidBindingDocument, match="schema version"):
            state_from_json(json.dumps(body))

    def test_an_unknown_field_is_refused(self, tmp_path: Path) -> None:
        body = self.doc(tmp_path)
        body["surprise"] = 1

        with pytest.raises(InvalidBindingDocument, match="unknown fields"):
            state_from_json(json.dumps(body))

    def test_a_missing_field_is_refused(self, tmp_path: Path) -> None:
        body = self.doc(tmp_path)
        del body["project"]

        with pytest.raises(InvalidBindingDocument, match="missing field"):
            state_from_json(json.dumps(body))

    def test_a_wrong_type_is_refused(self, tmp_path: Path) -> None:
        body = self.doc(tmp_path)
        body["sequence"] = "one"

        with pytest.raises(InvalidBindingDocument, match="expected int"):
            state_from_json(json.dumps(body))


class TestReport:
    def test_it_reads_back_equal(self, tmp_path: Path) -> None:
        ex, _ = done(tmp_path)
        report = report_of(ex)

        assert report_from_json(report_to_json(report)) == report

    def test_it_carries_everything_the_pipeline_and_a_person_need(self, tmp_path: Path) -> None:
        ex, _ = done(tmp_path)

        report = report_of(ex)

        assert report.connection.version == "19.0.3.2" and report.connection.edition == "studio"
        assert report.project_name == "Test edit" and report.timeline_name == "Test edit"
        assert any(c.operation == "transition.insert" for c in report.capabilities)
        assert report.count(OperationStatus.EXECUTED) > 10 and report.verified() >= 10
        assert sorted(m.asset_id for m in report.media) == [
            "broll1", "cap-file", "gfx1", "rec1", "song1", "whoosh1",
        ]  # fmt: skip
        assert all(s.applied for s in report.settings) and report.alternatives
        assert report.log and report.started_at == T0 and report.config_hash == "c"

    def test_an_instruction_scripting_cannot_carry_out_makes_it_partial_never_a_flat_success(
        self, tmp_path: Path
    ) -> None:
        ex, _ = done(tmp_path)

        report = report_of(ex)

        assert report.state is ExecutionState.PARTIALLY_COMPLETED
        assert report.outcome is Outcome.PARTIAL
        assert report.count(OperationStatus.PARTIAL) >= 1  # the transition marker
        assert report.not_verifiable()  # fonts cannot be inspected

    def test_the_text_is_deterministic(self, tmp_path: Path) -> None:
        ex, _ = done(tmp_path)
        report = report_of(ex)

        assert report_to_json(report) == report_to_json(report)

    def test_another_schema_is_refused(self, tmp_path: Path) -> None:
        ex, _ = done(tmp_path)
        body = json.loads(report_to_json(report_of(ex)))
        body["schema_version"] = 7

        with pytest.raises(InvalidBindingDocument, match="schema version"):
            report_from_json(json.dumps(body))


class TestOutcome:
    def op(self, status: OperationStatus) -> Any:
        from media_house.modules.resolve_adapter.domain.report import OperationRecord

        return OperationRecord("op-1", "x", Area.CLIP, "place", status, Strategy.NATIVE_API)

    def check(self, state: Verification) -> CheckRecord:
        return CheckRecord(Area.CLIP, "c", state)

    def outcome(
        self,
        state: ExecutionState = ExecutionState.COMPLETED,
        ops: tuple[Any, ...] = (),
        checks: tuple[CheckRecord, ...] = (),
        warnings: tuple[str, ...] = (),
    ) -> Outcome:
        return derive_outcome(state, ops, checks, SyncRecord("x").conflicts, warnings)

    def test_everything_done_and_verified(self) -> None:
        assert (
            self.outcome(
                ops=(self.op(OperationStatus.EXECUTED),),
                checks=(self.check(Verification.VERIFIED),),
            )
            is Outcome.COMPLETED
        )

    def test_a_warning_or_something_unverifiable_is_completed_with_warnings(self) -> None:
        assert self.outcome(warnings=("w",)) is Outcome.COMPLETED_WITH_WARNINGS
        assert (
            self.outcome(checks=(self.check(Verification.NOT_VERIFIABLE),))
            is Outcome.COMPLETED_WITH_WARNINGS
        )

    @pytest.mark.parametrize(
        "status", [OperationStatus.FAILED, OperationStatus.UNSUPPORTED, OperationStatus.PARTIAL]
    )
    def test_an_operation_not_carried_out_is_partial(self, status: OperationStatus) -> None:
        assert self.outcome(ops=(self.op(status),)) is Outcome.PARTIAL

    def test_a_failed_verification_is_partial(self) -> None:
        assert self.outcome(checks=(self.check(Verification.FAILED),)) is Outcome.PARTIAL

    def test_the_terminal_states_decide_alone(self) -> None:
        assert self.outcome(ExecutionState.FAILED) is Outcome.FAILED
        assert self.outcome(ExecutionState.INTERRUPTED) is Outcome.INTERRUPTED
        assert self.outcome(ExecutionState.CANCELLED) is Outcome.CANCELLED
