"""Identity of a set and of its outputs: everything that changes the result, nothing else."""

import hashlib
import json

from media_house.modules.visual_asset_generation.domain.model import (
    PLANNER_VERSION,
    SCHEMA_VERSION,
    EditRef,
    ItemStatus,
    PreparedAsset,
    ResultState,
)
from media_house.modules.visual_asset_generation.domain.requests import AssetRequest
from media_house.modules.visual_asset_generation.domain.values import JsonValue


def digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def set_cache_key(
    requests: tuple[AssetRequest, ...],
    *,
    project_id: str | None,
    edit: EditRef | None,
    style_fingerprint: str | None,
    configuration_fingerprint: str,
) -> dict[str, JsonValue]:
    """Digests only (the library caps a processing config at 64 KiB)."""
    return {
        "requests": digest([r.to_config() for r in requests]),
        "project_id": project_id,
        "edit": None if edit is None else f"{edit.edit_plan_id}@{edit.plan_version}",
        "style": style_fingerprint,
        "configuration": configuration_fingerprint,
        "versions": {"planner": PLANNER_VERSION, "schema": SCHEMA_VERSION},
    }


def state_of(items: tuple[PreparedAsset, ...]) -> ResultState:
    return (
        ResultState.PARTIAL if any(i.status is ItemStatus.FAILED for i in items) else ResultState.OK
    )
