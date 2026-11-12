"""The upload copy of a designed thumbnail: 1280x720 JPEG under YouTube's 2 MB limit."""

from pathlib import Path
from typing import Final

from media_house.modules.media_library.application.contracts import MediaError, MediaLibrary
from media_house.modules.thumbnail_design.application.ports import ThumbnailRenderer
from media_house.shared.errors import Err, Ok, Result

UPLOAD_WIDTH: Final = 1280
UPLOAD_MAX_BYTES: Final = 2_000_000


class ExportThumbnail:
    def __init__(self, library: MediaLibrary, renderer: ThumbnailRenderer) -> None:
        self._library = library
        self._renderer = renderer

    def execute(self, asset_id: str, destination: Path) -> Result[Path, MediaError]:
        """Write the copy to ``destination`` (a ``.jpg`` file); the stored picture is untouched."""
        source = self._library.local_path(asset_id)
        if isinstance(source, Err):
            return source
        return Ok(
            self._renderer.export(
                source.value, destination.with_suffix(".jpg"), UPLOAD_WIDTH, UPLOAD_MAX_BYTES
            )
        )
