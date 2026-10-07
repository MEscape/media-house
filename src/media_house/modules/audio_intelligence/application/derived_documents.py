"""Find-or-store of the JSON documents every analysis stage keeps as derived library assets.

Transcript, acoustic measurements and the timeline all follow one pattern: look the document up
by its processing fingerprint, treat an unreadable one as absent (the caller recomputes), and
register a freshly computed one. This is the single place that knows how.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from media_house.modules.audio_intelligence.domain.errors import AudioIntelligenceError
from media_house.modules.media_library.application.contracts import (
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.shared.errors import Err, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_DISPLAY_NAME_MAX = 255


def require_audio_or_video(source: MediaAssetDto, action: str) -> Err[ValidationError] | None:
    """An ``Err`` for any asset that is neither audio nor video, else ``None``."""
    if source.media_type in {MediaType.AUDIO, MediaType.VIDEO}:
        return None
    return Err(
        ValidationError(
            f"Asset {source.id} is {source.media_type.value}, not audio or video",
            field="source_asset_id",
            user_message=f"Only audio and video files can be {action}.",
        ),
    )


@dataclass(frozen=True, slots=True)
class DocumentSpec:
    """What identifies and describes one derived document of a source asset."""

    operation: str
    version: int
    config: Mapping[str, JsonValue]


class DerivedDocuments:
    """Reads and writes derived JSON documents through the Media Library (no storage of its own)."""

    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._paths = paths

    def fingerprint(self, source_id: str, spec: DocumentSpec) -> Result[str, MediaError]:
        return self._library.fingerprint(source_id, spec.operation, spec.config, spec.version)

    def load[T](
        self,
        source_id: str,
        fingerprint: str,
        parse: Callable[[str], T],
    ) -> tuple[T, MediaAssetDto] | None:
        """The stored document and its asset, or ``None`` if absent or unusable."""
        asset = self._library.find_derived_asset(source_id, fingerprint)
        if asset is None:
            return None
        path = self._library.local_path(asset.id)
        if isinstance(path, Err):
            _log.warning("Stored document missing, recomputing", asset_id=asset.id)
            return None
        try:
            return parse(path.value.read_text(encoding="utf-8")), asset
        except (OSError, UnicodeDecodeError, AudioIntelligenceError) as exc:
            _log.warning(
                "Stored document unusable, recomputing", asset_id=asset.id, reason=str(exc)
            )
            return None

    def store(
        self,
        source: MediaAssetDto,
        text: str,
        *,
        filename: str,
        spec: DocumentSpec,
        display_name: str,
        metadata: Mapping[str, JsonValue],
    ) -> Result[MediaAssetDto, MediaError]:
        with self._paths.temporary_directory(prefix=f"{spec.operation}-") as tmp:
            document = tmp / filename
            document.write_text(text, encoding="utf-8")
            registered = self._library.register_derived(
                source.id,
                document,
                operation=spec.operation,
                config=spec.config,
                version=spec.version,
                display_name=display_name[:_DISPLAY_NAME_MAX],
                metadata={"processing_type": spec.operation, "source_asset_id": source.id}
                | dict(metadata),
            )
        if isinstance(registered, Err):
            return registered
        return Ok(registered.value.asset)
