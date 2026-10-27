"""Business errors of story architecture."""

from media_house.shared.errors import DomainError


class StoryArchitectureError(DomainError):
    code = "story_architecture.error"


class InvalidStoryConfig(StoryArchitectureError):
    code = "story_architecture.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid story configuration: {detail}",
            user_message=f"The story settings are not valid: {detail}",
        )


class MissingAnalysis(StoryArchitectureError):
    """The content analysis the plan rests on is not stored and was not asked for."""

    code = "story_architecture.missing_analysis"

    def __init__(self, asset_id: str) -> None:
        super().__init__(
            f"No content analysis for asset {asset_id}",
            user_message="Analyse the recording's content first, or ask for it to be produced.",
        )


class NothingToPlan(StoryArchitectureError):
    code = "story_architecture.nothing_to_plan"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Nothing to plan: {reason}",
            user_message="The recording has no analysed speech to build a story from.",
        )


class InvalidStoryPlan(StoryArchitectureError):
    """The planner's own result failed validation. It is a bug, never stored."""

    code = "story_architecture.invalid_plan"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Story plan rejected: " + "; ".join(problems[:3]),
            user_message="The story plan could not be built consistently.",
        )


class InvalidStoryDocument(StoryArchitectureError):
    """A stored plan that cannot be read back (damaged, or from an unknown schema)."""

    code = "story_architecture.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored story plan unusable: {detail}")
