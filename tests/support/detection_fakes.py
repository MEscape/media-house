"""Doubles and builders for the relationship-detection tests (no media, no probing)."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.modules.asset_relationships.application.detect_relationships import (
    DetectionSettings,
    DetectRelationships,
)
from media_house.modules.asset_relationships.application.propose_relationship import (
    ProposeRelationship,
)
from media_house.modules.asset_relationships.application.reconciliation import (
    ReconcileRelationships,
    ReconciliationSettings,
)
from media_house.modules.asset_relationships.domain.detection import DETECTORS, Detector
from media_house.modules.asset_relationships.infrastructure.sqlite_detection_store import (
    SqliteDetectionStore,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_reconciliation_store import (
    SqliteReconciliationStore,
)
from media_house.modules.asset_relationships.infrastructure.sqlite_repository import (
    SqliteRelationshipRepository,
)
from media_house.modules.media_inspection.application.contracts import InspectionResult
from media_house.modules.media_inspection.domain.model import ContainerInfo, ProductionMetadata
from media_house.modules.media_library.application.dto import DerivationDto, MediaAssetDto
from tests.support.asset_dto import make_asset
from tests.support.fakes import FixedClock
from tests.support.inspection_fakes import audio, inspect, observed, video
from tests.support.relationship_fakes import FakeAssetLookup, original

START = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)


def inspection(
    asset_id: str,
    *,
    created: datetime | None = START,
    seconds: float | None = 60.0,
    device: str | None = None,
    scene: str | None = None,
    take: str | None = None,
    reel: str | None = None,
    fingerprint: str = "fp-1",
) -> InspectionResult:
    """A stored inspection of ``asset_id`` with the production facts the detectors read."""
    media = observed(
        videos=(video(),),
        audios=(audio(),),
        container=ContainerInfo(format_names=("mp4",), duration=seconds, start_time=0.0),
        production=ProductionMetadata(
            creation_time=created, device=device, scene=scene, take=take, reel_id=reel
        ),
    )
    document: MediaAssetDto = replace(
        make_asset(f"insp-{asset_id}"),
        derivation=DerivationDto(asset_id, "media_inspection", 1, {}, fingerprint),
    )
    return InspectionResult(inspect(media), document, created=False)


class FakeInspections:
    """``InspectionCatalog`` over a dict; it counts lookups and never probes."""

    def __init__(self, *results: tuple[str, InspectionResult]) -> None:
        self.results: dict[str, InspectionResult] = dict(results)
        self.lookups: list[str] = []

    def find(self, asset_id: str) -> InspectionResult | None:
        self.lookups.append(asset_id)
        return self.results.get(asset_id)


class Rig:
    """The real detection use case over ONE real SQLite file (claims, marks, facts, reconciliation).

    Only the library and the inspections are fakes. Using the real stores matters: the rules about
    decisions, staleness and concurrency live in how they behave, not in a double.
    """

    def __init__(
        self,
        tmp_path: Path,
        assets: Mapping[str, MediaAssetDto] | None = None,
        settings: DetectionSettings | None = None,
        clock: Clock | None = None,
        detectors: Mapping[str, Detector] = DETECTORS,
        share: "Rig | None" = None,
    ) -> None:
        """``share``: a second use case over the same database, library and inspections (a new
        build of the detectors, say)."""
        self.clock: Clock = clock or (share.clock if share else FixedClock())
        self.library: FakeAssetLookup = (
            share.library if share else FakeAssetLookup(*(assets or {}).values())
        )
        self.path: Path = share.path if share else tmp_path / "relationships.db"
        self.repository = SqliteRelationshipRepository(self.path)
        self.inspections: FakeInspections = share.inspections if share else FakeInspections()
        self.store = SqliteDetectionStore(self.path)
        self.detection = DetectRelationships(
            self.store,
            self.inspections,
            self.library,
            ProposeRelationship(self.repository, self.library, self.clock),
            self.repository,
            self.clock,
            settings,
            detectors,
        )

    def add(
        self, asset_id: str, *, minutes: int = 0, inspect_it: bool = True, **facts: object
    ) -> None:
        self.library.add(original(asset_id, created_at=START + timedelta(minutes=minutes)))
        if inspect_it:
            self.inspections.results[asset_id] = inspection(asset_id, **facts)  # type: ignore[arg-type]

    def reconciler(self, *checks: object) -> ReconcileRelationships:
        """Reconciliation over the same file, with the given ``BasisCheck`` ports."""
        return ReconcileRelationships(
            SqliteReconciliationStore(self.path),
            self.library,
            self.clock,
            checks,  # type: ignore[arg-type]
            ReconciliationSettings(),
        )
