"""Business errors of the render manager."""

from media_house.shared.errors import DomainError


class RenderManagerError(DomainError):
    code = "render_manager.error"


class InvalidRenderProfile(RenderManagerError):
    code = "render_manager.invalid_profile"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid render profile: {detail}",
            user_message=f"The render profile is not valid: {detail}",
        )


class UnknownRenderProfile(RenderManagerError):
    code = "render_manager.unknown_profile"

    def __init__(self, profile_id: str, known: list[str]) -> None:
        super().__init__(
            f"Unknown render profile {profile_id!r}",
            user_message=f"There is no render profile '{profile_id}' (known: {', '.join(known)}).",
        )


class RenderRefused(RenderManagerError):
    """The request is not one this render may carry out (reasons say why)."""

    code = "render_manager.refused"

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(
            "Render refused: " + "; ".join(reasons),
            user_message="The render was not started: " + "; ".join(reasons[:3]),
            details={"reasons": reasons},
        )
        self.reasons = tuple(reasons)


class NotControllable(RenderManagerError):
    code = "render_manager.not_controllable"

    def __init__(self, what: str, why: str) -> None:
        super().__init__(
            f"Cannot {what}: {why}", user_message=f"The render cannot be {what}d: {why}"
        )


class UnknownRender(RenderManagerError):
    code = "render_manager.unknown_render"

    def __init__(self, render_id: str) -> None:
        super().__init__(
            f"Unknown render {render_id}", user_message=f"There is no render '{render_id}'."
        )


class InvalidRenderDocument(RenderManagerError):
    code = "render_manager.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Stored render report is unreadable: {detail}",
            user_message="A stored render report could not be read.",
        )
