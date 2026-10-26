"""Module registration: how Asset Relationships plugs into the application.

It owns one small database (``asset_relationships.db``: stored claims, their evidence and their
status history). Assets, checksums and derivation stay with the Media Library, and proxy provenance
with the Proxy Manager; both are reached only through their public contracts. Recorded lineage is
projected from them at read time and never stored.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.asset_relationships.application.contracts import (
    RelationshipCatalog,
    RelationshipEditor,
    RelationshipMaintenance,
)
from media_house.modules.asset_relationships.application.decide_relationship import (
    DecideRelationship,
)
from media_house.modules.asset_relationships.application.editing import RelationshipEditing
from media_house.modules.asset_relationships.application.lineage_projection import (
    LineageProjection,
    LineageSettings,
)
from media_house.modules.asset_relationships.application.propose_relationship import (
    ProposeRelationship,
)
from media_house.modules.asset_relationships.application.query_relationships import (
    QueryRelationships,
)
from media_house.modules.asset_relationships.application.reconciliation import (
    ReconcileRelationships,
    ReconciliationSettings,
)
from media_house.modules.asset_relationships.domain.reconciliation import ReconciliationStore
from media_house.modules.asset_relationships.domain.repository import RelationshipRepository
from media_house.modules.asset_relationships.infrastructure.sqlite_reconciliation_store import (
    SqliteReconciliationStore,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_repository import (
    SqliteRelationshipRepository,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.proxy_manager.application.contracts import ProxyCatalog
from media_house.shared.filesystem import AppPaths


class AssetRelationshipsModule:
    name: str = "asset_relationships"

    def register(self, container: Container) -> None:
        container.register_factory(
            RelationshipRepository,
            lambda c: SqliteRelationshipRepository(
                c.resolve(AppPaths).data_dir / "asset_relationships.db"
            ),
        )
        container.register_factory(
            ProposeRelationship,
            lambda c: ProposeRelationship(
                c.resolve(RelationshipRepository), c.resolve(MediaLibrary), c.resolve(Clock)
            ),
        )
        container.register_factory(
            DecideRelationship,
            lambda c: DecideRelationship(
                c.resolve(RelationshipRepository), c.resolve(MediaLibrary), c.resolve(Clock)
            ),
        )
        container.register_factory(
            LineageProjection,
            lambda c: LineageProjection(
                c.resolve(MediaLibrary), c.resolve(ProxyCatalog), LineageSettings()
            ),
        )
        container.register_factory(
            QueryRelationships,
            lambda c: QueryRelationships(
                c.resolve(RelationshipRepository),
                c.resolve(MediaLibrary),
                c.resolve(LineageProjection),
            ),
        )
        container.register_factory(
            RelationshipEditing,
            lambda c: RelationshipEditing(
                c.resolve(ProposeRelationship), c.resolve(DecideRelationship)
            ),
        )
        container.register_factory(
            ReconciliationStore,
            lambda c: SqliteReconciliationStore(
                c.resolve(AppPaths).data_dir / "asset_relationships.db"
            ),
        )
        # no detector exists yet, so no check: nothing is invalidated by version in production
        container.register_factory(
            ReconcileRelationships,
            lambda c: ReconcileRelationships(
                c.resolve(ReconciliationStore),
                c.resolve(MediaLibrary),
                c.resolve(Clock),
                (),
                ReconciliationSettings(),
            ),
        )
        container.register_factory(
            RelationshipMaintenance, lambda c: c.resolve(ReconcileRelationships)
        )
        container.register_factory(RelationshipCatalog, lambda c: c.resolve(QueryRelationships))
        container.register_factory(RelationshipEditor, lambda c: c.resolve(RelationshipEditing))
