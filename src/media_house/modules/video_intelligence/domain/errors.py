"""Expected failures of video intelligence (the user can act on them)."""

from media_house.shared.errors import DomainError


class VideoIntelligenceError(DomainError):
    """Base class for failures the user can act on."""

    code = "video_intelligence.failed"


class NoVideoStream(VideoIntelligenceError):
    code = "video_intelligence.no_video_stream"

    def __init__(self) -> None:
        super().__init__(
            "The media has no video stream",
            user_message="This file has no video to analyse.",
        )


class UnreadableVideo(VideoIntelligenceError):
    code = "video_intelligence.unreadable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Video cannot be read: {reason}",
            user_message="This video could not be decoded. It may be damaged or truncated.",
        )


class InspectionUnavailable(VideoIntelligenceError):
    """The technical facts the analysis needs could neither be found nor obtained."""

    code = "video_intelligence.inspection_unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Technical media facts unavailable: {reason}",
            user_message="The technical details of this video could not be read.",
        )


class InvalidProfile(VideoIntelligenceError):
    code = "video_intelligence.invalid_profile"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid processing profile: {reason}",
            user_message=f"The analysis settings are not valid: {reason}",
        )


class InvalidAnalysisDocument(VideoIntelligenceError):
    """A stored document is malformed or from an unsupported schema."""

    code = "video_intelligence.invalid_document"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid analysis document: {reason}",
            user_message="A stored analysis is damaged or has an unsupported format.",
        )


class InvalidAnalysis(VideoIntelligenceError):
    """Assembly produced a result that violates its own contract; nothing is stored."""

    code = "video_intelligence.invalid_analysis"

    def __init__(self, findings: tuple[str, ...]) -> None:
        super().__init__(
            "The analysis produced an inconsistent result: " + "; ".join(findings),
            user_message="The video analysis result was inconsistent and was discarded.",
        )
