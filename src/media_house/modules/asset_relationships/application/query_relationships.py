"""Use case: read what is known about an asset - stored claims and recorded lineage together.

Stored claims come from this module's database; recorded lineage is projected at read time from the
library and proxy provenance. Both are ordered by relationship id (a stable, deterministic
order), so one cursor pages through the merged answer, and ``origin`` / ``stored`` always say
which is which.
"""

from media_house.modules.asset_relationships.application import cursor as page_cursor
from media_house.modules.asset_relationships.application.commands import RelationshipQuery
from media_house.modules.asset_relationships.application.dto import (
    RelationshipDto,
    RelationshipPage,
)
from media_house.modules.asset_relationships.application.lineage_projection import (
    LineageProjection,
)
from media_house.modules.asset_relationships.application.mapping import endpoint_states, to_dto
from media_house.modules.asset_relationships.application.ports import AssetLookup
from media_house.modules.asset_relationships.domain.errors import RelationshipNotFound
from media_house.modules.asset_relationships.domain.limitations import (
    exact_duplicate_limitations,
)
from media_house.modules.asset_relationships.domain.lineage import projection_scope
from media_house.modules.asset_relationships.domain.relationship import AssetRelationship
from media_house.modules.asset_relationships.domain.repository import (
    RelationshipCriteria,
    RelationshipRepository,
)
from media_house.modules.asset_relationships.domain.taxonomy import STORED_TYPES
from media_house.modules.asset_relationships.domain.values import (
    STORABLE_ORIGINS,
    Limitation,
    RelationshipId,
    grades_at_least,
)
from media_house.modules.media_library.application.contracts import AssetRole
from media_house.shared.errors import Err, NotFoundError, Ok, Result, ValidationError

#: Findings listed on a page; the rest are only counted.
MAX_FINDINGS = 20


class QueryRelationships:
    """Read-only. Endpoint state is checked against the live library on every read."""

    def __init__(
        self,
        repository: RelationshipRepository,
        library: AssetLookup,
        projection: LineageProjection,
    ) -> None:
        self._repository = repository
        self._library = library
        self._projection = projection

    def relationships_of(
        self, asset_id: str, query: RelationshipQuery | None = None
    ) -> Result[RelationshipPage, ValidationError | NotFoundError]:
        query = query or RelationshipQuery()
        asset = self._library.get(asset_id)
        if isinstance(asset, Err):
            return Err(_not_found_or_invalid(asset.error))
        if not 1 <= query.page_size <= RelationshipQuery.MAX_PAGE_SIZE:
            return Err(
                ValidationError(
                    f"page_size must be between 1 and {RelationshipQuery.MAX_PAGE_SIZE}",
                    field="page_size",
                )
            )
        scope = projection_scope(
            types=query.types,
            origins=query.origins,
            statuses_filtered=bool(query.statuses),
            include_projected=query.include_projected,
        )
        after, cursor_total = page_cursor.decode(query.cursor)
        stored = self._stored(asset_id, query, after)
        projected: list[RelationshipDto] = []
        lineage = None
        if scope:
            lineage = self._projection.project(
                asset.value, scope, include_archived=query.include_archived
            )
            projected = [p for p in lineage.items if after is None or p.relationship_id > after]

        # one deterministic order over both: the relationship id
        merged: list[AssetRelationship | RelationshipDto] = sorted(
            [*stored, *projected], key=_id_of
        )
        page = merged[: query.page_size]
        scan_total = lineage.scan_total if lineage is not None else None
        next_cursor = (
            page_cursor.encode(_id_of(page[-1]), scan_total)
            if len(merged) > query.page_size
            else None
        )
        limitations = exact_duplicate_limitations(
            queried_is_original=asset.value.role is AssetRole.ORIGINAL,
            types_in_scope=scope,
        )
        if lineage is not None and lineage.proxy_state_unavailable:
            limitations = (*limitations, Limitation.PROXY_STATE_UNAVAILABLE)
        if cursor_total is not None and scan_total is not None and cursor_total != scan_total:
            limitations = (*limitations, Limitation.LINEAGE_CHANGED_WHILE_PAGING)
        findings = lineage.findings if lineage is not None else ()
        return Ok(
            RelationshipPage(
                items=self._dtos(page),
                next_cursor=next_cursor,
                limitations=limitations,
                truncated=lineage.truncated if lineage is not None else False,
                findings=findings[:MAX_FINDINGS],
                findings_omitted=max(0, len(findings) - MAX_FINDINGS),
            )
        )

    def get(self, relationship_id: str) -> Result[RelationshipDto, NotFoundError]:
        """A stored claim by id. Recorded lineage is not stored: ask about an asset to get it."""
        stored = self._repository.get(RelationshipId(relationship_id)) if relationship_id else None
        if stored is None:
            return Err(RelationshipNotFound(relationship_id))
        return Ok(self._dtos([stored])[0])

    # ------------------------------------------------------------------ helpers
    def _stored(
        self, asset_id: str, query: RelationshipQuery, after: str | None
    ) -> list[AssetRelationship]:
        """The stored claims the query asks for (none when it asks only for lineage)."""
        types = query.types & STORED_TYPES if query.types else frozenset()
        origins = query.origins & STORABLE_ORIGINS if query.origins else frozenset()
        if (query.types and not types) or (query.origins and not origins):
            return []
        return list(
            self._repository.find(
                RelationshipCriteria(
                    asset_id=asset_id,
                    types=types,
                    statuses=query.statuses,
                    grades=grades_at_least(query.min_grade) if query.min_grade else frozenset(),
                    origins=origins,
                    after=after,
                    limit=query.page_size + 1,
                )
            )
        )

    def _dtos(self, page: list[AssetRelationship | RelationshipDto]) -> tuple[RelationshipDto, ...]:
        """DTOs in page order; stored claims get endpoint state from one library call."""
        claims = [item for item in page if isinstance(item, AssetRelationship)]
        ends = sorted({a for r in claims for a in (r.asset_a, r.asset_b)})
        states = endpoint_states(ends, self._library.get_many(ends) if ends else [])
        return tuple(
            to_dto(item, states) if isinstance(item, AssetRelationship) else item for item in page
        )


def _id_of(item: AssetRelationship | RelationshipDto) -> str:
    return item.id.value if isinstance(item, AssetRelationship) else item.relationship_id


def _not_found_or_invalid(error: object) -> ValidationError | NotFoundError:
    if isinstance(error, NotFoundError | ValidationError):
        return error
    return NotFoundError(str(error))
