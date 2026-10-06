"""Domain events of the media library."""

from dataclasses import dataclass

from media_house.shared.events import DomainEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class MediaAssetImported(DomainEvent):
    asset_id: str
    media_type: str
    checksum: str


@dataclass(frozen=True, kw_only=True, slots=True)
class DerivedAssetRegistered(DomainEvent):
    asset_id: str
    source_asset_id: str
    operation: str
    fingerprint: str


@dataclass(frozen=True, kw_only=True, slots=True)
class MediaAssetDeleted(DomainEvent):
    asset_id: str
    media_type: str


@dataclass(frozen=True, kw_only=True, slots=True)
class MediaGroupCreated(DomainEvent):
    group_id: str
    name: str
