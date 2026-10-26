"""The stored claim and its lifecycle.

``ClaimInput`` is what a detector or a person asserts, validated and put in canonical order.
``AssetRelationship`` is the stored claim: evidence, grade, status and its status history.

Rules that matter:

* A claim is a proposal until a person confirms it; no stored type is confirmed by automation.
* A decision (``CONFIRMED`` or ``REJECTED``) is never changed by a refresh. A refresh may add or
  supersede evidence and mark the claim ``outdated_since``; the status stays.
* Repeating the same inputs changes nothing, ``updated_at`` included.
"""

from dataclasses import dataclass
from datetime import datetime

from media_house.modules.asset_relationships.domain.assessment import assess
from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.errors import IllegalTransition
from media_house.modules.asset_relationships.domain.evidence import RelationshipEvidence
from media_house.modules.asset_relationships.domain.intervals import ClockOffset, TimeInterval
from media_house.modules.asset_relationships.domain.taxonomy import canonical_endpoints, spec_of
from media_house.modules.asset_relationships.domain.values import (
    STORABLE_ORIGINS,
    Grade,
    RelationshipId,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)
from media_house.shared.errors import InvariantViolation

_DECIDED = frozenset({RelationshipStatus.CONFIRMED, RelationshipStatus.REJECTED})


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One status change: what it was, what it became, when, why, and what evidence stood."""

    status_from: RelationshipStatus | None
    status_to: RelationshipStatus
    at: datetime
    note: str
    evidence_snapshot: tuple[str, ...]


def _check_intervals(
    first: TimeInterval | None, second: TimeInterval | None, offset: ClockOffset | None
) -> None:
    """Intervals of an overlap claim must overlap, as far as that can be established."""
    if first is None or second is None:
        return
    if offset is not None:
        if offset.uncertainty is None:
            return  # an unknown uncertainty cannot be used to refute anything
        moved = first.shifted(offset.seconds, second.coordinates).widened(offset.uncertainty)
        compared = moved.overlaps(second)
    elif first.coordinates == second.coordinates:
        compared = first.overlaps(second)
    else:
        return  # different clocks and no offset: nothing can be compared
    if not compared:
        raise InvariantViolation(
            "The intervals of an overlap claim do not overlap",
            user_message="The two time ranges do not overlap.",
        )


@dataclass(frozen=True, slots=True)
class ClaimInput:
    """A validated assertion in canonical order, ready to create or refresh a relationship."""

    type: RelationshipType
    asset_a: str
    asset_b: str
    origin: RelationshipOrigin
    basis: Basis
    evidence: tuple[RelationshipEvidence, ...]
    a_interval: TimeInterval | None = None
    b_interval: TimeInterval | None = None
    #: Maps time in ``asset_a`` to time in ``asset_b``.
    offset: ClockOffset | None = None

    @classmethod
    def of(
        cls,
        relationship_type: RelationshipType,
        first: str,
        second: str,
        *,
        origin: RelationshipOrigin,
        basis: Basis,
        evidence: tuple[RelationshipEvidence, ...],
        first_interval: TimeInterval | None = None,
        second_interval: TimeInterval | None = None,
        offset: ClockOffset | None = None,
    ) -> "ClaimInput":
        """Validate, then order the endpoints (intervals follow them; an offset is inverted)."""
        spec = spec_of(relationship_type)
        if not spec.storable:
            raise InvariantViolation(
                f"{relationship_type.value} is recorded lineage and cannot be stored",
                user_message="That relationship comes from the library's own records.",
            )
        if origin not in STORABLE_ORIGINS:
            raise InvariantViolation(f"A stored claim cannot have origin {origin.value}")
        if not evidence:
            raise InvariantViolation(
                "A claim needs evidence", user_message="Say why the assets are related."
            )
        for item in evidence:
            if item.kind not in spec.admissible:
                raise InvariantViolation(
                    f"{item.kind.value} is not admissible evidence for {relationship_type.value}",
                    user_message="That kind of evidence cannot support this relationship.",
                )
        if not spec.intervals and (first_interval or second_interval or offset):
            raise InvariantViolation(f"{relationship_type.value} has no time ranges")
        a, b, swapped = canonical_endpoints(relationship_type, first, second)
        a_interval, b_interval = (
            (second_interval, first_interval) if swapped else (first_interval, second_interval)
        )
        if swapped and offset is not None:
            offset = offset.inverted()
        _check_intervals(a_interval, b_interval, offset)
        # fail now, not at the first assessment, if the evidence cannot be graded
        assess(relationship_type, evidence)
        return cls(relationship_type, a, b, origin, basis, evidence, a_interval, b_interval, offset)

    @property
    def relationship_id(self) -> RelationshipId:
        return RelationshipId.of(self.type, self.asset_a, self.asset_b)


def _ordered(evidence: tuple[RelationshipEvidence, ...]) -> tuple[RelationshipEvidence, ...]:
    return tuple(
        sorted(evidence, key=lambda e: (e.superseded, e.kind.value, e.provider, e.provider_version))
    )


class AssetRelationship:
    """A stored claim about two assets. Endpoints are library asset ids, never paths or hashes."""

    def __init__(
        self,
        *,
        id: RelationshipId,  # noqa: A002
        type: RelationshipType,  # noqa: A002
        asset_a: str,
        asset_b: str,
        status: RelationshipStatus,
        grade: Grade,
        origin: RelationshipOrigin,
        basis: Basis,
        evidence: tuple[RelationshipEvidence, ...],
        history: tuple[DecisionRecord, ...],
        created_at: datetime,
        updated_at: datetime,
        a_interval: TimeInterval | None = None,
        b_interval: TimeInterval | None = None,
        offset: ClockOffset | None = None,
        contradicted: bool = False,
        outdated_since: datetime | None = None,
        endpoint_missing_since: datetime | None = None,
    ) -> None:
        spec = spec_of(type)
        if not spec.storable:
            raise InvariantViolation(f"{type.value} cannot be stored")
        if canonical_endpoints(type, asset_a, asset_b) != (asset_a, asset_b, False):
            raise InvariantViolation("Relationship endpoints are not in canonical order")
        if id != RelationshipId.of(type, asset_a, asset_b):
            raise InvariantViolation("Relationship id does not match its type and endpoints")
        self.id = id
        self.type = type
        self.asset_a = asset_a
        self.asset_b = asset_b
        self.status = status
        self.grade = grade
        self.origin = origin
        self.basis = basis
        self.evidence = _ordered(evidence)
        self.history = history
        self.created_at = created_at
        self.updated_at = updated_at
        self.a_interval = a_interval
        self.b_interval = b_interval
        self.offset = offset
        self.contradicted = contradicted
        self.outdated_since = outdated_since
        #: When reconciliation first found an endpoint gone from the library; ``None`` = healthy.
        #: Not a status: it never changes what a person decided.
        self.endpoint_missing_since = endpoint_missing_since

    # ------------------------------------------------------------------ creation
    @classmethod
    def create(cls, claim: ClaimInput, *, now: datetime) -> "AssetRelationship":
        assessment = assess(claim.type, claim.evidence)
        return cls(
            id=claim.relationship_id,
            type=claim.type,
            asset_a=claim.asset_a,
            asset_b=claim.asset_b,
            status=RelationshipStatus.PROPOSED,
            grade=assessment.grade,
            origin=claim.origin,
            basis=claim.basis,
            evidence=claim.evidence,
            history=(
                DecisionRecord(
                    None,
                    RelationshipStatus.PROPOSED,
                    now,
                    "proposed",
                    tuple(e.key for e in claim.evidence),
                ),
            ),
            created_at=now,
            updated_at=now,
            a_interval=claim.a_interval,
            b_interval=claim.b_interval,
            offset=claim.offset,
            contradicted=assessment.contradicted,
        )

    # ------------------------------------------------------------------ queries
    @property
    def decided(self) -> bool:
        return self.status in _DECIDED

    @property
    def current_evidence(self) -> tuple[RelationshipEvidence, ...]:
        return tuple(e for e in self.evidence if not e.superseded)

    # ------------------------------------------------------------------ refresh
    def refresh(self, claim: ClaimInput, *, now: datetime) -> bool:
        """Bring the claim up to date with a new assertion of the same pair and type.

        Returns whether anything changed (``updated_at`` moves only then). A decided claim keeps its
        status, intervals and offset; new or differing evidence is added and the claim is marked
        ``outdated_since`` so a person can look again.
        """
        if claim.relationship_id != self.id:
            raise InvariantViolation("A claim can only refresh the relationship it describes")
        before = self._state()
        if self.decided:
            self._refresh_decided(claim, now)
        else:
            self._refresh_undecided(claim, now)
        changed = self._state() != before
        if changed:
            self.updated_at = now
        return changed

    def _state(self) -> tuple[object, ...]:
        return (
            self.status,
            self.grade,
            self.contradicted,
            self.basis,
            self.evidence,
            self.a_interval,
            self.b_interval,
            self.offset,
            self.outdated_since,
            self.endpoint_missing_since,
            len(self.history),
        )

    def _refresh_undecided(self, claim: ClaimInput, now: datetime) -> None:
        providers = {e.provider for e in claim.evidence}
        kept = [e for e in self.current_evidence if e.provider not in providers]
        for item in claim.evidence:
            existing = next((e for e in self.current_evidence if e.same_content(item)), None)
            kept.append(existing or item)
        self._set_evidence(tuple(kept))
        self.basis = claim.basis
        self.a_interval, self.b_interval, self.offset = (
            claim.a_interval,
            claim.b_interval,
            claim.offset,
        )
        if self.status is RelationshipStatus.STALE:
            self._record(RelationshipStatus.PROPOSED, now, "re-detected")

    def _refresh_decided(self, claim: ClaimInput, now: datetime) -> None:
        evidence = list(self.evidence)
        differs = False
        for item in claim.evidence:
            same_slot = [
                e
                for e in evidence
                if not e.superseded and e.kind is item.kind and e.provider == item.provider
            ]
            if any(e.same_content(item) for e in same_slot):
                continue
            evidence = [e.as_superseded() if e in same_slot else e for e in evidence]
            evidence.append(item)
            differs = True
        self._set_evidence(tuple(evidence))
        if (
            differs
            or claim.basis != self.basis
            or (claim.a_interval, claim.b_interval, claim.offset)
            != (self.a_interval, self.b_interval, self.offset)
        ) and self.outdated_since is None:
            self.outdated_since = now
        self.basis = claim.basis

    def _set_evidence(self, evidence: tuple[RelationshipEvidence, ...]) -> None:
        self.evidence = _ordered(evidence)
        assessment = assess(self.type, self.evidence)
        self.grade = assessment.grade
        self.contradicted = assessment.contradicted

    # ------------------------------------------------------------------ status
    def mark_stale(self, *, now: datetime) -> None:
        """A proposal whose basis is out of date. Decided claims are never made stale."""
        if self.status is not RelationshipStatus.PROPOSED:
            raise IllegalTransition(
                f"Only a proposed relationship can become stale, not {self.status.value}"
            )
        self._record(RelationshipStatus.STALE, now, "basis out of date")
        self.updated_at = now

    def mark_outdated(self, *, now: datetime) -> bool:
        """A decided claim whose basis is out of date. The status is not touched.

        Returns whether anything changed. Only a decided claim can be outdated: a proposal is made
        ``STALE`` instead.
        """
        if not self.decided:
            raise IllegalTransition(
                f"Only a decided relationship can be outdated, not {self.status.value}"
            )
        if self.outdated_since is not None:
            return False
        self.outdated_since = now
        self.updated_at = now
        return True

    # ------------------------------------------------------------------ endpoint health
    def mark_endpoint_missing(self, *, now: datetime) -> bool:
        """Remember the FIRST time an endpoint was found gone (retention counts from it)."""
        if self.endpoint_missing_since is not None:
            return False
        self.endpoint_missing_since = now
        self.updated_at = now
        return True

    def clear_endpoint_missing(self, *, now: datetime) -> bool:
        """Every endpoint is present again."""
        if self.endpoint_missing_since is None:
            return False
        self.endpoint_missing_since = None
        self.updated_at = now
        return True

    def confirm(self, *, now: datetime, note: str = "") -> None:
        if self.status not in {RelationshipStatus.PROPOSED, RelationshipStatus.REJECTED}:
            raise IllegalTransition(
                f"A {self.status.value} relationship cannot be confirmed",
                user_message="This relationship cannot be confirmed in its current state.",
            )
        self._decide(RelationshipStatus.CONFIRMED, now, note)

    def reject(self, *, now: datetime, note: str = "") -> None:
        if self.status is RelationshipStatus.REJECTED:
            raise IllegalTransition(
                "The relationship is already rejected",
                user_message="This relationship is already rejected.",
            )
        self._decide(RelationshipStatus.REJECTED, now, note)

    def _decide(self, status: RelationshipStatus, now: datetime, note: str) -> None:
        self._record(status, now, note.strip())
        self.outdated_since = None
        self.updated_at = now

    def _record(self, status: RelationshipStatus, now: datetime, note: str) -> None:
        self.history = (
            *self.history,
            DecisionRecord(
                self.status, status, now, note, tuple(e.key for e in self.current_evidence)
            ),
        )
        self.status = status
