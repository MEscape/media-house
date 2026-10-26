"""The editor contract's implementation: assert a claim, confirm it, reject it."""

from media_house.modules.asset_relationships.application.commands import (
    DecideRelationshipCommand,
    Decision,
    ProposeRelationshipCommand,
)
from media_house.modules.asset_relationships.application.decide_relationship import (
    DecideRelationship,
)
from media_house.modules.asset_relationships.application.dto import RelationshipDto
from media_house.modules.asset_relationships.application.propose_relationship import (
    ProposeRelationship,
)
from media_house.shared.errors import ConflictError, NotFoundError, Result, ValidationError


class RelationshipEditing:
    """One object for the three writing actions, each delegated to its own use case."""

    def __init__(self, propose: ProposeRelationship, decide: DecideRelationship) -> None:
        self._propose = propose
        self._decide = decide

    def propose(
        self, command: ProposeRelationshipCommand
    ) -> Result[RelationshipDto, ValidationError | NotFoundError]:
        return self._propose.execute(command)

    def confirm(
        self, relationship_id: str, note: str = ""
    ) -> Result[RelationshipDto, NotFoundError | ConflictError]:
        return self._decide.execute(
            DecideRelationshipCommand(relationship_id, Decision.CONFIRM, note)
        )

    def reject(
        self, relationship_id: str, note: str = ""
    ) -> Result[RelationshipDto, NotFoundError | ConflictError]:
        return self._decide.execute(
            DecideRelationshipCommand(relationship_id, Decision.REJECT, note)
        )
