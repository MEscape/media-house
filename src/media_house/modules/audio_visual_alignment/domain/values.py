"""Vocabulary, versions and small value helpers of audio-visual alignment.

Nothing here names an editing action. Every member describes what was observed or inferred.
"""

from enum import StrEnum

from media_house.shared.errors import InvariantViolation

#: Library operation and document type of the stored result.
RESULT_OPERATION = "audio_visual_alignment"
RESULT_DOCUMENT = "audio_visual_alignment.document"

#: Bump when any rule or formula changes what a result contains; stored results then miss.
ALIGNMENT_VERSION = 1
#: Bump when the stored shape changes.
SCHEMA_VERSION = 1
#: Bump when ``scoring.py`` changes what a strength means.
SCORING_VERSION = 1
#: Bump when a word is added to or removed from any enum below (readers refuse unknown members).
VOCABULARY_VERSION = 1


class HowObtained(StrEnum):
    """How an upstream input reached this run (the proof that nothing was done twice)."""

    SUPPLIED = "supplied"
    REUSED = "reused"
    REQUESTED = "requested"
    NOT_USED = "not_used"
    NOT_AVAILABLE = "not_available"


class Acquire(StrEnum):
    """Whether missing upstream ANALYSES may be computed. Inspection is cheap and always allowed."""

    STORED = "stored"
    REQUEST = "request"


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"


class CorrectionSource(StrEnum):
    """Where a clock correction comes from. ``NONE`` means the upstream seconds were taken as is."""

    NONE = "none"
    INSPECTION = "inspection"
    SUPPLIED = "supplied"


class ProbeState(StrEnum):
    """Outcome of the coarse speech-versus-mouth lag probe."""

    CONSISTENT = "consistent"  # a clear peak at (or near) zero lag
    OFFSET_INDICATED = "offset_indicated"  # a clear peak away from zero
    AMBIGUOUS = "ambiguous"  # several lags fit about equally well
    INSUFFICIENT = "insufficient"  # too little overlapping speech and face evidence


class Visibility(StrEnum):
    """What the picture says about the source of a stretch of speech.

    These are DIFFERENT facts and are never merged: a quiet face is not proof that the voice is
    off screen, and a missing face track is not proof that nobody visible is speaking.
    """

    SPEAKING_FACE = "speaking_face"  # a face is visible and its mouth moves while speech is heard
    QUIET_FACE = "quiet_face"  # a face is visible with enough samples and its mouth is still
    NO_FACE_DETECTED = "no_face_detected"  # the face analyser ran and found no face
    AMBIGUOUS = "ambiguous"  # several people visible, only the main face is measured
    VISUAL_UNAVAILABLE = "visual_unavailable"  # face analysis did not run for this shot
    INSUFFICIENT = "insufficient"  # a face is there but there are too few samples to say


class LinkState(StrEnum):
    SUPPORTED = "supported"
    CANDIDATE = "candidate"
    CONFLICT = "conflict"


class EvidenceGrade(StrEnum):
    """How a spoken passage and a shot relate. Ordered from the most to the least specific.

    There is no grade for a demonstrated action or a contradiction: nothing upstream measures
    them, and the absence of a match is never a contradiction.
    """

    OBSERVED_TEXT = "observed_text"  # a key term is among the text read off the screen
    DESCRIBED = "described"  # a key term is in a generated description of the shot
    ENTITY_LABEL = "entity_label"  # a key term equals a detected object label
    SEMANTIC_CANDIDATE = "semantic_candidate"  # embedding similarity stands out from the rest
    NONE_FOUND = "none_found"  # visual text evidence existed and nothing matched
    UNKNOWN = "unknown"  # the shot has no examinable visual evidence


class EvidenceKind(StrEnum):
    TEMPORAL_OVERLAP = "temporal_overlap"
    SPEECH_MOUTH_CORRELATION = "speech_mouth_correlation"
    MOUTH_SAMPLES = "mouth_samples"
    TEXT_TERM = "text_term"
    OBJECT_LABEL = "object_label"
    EMBEDDING_SIMILARITY = "embedding_similarity"
    EVENT_COINCIDENCE = "event_coincidence"
    CLOCK = "clock"
    SPEAKER_TURN = "speaker_turn"


class OpportunityKind(StrEnum):
    """Intervals worth a downstream look. Evidence only: none of them says what to do."""

    NO_VISIBLE_SPEAKER_WINDOW = "no_visible_speaker_window"
    CONTENT_PASSAGE_CONTEXT = "content_passage_context"


class BoundaryKind(StrEnum):
    SPEECH_CROSSES_CUT = "speech_crosses_cut"


class FlagKind(StrEnum):
    VIDEO_ORIGIN_UNVERIFIED = "video_origin_unverified"
    VIDEO_ORIGIN_CORRECTED = "video_origin_corrected"
    SYNC_OFFSET_INDICATED = "sync_offset_indicated"
    SYNC_PROBE_WEAK = "sync_probe_weak"
    DURATION_MISMATCH = "duration_mismatch"
    NO_SPEAKER_LABELS = "no_speaker_labels"
    SPEAKER_LINK_CONFLICT = "speaker_link_conflict"
    SPEAKER_CONTRADICTED = "speaker_contradicted"
    MOUTH_SIGNAL_COARSE = "mouth_signal_coarse"
    LANGUAGE_MISMATCH = "language_mismatch"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"


def unit_interval(value: float, name: str) -> float:
    """``value`` if it lies in [0, 1], else an invariant violation."""
    if not 0.0 <= value <= 1.0:
        raise InvariantViolation(f"{name} must be within [0, 1]", details={"value": value})
    return value


def optional_unit_interval(value: float | None, name: str) -> float | None:
    return None if value is None else unit_interval(value, name)
