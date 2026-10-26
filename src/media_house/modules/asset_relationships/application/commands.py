"""Inputs of the use cases."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.asset_relationships.domain.intervals import ClockOffset, TimeInterval
from media_house.modules.asset_relationships.domain.values import (
    EvidenceKind,
    Grade,
    JsonValue,
    RelationshipOrigin,
    RelationshipStatus,
    RelationshipType,
)


@dataclass(frozen=True, slots=True)
class EvidenceSubmission:
    """One piece of evidence for a claim; the observation time is taken from the clock."""

    kind: EvidenceKind
    provider: str
    provider_version: str
    detail: Mapping[str, JsonValue] = field(default_factory=dict)
    offset: ClockOffset | None = None


@dataclass(frozen=True, slots=True)
class ProposeRelationshipCommand:
    """Assert that two assets are related. Detectors and people both use it.

    Asserting an existing pair again refreshes its evidence; it never overwrites a decision.
    ``asset_a`` and ``asset_b`` may be given in either order for a symmetric type: intervals follow
    their asset and the offset maps time in ``asset_a`` to time in ``asset_b``.
    """

    relationship_type: RelationshipType
    asset_a: str
    asset_b: str
    origin: RelationshipOrigin
    evidence: tuple[EvidenceSubmission, ...]
    basis: Basis = field(default_factory=Basis.manual)
    a_interval: TimeInterval | None = None
    b_interval: TimeInterval | None = None
    offset: ClockOffset | None = None


class Decision(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class DecideRelationshipCommand:
    relationship_id: str
    decision: Decision
    note: str = ""


@dataclass(frozen=True, slots=True)
class RelationshipQuery:
    """Filter and page what is known about one asset. Empty collections mean "no restriction".

    Stored claims and recorded lineage (projected at read time) are answered together, in id order.
    A status filter asks for claims only: recorded lineage has no status. ``origins`` tells them
    apart: ``detector`` and ``user`` are claims; ``library_record`` and ``proxy_record`` are
    lineage.
    """

    MAX_PAGE_SIZE: ClassVar[int] = 200

    types: frozenset[RelationshipType] = frozenset()
    statuses: frozenset[RelationshipStatus] = frozenset()
    #: This grade or a stronger one.
    min_grade: Grade | None = None
    origins: frozenset[RelationshipOrigin] = frozenset()
    #: The previous page's ``next_cursor``.
    cursor: str | None = None
    page_size: int = 50
    #: Answer recorded lineage (``exact_duplicate_of``, ``proxy_of``, ``derived_from``) as well.
    include_projected: bool = True
    #: Show recorded lineage whose OTHER end is archived. It does not touch stored claims, which
    #: always come back with ``endpoint_states``.
    include_archived: bool = False
