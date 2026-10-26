"""Use case: assert that two assets are related, or refresh an existing assertion."""

from media_house.core.application.ports import Clock
from media_house.modules.asset_relationships.application.commands import (
    ProposeRelationshipCommand,
)
from media_house.modules.asset_relationships.application.dto import RelationshipDto
from media_house.modules.asset_relationships.application.mapping import endpoint_states, to_dto
from media_house.modules.asset_relationships.application.ports import AssetLookup
from media_house.modules.asset_relationships.domain.evidence import RelationshipEvidence
from media_house.modules.asset_relationships.domain.relationship import (
    AssetRelationship,
    ClaimInput,
)
from media_house.modules.asset_relationships.domain.repository import RelationshipRepository
from media_house.modules.media_library.application.contracts import MediaNotFound
from media_house.shared.errors import (
    DomainError,
    Err,
    InvariantViolation,
    NotFoundError,
    Ok,
    Result,
    ValidationError,
)
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class ProposeRelationship:
    """Validate a claim, check both assets exist, then create it or refresh the stored one.

    Idempotent: the same assertion again changes nothing. A decided relationship keeps its status.
    """

    def __init__(
        self, repository: RelationshipRepository, library: AssetLookup, clock: Clock
    ) -> None:
        self._repository = repository
        self._library = library
        self._clock = clock

    def execute(
        self, command: ProposeRelationshipCommand
    ) -> Result[RelationshipDto, ValidationError | NotFoundError]:
        observed_at = self._clock.now()
        try:
            claim = ClaimInput.of(
                command.relationship_type,
                command.asset_a,
                command.asset_b,
                origin=command.origin,
                basis=command.basis,
                evidence=tuple(
                    RelationshipEvidence(
                        kind=item.kind,
                        provider=item.provider,
                        provider_version=item.provider_version,
                        observed_at=observed_at,
                        detail=item.detail,
                        offset=item.offset,
                    )
                    for item in command.evidence
                ),
                first_interval=command.a_interval,
                second_interval=command.b_interval,
                offset=command.offset,
            )
        except DomainError as exc:
            return Err(ValidationError(str(exc), user_message=exc.user_message))

        found = self._library.get_many([claim.asset_a, claim.asset_b])
        states = endpoint_states([claim.asset_a, claim.asset_b], found)
        for asset_id in (claim.asset_a, claim.asset_b):
            if asset_id not in {asset.id for asset in found}:
                return Err(MediaNotFound(asset_id))

        def change(existing: AssetRelationship | None) -> AssetRelationship | None:
            if existing is None:
                return AssetRelationship.create(claim, now=observed_at)
            return existing if existing.refresh(claim, now=observed_at) else None

        stored = self._repository.update(claim.relationship_id, change)
        if stored is None:
            raise InvariantViolation("The store returned nothing for a stored relationship")
        _log.info(
            "Relationship asserted",
            relationship_id=stored.id.value,
            type=stored.type.value,
            status=stored.status.value,
            grade=stored.grade.value,
        )
        return Ok(to_dto(stored, states))
