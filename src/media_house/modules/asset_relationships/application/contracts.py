"""The Asset Relationships PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``asset_relationships``.

A relationship is a CLAIM about two library assets, with the evidence that supports it. Assets are
referenced by their library id, never by path or hash. A claim is a proposal until a person confirms
it; its ``grade`` says how well the evidence supports it for its type (ordinal, never a probability)
and never makes it trusted lineage.

* ``RelationshipCatalog`` only reads; it never computes and never writes.
* ``RelationshipEditor`` writes claims and human decisions. Asserting the same pair again refreshes
  its evidence; a decided relationship keeps its status.

Stored claims (``temporally_overlaps_with``, ``alternate_take_of``) come with a ``status`` and a
``grade``; recorded lineage (``exact_duplicate_of``, ``proxy_of``, ``derived_from``) is projected
from the library and proxy provenance at read time, has no status and grade ``recorded``.
``exact_duplicate_of`` is directed (duplicate -> original) and a page for an original carries
``Limitation.EXACT_DUPLICATE_REVERSE_PARTIAL`` even when nothing is found.

* ``RelationshipDetection`` PROPOSES claims from stored inspections (never probes, never confirms
  or rejects anything). Nothing runs unless a caller names the detectors.
"""

from typing import Protocol

from media_house.modules.asset_relationships.application.commands import (
    EvidenceSubmission,
    ProposeRelationshipCommand,
    RelationshipQuery,
)
from media_house.modules.asset_relationships.application.detect_relationships import (
    AnalyzeAssetCommand,
    AssetAnalysis,
    DetectCommand,
    DetectionSettings,
    DetectorResult,
    DetectOutcome,
    DetectReason,
    DetectStatus,
)
from media_house.modules.asset_relationships.application.dto import (
    EndpointState,
    EvidenceDto,
    IntervalDto,
    OffsetDto,
    RelationshipDto,
    RelationshipError,
    RelationshipPage,
    StatusChangeDto,
)
from media_house.modules.asset_relationships.application.reconciliation import (
    OutcomeReason,
    OutcomeStatus,
    ReconcileCommand,
    ReconcileOutcome,
)
from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.detection import DETECTORS
from media_house.modules.asset_relationships.domain.detection_store import MarkOutcome
from media_house.modules.asset_relationships.domain.intervals import (
    ClockOffset,
    TimeCoordinates,
    TimeInterval,
)
from media_house.modules.asset_relationships.domain.lineage import FindingKind, LineageFinding
from media_house.modules.asset_relationships.domain.values import (
    ClockDomain,
    EvidenceKind,
    Grade,
    JsonValue,
    Limitation,
    OffsetOrigin,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import NotFoundError, Result, ValidationError


class RelationshipCatalog(Protocol):
    """Read the stored claims about an asset."""

    def relationships_of(
        self, asset_id: str, query: RelationshipQuery | None = None
    ) -> Result[RelationshipPage, ValidationError | NotFoundError]:
        """Claims touching the asset on either end, in id order; unknown asset is NotFound."""
        ...

    def get(self, relationship_id: str) -> Result[RelationshipDto, NotFoundError]: ...


class RelationshipEditor(Protocol):
    """Assert claims and record human decisions."""

    def propose(
        self, command: ProposeRelationshipCommand
    ) -> Result[RelationshipDto, ValidationError | NotFoundError]:
        """Create the claim, or refresh the stored one. Both assets must exist in the library."""
        ...

    def confirm(
        self, relationship_id: str, note: str = ""
    ) -> Result[RelationshipDto, RelationshipError]: ...

    def reject(
        self, relationship_id: str, note: str = ""
    ) -> Result[RelationshipDto, RelationshipError]:
        """Rejects the pair and type; a later detection cannot revive it, only a person can."""
        ...


class RelationshipMaintenance(Protocol):
    """Keep the stored claims truthful against the live library. Nothing runs by itself.

    ``reconcile`` is blocking (library lookups, database writes): run it as a job. A sweep is
    bounded per job and resumable; call it again until the outcome is ``COMPLETED``. Cancelling a
    job records ``INTERRUPTED`` and raises ``OperationCancelledError``.
    """

    def reconcile(self, command: ReconcileCommand, ctx: JobContext) -> ReconcileOutcome: ...

    def last_run(self) -> ReconcileOutcome | None:
        """The outcome of the last real run (``NOT_DUE`` answers are not recorded)."""
        ...


class RelationshipDetection(Protocol):
    """Propose claims from stored inspections. Caller-driven; nothing runs by itself.

    ``analyze`` looks at one asset; ``detect`` walks the library's originals under a budget and
    resumes where it stopped (call it again until it reports ``COMPLETED``). Both are blocking:
    run them as jobs. A detector is only run when named, ``detector_ids`` lists the ones that exist.
    """

    def detector_ids(self) -> tuple[str, ...]: ...

    def analyze(
        self, command: AnalyzeAssetCommand, ctx: JobContext
    ) -> Result[AssetAnalysis, ValidationError]: ...

    def detect(
        self, command: DetectCommand, ctx: JobContext
    ) -> Result[DetectOutcome, ValidationError]: ...

    def last_run(self) -> DetectOutcome | None: ...


DETECTOR_IDS: tuple[str, ...] = tuple(sorted(DETECTORS))

__all__ = [
    "DETECTOR_IDS",
    "AnalyzeAssetCommand",
    "AssetAnalysis",
    "Basis",
    "ClockDomain",
    "ClockOffset",
    "DetectCommand",
    "DetectOutcome",
    "DetectReason",
    "DetectStatus",
    "DetectionSettings",
    "DetectorResult",
    "EndpointState",
    "EvidenceDto",
    "EvidenceKind",
    "EvidenceSubmission",
    "FindingKind",
    "Grade",
    "IntervalDto",
    "JsonValue",
    "Limitation",
    "LineageFinding",
    "MarkOutcome",
    "OffsetDto",
    "OffsetOrigin",
    "OutcomeReason",
    "OutcomeStatus",
    "ProposeRelationshipCommand",
    "ReconcileCommand",
    "ReconcileOutcome",
    "RelationshipCatalog",
    "RelationshipDetection",
    "RelationshipDto",
    "RelationshipEditor",
    "RelationshipError",
    "RelationshipMaintenance",
    "RelationshipOrigin",
    "RelationshipPage",
    "RelationshipQuery",
    "RelationshipStatus",
    "RelationshipType",
    "StatusChangeDto",
    "TimeCoordinates",
    "TimeInterval",
]
