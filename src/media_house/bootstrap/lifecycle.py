"""Startup phases and ordered, failure-tolerant shutdown."""

from collections.abc import Callable
from enum import StrEnum

from media_house.shared.errors import ErrorCategory, MediaHouseError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


class StartupPhase(StrEnum):
    CONFIGURATION = "configuration"
    LOGGING = "logging"
    ENVIRONMENT = "environment"
    INFRASTRUCTURE = "infrastructure"
    SERVICES = "services"
    PRESENTATION = "presentation"


class StartupError(MediaHouseError):
    """Startup failed in a known phase. Carries the cause's id and user message."""

    category = ErrorCategory.INFRASTRUCTURE
    code = "startup.failed"
    default_user_message = "Media-House could not start."
    recoverable = False

    def __init__(self, phase: StartupPhase, cause: BaseException) -> None:
        cause_id = cause.error_id if isinstance(cause, MediaHouseError) else None
        user_message = (
            cause.user_message if isinstance(cause, MediaHouseError) else self.default_user_message
        )
        super().__init__(
            f"Startup failed during {phase.value}: {cause}",
            user_message=user_message,
            details={"phase": phase.value},
            error_id=cause_id,
        )
        self.phase = phase


class ShutdownStack:
    """LIFO cleanup callbacks. One failing callback never prevents the others from running."""

    def __init__(self) -> None:
        self._callbacks: list[tuple[str, Callable[[], None]]] = []

    def push(self, name: str, callback: Callable[[], None]) -> None:
        self._callbacks.append((name, callback))

    def close(self) -> None:
        while self._callbacks:
            name, callback = self._callbacks.pop()
            try:
                callback()
            except Exception:  # noqa: BLE001 - shutdown must continue past a failing step
                _log.exception("Shutdown step failed", step=name)
