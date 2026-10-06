"""Use cases for groups (collections) and group membership."""

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.dto import MediaGroupDto
from media_house.modules.media_library.application.mapping import to_group_dto
from media_house.modules.media_library.domain.errors import (
    GroupNameTaken,
    GroupNotFound,
    MediaNotFound,
)
from media_house.modules.media_library.domain.media_group import MediaGroup
from media_house.modules.media_library.domain.repository import (
    MediaAssetRepository,
    MediaGroupRepository,
)
from media_house.modules.media_library.domain.values import GroupName, MediaAssetId, MediaGroupId
from media_house.shared.errors import (
    ConflictError,
    DomainError,
    Err,
    NotFoundError,
    Ok,
    Result,
    ValidationError,
)
from media_house.shared.events import EventPublisher
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type _GroupError = ValidationError | NotFoundError | ConflictError


class ManageGroups:
    """Create, rename, delete groups and (un)link assets.

    Removing an asset from a group, or deleting a group, never deletes assets or files.
    """

    def __init__(
        self,
        groups: MediaGroupRepository,
        assets: MediaAssetRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._groups = groups
        self._assets = assets
        self._clock = clock
        self._events = events

    def create(
        self,
        name: str,
        *,
        parent_id: str | None = None,
        description: str = "",
    ) -> Result[MediaGroupDto, _GroupError]:
        try:
            group_name = GroupName.of(name)
            parent = MediaGroupId(parent_id) if parent_id else None
        except DomainError as exc:
            return Err(ValidationError(str(exc), field="name", user_message=exc.user_message))
        if parent is not None and self._groups.get(parent) is None:
            return Err(GroupNotFound(parent.value))
        group = MediaGroup.create(
            group_name,
            now=self._clock.now(),
            parent_id=parent,
            description=description,
        )
        try:
            self._groups.add(group)
        except GroupNameTaken as exc:
            return Err(exc)
        for event in group.pull_events():
            self._events.publish(event)
        _log.info("Group created", group_id=group.id.value)
        return Ok(to_group_dto(group, 0))

    def rename(self, group_id: str, name: str) -> Result[MediaGroupDto, _GroupError]:
        try:
            group_name = GroupName.of(name)
        except DomainError as exc:
            return Err(ValidationError(str(exc), field="name", user_message=exc.user_message))
        group = self._groups.get(MediaGroupId(group_id)) if group_id else None
        if group is None:
            return Err(GroupNotFound(group_id))
        group.rename(group_name, now=self._clock.now())
        try:
            self._groups.save(group)
        except GroupNameTaken as exc:
            return Err(exc)
        return Ok(to_group_dto(group, self._groups.member_counts().get(group.id.value, 0)))

    def delete(self, group_id: str) -> Result[None, NotFoundError | ConflictError]:
        """Delete an empty-of-children group. Member assets stay in the library."""
        gid = MediaGroupId(group_id) if group_id else None
        if gid is None or self._groups.get(gid) is None:
            return Err(GroupNotFound(group_id))
        if self._groups.has_children(gid):
            return Err(
                ConflictError(
                    f"Group {group_id} has child groups",
                    user_message="Delete or move the sub-groups first.",
                ),
            )
        self._groups.delete(gid)
        _log.info("Group deleted", group_id=group_id)
        return Ok(None)

    def list_groups(self) -> list[MediaGroupDto]:
        counts = self._groups.member_counts()
        return [to_group_dto(g, counts.get(g.id.value, 0)) for g in self._groups.list_all()]

    def add_asset(self, group_id: str, asset_id: str) -> Result[bool, NotFoundError]:
        """Link an asset to a group (idempotent). ``Ok(True)`` when the link is new."""
        resolved = self._resolve(group_id, asset_id)
        if isinstance(resolved, Err):
            return resolved
        gid, aid = resolved.value
        added = self._groups.add_member(gid, aid, now=self._clock.now())
        if added:
            _log.info("Asset linked to group", asset_id=asset_id, group_id=group_id)
        return Ok(added)

    def remove_asset(self, group_id: str, asset_id: str) -> Result[bool, NotFoundError]:
        """Unlink an asset from a group. The asset and its file are left untouched."""
        resolved = self._resolve(group_id, asset_id)
        if isinstance(resolved, Err):
            return resolved
        gid, aid = resolved.value
        removed = self._groups.remove_member(gid, aid)
        if removed:
            _log.info("Asset removed from group", asset_id=asset_id, group_id=group_id)
        return Ok(removed)

    def _resolve(
        self,
        group_id: str,
        asset_id: str,
    ) -> Result[tuple[MediaGroupId, MediaAssetId], NotFoundError]:
        if not group_id or self._groups.get(MediaGroupId(group_id)) is None:
            return Err(GroupNotFound(group_id))
        if not asset_id or self._assets.get(MediaAssetId(asset_id)) is None:
            return Err(MediaNotFound(asset_id))
        return Ok((MediaGroupId(group_id), MediaAssetId(asset_id)))
