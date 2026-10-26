"""What the use cases need from other modules, as narrow ports the real contracts satisfy."""

from collections.abc import Sequence
from typing import Protocol

from media_house.modules.asset_relationships.domain.basis import Basis
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaQuery,
    Page,
)
from media_house.shared.errors import Result


class AssetLookup(Protocol):
    """The part of ``MediaLibrary`` that stored claims need (``MediaLibrary`` satisfies it)."""

    def get(self, asset_id: str) -> Result[MediaAssetDto, MediaError]: ...

    def get_many(self, asset_ids: Sequence[str]) -> list[MediaAssetDto]: ...


class LineageLibrary(Protocol):
    """The part of ``MediaLibrary`` that recorded lineage needs (``MediaLibrary`` satisfies it)."""

    def get(self, asset_id: str) -> Result[MediaAssetDto, MediaError]: ...

    def find_by_checksum(self, checksum: str) -> MediaAssetDto | None:
        """The ORIGINAL with this checksum, if any (derived assets are not found)."""
        ...

    def search(self, query: MediaQuery | None = None) -> Page[MediaAssetDto]: ...


class BasisCheck(Protocol):
    """Says whether the basis of a claim (a detector's version, its inputs) is still current."""

    def is_current(self, basis: Basis) -> bool | None:
        """``False``: out of date. ``True``: current. ``None``: unknown detector.

        Called by reconciliation while a claim is being judged, so it must be quick and must not
        call back into this module's store.
        """
        ...
