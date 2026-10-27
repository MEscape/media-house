"""In-memory doubles for asset relationships: the store and the library lookup.

Like the real adapter, the store hands out copies (never live aggregates), applies a change
atomically under a lock, and filters, orders and pages exactly as the SQL does, so the repository
contract tests run against both.
"""

import threading
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

from media_house.modules.asset_relationships.domain.errors import CorruptClaim
from media_house.modules.asset_relationships.domain.evidence import RelationshipEvidence
from media_house.modules.asset_relationships.domain.intervals import (
    ClockOffset,
    TimeCoordinates,
    TimeInterval,
)
from media_house.modules.asset_relationships.domain.reconciliation import (
    ClaimRef,
    ClaimVerdict,
    ScanState,
)
from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.domain.repository import RelationshipCriteria
from media_house.modules.asset_relationships.domain.values import (
    ClockDomain,
    EvidenceKind,
    OffsetOrigin,
    RelationshipId,
)
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    DerivationDto,
    MediaAssetDto,
    MediaError,
    MediaNotFound,
    MediaQuery,
    MediaType,
    Page,
)
from media_house.modules.proxy_manager.application.contracts import (
    METADATA_KEY,
    Condition,
    Evidence,
    ProxyDto,
    Verdict,
)
from media_house.shared.errors import Err, InvariantViolation, Ok, PersistenceError, Result
from tests.support.asset_dto import make_asset

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

WALL = TimeCoordinates(ClockDomain.WALL_CLOCK, "unix_epoch_utc")
CONTAINER = TimeCoordinates(ClockDomain.CONTAINER, "container_start")


def interval(
    start: int | Fraction, end: int | Fraction, coordinates: TimeCoordinates = WALL
) -> TimeInterval:
    return TimeInterval(Fraction(start), Fraction(end), coordinates)


def clock_offset(
    seconds: int | Fraction = 0,
    uncertainty: int | Fraction | None = 0,
    origin: OffsetOrigin = OffsetOrigin.WALL_CLOCK_DIFFERENCE,
) -> ClockOffset:
    return ClockOffset(
        Fraction(seconds), None if uncertainty is None else Fraction(uncertainty), origin
    )


def wall_clock(
    trust: str = "unverified", *, offset: ClockOffset | None = None
) -> RelationshipEvidence:
    return RelationshipEvidence(
        kind=EvidenceKind.WALL_CLOCK_OVERLAP,
        provider="overlap-detector",
        provider_version="1",
        observed_at=T0,
        detail={"clock_trust": trust},
        offset=offset,
    )


def tag_match(
    scene_a: str = "12",
    scene_b: str = "12",
    take_a: str = "1",
    take_b: str = "2",
    *,
    same_device: bool | None = None,
    same_reel: bool | None = None,
) -> RelationshipEvidence:
    flags = {"same_device": same_device, "same_reel": same_reel}
    return RelationshipEvidence(
        kind=EvidenceKind.PRODUCTION_TAG_MATCH,
        provider="tag-detector",
        provider_version="1",
        observed_at=T0,
        detail={
            "scene_a": scene_a,
            "scene_b": scene_b,
            "take_a": take_a,
            "take_b": take_b,
            **{name: value for name, value in flags.items() if value is not None},
        },
    )


def assertion(note: str = "") -> RelationshipEvidence:
    return RelationshipEvidence(
        kind=EvidenceKind.USER_ASSERTION,
        provider="user",
        provider_version="1",
        observed_at=T0,
        detail={"note": note} if note else {},
    )


