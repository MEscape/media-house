"""Expected failures of audio intelligence."""

from media_house.shared.errors import DomainError


class AudioIntelligenceError(DomainError):
    """Base class for failures the user can act on."""

    code = "audio_intelligence.failed"


class NoAudioTrack(AudioIntelligenceError):
    code = "audio_intelligence.no_audio_track"

    def __init__(self) -> None:
        super().__init__(
            "The media has no audio stream",
            user_message="This file has no audio track to transcribe.",
        )


class UnreadableAudio(AudioIntelligenceError):
    code = "audio_intelligence.unreadable"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Media cannot be read: {reason}",
            user_message="This file could not be read as audio or video.",
        )


class AlignmentUnavailable(AudioIntelligenceError):
    """Forced alignment is impossible and the configuration forbids a lower-quality fallback."""

    code = "audio_intelligence.alignment_unavailable"

    def __init__(self, language: str, reason: str) -> None:
        super().__init__(
            f"Word alignment unavailable for language {language!r}: {reason}",
            user_message=(
                f"Word-level alignment is not available for language '{language}'. "
                "Choose an alignment model or allow segment-level timestamps."
            ),
        )


class InvalidTranscript(AudioIntelligenceError):
    """A stored or supplied transcript document is malformed or from an unknown schema."""

    code = "audio_intelligence.invalid_transcript"

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid transcript: {reason}",
            user_message="The stored transcript is damaged or has an unsupported format.",
        )


class InvalidTimeline(AudioIntelligenceError):
    """Fusion produced a timeline that violates its own contract; nothing is stored or cached."""

    code = "audio_intelligence.invalid_timeline"

    def __init__(self, findings: tuple[str, ...]) -> None:
        super().__init__(
            "Audio analysis produced an inconsistent timeline: " + "; ".join(findings),
            user_message="The audio analysis result was inconsistent and was discarded.",
        )
