"""The sound files a mix plays, as they are in the Media Library NOW.

The timeline may be older than the library: a file can have been removed or replaced since. The
original recording is never touched; the file used is the one the timeline points at (the
original, or the improved derivative the compiler chose).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from media_house.modules.audio_mix_engine.domain.spec import SourceRef
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.errors import Err

#: The metadata key under which the sound library import describes a library file.
_LIBRARY_KEY = "sound_library"


@dataclass(frozen=True, slots=True)
class SoundFile:
    asset_id: str
    path: Path | None
    exists: bool
    #: Its content is not what the timeline was built from.
    changed: bool
    checksum: str | None
    #: The sound library's categories of the file (``impact``, ``whoosh``, ``pop`` ...), if any.
    categories: tuple[str, ...] = ()


class SoundFiles:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def resolve(self, sources: Iterable[SourceRef]) -> dict[str, SoundFile]:
        found: dict[str, SoundFile] = {}
        for ref in sorted(sources, key=lambda s: s.source_id):
            asset = self._library.get(ref.asset_id)
            path = self._library.local_path(ref.asset_id)
            if isinstance(asset, Err) or isinstance(path, Err) or not path.value.is_file():
                found[ref.source_id] = SoundFile(ref.source_id, None, False, False, None)
                continue
            changed = ref.checksum is not None and asset.value.checksum != ref.checksum
            found[ref.source_id] = SoundFile(
                ref.source_id, path.value, True, changed, asset.value.checksum,
                _categories(asset.value.metadata),
            )  # fmt: skip
        return found


def _categories(metadata: Mapping[str, object]) -> tuple[str, ...]:
    """The categories the sound library import recorded on the asset (metadata, not a guess)."""
    entry = metadata.get(_LIBRARY_KEY)
    found = entry.get("categories") if isinstance(entry, Mapping) else None
    return tuple(c for c in found if isinstance(c, str)) if isinstance(found, list) else ()
