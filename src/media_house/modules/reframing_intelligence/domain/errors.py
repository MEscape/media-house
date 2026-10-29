"""Business errors of reframing intelligence."""

from media_house.shared.errors import DomainError


class ReframingIntelligenceError(DomainError):
    code = "reframing_intelligence.error"


class InvalidReframingConfig(ReframingIntelligenceError):
    code = "reframing_intelligence.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid reframing configuration: {detail}",
            user_message=f"The reframing settings are not valid: {detail}",
        )


class MissingPrerequisite(ReframingIntelligenceError):
    """The picture analysis of a recording is not stored and was not supplied."""

    code = "reframing_intelligence.missing_prerequisite"

    def __init__(self, asset_id: str, what: str) -> None:
        super().__init__(
            f"Recording {asset_id} has no {what}",
            user_message=f"The recording needs a {what} before framing can be recommended.",
            details={"asset_id": asset_id},
        )


class NothingToFrame(ReframingIntelligenceError):
    """The analysed recordings contain no shot to frame."""

    code = "reframing_intelligence.nothing_to_frame"

    def __init__(self) -> None:
        super().__init__(
            "The recordings contain no shot",
            user_message="There is no shot to recommend framing for.",
        )


class InvalidReframingAnalysis(ReframingIntelligenceError):
    """The analyser's own result failed validation. It is a bug, never stored."""

    code = "reframing_intelligence.invalid_analysis"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Reframing analysis rejected: " + "; ".join(problems[:3]),
            user_message="The framing recommendations could not be built consistently.",
        )


class InvalidReframingDocument(ReframingIntelligenceError):
    """A stored analysis that cannot be read back (damaged, or from an unknown schema)."""

    code = "reframing_intelligence.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored reframing analysis unusable: {detail}")
