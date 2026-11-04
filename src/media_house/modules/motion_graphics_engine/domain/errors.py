"""Business errors of the motion graphics engine."""

from media_house.shared.errors import DomainError


class MotionGraphicsError(DomainError):
    code = "motion_graphics_engine.error"


class InvalidGraphicsConfig(MotionGraphicsError):
    code = "motion_graphics_engine.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid motion graphics configuration: {detail}",
            user_message=f"The motion graphics settings are not valid: {detail}",
        )


class InvalidGraphicRequest(MotionGraphicsError):
    """A request that cannot be built at all (unknown zone, no duration, missing words...)."""

    code = "motion_graphics_engine.invalid_request"

    def __init__(self, request_id: str, detail: str) -> None:
        super().__init__(
            f"Graphic {request_id}: {detail}",
            user_message=f"The graphic {request_id} cannot be built: {detail}",
            details={"request_id": request_id},
        )


class StyleMismatch(MotionGraphicsError):
    """The style does not fit the request (other output size, unknown style document)."""

    code = "motion_graphics_engine.style_mismatch"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Style mismatch: {detail}",
            user_message=f"The visual style cannot be used for these graphics: {detail}",
        )


class RendererNotAvailable(MotionGraphicsError):
    """HyperFrames (or its browser) is not installed. Nothing is downloaded during a run."""

    code = "motion_graphics_engine.renderer_not_available"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Graphics renderer not available: {detail}",
            user_message=f"The graphics renderer is not set up: {detail}",
        )


class RenderFailed(MotionGraphicsError):
    code = "motion_graphics_engine.render_failed"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Graphic render failed: {detail}",
            user_message=f"A graphic could not be rendered: {detail}",
        )


class NothingToRender(MotionGraphicsError):
    code = "motion_graphics_engine.nothing_to_render"

    def __init__(self) -> None:
        super().__init__("No graphic was requested", user_message="There is no graphic to render.")


class InvalidGraphicsPlan(MotionGraphicsError):
    """The planner's own result failed validation (a CRITICAL issue). Never stored."""

    code = "motion_graphics_engine.invalid_plan"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Graphics plan rejected: " + "; ".join(problems[:3]),
            user_message="The graphics plan could not be built consistently.",
        )


class InvalidGraphicsDocument(MotionGraphicsError):
    """A stored plan that cannot be read back (damaged, or from an unknown schema)."""

    code = "motion_graphics_engine.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Unreadable graphics plan: {detail}",
            user_message="A stored graphics plan could not be read.",
        )
