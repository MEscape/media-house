"""Requests to the proxy manager."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.modules.proxy_manager.domain.profile import DEFAULT_PROFILE
from media_house.modules.proxy_manager.domain.values import JsonValue


@dataclass(frozen=True, slots=True)
class EnsureProxyCommand:
    """Make sure a valid proxy of ``profile`` exists for the source and is the one served."""

    source_asset_id: str
    profile: str = DEFAULT_PROFILE
    #: Flat overrides of the profile, e.g. ``{"max_long_edge": 480}``.
    overrides: Mapping[str, JsonValue] = field(default_factory=dict)
