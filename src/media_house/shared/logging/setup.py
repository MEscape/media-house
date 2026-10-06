"""Logging configuration. Called once from the bootstrap."""

import logging
import logging.handlers
import sys
from dataclasses import dataclass, field
from pathlib import Path

from media_house.shared.configuration import LoggingSettings
from media_house.shared.errors import InfrastructureError
from media_house.shared.logging.formatters import ConsoleFormatter, ContextFilter, JsonFormatter

ROOT_LOGGER_NAME = "media_house"
LOG_FILE_NAME = "media-house.log"


@dataclass(slots=True)
class LoggingHandle:
    """Owns the handlers so shutdown can flush and close them deterministically."""

    handlers: list[logging.Handler] = field(default_factory=list)

    def close(self) -> None:
        logger = logging.getLogger(ROOT_LOGGER_NAME)
        for handler in self.handlers:
            handler.flush()
            handler.close()
            logger.removeHandler(handler)
        self.handlers.clear()


def configure_logging(settings: LoggingSettings, *, log_dir: Path) -> LoggingHandle:
    """Attach handlers to the ``media_house`` logger (not the root logger).

    Third-party libraries keep their own logging behaviour; ours is isolated.
    Safe to call repeatedly: previous handlers installed by us are replaced.
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    for old in [h for h in logger.handlers if getattr(h, "_media_house", False)]:
        logger.removeHandler(old)
        old.close()

    logger.setLevel(settings.level.value)
    logger.propagate = False
    handle = LoggingHandle()
    context_filter = ContextFilter()

    def attach(handler: logging.Handler, formatter: logging.Formatter) -> None:
        handler.setFormatter(formatter)
        handler.addFilter(context_filter)
        handler._media_house = True  # type: ignore[attr-defined]  # marker for idempotent re-config
        logger.addHandler(handler)
        handle.handlers.append(handler)

    if settings.console:
        attach(
            logging.StreamHandler(sys.stderr),
            JsonFormatter() if settings.json_console else ConsoleFormatter(),
        )
    if settings.file:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / LOG_FILE_NAME,
                maxBytes=settings.max_file_bytes,
                backupCount=settings.backup_count,
                encoding="utf-8",
            )
        except OSError as exc:
            raise InfrastructureError(
                f"Cannot open log file in {log_dir}",
                user_message="Media-House cannot write its log file. Check folder permissions.",
                details={"log_dir": str(log_dir)},
            ) from exc
        attach(file_handler, JsonFormatter())
    if not handle.handlers:
        logger.addHandler(logging.NullHandler())
    return handle
