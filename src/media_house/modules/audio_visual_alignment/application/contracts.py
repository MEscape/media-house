"""The Audio-Visual Alignment PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``audio_visual_alignment``.

``AudioVisualAligner.execute`` reads stored audio, video and content analyses; with
``Acquire.REQUEST`` it may ask their owners to produce missing ones, so run it as a job. It
returns a typed ``AlignmentDocument`` that RELATES what is heard to what is seen: the clock, what
the picture shows while speech is heard, which shots go with which passages, which sounds
coincide with which movements. It records evidence and uncertainty. It never decides what to
cut, show or replace, and never touches media or a timeline.

    result = aligner.execute(AlignCommand(video_asset_id), ctx)
    if isinstance(result, Ok):
        doc = result.value.document
        source = doc.speech_source("12")          # what is on screen while segment 12 is heard
        options = doc.matches_for("seg_0003")     # ranked candidate shots for a passage

Times: ``start``/``end`` and every ``*_seconds`` field are on the common clock (the container
timeline). Picture-side references keep the video analysis's own ``FrameTime`` and ``TimeRange``.
Stored results are read without recomputation through ``AlignmentCatalog``.
"""

from typing import Protocol

from media_house.modules.audio_visual_alignment.application.align import (
    AlignCommand,
    AlignError,
    AlignmentResult,
    TextEmbedder,
)
from media_house.modules.audio_visual_alignment.domain.config import (
    DEFAULT_PROFILE,
    PROFILES,
    AlignmentProfile,
    get_profile,
)
from media_house.modules.audio_visual_alignment.domain.errors import (
    AlignmentError,
    IncompatibleInputs,
    InvalidAlignmentConfig,
    InvalidAlignmentDocument,
    MissingPrerequisite,
)
from media_house.modules.audio_visual_alignment.domain.model import (
    AlignmentDocument,
    BoundaryRelation,
    ClockCorrection,
    Evidence,
    Flag,
    InputRecord,
    LagProbe,
    Opportunity,
    Provenance,
    Scores,
    ShotSpeech,
    SoundAction,
    SpeakerLink,
    SpeechSource,
    StageReport,
    SyncAssessment,
    TextMatch,
    VideoRef,
)
from media_house.modules.audio_visual_alignment.domain.serialization import (
    alignment_from_json,
    alignment_to_json,
)
from media_house.modules.audio_visual_alignment.domain.validation import validate_document
from media_house.modules.audio_visual_alignment.domain.values import (
    ALIGNMENT_VERSION,
    SCHEMA_VERSION,
    SCORING_VERSION,
    VOCABULARY_VERSION,
    Acquire,
    BoundaryKind,
    CorrectionSource,
    EvidenceGrade,
    EvidenceKind,
    FlagKind,
    HowObtained,
    LinkState,
    OpportunityKind,
    ProbeState,
    Severity,
    StageState,
    Visibility,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class AudioVisualAligner(Protocol):
    """Relate the stored audio, video and content analyses of a recording."""

    def execute(
        self, command: AlignCommand, ctx: JobContext
    ) -> Result[AlignmentResult, AlignError]: ...


class AlignmentCatalog(Protocol):
    """Read stored alignments. Nothing is computed."""

    def latest(
        self, video_asset_id: str
    ) -> Result[tuple[AlignmentDocument, MediaAssetDto] | None, MediaError]: ...


__all__ = [
    "ALIGNMENT_VERSION",
    "DEFAULT_PROFILE",
    "PROFILES",
    "SCHEMA_VERSION",
    "SCORING_VERSION",
    "VOCABULARY_VERSION",
    "Acquire",
    "AlignCommand",
    "AlignError",
    "AlignmentCatalog",
    "AlignmentDocument",
    "AlignmentError",
    "AlignmentProfile",
    "AlignmentResult",
    "AudioVisualAligner",
    "BoundaryKind",
    "BoundaryRelation",
    "ClockCorrection",
    "CorrectionSource",
    "Evidence",
    "EvidenceGrade",
    "EvidenceKind",
    "Flag",
    "FlagKind",
    "HowObtained",
    "IncompatibleInputs",
    "InputRecord",
    "InvalidAlignmentConfig",
    "InvalidAlignmentDocument",
    "LagProbe",
    "LinkState",
    "MissingPrerequisite",
    "Opportunity",
    "OpportunityKind",
    "ProbeState",
    "Provenance",
    "Scores",
    "Severity",
    "ShotSpeech",
    "SoundAction",
    "SpeakerLink",
    "SpeechSource",
    "StageReport",
    "StageState",
    "SyncAssessment",
    "TextEmbedder",
    "TextMatch",
    "VideoRef",
    "Visibility",
    "alignment_from_json",
    "alignment_to_json",
    "get_profile",
    "validate_document",
]
