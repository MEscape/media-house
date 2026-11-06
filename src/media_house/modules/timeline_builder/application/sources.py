"""What the Media Library and the proxy catalog say about the files a specification points at NOW.

The specification may be older than the library: a file can have been removed or replaced since
it was compiled. Nothing is probed; a proxy is only looked up (``proxy_manager`` keeps the source
as the identity, so a clip never stores a proxy id).
"""

from collections.abc import Iterable

from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.proxy_manager.application.contracts import ProxyCatalog
from media_house.modules.timeline_builder.domain.facts import LiveSource, LiveSources
from media_house.shared.errors import Err


class LiveSourceReader:
    def __init__(self, library: MediaLibrary, proxies: ProxyCatalog) -> None:
        self._library = library
        self._proxies = proxies

    def read(self, asset_ids: Iterable[str], *, proxy_profile: str) -> LiveSources:
        found: dict[str, LiveSource] = {}
        for asset_id in sorted({i for i in asset_ids if i}):
            got = self._library.get(asset_id)
            if isinstance(got, Err):
                found[asset_id] = LiveSource(asset_id=asset_id, exists=False, available=False)
                continue
            dto = got.value
            path = self._library.local_path(asset_id)
            available = not isinstance(path, Err) and path.value.is_file()
            proxy = None
            if dto.media_type.value == "video" and dto.derivation is None:
                served = self._proxies.find(asset_id, proxy_profile)
                if served is not None and served.verdict.value == "valid":
                    proxy = served.proxy_asset_id
            found[asset_id] = LiveSource(
                asset_id=asset_id,
                exists=True,
                available=available,
                checksum=dto.checksum,
                name=dto.display_name,
                proxy_asset_id=proxy,
            )
        return LiveSources(found=found)
