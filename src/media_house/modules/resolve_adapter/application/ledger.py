"""The ledger of one run: operations, settings, media, checks and notes, collected as they happen.

Every Resolve operation is recorded against the timeline element it carries out, with whether it
was executed and whether it was then verified by reading Resolve back. The run's report is built
from it, so the report is what happened, not what was intended.
"""

from dataclasses import dataclass, field, replace

from media_house.modules.resolve_adapter.domain.report import (
    AlternativeRecord,
    CheckRecord,
    MediaRecord,
    OperationRecord,
    SettingRecord,
)
from media_house.modules.resolve_adapter.domain.values import (
    Area,
    ExecutionState,
    OperationStatus,
    Strategy,
    Verification,
)


@dataclass(slots=True)
class Ledger:
    operations: list[OperationRecord] = field(default_factory=list)
    settings: list[SettingRecord] = field(default_factory=list)
    media: list[MediaRecord] = field(default_factory=list)
    checks: list[CheckRecord] = field(default_factory=list)
    alternatives: list[AlternativeRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    state: ExecutionState = ExecutionState.QUEUED
    #: Capability ids a read-back confirmed during this run.
    observed: set[str] = field(default_factory=set)

    def enter(self, state: ExecutionState) -> None:
        self.state = state
        self.note(f"state: {state.value}")

    def note(self, text: str) -> None:
        self.log.append(text)

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)
        self.log.append(f"warning: {text}")

    def fail(self, text: str) -> None:
        self.errors.append(text)
        self.log.append(f"error: {text}")

    def op(
        self,
        element_id: str,
        area: Area,
        action: str,
        status: OperationStatus,
        *,
        strategy: Strategy = Strategy.NATIVE_API,
        verification: Verification = Verification.NOT_CHECKED,
        detail: str = "",
        ref: str = "",
    ) -> None:
        self.operations.append(
            OperationRecord(
                op_id=f"op-{len(self.operations) + 1:04d}",
                element_id=element_id,
                area=area,
                action=action,
                status=status,
                strategy=strategy,
                verification=verification,
                detail=detail,
                resolve_ref=ref,
            )
        )

    def verify(
        self, element_id: str, area: Area, verification: Verification, detail: str = ""
    ) -> None:
        """Attach a read-back result to the newest operation of an element in an area."""
        for position in range(len(self.operations) - 1, -1, -1):
            o = self.operations[position]
            if o.element_id == element_id and o.area is area and o.action in {"place", "keep"}:
                self.operations[position] = replace(
                    o,
                    verification=verification,
                    detail=(f"{o.detail}; {detail}" if o.detail and detail else detail or o.detail),
                )
                return

    def confirm(self, *operations: str) -> None:
        self.observed.update(operations)
