"""The logging API modules use: ``log = get_logger(__name__)``; ``log.info("msg", key=value)``."""

import logging

FIELDS_ATTR = "mh_fields"


class StructuredLogger:
    """Thin wrapper adding keyword fields to the standard library logger."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    @property
    def name(self) -> str:
        return self._logger.name

    def debug(self, message: str, **fields: object) -> None:
        self._log(logging.DEBUG, message, None, fields)

    def info(self, message: str, **fields: object) -> None:
        self._log(logging.INFO, message, None, fields)

    def warning(self, message: str, **fields: object) -> None:
        self._log(logging.WARNING, message, None, fields)

    def error(
        self,
        message: str,
        *,
        exc_info: BaseException | None = None,
        **fields: object,
    ) -> None:
        self._log(logging.ERROR, message, exc_info, fields)

    def exception(self, message: str, **fields: object) -> None:
        """Log at ERROR level with the exception currently being handled."""
        self._log(logging.ERROR, message, True, fields)

    def _log(
        self,
        level: int,
        message: str,
        exc_info: BaseException | bool | None,
        fields: dict[str, object],
    ) -> None:
        if self._logger.isEnabledFor(level):
            # stacklevel=3 -> report the caller of debug()/info()/..., not this file.
            self._logger.log(
                level,
                message,
                exc_info=exc_info,
                extra={FIELDS_ATTR: fields},
                stacklevel=3,
            )


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(logging.getLogger(name))
