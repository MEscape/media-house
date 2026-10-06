"""Central error boundary: turns any exception into a loggable, presentable report."""

from dataclasses import dataclass

from media_house.shared.errors.base import (
    ErrorCategory,
    MediaHouseError,
    UnexpectedError,
)
from media_house.shared.logging.api import StructuredLogger, get_logger

_EXPECTED = frozenset(
    {ErrorCategory.DOMAIN, ErrorCategory.VALIDATION, ErrorCategory.APPLICATION},
)


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """What the presentation layer is allowed to know about a failure."""

    error_id: str
    category: ErrorCategory
    code: str
    user_message: str
    recoverable: bool
    #: Exception type + message only. Never a traceback.
    technical_summary: str


class ErrorHandler:
    """Logs an exception once, with full diagnostics, and returns a safe report.

    Policy:

    * expected categories (domain/validation/application) -> INFO, no traceback;
    * infrastructure / external / configuration -> ERROR with traceback;
    * anything else is wrapped in :class:`UnexpectedError` (chained) -> ERROR.
    """

    def __init__(self, logger: StructuredLogger | None = None) -> None:
        self._log = logger or get_logger("media_house.errors")

    def handle(self, exc: BaseException, *, operation: str | None = None) -> ErrorReport:
        error = exc if isinstance(exc, MediaHouseError) else self._wrap(exc)
        fields: dict[str, object] = {
            "error_id": error.error_id,
            "error_code": error.code,
            "error_category": error.category.value,
            "operation": operation,
            **{f"detail_{key}": value for key, value in error.details.items()},
        }
        if error.category in _EXPECTED:
            self._log.info(f"Operation failed: {error}", **fields)
        else:
            self._log.error(f"Operation failed: {error}", exc_info=exc, **fields)
        return ErrorReport(
            error_id=error.error_id,
            category=error.category,
            code=error.code,
            user_message=error.user_message,
            recoverable=error.recoverable,
            technical_summary=f"{type(exc).__name__}: {exc}",
        )

    @staticmethod
    def _wrap(exc: BaseException) -> UnexpectedError:
        wrapped = UnexpectedError(f"{type(exc).__name__}: {exc}")
        wrapped.__cause__ = exc
        return wrapped
