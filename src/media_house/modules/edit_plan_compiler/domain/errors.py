"""Business errors of the edit plan compiler."""

from media_house.shared.errors import DomainError


class EditPlanCompilerError(DomainError):
    code = "edit_plan_compiler.error"


class InvalidCompilerConfig(EditPlanCompilerError):
    code = "edit_plan_compiler.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid compiler configuration: {detail}",
            user_message=f"The timeline compiler settings are not valid: {detail}",
        )


class InvalidCompileSource(EditPlanCompilerError):
    """The edit handed in is malformed (the caller's mapping is wrong)."""

    code = "edit_plan_compiler.invalid_source"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid compile source: {detail}",
            user_message=f"The edit given to the timeline compiler is not valid: {detail}",
        )


class NothingToCompile(EditPlanCompilerError):
    """The edit has no clip at all."""

    code = "edit_plan_compiler.nothing_to_compile"

    def __init__(self) -> None:
        super().__init__(
            "The edit has no clip", user_message="There is nothing to put on a timeline."
        )


class TimelineBlocked(EditPlanCompilerError):
    """CRITICAL findings: the timeline cannot be executed safely, so nothing is stored."""

    code = "edit_plan_compiler.timeline_blocked"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Timeline blocked: " + "; ".join(problems[:5]),
            user_message="The timeline cannot be built safely: " + "; ".join(problems[:3]),
            details={"problems": problems},
        )
        self.problems = tuple(problems)


class StyleRequired(EditPlanCompilerError):
    """No resolved channel style: the creator identity cannot be applied or checked."""

    code = "edit_plan_compiler.style_required"

    def __init__(self) -> None:
        super().__init__(
            "No resolved visual style was given or stored",
            user_message="The channel style is missing, so the creator identity cannot be applied.",
        )


class InvalidTimelineDocument(EditPlanCompilerError):
    """A stored specification that cannot be read back (damaged, or from an unknown schema)."""

    code = "edit_plan_compiler.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored timeline specification unusable: {detail}")
