"""What callers see of a proxy. Domain objects never leave the module."""

from dataclasses import dataclass

from media_house.modules.proxy_manager.domain.values import Condition, Evidence, Verdict


@dataclass(frozen=True, slots=True)
class ProxyDto:
    """The proxy a slot serves. ``source_asset_id`` is the identity; the proxy is a derivative."""

    source_asset_id: str
    #: Library id of the proxy file. It changes when a proxy is replaced; the source id never does.
    proxy_asset_id: str
    profile: str
    profile_version: int
    spec_fingerprint: str
    condition: Condition
    verdict: Verdict
    #: What the verdict rests on: ``packets`` means complete and consistent, NOT proven decodable.
    evidence: Evidence
    codec: str
    width: int
    height: int
    size_bytes: int
    #: Deliberate departures from the source timeline, e.g. ``start_time_rebased``.
    transformations: tuple[str, ...]
    #: Things worth knowing that do not make the proxy wrong, e.g. a lost language tag.
    warnings: tuple[str, ...]
    encoder: str | None
    toolchain: str | None
    #: ``True`` when an existing proxy was served and nothing was encoded.
    reused: bool
