"""A ``MediaAssetDto`` with sensible defaults for tests that need an asset but no storage."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from media_house.modules.media_library.application.contracts import (
    AssetRole,
    AssetStatus,
    MediaAssetDto,
    MediaType,
    SourceType,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_asset(asset_id: str = "clip", **changes: Any) -> MediaAssetDto:
    base = MediaAssetDto(
        id=asset_id,
        media_type=MediaType.VIDEO,
        mime_type="video/mp4",
        extension=".mp4",
        original_filename="clip.mp4",
        display_name="clip",
        file_size=1,
        checksum="c" * 64,
        role=AssetRole.ORIGINAL,
        status=AssetStatus.ACTIVE,
        is_favorite=False,
        source_type=SourceType.LOCAL_FILE,
        created_at=NOW,
        updated_at=NOW,
    )
    return replace(base, **changes)
