"""The Audio Intelligence PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``audio_intelligence``.

``execute`` is blocking (FFmpeg, speech recognition, alignment, file I/O): run it as a job.
The result carries a typed ``Transcript`` (never raw WhisperX objects) plus the Media Library
assets it lives in. ``created`` is ``False`` when the identical transcript already existed and
no speech engine ran.

Typical use from another module::

    result = audio_engine.execute(TranscribeAudioCommand(voiceover_asset_id), ctx)
    if isinstance(result, Ok):
        timeline = result.value.transcript
        word = timeline.word_at(12.5)
        frame = timeline.to_frame(12.5, fps=30)
"""

from typing import Protocol

from media_house.modules.audio_intelligence.application.analyze_audio import (
    AnalysisResult,
    AnalyzeAudioCommand,
    AnalyzeError,
)
from media_house.modules.audio_intelligence.application.transcribe_audio import (
    TranscribeAudioCommand,
    TranscribeError,
    TranscriptionResult,
)
from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    BREATH,
    ENERGY_DROP,
    ENERGY_SPIKE,
    LAUGHTER,
    PAUSE,
    PITCH_FALL,
    PITCH_RISE,
    SILENCE,
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.baseline import Baseline
from media_house.modules.audio_intelligence.domain.analysis.config import (
    AcousticConfig,
    AnalysisConfig,
    AudioIntelligenceConfig,
    ScoringConfig,
)
from media_house.modules.audio_intelligence.domain.analysis.features import (
    EnergyChange,
    EnergySummary,
    PitchChange,
    PitchSummary,
    RateSummary,
    ScoredSignal,
    SegmentAcoustics,
    WordAcoustics,
)
from media_house.modules.audio_intelligence.domain.analysis.pauses import Pause
from media_house.modules.audio_intelligence.domain.analysis.scoring import (
    BROLL,
    CUT,
    MOMENT,
    MUSIC_BREAK,
    MUSIC_DUCK,
    ZOOM_EMPHASIS,
    EditingSignal,
    Scorer,
    WordSignals,
)
from media_house.modules.audio_intelligence.domain.analysis.serialization import (
    timeline_from_json,
    timeline_to_json,
)
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AcousticSample,
    AnalysisMetadata,
    AudioIntelligenceTimeline,
    TimelineSegment,
    TimelineWord,
)
from media_house.modules.audio_intelligence.domain.analysis.validation import validate_timeline
from media_house.modules.audio_intelligence.domain.errors import (
    AlignmentUnavailable,
    AudioIntelligenceError,
    InvalidTranscript,
    NoAudioTrack,
    UnreadableAudio,
)
from media_house.modules.audio_intelligence.domain.frames import Fps, FrameRounding
from media_house.modules.audio_intelligence.domain.raw import (
    ALIGNMENT_FORCED,
    ALIGNMENT_NONE,
    ALIGNMENT_SEGMENT,
)
from media_house.modules.audio_intelligence.domain.serialization import (
    SCHEMA_VERSION,
    from_json,
    to_json,
)
from media_house.modules.audio_intelligence.domain.text import NormalizationOptions
from media_house.modules.audio_intelligence.domain.transcript import (
    Transcript,
    TranscriptMetadata,
    TranscriptSegment,
    TranscriptWord,
    WordTiming,
)
from media_house.modules.audio_intelligence.domain.validation import (
    Severity,
    ValidationIssue,
    validate_transcript,
)
from media_house.modules.audio_intelligence.domain.values import (
    AlignmentFailurePolicy,
    PreparationConfig,
    TranscriptionConfig,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class AudioEngine(Protocol):
    """Transcribe an audio/video library asset into a reusable word-level timeline."""

    def execute(
        self,
        command: TranscribeAudioCommand,
        ctx: JobContext,
    ) -> Result[TranscriptionResult, TranscribeError]: ...


class AudioAnalyzer(Protocol):
    """Analyse an audio/video library asset into ONE synchronized Audio Intelligence Timeline.

    Blocking (run it as a job). Reuses transcript, acoustic measurements and the final
    timeline from the Media Library when their inputs and versions are unchanged.

    The timeline offers evidence (``timeline.word_at(t).pitch`` / ``.energy`` / ``.rate``,
    ``emphasis_score``, ``events_between``, ``pause_after``) and generic editing SIGNALS
    (``moment_at``, ``signals_between``). Editing decisions belong to downstream consumers.
    """

    def execute(
        self,
        command: AnalyzeAudioCommand,
        ctx: JobContext,
    ) -> Result[AnalysisResult, AnalyzeError]: ...


__all__ = [
    "ALIGNMENT_FORCED",
    "ALIGNMENT_NONE",
    "ALIGNMENT_SEGMENT",
    "BREATH",
    "BROLL",
    "CUT",
    "ENERGY_DROP",
    "ENERGY_SPIKE",
    "LAUGHTER",
    "MOMENT",
    "MUSIC_BREAK",
    "MUSIC_DUCK",
    "PAUSE",
    "PITCH_FALL",
    "PITCH_RISE",
    "SCHEMA_VERSION",
    "SILENCE",
    "ZOOM_EMPHASIS",
    "AcousticConfig",
    "AcousticSample",
    "AcousticTrack",
    "AlignmentFailurePolicy",
    "AlignmentUnavailable",
    "AnalysisConfig",
    "AnalysisMetadata",
    "AnalysisResult",
    "AnalyzeAudioCommand",
    "AnalyzeError",
    "AudioAnalyzer",
    "AudioEngine",
    "AudioEvent",
    "AudioIntelligenceConfig",
    "AudioIntelligenceError",
    "AudioIntelligenceTimeline",
    "Baseline",
    "EditingSignal",
    "EnergyChange",
    "EnergySummary",
    "Fps",
    "FrameRounding",
    "InvalidTranscript",
    "NoAudioTrack",
    "NormalizationOptions",
    "Pause",
    "PitchChange",
    "PitchSummary",
    "PreparationConfig",
    "RateSummary",
    "ScoredSignal",
    "Scorer",
    "ScoringConfig",
    "SegmentAcoustics",
    "Severity",
    "TimelineSegment",
    "TimelineWord",
    "TranscribeAudioCommand",
    "TranscribeError",
    "Transcript",
    "TranscriptMetadata",
    "TranscriptSegment",
    "TranscriptWord",
    "TranscriptionConfig",
    "TranscriptionResult",
    "UnreadableAudio",
    "ValidationIssue",
    "WordAcoustics",
    "WordSignals",
    "WordTiming",
    "from_json",
    "timeline_from_json",
    "timeline_to_json",
    "to_json",
    "validate_timeline",
    "validate_transcript",
]
