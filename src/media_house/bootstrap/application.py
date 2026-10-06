"""Headless application: configuration -> logging -> environment -> infrastructure -> services.

Nothing here imports Qt at runtime, so the whole startup/shutdown sequence can be
exercised in tests and in ``--check`` mode without a display.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_house import __version__
from media_house.bootstrap.composition import (
    Infrastructure,
    compose_infrastructure,
    compose_services,
)
from media_house.bootstrap.environment import validate_environment
from media_house.bootstrap.lifecycle import ShutdownStack, StartupError, StartupPhase
from media_house.core.modules import ApplicationModule, Container
from media_house.shared.configuration import AppSettings, Environment, LogLevel, load_settings
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import configure_logging, get_logger

_log = get_logger(__name__)
HOME_ENV_KEY = "MEDIA_HOUSE_HOME"


@dataclass(frozen=True, slots=True)
class StartupOptions:
    #: Snapshot of the process environment (read once by the CLI; injectable for tests).
    env: Mapping[str, str] = field(default_factory=dict)
    environment: Environment | None = None
    home: Path | None = None
    log_level: LogLevel | None = None
    #: Modules to install; ``None`` means the product's default set.
    modules: Sequence[ApplicationModule] | None = None


class Application:
    """A started, headless Media-House. Call :meth:`shutdown` when done."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        paths: AppPaths,
        container: Container,
        infrastructure: Infrastructure,
        modules: Sequence[ApplicationModule],
        shutdown_stack: ShutdownStack,
    ) -> None:
        self.settings = settings
        self.paths = paths
        self.container = container
        self.infrastructure = infrastructure
        self.modules = tuple(modules)
        self._shutdown_stack = shutdown_stack
        self._running = True

    @classmethod
    def start(cls, options: StartupOptions) -> "Application":
        stack = ShutdownStack()
        phase = StartupPhase.CONFIGURATION
        try:
            home = options.home or (
                Path(options.env[HOME_ENV_KEY]) if options.env.get(HOME_ENV_KEY) else None
            )
            paths = AppPaths.under_root(home) if home else AppPaths.for_user()
            settings = load_settings(
                config_file=paths.settings_file,
                env=options.env,
                overrides=_overrides(options),
            )

            phase = StartupPhase.LOGGING
            stack.push("logging", configure_logging(settings.logging, log_dir=paths.log_dir).close)
            _log.info(
                "Starting Media-House",
                version=__version__,
                environment=settings.environment.value,
            )

            phase = StartupPhase.ENVIRONMENT
            validate_environment(paths)

            phase = StartupPhase.INFRASTRUCTURE
            infrastructure = compose_infrastructure(settings, stack)

            phase = StartupPhase.SERVICES
            modules = options.modules if options.modules is not None else _default_modules()
            container = compose_services(settings, paths, infrastructure, modules)
        except Exception as exc:
            _log.error("Startup failed", exc_info=exc, phase=phase.value)
            stack.close()
            raise StartupError(phase, exc) from exc

        _log.info("Startup complete", modules=[m.name for m in modules])
        return cls(
            settings=settings,
            paths=paths,
            container=container,
            infrastructure=infrastructure,
            modules=modules,
            shutdown_stack=stack,
        )

    def shutdown(self) -> None:
        """Idempotent. Stops background work, then flushes and closes logging."""
        if not self._running:
            return
        self._running = False
        _log.info("Shutting down")
        self._shutdown_stack.close()


def _overrides(options: StartupOptions) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if options.environment is not None:
        overrides["environment"] = options.environment.value
    if options.log_level is not None:
        overrides["logging"] = {"level": options.log_level.value}
    return overrides


def _default_modules() -> Sequence[ApplicationModule]:
    from media_house.bootstrap.modules import installed_modules

    return installed_modules()
