"""A separate OS process that writes to the same relationship database as the test.

Run as ``python -m tests.support.relationship_worker DATABASE ASSET_A ASSET_B``. It tries to create
the claim for the pair and prints ``created`` or ``existing``, so the cross-process test can prove
that racing processes (own connections, own migrations) converge on one row.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.evidence import RelationshipEvidence
from media_house.modules.asset_relationships.domain.relationship import (
    AssetRelationship,
    ClaimInput,
)
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    RelationshipOrigin,
    RelationshipType,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_repository import (
    SqliteRelationshipRepository,
)


def main(database: str, first: str, second: str) -> None:
    now = datetime.now(UTC)
    claim = ClaimInput.of(
        RelationshipType.ALTERNATE_TAKE_OF,
        first,
        second,
        origin=RelationshipOrigin.DETECTOR,
        basis=Basis("worker", "1"),
        evidence=(
            RelationshipEvidence(
                kind=EvidenceKind.PRODUCTION_TAG_MATCH,
                provider="worker",
                provider_version="1",
                observed_at=now,
                detail={"scene_a": "1", "scene_b": "1", "take_a": "1", "take_b": "2"},
            ),
        ),
    )
    outcome: list[str] = []

    def change(existing: AssetRelationship | None) -> AssetRelationship | None:
        if existing is not None:
            outcome.append("existing")
            return None
        outcome.append("created")
        return AssetRelationship.create(claim, now=now)

    SqliteRelationshipRepository(Path(database)).update(claim.relationship_id, change)
    sys.stdout.write(outcome[-1] + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:4])
