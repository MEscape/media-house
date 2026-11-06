"""The files a timeline plays, as they are in the Media Library NOW.

The built timeline may be older than the library: a file can have been removed or replaced since.
Nothing is probed. A proxy is asked from the proxy manager (the source stays the identity; a
proxy's asset id can change), and only a valid one is offered to Resolve.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.proxy_manager.application.contracts import ProxyCatalog
from media_house.modules.resolve_adapter.domain.facts import MediaFact
from media_house.shared.errors import Err


@dataclass(frozen=True, slots=True)
class SourceFile:
    asset_id: str
    path: Path | None
    #: The library still has it and the file is on disk.
    exists: bool
    #: Its content is not what the timeline was built from.
    changed: bool
    proxy_path: Path | None = None


class SourceFiles:
    def __init__(self, library: MediaLibrary, proxies: ProxyCatalog) -> None:
        self._library = library
        self._proxies = proxies

    def resolve(
        self, media: Iterable[MediaFact], *, proxy_profile: str, with_proxies: bool
    ) -> dict[str, SourceFile]:
        found: dict[str, SourceFile] = {}
        for m in sorted(media, key=lambda m: m.asset_id):
            found[m.asset_id] = self._one(m, proxy_profile, with_proxies)
        return found

    def _one(self, media: MediaFact, proxy_profile: str, with_proxies: bool) -> SourceFile:
        asset = self._library.get(media.asset_id)
        if isinstance(asset, Err):
            return SourceFile(media.asset_id, None, exists=False, changed=False)
        path = self._library.local_path(media.asset_id)
        if isinstance(path, Err) or not path.value.is_file():
            return SourceFile(media.asset_id, None, exists=False, changed=False)
        changed = media.checksum is not None and asset.value.checksum != media.checksum
        proxy_path: Path | None = None
        if with_proxies and media.media_type == "video" and asset.value.derivation is None:
            served = self._proxies.find(media.asset_id, proxy_profile)
            if served is not None and served.verdict.value == "valid":
                located = self._library.local_path(served.proxy_asset_id)
                if not isinstance(located, Err) and located.value.is_file():
                    proxy_path = located.value
        return SourceFile(media.asset_id, path.value, True, changed, proxy_path)
