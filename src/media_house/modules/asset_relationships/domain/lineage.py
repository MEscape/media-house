"""Recorded lineage: which derivatives are meaningful media relationships, and what a query covers.

Pure rules over facts the application reads from the library and proxy provenance. Nothing here is
stored: lineage is projected at read time from the authoritative records, so it cannot go stale.
"""

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import StrEnum

from media_house.modules.asset_relationships.domain.taxonomy import LINEAGE_TYPES
from media_house.modules.asset_relationships.domain.values import (
    RelationshipOrigin,
    RelationshipType,
)

#: Version of these rules. It is part of every projected item, so a change in meaning is visible.
LINEAGE_PROJECTION_VERSION = "1"


class LineageSkip(StrEnum):
    """Why a derivative is not reported as a media relationship."""

    NOT_MEDIA = "not_media"  # an analysis, inspection or transcript document
    UNTRUSTED_PROVENANCE = "untrusted_provenance"  # proxy provenance disagrees with the derivation
    PREVIEW = "preview"  # a thumbnail or another display-only artifact
    EXCLUDED_OPERATION = "excluded_operation"  # media, but an internal intermediate


class FindingKind(StrEnum):
    """A problem found in the library's own records while projecting lineage."""

    SOURCE_MISSING = "source_missing"  # a derivative whose source asset cannot be found
    UNTRUSTED_PROVENANCE = "untrusted_provenance"  # proxy provenance disagrees with the derivation


@dataclass(frozen=True, slots=True)
class LineageFinding:
    kind: FindingKind
    #: The derivative the finding is about.
    asset_id: str


@dataclass(frozen=True, slots=True)
class DerivativeFacts:
    """What decides how a library derivative relates to its source."""

    source_asset_id: str
    #: The asset is audio, video or an image (not a document).
    is_media: bool
    #: The library marks it display-only (role ``PREVIEW``).
    is_preview: bool
    operation: str
    #: The source a valid proxy provenance record names; ``None`` when there is none.
    provenance_source_id: str | None


@dataclass(frozen=True, slots=True)
class Classification:
    #: ``None`` when the derivative is not reported; ``skip`` then says why.
    relationship_type: RelationshipType | None
    skip: LineageSkip | None = None


def classify_derivative(
    facts: DerivativeFacts, excluded_operations: AbstractSet[str]
) -> Classification:
    """``proxy_of``, ``derived_from`` or nothing, in a fixed order.

    1. A document is not media. 2. A proxy needs the derivation record AND the provenance to name
    the same source; a provenance record that disagrees is not trusted (asset metadata is editable).
    3. A preview that is not a proxy (a thumbnail) is not lineage. 4. An excluded operation is an
    internal intermediate. 5. Anything else is a derived media asset.
    """
    if not facts.is_media:
        return Classification(None, LineageSkip.NOT_MEDIA)
    if facts.provenance_source_id is not None:
        if facts.provenance_source_id == facts.source_asset_id:
            return Classification(RelationshipType.PROXY_OF)
        return Classification(None, LineageSkip.UNTRUSTED_PROVENANCE)
    if facts.is_preview:
        return Classification(None, LineageSkip.PREVIEW)
    if facts.operation in excluded_operations:
        return Classification(None, LineageSkip.EXCLUDED_OPERATION)
    return Classification(RelationshipType.DERIVED_FROM)


def origin_of(relationship_type: RelationshipType) -> RelationshipOrigin:
    """The authority a projected type comes from."""
    if relationship_type is RelationshipType.PROXY_OF:
        return RelationshipOrigin.PROXY_RECORD
    return RelationshipOrigin.LIBRARY_RECORD


def projection_scope(
    *,
    types: AbstractSet[RelationshipType],
    origins: AbstractSet[RelationshipOrigin],
    statuses_filtered: bool,
    include_projected: bool,
) -> frozenset[RelationshipType]:
    """The lineage types a query asks the projection to answer (empty: no projection runs).

    A recorded fact has no status, so a status filter asks only for claims. Empty ``types`` and
    ``origins`` mean "no restriction".
    """
    if not include_projected or statuses_filtered:
        return frozenset()
    wanted = LINEAGE_TYPES & types if types else LINEAGE_TYPES
    if origins:
        wanted = frozenset(t for t in wanted if origin_of(t) in origins)
    return frozenset(wanted)
