"""Domain -> DTO. The only place that knows both shapes."""

from collections.abc import Iterable, Sequence

from media_house.modules.asset_relationships.application.dto import (
    EndpointState,
    EvidenceDto,
    IntervalDto,
    OffsetDto,
    RelationshipDto,
    StatusChangeDto,
)
from media_house.modules.asset_relationships.domain.evidence import RelationshipEvidence
from media_house.modules.asset_relationships.domain.intervals import ClockOffset, TimeInterval
from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.domain.taxonomy import spec_of
from media_house.modules.media_library.application.contracts import AssetStatus, MediaAssetDto


def endpoint_states(
    asset_ids: Iterable[str], found: Sequence[MediaAssetDto]
) -> dict[str, EndpointState]:
    """Active, archived or missing for every id, from what the library returned for them."""
    by_id = {asset.id: asset for asset in found}
    states: dict[str, EndpointState] = {}
    for asset_id in asset_ids:
        asset = by_id.get(asset_id)
        if asset is None:
            states[asset_id] = EndpointState.MISSING
        elif asset.status is AssetStatus.ARCHIVED:
            states[asset_id] = EndpointState.ARCHIVED
        else:
            states[asset_id] = EndpointState.ACTIVE
    return states


def _interval(interval: TimeInterval | None) -> IntervalDto | None:
    if interval is None:
        return None
    return IntervalDto(
        interval.start, interval.end, interval.coordinates.domain, interval.coordinates.origin
    )


def _offset(offset: ClockOffset | None) -> OffsetDto | None:
    if offset is None:
        return None
    return OffsetDto(offset.seconds, offset.uncertainty, offset.origin)


def evidence_dto(item: RelationshipEvidence) -> EvidenceDto:
    return EvidenceDto(
        kind=item.kind,
        provider=item.provider,
        provider_version=item.provider_version,
        observed_at=item.observed_at,
        detail=dict(item.detail),
        offset=_offset(item.offset),
        superseded=item.superseded,
    )


def to_dto(relationship: AssetRelationship, states: dict[str, EndpointState]) -> RelationshipDto:
    return RelationshipDto(
        relationship_id=relationship.id.value,
        type=relationship.type,
        source_asset_id=relationship.asset_a,
        target_asset_id=relationship.asset_b,
        symmetric=spec_of(relationship.type).symmetric,
        status=relationship.status,
        grade=relationship.grade,
        origin=relationship.origin,
        evidence=tuple(evidence_dto(e) for e in relationship.evidence),
        source_interval=_interval(relationship.a_interval),
        target_interval=_interval(relationship.b_interval),
        offset=_offset(relationship.offset),
        basis_detector=relationship.basis.detector,
        basis_version=relationship.basis.version,
        contradicted=relationship.contradicted,
        outdated_since=relationship.outdated_since,
        endpoint_states={
            asset_id: states[asset_id] for asset_id in (relationship.asset_a, relationship.asset_b)
        },
        history=tuple(
            StatusChangeDto(h.status_from, h.status_to, h.at, h.note) for h in relationship.history
        ),
        created_at=relationship.created_at,
        updated_at=relationship.updated_at,
        endpoint_missing_since=relationship.endpoint_missing_since,
    )
