"""Value objects, enums and JSON helpers of the media library."""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath, PureWindowsPath
from typing import ClassVar

from media_house.shared.errors import InvariantViolation
from media_house.shared.types import new_id

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]

#: Upper bound for any JSON document stored on an asset (technical info, metadata, config).
MAX_JSON_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 16


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class MediaType(StrEnum):
    """Coarse family of a media asset. Extend here; nothing else switches on file names."""

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    OTHER = "other"


class AssetRole(StrEnum):
    """How an asset came to exist."""

    ORIGINAL = "original"  # imported, treated as immutable
    DERIVED = "derived"  # produced from another asset by a processing operation
    PREVIEW = "preview"  # derived asset meant for display only (thumbnails, waveforms)


class AssetStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class SourceType(StrEnum):
    LOCAL_FILE = "local_file"
    WEB_DOWNLOAD = "web_download"
    UPLOAD = "upload"
    GENERATED = "generated"
    AI_GENERATED = "ai_generated"
    EXTERNAL_SERVICE = "external_service"
    DERIVED = "derived"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Identifiers
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MediaAssetId:
    """Stable identity of an asset. Opaque; never derived from a path or filename."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise InvariantViolation("MediaAssetId must not be empty")

    @classmethod
    def new(cls) -> "MediaAssetId":
        return cls(new_id())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MediaGroupId:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise InvariantViolation("MediaGroupId must not be empty")

    @classmethod
    def new(cls) -> "MediaGroupId":
        return cls(new_id())

    def __str__(self) -> str:
        return self.value


_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_EXTENSION = re.compile(r"\.[a-z0-9]{1,10}")
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class Checksum:
    """SHA-256 of the file content, lowercase hex."""

    value: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.fullmatch(self.value):
            raise InvariantViolation("Checksum must be 64 lowercase hex characters")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class StorageKey:
    """Logical location of a blob inside a storage backend.

    Always relative, always ``/``-separated, never contains ``..``. Backends map it to
    a physical location; callers never build paths themselves.
    """

    value: str

    def __post_init__(self) -> None:
        parts = self.value.split("/")
        if not self.value or len(self.value) > 512 or any(not _SEGMENT.fullmatch(p) for p in parts):
            raise InvariantViolation("Invalid storage key", details={"key": self.value[:80]})
        if any(p in {".", ".."} for p in parts):
            raise InvariantViolation("Storage key must not contain dot segments")

    @classmethod
    def for_content(cls, media_type: MediaType, checksum: Checksum, extension: str) -> "StorageKey":
        """Content-addressed, sharded layout: ``image/ab/cd/<sha256>.png``."""
        if not _EXTENSION.fullmatch(extension):
            raise InvariantViolation("Invalid file extension", details={"extension": extension})
        c = checksum.value
        return cls(f"{media_type.value}/{c[:2]}/{c[2:4]}/{c}{extension}")

    def __str__(self) -> str:
        return self.value


# --------------------------------------------------------------------------- #
# Names, tags, filenames
# --------------------------------------------------------------------------- #
def _check_label(value: str, *, what: str, max_length: int) -> None:
    if not value or value != value.strip():
        raise InvariantViolation(
            f"{what} must be non-empty and trimmed",
            user_message=f"Please enter a {what.lower()}.",
        )
    if len(value) > max_length:
        raise InvariantViolation(
            f"{what} too long",
            user_message=f"A {what.lower()} can have at most {max_length} characters.",
        )
    if any(not ch.isprintable() for ch in value):
        raise InvariantViolation(
            f"{what} contains control characters",
            user_message=f"The {what.lower()} contains characters that are not allowed.",
        )


@dataclass(frozen=True, slots=True)
class MediaName:
    """Display name of an asset (editable; unlike the id it may change at any time)."""

    MAX_LENGTH: ClassVar[int] = 255
    value: str

    def __post_init__(self) -> None:
        _check_label(self.value, what="Name", max_length=self.MAX_LENGTH)

    @classmethod
    def of(cls, raw: str) -> "MediaName":
        return cls(raw.strip())

    @classmethod
    def from_filename(cls, filename: str) -> "MediaName":
        """Derive a readable name from a file name (stem, cleaned, truncated)."""
        stem = PurePath(filename).stem or filename
        cleaned = "".join(ch for ch in stem if ch.isprintable()).strip()
        return cls(cleaned[: cls.MAX_LENGTH].strip() or "Untitled")

    @property
    def key(self) -> str:
        return self.value.casefold()

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class GroupName:
    """Name of a group; unique case-insensitively among siblings."""

    MAX_LENGTH: ClassVar[int] = 80
    value: str

    def __post_init__(self) -> None:
        _check_label(self.value, what="Group name", max_length=self.MAX_LENGTH)

    @classmethod
    def of(cls, raw: str) -> "GroupName":
        return cls(raw.strip())

    @property
    def key(self) -> str:
        return self.value.casefold()

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Tag:
    """A normalised tag: casefolded, single-spaced, 1-50 printable characters, no commas."""

    MAX_LENGTH: ClassVar[int] = 50
    value: str

    def __post_init__(self) -> None:
        _check_label(self.value, what="Tag", max_length=self.MAX_LENGTH)
        if "," in self.value or self.value != _normalise_tag(self.value):
            raise InvariantViolation(
                "Tag is not normalised",
                user_message="Tags cannot contain commas.",
                details={"tag": self.value},
            )

    @classmethod
    def of(cls, raw: str) -> "Tag":
        return cls(_normalise_tag(raw))

    def __str__(self) -> str:
        return self.value


def _normalise_tag(raw: str) -> str:
    return " ".join(raw.split()).casefold()


_FORBIDDEN_FILENAME_CHARS = str.maketrans(dict.fromkeys('<>:"|?*', "_"))


def sanitize_filename(raw: str) -> str:
    """Reduce an untrusted file name to a harmless leaf name.

    Drops directory components (both separators), control characters and Windows-hostile
    characters, and caps the length while keeping the extension. Used for *display and
    provenance only*: storage never uses it.
    """
    name = PureWindowsPath(raw).name
    name = "".join(ch for ch in name if ch.isprintable()).translate(_FORBIDDEN_FILENAME_CHARS)
    name = name.strip(" .")
    if len(name) > 255:
        suffix = PurePath(name).suffix[:20]
        name = name[: 255 - len(suffix)] + suffix
    return name or "unnamed"


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def normalise_json(value: object, *, _depth: int = 0) -> JsonValue:
    """Return a deep, canonical, JSON-safe copy of ``value`` or raise ``InvariantViolation``.

    Integral floats become ints (``30.0`` and ``30`` are the same configuration), tuples
    become lists, mapping keys must be strings and non-finite numbers are rejected.
    """
    if _depth > _MAX_JSON_DEPTH:
        raise InvariantViolation("JSON value is nested too deeply")
    match value:
        case None | bool() | str() | int():
            return value
        case float():
            if not math.isfinite(value):
                raise InvariantViolation("JSON numbers must be finite")
            return int(value) if value.is_integer() else value
        case list() | tuple():
            return [normalise_json(item, _depth=_depth + 1) for item in value]
        case Mapping():
            result: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise InvariantViolation("JSON object keys must be strings")
                result[key] = normalise_json(item, _depth=_depth + 1)
            return result
        case _:
            raise InvariantViolation(
                f"Value of type {type(value).__name__} is not JSON-serialisable",
            )


def canonical_json(value: object) -> str:
    """Deterministic JSON text (sorted keys, no whitespace, ASCII)."""
    return json.dumps(
        normalise_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def validate_json_object(value: JsonObject | None, *, what: str) -> dict[str, JsonValue]:
    """Normalised copy of a JSON object, bounded in size."""
    normalised = normalise_json(dict(value or {}))
    if not isinstance(normalised, dict):  # pragma: no cover - dict() always yields a dict
        raise InvariantViolation(f"{what} must be a JSON object")
    if len(canonical_json(normalised)) > MAX_JSON_BYTES:
        raise InvariantViolation(
            f"{what} exceeds {MAX_JSON_BYTES} bytes",
            user_message=f"The {what} are too large.",
        )
    return normalised


# --------------------------------------------------------------------------- #
# Provenance and derivation
# --------------------------------------------------------------------------- #
_URL_USERINFO = re.compile(r"^(https?://)[^/?#@]*@", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MediaSource:
    """Where an asset came from. URLs are stored without credentials, query or fragment."""

    source_type: SourceType = SourceType.UNKNOWN
    url: str | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        if self.url is not None and (
            len(self.url) > 2048
            or not re.match(r"https?://\S+$", self.url, re.IGNORECASE)
            or any(not ch.isprintable() for ch in self.url)
        ):
            raise InvariantViolation("Source URL is not a valid http(s) URL")
        if self.provider is not None:
            _check_label(self.provider, what="Provider", max_length=100)

    @classmethod
    def of(
        cls,
        source_type: SourceType = SourceType.UNKNOWN,
        *,
        url: str | None = None,
        provider: str | None = None,
    ) -> "MediaSource":
        """Normalise user input: strip credentials, query and fragment from ``url``."""
        clean_url: str | None = None
        if url is not None and url.strip():
            candidate = url.strip().split("#", 1)[0].split("?", 1)[0]
            clean_url = _URL_USERINFO.sub(r"\1", candidate)
        clean_provider = provider.strip() if provider and provider.strip() else None
        return cls(source_type, clean_url, clean_provider)


@dataclass(frozen=True, slots=True)
class ProcessingFingerprint:
    """Deterministic identity of a derivative: source content + operation + version + config."""

    SCHEME_VERSION: ClassVar[int] = 1
    value: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.fullmatch(self.value):
            raise InvariantViolation("Fingerprint must be 64 lowercase hex characters")

    @classmethod
    def compute(
        cls,
        *,
        source_checksum: Checksum,
        operation: str,
        version: int,
        config: JsonObject,
    ) -> "ProcessingFingerprint":
        payload = canonical_json(
            {
                "scheme": cls.SCHEME_VERSION,
                "source": source_checksum.value,
                "operation": operation,
                "version": version,
                "config": config,
            },
        )
        return cls(hashlib.sha256(payload.encode("ascii")).hexdigest())

    def __str__(self) -> str:
        return self.value


_OPERATION = re.compile(r"[a-z][a-z0-9_.-]{0,63}")


@dataclass(frozen=True, slots=True, eq=False)
class Derivation:
    """Link from a derived asset to its source plus how it was produced."""

    source_asset_id: MediaAssetId
    operation: str
    version: int
    config: JsonObject
    fingerprint: ProcessingFingerprint
    _frozen_config: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not _OPERATION.fullmatch(self.operation):
            raise InvariantViolation(
                "Operation must be lowercase letters, digits, '_', '.', '-'",
                details={"operation": self.operation},
            )
        if self.version < 1:
            raise InvariantViolation("Processing version must be >= 1")
        normalised = validate_json_object(self.config, what="processing configuration")
        object.__setattr__(self, "config", normalised)
        object.__setattr__(self, "_frozen_config", canonical_json(normalised))

    @classmethod
    def create(
        cls,
        *,
        source_asset_id: MediaAssetId,
        source_checksum: Checksum,
        operation: str,
        version: int,
        config: JsonObject,
    ) -> "Derivation":
        """Build a derivation and compute its fingerprint from the source content."""
        validate = validate_json_object(config, what="processing configuration")
        fingerprint = ProcessingFingerprint.compute(
            source_checksum=source_checksum,
            operation=operation,
            version=version,
            config=validate,
        )
        return cls(source_asset_id, operation, version, validate, fingerprint)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Derivation) and (
            self.source_asset_id,
            self.operation,
            self.version,
            self._frozen_config,
            self.fingerprint,
        ) == (
            other.source_asset_id,
            other.operation,
            other.version,
            other._frozen_config,
            other.fingerprint,
        )

    def __hash__(self) -> int:
        return hash((self.source_asset_id, self.operation, self.version, self.fingerprint))
