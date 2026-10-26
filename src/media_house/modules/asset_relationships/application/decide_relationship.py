"""Use case: a person confirms or rejects a relationship."""

from media_house.core.application.ports import Clock
from media_house.modules.asset_relationships.application.commands import (
    DecideRelationshipCommand,
    Decision,
)
from media_house.modules.asset_relationships.application.dto import RelationshipDto
from media_house.modules.asset_relationships.application.mapping import endpoint_states, to_dto
from media_house.modules.asset_relationships.application.ports import AssetLookup
from media_house.modules.asset_relationships.domain.errors import (
    IllegalTransition,
    RelationshipNotFound,
)
from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.domain.repository import RelationshipRepository
from media_house.modules.asset_relationships.domain.values import RelationshipId
from media_house.shared.errors import ConflictError, Err, NotFoundError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class DecideRelationship:
    """Record a human decision. Only a person changes a decided relationship, never automation."""

    def __init__(
        self, repository: RelationshipRepository, library: AssetLookup, clock: Clock
    ) -> None:
        self._repository = repository
        self._library = library
        self._clock = clock

    def execute(
        self, command: DecideRelationshipCommand
    ) -> Result[RelationshipDto, NotFoundError | ConflictError]:
        if not command.relationship_id:
            return Err(RelationshipNotFound(command.relationship_id))
        now = self._clock.now()

        def change(existing: AssetRelationship | None) -> AssetRelationship | None:
            if existing is None:
                raise RelationshipNotFound(command.relationship_id)
            if command.decision is Decision.CONFIRM:
                existing.confirm(now=now, note=command.note)
            else:
                existing.reject(now=now, note=command.note)
            return existing

        try:
            stored = self._repository.update(RelationshipId(command.relationship_id), change)
        except RelationshipNotFound as exc:
            return Err(exc)
        except IllegalTransition as exc:
            return Err(ConflictError(str(exc), user_message=exc.user_message))
        if stored is None:
            return Err(RelationshipNotFound(command.relationship_id))
        _log.info(
            "Relationship decided",
            relationship_id=stored.id.value,
            decision=command.decision.value,
            status=stored.status.value,
        )
        ends = [stored.asset_a, stored.asset_b]
        return Ok(to_dto(stored, endpoint_states(ends, self._library.get_many(ends))))