class InMemoryRelationshipRepository:
    def __init__(self) -> None:
        self._items: dict[str, AssetRelationship] = {}
        self._lock = threading.RLock()

    def update(
        self,
        relationship_id: RelationshipId,
        change: Callable[[AssetRelationship | None], AssetRelationship | None],
    ) -> AssetRelationship | None:
        with self._lock:
            found = deepcopy(self._items.get(relationship_id.value))
            updated = change(found)
            if updated is None:
                return found
            if updated.id != relationship_id:
                raise InvariantViolation("A change cannot move a relationship to another id")
            self._items[relationship_id.value] = deepcopy(updated)
            return deepcopy(updated)

    def get(self, relationship_id: RelationshipId) -> AssetRelationship | None:
        with self._lock:
            found = self._items.get(relationship_id.value)
            return deepcopy(found) if found else None

    def find(self, criteria: RelationshipCriteria) -> Sequence[AssetRelationship]:
        with self._lock:
            matching = [
                r
                for r in self._items.values()
                if criteria.asset_id in (r.asset_a, r.asset_b)
                and (not criteria.types or r.type in criteria.types)
                and (not criteria.statuses or r.status in criteria.statuses)
                and (not criteria.grades or r.grade in criteria.grades)
                and (not criteria.origins or r.origin in criteria.origins)
                and (criteria.after is None or r.id.value > criteria.after)
            ]
            matching.sort(key=lambda r: r.id.value)
            return [deepcopy(r) for r in matching[: criteria.limit]]


class FakeAssetLookup:
    """The library ports over a dict, with the library's own search and checksum semantics.

    ``searches`` and ``checksum_lookups`` record every call, so a test can prove how much work a
    query did. ``remove`` makes an asset disappear.
    """

    def __init__(self, *assets: MediaAssetDto) -> None:
        self._assets = {a.id: a for a in assets}
        self.searches: list[MediaQuery] = []
        self.checksum_lookups: list[str] = []
        self.gets: list[str] = []
        self.get_many_calls: list[list[str]] = []
        #: The next this-many ``get_many`` calls raise ``PersistenceError`` (a failing library).
        self.fail_get_many = 0
        #: Raised by every ``get_many`` call when set (an unexpected defect).
        self.defect: Exception | None = None
        #: Called after ``get_many`` computed its answer and before it returns, so a test can
        #: change the world "while the job is looking" (a decision, a restored asset, a rival job).
        self.on_get_many: Callable[[list[str]], object] | None = None

    def add(self, asset: MediaAssetDto) -> None:
        self._assets[asset.id] = asset

    def remove(self, asset_id: str) -> None:
        del self._assets[asset_id]

    def has(self, asset_id: str) -> bool:
        return asset_id in self._assets

    def get(self, asset_id: str) -> Result[MediaAssetDto, MediaError]:
        self.gets.append(asset_id)
        found = self._assets.get(asset_id)
        return Ok(found) if found else Err(MediaNotFound(asset_id))

    def get_many(self, asset_ids: Sequence[str]) -> list[MediaAssetDto]:
        self.get_many_calls.append(list(asset_ids))
        if self.defect is not None:
            raise self.defect
        if self.fail_get_many > 0:
            self.fail_get_many -= 1
            raise PersistenceError("The media database is unavailable")
        answer = [self._assets[i] for i in dict.fromkeys(asset_ids) if i in self._assets]
        if self.on_get_many is not None:
            self.on_get_many(list(asset_ids))
        return answer

    def find_by_checksum(self, checksum: str) -> MediaAssetDto | None:
        """Like the library: an ORIGINAL only."""
        self.checksum_lookups.append(checksum)
        return next(
            (
                a
                for a in self._assets.values()
                if a.checksum == checksum and a.derivation is None and a.role is AssetRole.ORIGINAL
            ),
            None,
        )

    def search(self, query: MediaQuery | None = None) -> Page[MediaAssetDto]:
        """``source_asset_id``, ``roles``, ``statuses``, oldest first, paged like the library."""
        query = query or MediaQuery()
        self.searches.append(query)
        matching = [
            a
            for a in self._assets.values()
            if (query.source_asset_id is None or _source_of(a) == query.source_asset_id)
            and (not query.roles or a.role in query.roles)
            and (not query.statuses or a.status in query.statuses)
            and (not query.media_types or a.media_type in query.media_types)
            and (query.created_from is None or a.created_at >= query.created_from)
        ]
        matching.sort(key=lambda a: (a.created_at, a.id))
        return Page(
            matching[query.offset : query.offset + query.page_size],
            len(matching),
            query.page,
            query.page_size,
        )


