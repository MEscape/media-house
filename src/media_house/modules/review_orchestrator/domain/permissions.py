"""Who may decide what: three levels of authority and a project's grants.

``AUTOMATIC``     may run without asking, but only where the project said so in advance.
``SUGGESTED``     is proposed; a person approves it.
``MANUAL_ONLY``   happens only on a person's explicit instruction.

A project can make an operation stricter or (where the floor allows) looser, but a floor is never
lowered and confidence never raises an operation's authority: nothing here knows about confidence.
"""

from dataclasses import dataclass
from enum import StrEnum


class Authority(StrEnum):
    AUTOMATIC = "automatic"
    SUGGESTED = "suggested"
    MANUAL_ONLY = "manual_only"


_STRICTNESS = {Authority.AUTOMATIC: 0, Authority.SUGGESTED: 1, Authority.MANUAL_ONLY: 2}


class Operation(StrEnum):
    TECHNICAL_REPAIR = "technical_repair"
    ASSET_RECONNECTION = "asset_reconnection"
    DERIVATIVE_REBUILD = "derivative_rebuild"
    LOCAL_CREATIVE_REVISION = "local_creative_revision"
    GLOBAL_CREATIVE_REVISION = "global_creative_revision"
    SECTION_UNLOCK = "section_unlock"
    TIMELINE_RECONSTRUCTION = "timeline_reconstruction"
    FINAL_POLISH_CHANGE = "final_polish_change"
    RENDER_PREPARATION = "render_preparation"
    FINAL_EXPORT = "final_export"
    DELIVERY_APPROVAL = "delivery_approval"
    OVERRIDE_BLOCKER = "override_blocker"
    #: Overriding a decision a person already made (an approved or rejected creative choice).
    OVERRIDE_DECISION = "override_decision"


#: The least strict a project may make an operation.
FLOORS: dict[Operation, Authority] = {
    Operation.TECHNICAL_REPAIR: Authority.AUTOMATIC,
    Operation.ASSET_RECONNECTION: Authority.AUTOMATIC,
    Operation.DERIVATIVE_REBUILD: Authority.AUTOMATIC,
    Operation.LOCAL_CREATIVE_REVISION: Authority.SUGGESTED,
    Operation.GLOBAL_CREATIVE_REVISION: Authority.SUGGESTED,
    Operation.SECTION_UNLOCK: Authority.MANUAL_ONLY,
    Operation.TIMELINE_RECONSTRUCTION: Authority.SUGGESTED,
    Operation.FINAL_POLISH_CHANGE: Authority.AUTOMATIC,
    Operation.RENDER_PREPARATION: Authority.AUTOMATIC,
    Operation.FINAL_EXPORT: Authority.MANUAL_ONLY,
    Operation.DELIVERY_APPROVAL: Authority.MANUAL_ONLY,
    Operation.OVERRIDE_BLOCKER: Authority.MANUAL_ONLY,
    Operation.OVERRIDE_DECISION: Authority.MANUAL_ONLY,
}
#: What an operation is without a grant. Technical repairs are only AUTOMATIC when the project
#: says so; a render preparation changes nothing a person decided.
DEFAULTS: dict[Operation, Authority] = {
    **FLOORS,
    Operation.TECHNICAL_REPAIR: Authority.SUGGESTED,
    Operation.ASSET_RECONNECTION: Authority.SUGGESTED,
    Operation.DERIVATIVE_REBUILD: Authority.SUGGESTED,
    Operation.FINAL_POLISH_CHANGE: Authority.SUGGESTED,
}


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    """A project's grants. Anything not listed has its default."""

    grants: tuple[tuple[Operation, Authority], ...] = ()
    #: The validated export must also be approved for delivery by a person.
    require_delivery_approval: bool = True
    #: A HIGH issue blocks the final export until it is resolved or a person overrides it.
    high_issues_block_export: bool = True

    def authority(self, operation: Operation) -> Authority:
        """The effective authority: the project's grant, never less strict than the floor."""
        granted = dict(self.grants).get(operation, DEFAULTS[operation])
        floor = FLOORS[operation]
        return granted if _STRICTNESS[granted] >= _STRICTNESS[floor] else floor

    def granted(self, operation: Operation) -> bool:
        """Whether the project explicitly made this operation less strict than its default."""
        return _STRICTNESS[self.authority(operation)] < _STRICTNESS[DEFAULTS[operation]]

    def allows_unattended(self, operation: Operation) -> bool:
        return self.authority(operation) is Authority.AUTOMATIC
