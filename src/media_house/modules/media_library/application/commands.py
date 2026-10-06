"""Commands: the input of use cases."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from media_house.modules.media_library.domain.values import AssetRole, JsonValue, SourceType


@dataclass(frozen=True, slots=True)
class ImportMediaCommand:
    """Import a local file. Group ids and tags are applied even if the file is a duplicate."""

    path: Path
    display_name: str | None = None
    group_ids: Sequence[str] = ()
    tags: Sequence[str] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    source_type: SourceType = SourceType.LOCAL_FILE
    source_url: str | None = None
    source_provider: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterDerivedCommand:
    """Store the output of a processing step as a derivative of ``source_asset_id``.

    ``config`` and ``version`` must describe *everything* that influenced the output: they
    form the processing fingerprint that later lookups use to reuse this result.
    """

    source_asset_id: str
    path: Path
    operation: str
    config: Mapping[str, JsonValue]
    version: int = 1
    display_name: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    role: AssetRole = AssetRole.DERIVED