def _source_of(asset: MediaAssetDto) -> str | None:
    return asset.derivation.source_asset_id if asset.derivation else None


def checksum_of(asset_id: str) -> str:
    """A distinct, valid-looking checksum per asset id."""
    return asset_id.encode().hex().ljust(64, "0")[:64]


def original(asset_id: str, **changes: Any) -> MediaAssetDto:
    return make_asset(asset_id, checksum=checksum_of(asset_id), **changes)


def derivative(
    asset_id: str,
    source: str,
    *,
    operation: str = "video_improvement",
    media_type: MediaType = MediaType.VIDEO,
    role: AssetRole = AssetRole.DERIVED,
    metadata: Mapping[str, Any] | None = None,
    **changes: Any,
) -> MediaAssetDto:
    return make_asset(
        asset_id,
        media_type=media_type,
        role=role,
        checksum=changes.pop("checksum", checksum_of(asset_id)),
        metadata=metadata or {},
        derivation=DerivationDto(source, operation, 1, {}, f"fp-{asset_id}"),
        **changes,
    )


def proxy_metadata(source: str, profile: str = "analysis") -> dict[str, Any]:
    """What ``proxy_manager`` writes into a proxy asset's metadata (schema 1)."""
    return {
        "processing_type": "proxy",
        "source_asset_id": source,
        METADATA_KEY: {
            "schema": 1,
            "source_asset_id": source,
            "profile": profile,
            "profile_version": 1,
            "spec_fingerprint": "spec-fp",
            "spec": {},
            "verdict": "valid",
            "evidence": "packets",
            "validator_version": 1,
            "transformations": [],
            "encoder": "libx264",
            "toolchain": "test",
            "facts_origin": "probe",
            "attempt_id": "attempt-1",
            "warnings": [],
            "checks": [],
        },
    }


def proxy(asset_id: str, source: str, profile: str = "analysis", **changes: Any) -> MediaAssetDto:
    """A proxy asset as the library holds it: derived, preview role, with its provenance."""
    return derivative(
        asset_id,
        source,
        operation="proxy",
        role=AssetRole.PREVIEW,
        metadata=proxy_metadata(source, profile),
        **changes,
    )


class FakeProxyCatalog:
    """Which proxy each ``(source, profile)`` slot serves; records every ``find``."""

    def __init__(self) -> None:
        self.served: dict[tuple[str, str], str] = {}
        self.finds: list[tuple[str, str]] = []

    def find(self, source_asset_id: str, profile: str) -> ProxyDto | None:
        self.finds.append((source_asset_id, profile))
        proxy_id = self.served.get((source_asset_id, profile))
        if proxy_id is None:
            return None
        return ProxyDto(
            source_asset_id=source_asset_id,
            proxy_asset_id=proxy_id,
            profile=profile,
            profile_version=1,
            spec_fingerprint="spec-fp",
            condition=Condition.READY,
            verdict=Verdict.VALID,
            evidence=Evidence.PACKETS,
            codec="h264",
            width=320,
            height=180,
            size_bytes=1,
            transformations=(),
            warnings=(),
            encoder=None,
            toolchain=None,
            reused=False,
        )

    def condition(self, source_asset_id: str, profile: str) -> Condition:
        return Condition.READY if (source_asset_id, profile) in self.served else Condition.NONE


