"""Expected failures of content intelligence."""

from media_house.shared.errors import DomainError


class ContentIntelligenceError(DomainError):
    """Base class for failures the user can act on."""

    code = "content_intelligence.failed"


class TranscriptUnavailable(ContentIntelligenceError):
    """No transcript could be obtained for the request (nothing is invented in its place)."""

    code = "content_intelligence.transcript_unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"No transcript available: {reason}",
            user_message="There is no transcript to analyse for this media.",
        )


class InvalidTranscriptInput(ContentIntelligenceError):
    """A supplied transcript breaks its contract (ordering, ranges, empty ids)."""

    code = "content_intelligence.invalid_transcript"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid transcript: {reason}",
            user_message="The transcript is malformed and cannot be analysed.",
        )


class InvalidConfiguration(ContentIntelligenceError):
    code = "content_intelligence.invalid_configuration"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid configuration: {reason}",
            user_message="The analysis settings are not valid.",
        )


class InvalidAnalysisDocument(ContentIntelligenceError):
    """A stored result is damaged, from a newer schema, or breaks a model rule."""

    code = "content_intelligence.invalid_document"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid analysis document: {reason}",
            user_message="The stored content analysis is damaged or has an unsupported format.",
        )


class InvalidAnalysis(ContentIntelligenceError):
    """The engine produced a result that violates its own contract (never stored)."""

    code = "content_intelligence.invalid_analysis"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Analysis violates its contract: {reason}",
            user_message="The content analysis could not be completed consistently.",
        )
