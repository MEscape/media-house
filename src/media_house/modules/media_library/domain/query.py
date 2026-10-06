"""Search criteria and pagination: the question side of the repository ports."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from media_house.modules.media_library.domain.values import (
    AssetRole,
    AssetStatus,
    MediaType,
    SourceType,
    Tag,
)
from media_house.shared.errors import InvariantViolation


class SortField(StrEnum):
    """The only orderings callers may request (mapped to columns by the adapter)."""

    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    NAME = "name"
    FILE_SIZE = "file_size"
    DURATION = "duration"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True, slots=True)
class MediaQuery:
    """Filter + sort + page. Empty collections mean "no restriction".

    Defaults hide archived assets and previews, which is what a browsing UI wants.
    ``tags`` requires *all* listed tags; the other collections match *any* member.
    """

    MAX_PAGE_SIZE: ClassVar[int] = 200

    text: str | None = None
    media_types: frozenset[MediaType] = frozenset()
    group_ids: frozenset[str] = frozenset()
    include_subgroups: bool = True
    tags: frozenset[str] = frozenset()
    created_from: datetime | None = None
    created_to: datetime | None = None
    favorite: bool | None = None
    statuses: frozenset[AssetStatus] = frozenset({AssetStatus.ACTIVE})
    roles: frozenset[AssetRole] = frozenset({AssetRole.ORIGINAL, AssetRole.DERIVED})
    source_types: frozenset[SourceType] = frozenset()
    source_asset_id: str | None = None
    sort_by: SortField = SortField.CREATED_AT
    sort_order: SortOrder = SortOrder.DESC
    page: int = 1
    page_size: int = 50

    def __post_init__(self) -> None:
        if self.page < 1:
            raise InvariantViolation("page must be >= 1", user_message="Invalid page number.")
        if not 1 <= self.page_size <= self.MAX_PAGE_SIZE:
            raise InvariantViolation(
                f"page_size must be between 1 and {self.MAX_PAGE_SIZE}",
                user_message="Invalid page size.",
            )
        normalised = frozenset(Tag.of(t).value for t in self.tags)
        object.__setattr__(self, "tags", normalised)
        if self.text is not None:
            text = self.text.strip().casefold()
            object.__setattr__(self, "text", text or None)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of results plus the total number of matches."""

    items: Sequence[T]
    total: int
    page: int
    page_size: int

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total


@dataclass(frozen=True, slots=True)
class TagUsage:
    name: str
    count: int
