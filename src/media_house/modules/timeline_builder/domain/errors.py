"""Business errors of the timeline builder."""

from media_house.shared.errors import DomainError


class TimelineBuilderError(DomainError):
    code = "timeline_builder.error"


class InvalidBuilderConfig(TimelineBuilderError):
    code = "timeline_builder.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid builder configuration: {detail}",
            user_message=f"The timeline builder settings are not valid: {detail}",
        )


class InvalidSpecification(TimelineBuilderError):
    """The specification handed in is not one this builder can read."""

    code = "timeline_builder.invalid_specification"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid timeline specification: {detail}",
            user_message=f"The timeline specification cannot be built from: {detail}",
        )


class TimelineNotBuildable(TimelineBuilderError):
    """ERROR findings: the timeline cannot be built safely, so nothing is stored."""

    code = "timeline_builder.not_buildable"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Timeline not buildable: " + "; ".join(problems[:5]),
            user_message="The timeline cannot be built safely: " + "; ".join(problems[:3]),
            details={"problems": problems},
        )
        self.problems = tuple(problems)


class SpecificationRequired(TimelineBuilderError):
    """Neither a specification nor a stored one for the recording."""

    code = "timeline_builder.specification_required"

    def __init__(self) -> None:
        super().__init__(
            "No timeline specification was given or stored",
            user_message="There is no compiled timeline specification to build from.",
        )


class InvalidHumanEdit(TimelineBuilderError):
    code = "timeline_builder.invalid_edit"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid timeline edit: {detail}",
            user_message=f"That change cannot be recorded: {detail}",
        )


class NothingBuilt(TimelineBuilderError):
    """A change was recorded for a recording that has no built timeline yet."""

    code = "timeline_builder.nothing_built"

    def __init__(self) -> None:
        super().__init__(
            "No built timeline to change", user_message="There is no built timeline to change."
        )


class InvalidBuiltDocument(TimelineBuilderError):
    """A stored build that cannot be read back (damaged, or from an unknown schema)."""

    code = "timeline_builder.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored built timeline unusable: {detail}")
