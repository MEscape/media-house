"""Business errors of continuity intelligence."""

from media_house.shared.errors import DomainError


class ContinuityIntelligenceError(DomainError):
    code = "continuity_intelligence.error"


class InvalidContinuityConfig(ContinuityIntelligenceError):
    code = "continuity_intelligence.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid continuity configuration: {detail}",
            user_message=f"The continuity settings are not valid: {detail}",
        )


class InvalidSequence(ContinuityIntelligenceError):
    """The proposed sequence cannot be analysed (empty, a clip that ends before it starts ...)."""

    code = "continuity_intelligence.invalid_sequence"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Invalid sequence: " + "; ".join(problems[:3]),
            user_message="The proposed edit cannot be checked for continuity: "
            + "; ".join(problems[:2]),
        )


class NothingToJudge(ContinuityIntelligenceError):
    """The sequence has no cut between two picture clips."""

    code = "continuity_intelligence.nothing_to_judge"

    def __init__(self) -> None:
        super().__init__(
            "The sequence has no cut between two picture clips",
            user_message="The edit has no cut between two clips to check.",
        )


class InvalidContinuityAnalysis(ContinuityIntelligenceError):
    """The analyser's own result failed validation. It is a bug, never stored."""

    code = "continuity_intelligence.invalid_analysis"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Continuity analysis rejected: " + "; ".join(problems[:3]),
            user_message="The continuity analysis could not be built consistently.",
        )


class InvalidContinuityDocument(ContinuityIntelligenceError):
    """A stored analysis that cannot be read back (damaged, or from an unknown schema)."""

    code = "continuity_intelligence.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored continuity analysis unusable: {detail}")
