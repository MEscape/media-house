"""The MediaGroup aggregate: a logical collection of assets, optionally nested."""

from datetime import datetime

from media_house.core.domain import AggregateRoot
from media_house.modules.media_library.domain.events import MediaGroupCreated
from media_house.modules.media_library.domain.values import GroupName, MediaGroupId


class MediaGroup(AggregateRoot):
    """A user- or module-defined collection. Membership never duplicates files."""

    MAX_DESCRIPTION = 1000

    def __init__(
        self,
        *,
        id: MediaGroupId,  # noqa: A002
        name: GroupName,
        created_at: datetime,
        updated_at: datetime,
        parent_id: MediaGroupId | None = None,
        description: str = "",
    ) -> None:
        super().__init__()
        self.id = id
        self.name = name
        self.parent_id = parent_id
        self.description = description
        self.created_at = created_at
        self.updated_at = updated_at

    @classmethod
    def create(
        cls,
        name: GroupName,
        *,
        now: datetime,
        parent_id: MediaGroupId | None = None,
        description: str = "",
    ) -> "MediaGroup":
        group = cls(
            id=MediaGroupId.new(),
            name=name,
            parent_id=parent_id,
            description=description.strip()[: cls.MAX_DESCRIPTION],
            created_at=now,
            updated_at=now,
        )
        group._record(
            MediaGroupCreated(occurred_at=now, group_id=group.id.value, name=name.value),
        )
        return group

    def rename(self, name: GroupName, *, now: datetime) -> None:
        if name != self.name:
            self.name = name
            self.updated_at = now
