"""Domain-level errors of the media library.

Expected failures are *returned* by use cases as ``Err``; repositories and adapters
raise these (or ``PersistenceError``) and use cases translate them.
"""

from media_house.shared.errors import (
    ConflictError,
    InfrastructureError,
    NotFoundError,
    ValidationError,
)


class UnsupportedMediaType(ValidationError):
    """The file is not a media format this library knows how to ingest."""

    code = "media.unsupported_type"


class InvalidMedia(ValidationError):
    """The file looks like a supported format but is corrupt, unreadable or out of limits."""

    code = "media.invalid"


class MediaNotFound(NotFoundError):
    code = "media.not_found"

    def __init__(self, asset_id: str) -> None:
        super().__init__(
            f"Media asset not found: {asset_id}",
            user_message="The requested media item does not exist.",
            details={"asset_id": asset_id},
        )
        self.asset_id = asset_id


class GroupNotFound(NotFoundError):
    code = "media.group_not_found"

    def __init__(self, group_id: str) -> None:
        super().__init__(
            f"Media group not found: {group_id}",
            user_message="The requested group does not exist.",
            details={"group_id": group_id},
        )
        self.group_id = group_id


class DuplicateMedia(ConflictError):
    """An original with the same content checksum already exists."""

    code = "media.duplicate"

    def __init__(self, checksum: str) -> None:
        super().__init__(
            f"An original asset with checksum {checksum[:12]}… already exists",
            user_message="This file is already in the media library.",
            details={"checksum": checksum},
        )
        self.checksum = checksum


class DuplicateDerivative(ConflictError):
    """A derivative with the same processing fingerprint already exists."""

    code = "media.duplicate_derivative"

    def __init__(self, fingerprint: str) -> None:
        super().__init__(
            f"A derived asset with fingerprint {fingerprint[:12]}… already exists",
            user_message="This processed version already exists.",
            details={"fingerprint": fingerprint},
        )
        self.fingerprint = fingerprint


class GroupNameTaken(ConflictError):
    code = "media.group_name_taken"

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Group name already in use here: {name}",
            user_message=f"A group named '{name}' already exists in this location.",
        )


class MediaStorageError(InfrastructureError):
    """The storage backend failed (disk full, permissions, missing blob, ...)."""

    code = "media.storage_failed"
    default_user_message = "The media file could not be stored or read."
