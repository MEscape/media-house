"""Business errors of B-roll intelligence."""

from media_house.shared.errors import DomainError


class BrollIntelligenceError(DomainError):
    code = "broll_intelligence.error"


class InvalidBrollConfig(BrollIntelligenceError):
    code = "broll_intelligence.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid B-roll configuration: {detail}",
            user_message=f"The B-roll settings are not valid: {detail}",
        )


class NothingToCover(BrollIntelligenceError):
    """No passage was found that supplementary footage could serve."""

    code = "broll_intelligence.nothing_to_cover"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Nothing to cover: {reason}",
            user_message="No passage was found that supplementary footage would help.",
        )


class MissingTargetEvidence(BrollIntelligenceError):
    """Neither a content analysis nor a story plan exists: there is no passage to match."""

    code = "broll_intelligence.missing_target_evidence"

    def __init__(self, asset_id: str) -> None:
        super().__init__(
            f"No content analysis or story for asset {asset_id}",
            user_message="Analyse what the recording says (content analysis) first.",
        )


class InvalidBrollAnalysis(BrollIntelligenceError):
    """The analyser's own result failed validation. It is a bug, never stored."""

    code = "broll_intelligence.invalid_analysis"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "B-roll analysis rejected: " + "; ".join(problems[:3]),
            user_message="The B-roll analysis could not be built consistently.",
        )


class InvalidBrollDocument(BrollIntelligenceError):
    """A stored analysis that cannot be read back (damaged, or from an unknown schema)."""

    code = "broll_intelligence.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored B-roll analysis unusable: {detail}")
