"""Validating inspector: turns an untrusted file path into verified :class:`InspectedMedia`."""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from media_house.modules.media_library.application.ports import InspectedMedia
from media_house.modules.media_library.domain.errors import InvalidMedia, UnsupportedMediaType
from media_house.modules.media_library.domain.values import Checksum, MediaType, sanitize_filename
from media_house.modules.media_library.infrastructure.image_probes import (
    FormatProbe,
    default_image_probes,
)

_MIB = 1024 * 1024
_CHUNK = _MIB


def _default_size_limits() -> dict[MediaType, int]:
    return {
        MediaType.IMAGE: 200 * _MIB,
        MediaType.AUDIO: 2048 * _MIB,
        MediaType.VIDEO: 32 * 1024 * _MIB,
        MediaType.OTHER: 0,
    }


@dataclass(frozen=True, slots=True)
class MediaLimits:
    """Resource limits applied before any expensive work. A limit of 0 disables the type."""

    max_file_size_bytes: Mapping[MediaType, int] = field(default_factory=_default_size_limits)
    #: Guards against decompression bombs in later processing (width x height).
    max_pixels: int = 250_000_000


class FileMediaInspector:
    """Structurally implements ``MediaInspector``.

    Order matters for safety: refuse symlinks and non-files, sniff the header (cheap), enforce
    type-specific limits, and only then hash the whole file. The extension is never consulted.
    """

    def __init__(
        self,
        probes: Sequence[FormatProbe] | None = None,
        limits: MediaLimits | None = None,
    ) -> None:
        self._probes = tuple(probes) if probes is not None else default_image_probes()
        self._limits = limits or MediaLimits()

    def inspect(self, path: Path) -> InspectedMedia:
        size = self._check_file(path)
        for probe in self._probes:
            result = probe.probe(path, size)
            if result is None:
                continue
            limit = self._limits.max_file_size_bytes.get(result.media_type, 0)
            if limit <= 0:
                raise UnsupportedMediaType(
                    f"{result.media_type.value} files are not enabled",
                    user_message="This kind of media is not supported yet.",
                )
            if size > limit:
                raise InvalidMedia(
                    f"File too large: {size} > {limit} bytes",
                    user_message=f"This file is larger than the {limit // _MIB} MB limit.",
                )
            if (
                result.width is not None
                and result.height is not None
                and result.width * result.height > self._limits.max_pixels
            ):
                raise InvalidMedia(
                    "Image dimensions exceed the pixel limit",
                    user_message="This image is too large to process.",
                )
            return InspectedMedia(
                checksum=self._checksum(path),
                file_size=size,
                media_type=result.media_type,
                mime_type=result.mime_type,
                extension=result.extension,
                original_filename=sanitize_filename(path.name),
                width=result.width,
                height=result.height,
                duration_seconds=result.duration_seconds,
                technical=result.technical,
            )
        raise UnsupportedMediaType(
            "File content is not a supported media format",
            user_message="This file is not a supported image, audio or video format.",
        )

    @staticmethod
    def _check_file(path: Path) -> int:
        try:
            if path.is_symlink():
                raise InvalidMedia(
                    "Symbolic links are not accepted",
                    user_message="Shortcuts and symbolic links cannot be imported.",
                )
            if not path.is_file():
                raise InvalidMedia(
                    "Path is not a regular file",
                    user_message="The selected path is not a readable file.",
                )
            size = path.stat().st_size
        except OSError as exc:
            raise InvalidMedia(
                f"File cannot be read: {exc}",
                user_message="The file could not be read.",
            ) from exc
        if size == 0:
            raise InvalidMedia("File is empty", user_message="The file is empty.")
        return size

    @staticmethod
    def _checksum(path: Path) -> Checksum:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(_CHUNK):
                    digest.update(chunk)
        except OSError as exc:
            raise InvalidMedia(
                f"File cannot be read: {exc}",
                user_message="The file could not be read.",
            ) from exc
        return Checksum(digest.hexdigest())
