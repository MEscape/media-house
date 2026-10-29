"""Vocabulary and versions of continuity intelligence.

Scores (all in [0, 1], higher = more of the named thing, ``None`` = could not be stated, which is
NOT the same as fine). They answer different questions and are never merged:

=============================  ============================================================
score                          question
=============================  ============================================================
``visual_discontinuity_score``  how different do the two pictures look (embedding, scene,
                                framing size)? 0 identical, 1 unrelated. Heuristic.
``jump_cut_severity``           how likely is a cut inside one continuous picture to read as a
                                jump (the frame is the same but the subject has moved a visible
                                amount)? Heuristic ranking, not a probability.
``spatial_continuity_score``    how well do travel direction and eyeline survive the cut?
                                1 intact, 0 reversed. Higher is BETTER here (stated in the
                                issue as a risk of ``1 - score``). Heuristic.
``action_continuity_score``     how well does the subject's motion carry across the cut (where
                                it would be if it kept moving)? 1 intact. Heuristic.
``identity_mismatch_risk``      how likely is it that the subject or product differs? Derived
                                from identity evidence; ``None`` when identity is unknown.
``lighting_mismatch_score``     the largest normalised step in exposure, colour balance or
                                lighting setup. Measured values, derived step.
``audio_discontinuity_score``   the largest normalised step or defect in the sound at the cut
                                (level, noise floor, a clipped word, a cut breath).
``temporal_inconsistency_score`` how far a cut runs against the recording's own order inside one
                                scene without a declared reorder.
``narrative_confusion_risk``    how likely a cut across story sections is to confuse the viewer.
``detectability_confidence``    how much evidence backs the diagnosis (coverage x agreement).
                                Ordinal, never a calibrated probability.
``intentional_style_likelihood`` how likely the difference is a deliberate style choice, from the
                                profile and the story context. An ESTIMATE, never a fact.
``repair_difficulty``           how hard the cheapest listed repair is (0 trivial, 1 needs new
                                footage).
=============================  ============================================================

``Level`` (severity) is computed from the relevant score, the profile's weight for that kind of
problem, the strictness and the style estimate; it is not one of the scores.
"""

from enum import StrEnum
from typing import Final

ANALYZER_VERSION: Final = 2
SCHEMA_VERSION: Final = 1
VOCABULARY_VERSION: Final = 1
PROFILE_VERSION: Final = 1
DOCUMENT_TYPE: Final = "continuity_analysis"
RESULT_OPERATION: Final = "continuity_analysis"


class IssueType(StrEnum):
    JUMP_CUT = "jump_cut"
    VISUAL_DISCONTINUITY = "visual_discontinuity"
    SCREEN_DIRECTION = "screen_direction"
    EYELINE = "eyeline"
    ACTION_CONTINUITY = "action_continuity"
    OBJECT_STATE = "object_state"
    IDENTITY_MISMATCH = "identity_mismatch"
    LIGHTING_MISMATCH = "lighting_mismatch"
    CLIPPED_SPEECH = "clipped_speech"
    AUDIO_DISCONTINUITY = "audio_discontinuity"
    SYNC_UNCERTAIN = "sync_uncertain"
    TEMPORAL_INCONSISTENCY = "temporal_inconsistency"
    NARRATIVE_CONFUSION = "narrative_confusion"


class Scope(StrEnum):
    SHOT = "shot"
    TRANSITION = "transition"
    SEQUENCE = "sequence"
    SECTION = "section"


class Level(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


LEVEL_ORDER: Final = {Level.INFO: 0, Level.LOW: 1, Level.MEDIUM: 2, Level.HIGH: 3}


class IdentityVerdict(StrEnum):
    CONFIRMED_SAME = "confirmed_same"
    PROBABLE_SAME = "probable_same"
    UNCERTAIN = "uncertain"
    PROBABLE_MISMATCH = "probable_mismatch"
    CONFIRMED_MISMATCH = "confirmed_mismatch"
    UNKNOWN = "unknown"


class ScoreName(StrEnum):
    VISUAL_DISCONTINUITY = "visual_discontinuity_score"
    JUMP_CUT_SEVERITY = "jump_cut_severity"
    SPATIAL_CONTINUITY = "spatial_continuity_score"
    ACTION_CONTINUITY = "action_continuity_score"
    IDENTITY_MISMATCH_RISK = "identity_mismatch_risk"
    LIGHTING_MISMATCH = "lighting_mismatch_score"
    AUDIO_DISCONTINUITY = "audio_discontinuity_score"
    TEMPORAL_INCONSISTENCY = "temporal_inconsistency_score"
    NARRATIVE_CONFUSION = "narrative_confusion_risk"


class ScoreBasis(StrEnum):
    MEASURED = "measured"
    DERIVED = "derived"
    HEURISTIC = "heuristic"


class RepairKind(StrEnum):
    ADJUST_CUT_POINT = "adjust_cut_point"
    ALTERNATE_TAKE = "alternate_take"
    ALTERNATE_SHOT = "alternate_shot"
    BROLL_COVER = "broll_cover"
    REACTION_SHOT = "reaction_shot"
    REFRAME = "reframe"
    EXPOSURE_CORRECTION = "exposure_correction"
    COLOR_CORRECTION = "color_correction"
    AUDIO_CROSSFADE = "audio_crossfade"
    ROOM_TONE = "room_tone"
    AMBIENCE_BRIDGE = "ambience_bridge"
    J_CUT = "j_cut"
    L_CUT = "l_cut"
    REVIEW_SPEECH = "review_speech"
    REVIEW_ORDER = "review_order"
    VERIFY_SYNC = "verify_sync"
    CONFIRM_IDENTITY = "confirm_identity"
    KEEP_INTENTIONAL = "keep_intentional"


class Owner(StrEnum):
    """Who would execute a repair. Continuity executes nothing."""

    EDITING = "editing_intelligence"
    BROLL = "broll_intelligence"
    REFRAMING = "reframing_intelligence"
    VIDEO_IMPROVEMENT = "video_improvement"
    AUDIO_IMPROVEMENT = "audio_improvement"
    PERSON = "person"


class Availability(StrEnum):
    #: The capability exists and has been asked (a candidate was examined).
    CHECKED = "checked"
    #: The capability exists but no candidate was examined: the repair is only a direction.
    UNCHECKED = "unchecked"
    #: The module that would do it does not exist, or has nothing for this place.
    UNAVAILABLE = "unavailable"


class TransitionKind(StrEnum):
    SAME_SHOT = "same_shot"  # both sides lie inside one continuous shot
    SAME_RECORDING = "same_recording"
    CROSS_RECORDING = "cross_recording"


class Decision(StrEnum):
    ACCEPTED = "accepted"  # a person accepted the risk
    REJECTED = "rejected"  # a person says the finding is wrong


class StageState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    NOT_AVAILABLE = "not_available"
    SKIPPED = "skipped"


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"
    NOT_AVAILABLE = "not_available"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


class ResultState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"
