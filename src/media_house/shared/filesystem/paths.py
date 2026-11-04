"""Application directories. The only place that knows where things live on disk."""

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from media_house.shared.errors import InfrastructureError

APP_NAME = "media-house"
SETTINGS_FILE_NAME = "settings.toml"


@dataclass(frozen=True, slots=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    log_dir: Path
    #: Machine-level installs (models, renderer runtimes) shared by every home; ``None`` = only
    #: this home's own cache. ``scripts/fetch_models.py`` and ``setup_hyperframes.py`` install
    #: into the user cache, which an application started with ``--home`` falls back to.
    install_root: Path | None = None

    @classmethod
    def for_user(cls) -> "AppPaths":
        """OS-conventional locations (AppData / Library / XDG)."""
        return cls(
            config_dir=platformdirs.user_config_path(APP_NAME, appauthor=False),
            data_dir=platformdirs.user_data_path(APP_NAME, appauthor=False),
            cache_dir=platformdirs.user_cache_path(APP_NAME, appauthor=False),
            log_dir=platformdirs.user_log_path(APP_NAME, appauthor=False),
        )

    @classmethod
    def under_root(cls, root: Path) -> "AppPaths":
        """Self-contained layout for portable installs, tests and CI."""
        return cls(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            log_dir=root / "logs",
        )

    def install(self, *parts: str) -> Path:
        """A machine-level install: this home's own copy when it has one, else the shared one."""
        own = self.cache_dir.joinpath(*parts)
        if own.exists() or self.install_root is None:
            return own
        return self.install_root.joinpath(*parts)

    @property
    def settings_file(self) -> Path:
        return self.config_dir / SETTINGS_FILE_NAME

    @property
    def temp_dir(self) -> Path:
        return self.cache_dir / "tmp"

    def ensure_directories(self) -> None:
        for directory in (
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.log_dir,
            self.temp_dir,
        ):
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise InfrastructureError(
                    f"Cannot create directory {directory}",
                    user_message=(
                        "Media-House cannot create its data folders. "
                        "Check disk space and permissions."
                    ),
                    details={"directory": str(directory)},
                ) from exc

    @contextmanager
    def temporary_directory(self, prefix: str = "job-") -> Iterator[Path]:
        """A private scratch directory that is always removed afterwards."""
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.temp_dir, prefix=prefix) as name:
            yield Path(name)
