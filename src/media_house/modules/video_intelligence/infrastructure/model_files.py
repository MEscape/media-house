"""Model files on disk: where they are, what they are, and (only if allowed) fetching them once.

Analysis never needs a network. A model file that is missing makes ONE analyzer ``not_available``
with a message saying what to download. Downloading happens only when the caller explicitly allows
it (``RuntimeConfig.allow_model_download``), into the module's own model directory, and the file is
verified against a pinned checksum before it is used.
"""

import hashlib
import importlib.util
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from media_house.shared.errors import ExternalSystemError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_TIMEOUT_SECONDS = 120
_CHUNK = 1 << 20


def library_missing(*modules: str) -> str | None:
    """A message naming the first of ``modules`` that is not installed, else ``None``."""
    for name in modules:
        try:
            found = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            found = None
        if found is None:
            return f"the Python package {name!r} is not installed (install the 'vision' extra)"
    return None


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One model file: its name, where it comes from, its licence and its pinned checksum."""

    filename: str
    url: str
    license: str
    #: Leading hex digits of the file's SHA-256 (at least 16), pinned so a swapped file is refused.
    sha256_prefix: str

    @property
    def identity(self) -> str:
        return f"{self.filename}@{self.sha256_prefix}"


class ModelStore:
    """The directory the module keeps model files in (and others it may read them from)."""

    def __init__(self, directory: Path, *also_search: Path) -> None:
        self._directory = directory
        self._search = (directory, *also_search)

    @property
    def directory(self) -> Path:
        return self._directory

    def path(self, spec: ModelSpec) -> Path:
        """Where the file is: the first searched directory that has it, else the download target."""
        return next(
            (d / spec.filename for d in self._search if (d / spec.filename).is_file()),
            self._directory / spec.filename,
        )

    def present(self, spec: ModelSpec) -> bool:
        return self.path(spec).is_file()

    def missing_reason(self, spec: ModelSpec, allow_downloads: bool) -> str | None:
        """Why the model cannot be used now (``None`` when it is, or may be fetched on demand)."""
        if self.present(spec) or allow_downloads:
            return None
        return (
            f"the model file {spec.filename} ({spec.license}) is not in {self._directory}; "
            f"download {spec.url} there, or allow model downloads"
        )

    def ensure(self, spec: ModelSpec, allow_downloads: bool) -> Path:
        """The verified model file, downloading it first if allowed. Raises if it is unusable."""
        path = self.path(spec)
        if not path.is_file():
            if not allow_downloads:
                raise ExternalSystemError(self.missing_reason(spec, False) or "model missing")
            self._download(spec, path)
        if not _sha256(path).startswith(spec.sha256_prefix):
            raise ExternalSystemError(
                f"the model file {path.name} does not match its checksum {spec.sha256_prefix}",
                details={"path": str(path)},
            )
        return path

    def _download(self, spec: ModelSpec, destination: Path) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        _log.info("Downloading a model file", file=spec.filename, source=spec.url)
        try:
            with (
                urllib.request.urlopen(spec.url, timeout=_TIMEOUT_SECONDS) as reply,  # noqa: S310
                partial.open("wb") as out,
            ):
                while chunk := reply.read(_CHUNK):
                    out.write(chunk)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise ExternalSystemError(f"could not download {spec.filename}: {exc}") from exc
        if not _sha256(partial).startswith(spec.sha256_prefix):
            partial.unlink(missing_ok=True)
            raise ExternalSystemError(f"the downloaded {spec.filename} failed its checksum")
        partial.replace(destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
