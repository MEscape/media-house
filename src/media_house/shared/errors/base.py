"""Error taxonomy.

Every error that Media-House raises deliberately derives from
:class:`MediaHouseError` and belongs to exactly one :class:`ErrorCategory`.

* ``user_message`` is safe to show to end users (no paths, no stack traces).
* ``details`` is diagnostic context for developers; it is logged, never shown.
* ``error_id`` correlates what the user sees with what is in the log file.

Anything that is *not* a ``MediaHouseError`` is, by definition, unexpected and is
wrapped by :class:`~media_house.shared.errors.handler.ErrorHandler`.
"""

import uuid
from collections.abc import Mapping
from enum import StrEnum
from typing import ClassVar


class ErrorCategory(StrEnum):
    DOMAIN = "domain"
    VALIDATION = "validation"
    APPLICATION = "application"
    INFRASTRUCTURE = "infrastructure"
    EXTERNAL_SYSTEM = "external_system"
    CONFIGURATION = "configuration"
    UNEXPECTED = "unexpected"


def new_error_id() -> str:
    """Short, human-quotable identifier, e.g. ``ERR-3F9A12BC``."""
    return f"ERR-{uuid.uuid4().hex[:8].upper()}"


class MediaHouseError(Exception):
    category: ClassVar[ErrorCategory] = ErrorCategory.UNEXPECTED
    code: ClassVar[str] = "media_house.error"
    default_user_message: ClassVar[str] = "Something went wrong."
    #: Whether the application can reasonably keep running after this error.
    recoverable: ClassVar[bool] = True

    def __init__(
        self,
        message: str,
        *,
        user_message: str | None = None,
        details: Mapping[str, object] | None = None,
        error_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.user_message: str = user_message or self.default_user_message
        self.details: dict[str, object] = dict(details or {})
        self.error_id: str = error_id or new_error_id()


# --- Domain --------------------------------------------------------------- #
class DomainError(MediaHouseError):
    """A business rule was violated."""

    category = ErrorCategory.DOMAIN
    code = "domain.error"
    default_user_message = "The requested change is not allowed."


class InvariantViolation(DomainError):
    """An aggregate or value object would enter an invalid state."""

    code = "domain.invariant_violation"


# --- Validation ----------------------------------------------------------- #
class ValidationError(MediaHouseError):
    """Input is malformed. Raised/returned at system boundaries."""

    category = ErrorCategory.VALIDATION
    code = "validation.error"
    default_user_message = "Some of the entered data is not valid."

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        user_message: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message, user_message=user_message or message, details=details)
        self.field = field


# --- Application ---------------------------------------------------------- #
class ApplicationError(MediaHouseError):
    """A use case could not complete for a business-level reason."""

    category = ErrorCategory.APPLICATION
    code = "application.error"
    default_user_message = "The operation could not be completed."


class NotFoundError(ApplicationError):
    code = "application.not_found"
    default_user_message = "The requested item does not exist."


class ConflictError(ApplicationError):
    code = "application.conflict"
    default_user_message = "The operation conflicts with existing data."


class OperationCancelledError(ApplicationError):
    code = "application.cancelled"
    default_user_message = "The operation was cancelled."


# --- Infrastructure / external systems ------------------------------------ #
class InfrastructureError(MediaHouseError):
    """Our own technical plumbing failed (disk, database, ...)."""

    category = ErrorCategory.INFRASTRUCTURE
    code = "infrastructure.error"
    default_user_message = "A technical problem occurred while accessing local resources."


class PersistenceError(InfrastructureError):
    code = "infrastructure.persistence"
    default_user_message = "The application could not read or write its data."


class ExternalSystemError(InfrastructureError):
    """A tool or service we do not control failed or is unavailable."""

    category = ErrorCategory.EXTERNAL_SYSTEM
    code = "external.error"
    default_user_message = "An external tool or service reported a problem."


class ToolNotFoundError(ExternalSystemError):
    code = "external.tool_not_found"

    def __init__(self, executable: str) -> None:
        super().__init__(
            f"Executable not found: {executable}",
            user_message=(
                f"The required tool '{executable}' could not be found. "
                "Install it or check the tool settings."
            ),
            details={"executable": executable},
        )
        self.executable = executable


class ProcessTimeoutError(ExternalSystemError):
    code = "external.timeout"
    default_user_message = "An external tool did not finish in time."


class ProcessFailedError(ExternalSystemError):
    code = "external.process_failed"

    def __init__(self, executable: str, exit_code: int, stderr_tail: str) -> None:
        super().__init__(
            f"{executable} exited with code {exit_code}",
            user_message=f"The tool '{executable}' reported an error (exit code {exit_code}).",
            details={"executable": executable, "exit_code": exit_code, "stderr": stderr_tail},
        )
        self.exit_code = exit_code


# --- Configuration -------------------------------------------------------- #
class ConfigurationError(MediaHouseError):
    category = ErrorCategory.CONFIGURATION
    code = "configuration.error"
    default_user_message = "The application configuration is invalid."
    recoverable = False


# --- Unexpected ----------------------------------------------------------- #
class UnexpectedError(MediaHouseError):
    """Wrapper for programmer errors / unknown failures. Always chained ``from`` the cause."""

    category = ErrorCategory.UNEXPECTED
    code = "unexpected.error"
    default_user_message = "An unexpected error occurred. The problem has been recorded."