class InMemoryReconciliationStore:
    """The reconciliation port over the in-memory relationship store, with the SQL's semantics.

    Every claim is evaluated under the store's lock on a copy, a purge writes ``purge_log`` and
    deletes, the cursor moves by compare-and-set. ``corrupt`` makes chosen claims unreadable.
    """

    def __init__(self, repository: InMemoryRelationshipRepository) -> None:
        self._repository = repository
        self._state: dict[str, str] = {}
        self.purge_log: list[dict[str, Any]] = []
        self.corrupt: set[str] = set()
        self.reconcile_calls: list[str] = []

    def claims_after(self, cursor: str | None, limit: int) -> Sequence[ClaimRef]:
        with self._repository._lock:
            ids = sorted(i for i in self._repository._items if cursor is None or i > cursor)
            return [
                ClaimRef(
                    i,
                    self._repository._items[i].asset_a,
                    self._repository._items[i].asset_b,
                    self._repository._items[i].endpoint_missing_since is not None,
                    self._repository._items[i].basis.canonical(),
                )
                for i in ids[:limit]
            ]

    def reconcile_claim(
        self,
        relationship_id: str,
        decide: Callable[[AssetRelationship], ClaimVerdict],
        *,
        now: datetime,
    ) -> ClaimVerdict | None:
        self.reconcile_calls.append(relationship_id)
        with self._repository._lock:
            stored = self._repository._items.get(relationship_id)
            if stored is None:
                return None
            if relationship_id in self.corrupt:
                raise CorruptClaim(f"Stored relationship {relationship_id} is unreadable")
            claim = deepcopy(stored)
            verdict = decide(claim)
            if verdict.purge is not None:
                if claim.endpoint_missing_since is None:
                    raise InvariantViolation("purged before it was marked missing")
                decisions = [
                    h.at for h in claim.history if h.status_to.value in {"confirmed", "rejected"}
                ]
                self.purge_log.append(
                    {
                        "relationship_id": claim.id.value,
                        "type": claim.type,
                        "assets": (claim.asset_a, claim.asset_b),
                        "final_status": claim.status,
                        "decided_at": decisions[-1] if decisions else None,
                        "endpoint_missing_since": claim.endpoint_missing_since,
                        "purged_at": now,
                        "reason": verdict.purge,
                    }
                )
                del self._repository._items[relationship_id]
            elif verdict.changed:
                self._repository._items[relationship_id] = claim
            return verdict

    def load_state(self) -> ScanState:
        def moment(key: str) -> datetime | None:
            return datetime.fromisoformat(self._state[key]) if key in self._state else None

        return ScanState(
            claim_cursor=self._state.get("claim_cursor"),
            sweep_generation=int(self._state.get("sweep_generation", "0")),
            sweep_started_at=moment("sweep_started_at"),
            last_sweep_completed_at=moment("last_sweep_completed_at"),
            consecutive_failures=int(self._state.get("consecutive_failures", "0")),
            next_attempt_after=moment("next_attempt_after"),
            last_run=self._state.get("last_run"),
        )

    def advance_cursor(self, expected: str | None, new: str, *, now: datetime) -> bool:
        with self._repository._lock:
            if self._state.get("claim_cursor") != expected:
                return False
            self._state["claim_cursor"] = new
            if expected is None:
                self._state["sweep_started_at"] = now.isoformat()
            return True

    def complete_sweep(self, expected: str | None, *, now: datetime) -> bool:
        with self._repository._lock:
            if self._state.get("claim_cursor") != expected:
                return False
            self._state.pop("claim_cursor", None)
            self._state["sweep_generation"] = str(int(self._state.get("sweep_generation", "0")) + 1)
            self._state["last_sweep_completed_at"] = now.isoformat()
            return True

    def record_run(
        self,
        *,
        last_run: str | None,
        consecutive_failures: int,
        next_attempt_after: datetime | None,
        now: datetime,
    ) -> None:
        with self._repository._lock:
            if last_run is not None:
                self._state["last_run"] = last_run
            self._state["consecutive_failures"] = str(consecutive_failures)
            if next_attempt_after is None:
                self._state.pop("next_attempt_after", None)
            else:
                self._state["next_attempt_after"] = next_attempt_after.isoformat()
