"""The rules of reconciliation, as pure functions over a claim, and the port that stores its state.

Reconciliation keeps STORED claims truthful against the live library: it notices endpoints that are
gone, applies the grace and retention periods, and marks claims whose basis is out of date. It never
changes what a person decided, and it never touches recorded lineage (projected, not stored).
"""

from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.domain.values import RelationshipStatus


class PurgeReason(StrEnum):
    UNDECIDED_ORPHAN = "undecided_orphan"
    RETENTION_EXPIRED = "retention_expired"


@dataclass(frozen=True, slots=True)
class ReconcilePolicy:
    """How long a claim whose endpoint is gone is kept."""

    #: Proposals (``PROPOSED``, ``STALE``) have no human value; a short grace covers a library that
    #: was restored from a backup or briefly pointed elsewhere.
    undecided_orphan_grace: timedelta
    #: A decision is a person's work and is kept much longer.
    decided_retention: timedelta


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    """What one evaluation did to a claim, so a job can count it."""

    marked_missing: bool = False
    restored: bool = False
    marked_stale: bool = False
    marked_outdated: bool = False
    #: Set when the claim must now be deleted (with an audit record). Nothing was changed on it.
    purge: PurgeReason | None = None

    @property
    def changed(self) -> bool:
        return self.marked_missing or self.restored or self.marked_stale or self.marked_outdated


def reconcile_claim(
    claim: AssetRelationship,
    *,
    absent: AbstractSet[str],
    basis_current: bool | None,
    now: datetime,
    policy: ReconcilePolicy,
) -> ClaimVerdict:
    """Apply the reconciliation rules to ``claim`` as it is NOW and report what happened.

    ``absent`` holds the asset ids the library answered "not found" for in this batch. A library
    failure never reaches this function: it is a failure, not an absence. ``basis_current`` is
    ``False`` when a detector says the claim's basis is out of date, ``None`` when none knows it.

    A claim is purged only if it was ALREADY marked missing before this evaluation, so the sweep
    that first sees an absence never deletes anything. Whether the claim is decided is read from the
    claim itself, so a person's decision made after the batch was read is respected.
    """
    missing_now = claim.asset_a in absent or claim.asset_b in absent
    had_marker = claim.endpoint_missing_since
    if missing_now:
        if had_marker is not None:
            waited = now - had_marker
            limit = policy.decided_retention if claim.decided else policy.undecided_orphan_grace
            if waited >= limit:
                return ClaimVerdict(
                    purge=(
                        PurgeReason.RETENTION_EXPIRED
                        if claim.decided
                        else PurgeReason.UNDECIDED_ORPHAN
                    )
                )
        marked_missing = claim.mark_endpoint_missing(now=now)
        restored = False
    else:
        marked_missing = False
        restored = claim.clear_endpoint_missing(now=now)

    marked_stale = marked_outdated = False
    if basis_current is False:
        if claim.status is RelationshipStatus.PROPOSED:
            claim.mark_stale(now=now)
            marked_stale = True
        elif claim.decided:
            marked_outdated = claim.mark_outdated(now=now)
    return ClaimVerdict(marked_missing, restored, marked_stale, marked_outdated)


def looks_emptied(
    *, claims: int, absent_endpoints: int, endpoints: int, minimum: int, ratio: float
) -> bool:
    """Whether a batch looks like the library lost its data, not a few assets being deleted."""
    return claims >= minimum and endpoints > 0 and absent_endpoints / endpoints >= ratio


# --------------------------------------------------------------------------- the store port
@dataclass(frozen=True, slots=True)
class ClaimRef:
    """A claim as the sweep first sees it: enough to look its endpoints up, nothing hydrated.

    ``marked`` and ``basis`` let the job skip a claim that needs no attention without opening a
    transaction for it. They are only a hint: anything acted on is judged again on the stored claim.
    """

    relationship_id: str
    asset_a: str
    asset_b: str
    #: The claim already carries ``endpoint_missing_since``.
    marked: bool = False
    #: The claim's basis as stored (canonical text); empty when unknown.
    basis: str = ""


@dataclass(frozen=True, slots=True)
class ScanState:
    """The resumable state of the sweep (``scan_state`` rows)."""

    #: The last claim id fully handled in the current sweep; ``None`` between sweeps.
    claim_cursor: str | None = None
    sweep_generation: int = 0
    sweep_started_at: datetime | None = None
    last_sweep_completed_at: datetime | None = None
    consecutive_failures: int = 0
    next_attempt_after: datetime | None = None
    #: The last real run, as the text the application serialised (opaque to the store).
    last_run: str | None = None


class ReconciliationStore(Protocol):
    """Everything reconciliation needs from the module's own database."""

    def claims_after(self, cursor: str | None, limit: int) -> Sequence[ClaimRef]:
        """Up to ``limit`` claims whose id sorts after ``cursor``, in id order (a keyset)."""
        ...

    def reconcile_claim(
        self,
        relationship_id: str,
        decide: Callable[[AssetRelationship], ClaimVerdict],
        *,
        now: datetime,
    ) -> ClaimVerdict | None:
        """Load the claim, let ``decide`` evaluate (and change) it, and save: one transaction.

        ``decide`` sees the claim as it is stored at that moment. If it returns a purge verdict the
        claim is deleted and one ``purge_log`` row is written in the same transaction; otherwise a
        changed claim is saved. Returns ``None`` when the claim no longer exists. An unreadable
        claim raises ``CorruptClaim``.
        """
        ...

    def load_state(self) -> ScanState: ...

    def advance_cursor(self, expected: str | None, new: str, *, now: datetime) -> bool:
        """Move the cursor from ``expected`` to ``new``; ``False`` if another run moved it first."""
        ...

    def complete_sweep(self, expected: str | None, *, now: datetime) -> bool:
        """End the sweep: next generation, no cursor, completion time. ``False`` if it moved."""
        ...

    def record_run(
        self,
        *,
        last_run: str | None,
        consecutive_failures: int,
        next_attempt_after: datetime | None,
        now: datetime,
    ) -> None:
        """Remember the outcome of a run, the failure count and when the next attempt may start."""
        ...
