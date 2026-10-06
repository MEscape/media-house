"""Typed, validated application settings.

Settings are *external data*, so they are validated with pydantic at startup.
They never reach the domain layer: use cases receive plain values.

Layering, lowest to highest precedence::

    model defaults  <  per-environment defaults  <  user settings.toml
                    <  MEDIA_HOUSE_* environment variables  <  command line

Secrets are only ever read from ``MEDIA_HOUSE_SECRET_<NAME>`` environment
variables and are held as :class:`pydantic.SecretStr` so they print as ``***``.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Theme(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class _Section(BaseModel):
    # extra="forbid": a typo in settings.toml fails at startup instead of being ignored.
    model_config = ConfigDict(frozen=True, extra="forbid")


class LoggingSettings(_Section):
    level: LogLevel = LogLevel.INFO
    console: bool = False
    file: bool = True
    json_console: bool = False
    max_file_bytes: Annotated[int, Field(ge=10_000)] = 5_000_000
    backup_count: Annotated[int, Field(ge=0, le=100)] = 5


class ConcurrencySettings(_Section):
    max_workers: Annotated[int, Field(ge=1, le=32)] = 4
    shutdown_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 5.0


class UiSettings(_Section):
    theme: Theme = Theme.SYSTEM


class AppSettings(_Section):
    environment: Environment = Environment.PRODUCTION
    logging: LoggingSettings = LoggingSettings()
    concurrency: ConcurrencySettings = ConcurrencySettings()
    ui: UiSettings = UiSettings()
    secrets: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
