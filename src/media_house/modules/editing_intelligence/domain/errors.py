"""Business errors of editing intelligence."""

from media_house.shared.errors import DomainError


class EditingIntelligenceError(DomainError):
    code = "editing_intelligence.error"


class InvalidEditConfig(EditingIntelligenceError):
    code = "editing_intelligence.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid editing configuration: {detail}",
            user_message=f"The editing settings are not valid: {detail}",
        )


class MissingStoryPlan(EditingIntelligenceError):
    """No story plan exists for the recordings and none was supplied or asked for."""

    code = "editing_intelligence.missing_story_plan"

    def __init__(self, asset_ids: tuple[str, ...]) -> None:
        super().__init__(
            f"No story plan for {', '.join(asset_ids)}",
            user_message="Plan the story first, or ask for the story plan to be produced.",
        )


class InvalidStoryInput(EditingIntelligenceError):
    """The story plan the edit would rest on does not validate. Nothing is edited from it."""

    code = "editing_intelligence.invalid_story"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Story plan rejected: " + "; ".join(problems[:3]),
            user_message="The story plan is not consistent, so no edit was planned from it.",
        )


class NothingToEdit(EditingIntelligenceError):
    code = "editing_intelligence.nothing_to_edit"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Nothing to edit: {reason}",
            user_message="The story plan has no material to build an edit from.",
        )


class InvalidEditPlan(EditingIntelligenceError):
    """The editor's own result failed validation. It is a bug, never stored."""

    code = "editing_intelligence.invalid_plan"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Edit plan rejected: " + "; ".join(problems[:3]),
            user_message="The edit plan could not be built consistently.",
        )


class InvalidEditDocument(EditingIntelligenceError):
    """A stored plan that cannot be read back (damaged, or from an unknown schema)."""

    code = "editing_intelligence.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored edit plan unusable: {detail}")
