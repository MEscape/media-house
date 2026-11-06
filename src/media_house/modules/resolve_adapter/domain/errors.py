"""Business errors of the Resolve adapter."""

from media_house.shared.errors import DomainError


class ResolveAdapterError(DomainError):
    code = "resolve_adapter.error"


class ResolveUnavailable(ResolveAdapterError):
    """No usable connection to Resolve. ``state`` says why (not installed, not running, ...)."""

    code = "resolve_adapter.unavailable"

    def __init__(self, state: str, detail: str) -> None:
        super().__init__(
            f"DaVinci Resolve is not available ({state}): {detail}",
            user_message=f"DaVinci Resolve cannot be reached ({state}): {detail}",
            details={"state": state},
        )
        self.state = state


class ResolveCallFailed(ResolveAdapterError):
    """Resolve answered a call with a failure (``False`` / ``None``) or raised."""

    code = "resolve_adapter.call_failed"

    def __init__(self, operation: str, detail: str = "") -> None:
        text = f"Resolve could not {operation}" + (f": {detail}" if detail else "")
        super().__init__(text, user_message=text, details={"operation": operation})
        self.operation = operation


class ResolveConnectionLost(ResolveAdapterError):
    """Resolve went away in the middle of an execution. What it had done is unknown until the
    state is read again; nothing is assumed."""

    code = "resolve_adapter.connection_lost"

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"The connection to Resolve was lost during: {operation}",
            user_message="The connection to DaVinci Resolve was lost. Start it again and re-run; "
            "what was already done is detected, not repeated.",
            details={"operation": operation},
        )
        self.operation = operation


class InvalidAdapterConfig(ResolveAdapterError):
    code = "resolve_adapter.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid Resolve adapter configuration: {detail}",
            user_message=f"The Resolve settings are not valid: {detail}",
        )


class TimelineNotExecutable(ResolveAdapterError):
    """The built timeline cannot be carried out safely; nothing was changed in Resolve."""

    code = "resolve_adapter.not_executable"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Timeline not executable: " + "; ".join(problems[:5]),
            user_message="The timeline cannot be sent to Resolve: " + "; ".join(problems[:3]),
            details={"problems": problems},
        )
        self.problems = tuple(problems)


class ProjectConflict(ResolveAdapterError):
    """The target project holds work this run does not own. It is never replaced silently."""

    code = "resolve_adapter.project_conflict"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Resolve project conflict: {detail}",
            user_message=f"A Resolve project is in the way: {detail}",
        )


class ConfigurationNotApplied(ResolveAdapterError):
    """A setting that cannot change later (frame rate, resolution) is not what the timeline
    needs. Building on it would give a wrong project."""

    code = "resolve_adapter.configuration_not_applied"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Project settings not applied: " + "; ".join(problems[:5]),
            user_message="Resolve did not accept the project settings: " + "; ".join(problems[:3]),
            details={"problems": problems},
        )
        self.problems = tuple(problems)


class NothingBound(ResolveAdapterError):
    """A synchronisation was asked for, but this timeline was never sent to Resolve."""

    code = "resolve_adapter.nothing_bound"

    def __init__(self) -> None:
        super().__init__(
            "No Resolve project is bound to this timeline",
            user_message="This timeline has not been sent to DaVinci Resolve yet.",
        )


class InvalidBindingDocument(ResolveAdapterError):
    code = "resolve_adapter.invalid_binding"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid stored Resolve binding: {detail}",
            user_message="The stored link to the Resolve project cannot be read.",
        )


class RenderBlocked(ResolveAdapterError):
    """A render would deliver something wrong (open conflicts, proxy media, an unfinished
    project). The reasons are listed."""

    code = "resolve_adapter.render_blocked"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Render blocked: " + "; ".join(problems[:5]),
            user_message="The render was not started: " + "; ".join(problems[:3]),
            details={"problems": problems},
        )
        self.problems = tuple(problems)
