"""Business errors of pacing intelligence."""

from media_house.shared.errors import DomainError


class PacingIntelligenceError(DomainError):
    code = "pacing_intelligence.error"


class InvalidPacingConfig(PacingIntelligenceError):
    code = "pacing_intelligence.invalid_config"

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Invalid pacing configuration: {detail}",
            user_message=f"The pacing settings are not valid: {detail}",
        )


class MissingEvidence(PacingIntelligenceError):
    """No speech analysis and no shots: there is nothing to measure."""

    code = "pacing_intelligence.missing_evidence"

    def __init__(self, asset_id: str) -> None:
        super().__init__(
            f"No speech or picture analysis for asset {asset_id}",
            user_message="Analyse the recording's audio or video first.",
        )


class NothingToAnalyse(PacingIntelligenceError):
    code = "pacing_intelligence.nothing_to_analyse"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Nothing to analyse: {reason}",
            user_message="The recording has no speech or shots to analyse the pacing of.",
        )


class InvalidPacingAnalysis(PacingIntelligenceError):
    """The analyser's own result failed validation. It is a bug, never stored."""

    code = "pacing_intelligence.invalid_analysis"

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "Pacing analysis rejected: " + "; ".join(problems[:3]),
            user_message="The pacing analysis could not be built consistently.",
        )


class InvalidPacingDocument(PacingIntelligenceError):
    """A stored analysis that cannot be read back (damaged, or from an unknown schema)."""

    code = "pacing_intelligence.invalid_document"

    def __init__(self, detail: str) -> None:
        super().__init__(f"Stored pacing analysis unusable: {detail}")
