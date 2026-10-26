"""What callers see of a relationship. Domain objects never leave the module."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from fractions import Fraction

from media_house.modules.asset_relationships.domain.lineage import LineageFinding
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
from media_house.shared.errors import ConflictError, NotFoundError, ValidationError

type RelationshipError = ValidationError | NotFoundError | ConflictError


class EndpointState(StrEnum):
    """Where an endpoint stands in the live library, checked on every read."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class IntervalDto:
    """Half-open ``[start, end)`` in exact seconds on the named clock."""

    start: Fraction
    end: Fraction
    domain: ClockDomain
    #: What zero means on that clock.
    origin: str


@dataclass(frozen=True, slots=True)
class OffsetDto:
    """``target_time = source_time + seconds``. ``uncertainty`` of ``None`` means unknown."""

    seconds: Fraction
    uncertainty: Fraction | None
    origin: OffsetOrigin


@dataclass(frozen=True, slots=True)
class EvidenceDto:
    kind: EvidenceKind
    provider: str
    provider_version: str
    observed_at: datetime
    detail: Mapping[str, JsonValue]
    offset: OffsetDto | None
    #: Replaced by newer evidence but kept because a person has decided the claim.
    superseded: bool


@dataclass(frozen=True, slots=True)
class StatusChangeDto:
    status_from: RelationshipStatus | None
    status_to: RelationshipStatus
    at: datetime
    note: str


@dataclass(frozen=True, slots=True)
class RelationshipDto:
    """A claim with the evidence that explains it. Symmetric types list the lower id as source."""

    relationship_id: str
    type: RelationshipType
    source_asset_id: str
    target_asset_id: str
    symmetric: bool
    #: ``None`` for recorded lineage: a fact has no status and cannot be confirmed or rejected.
    status: RelationshipStatus | None
    #: How well the evidence supports the claim for its type. Ordinal, not a probability.
    grade: Grade
    origin: RelationshipOrigin
    evidence: tuple[EvidenceDto, ...]
    source_interval: IntervalDto | None
    target_interval: IntervalDto | None
    offset: OffsetDto | None
    #: The detector (or ``manual``) and version the claim rests on.
    basis_detector: str
    basis_version: str
    #: Evidence of defined kinds disagrees with the claim or with other evidence.
    contradicted: bool
    #: A decided claim whose evidence or basis changed since the decision; the status is unchanged.
    outdated_since: datetime | None
    endpoint_states: Mapping[str, EndpointState]
    history: tuple[StatusChangeDto, ...]
    created_at: datetime
    updated_at: datetime
    #: ``True``: a claim stored here. ``False``: recorded lineage projected from its authority at
    #: read time (library derivation or proxy provenance); its ``origin`` says which.
    stored: bool = True
    #: Proxies only. ``True``: the proxy its slot serves now. ``False``: kept (for example as the
    #: rollback of a replaced proxy) but not served. ``None``: not a proxy, or proxy_manager's
    #: catalog is not available to say.
    serving: bool | None = None
    #: When reconciliation first found an endpoint gone from the library; ``None``: healthy as far
    #: as the last sweep knows (reads show the live ``endpoint_states`` too). Claims only.
    endpoint_missing_since: datetime | None = None


@dataclass(frozen=True, slots=True)
class RelationshipPage:
    items: tuple[RelationshipDto, ...]
    #: Pass as the next query's ``cursor``; ``None`` on the last page.
    next_cursor: str | None = None
    #: What this answer cannot promise; absence in ``items`` is not proof when this is non-empty.
    limitations: tuple[Limitation, ...] = ()
    #: Recorded lineage was cut off at the scan cap, so some derivatives may be missing.
    truncated: bool = False
    #: Problems in the library's own records found while projecting lineage (at most 20 listed).
    findings: tuple[LineageFinding, ...] = ()
    #: Findings beyond the 20 listed.
    findings_omitted: int = 0
