"""Vocabulary and versions of B-roll intelligence.

Every score, role and reason is named here once. Score definitions (all in [0, 1], higher is
better, ``None`` = could not be stated, which is NOT the same as bad):

==========================  ============  =========================================================
score                       basis         meaning
==========================  ============  =========================================================
``semantic_relevance``      model/derived how closely what the shot shows matches the passage:
                                          words found in its observations (derived) blended with
                                          the visual and text embedding similarity (model estimate)
``evidence_strength``       derived       whether the shot actually SUPPORTS the specific statement,
                                          from its evidence role (see ``EvidenceRole``), how the
                                          association was observed and whether identity is confirmed
``narrative_relevance``     heuristic     how well the shot serves the beat's purpose; ``None``
                                          without a story
``visual_quality``          measured      the analysis's own quality scores and sharpness
``subject_visibility``      measured      the analysis's subject-visibility score
``composition_suitability`` derived       framing of the shot against what the intended use wants
``visual_activity``         derived       movement and change in the usable range (need-dependent:
                                          a demonstration wants change, an establishing shot calm)
``temporal_suitability``    derived       whether the evidence moments lie inside the chosen range
``continuity_compatibility`` heuristic    basic compatibility with the picture it interrupts
                                          (environment, light, camera); NOT a continuity assessment
``duration_suitability``    derived       usable seconds against the seconds the use needs
``novelty``                 derived       how much the shot adds over what is already chosen
``audio_suitability``       derived       whether the shot's own sound helps (action sound) or
                                          harms (speech) the intended use
``confidence``              heuristic     how reliable the match and its evidence are; an ordinal
                                          strength, never a calibrated probability
==========================  ============  =========================================================

A candidate is first sorted by whether it SATISFIES what the passage needs (see ``Support``) and
only then by the weighted mean of its available scores, so a beautiful shot of the wrong thing
can never outrank a plain shot of the right one.
"""

from enum import StrEnum
from typing import Final

ANALYZER_VERSION: Final = 1
SCHEMA_VERSION: Final = 1
VOCABULARY_VERSION: Final = 1
PROFILE_VERSION: Final = 1
DOCUMENT_TYPE: Final = "broll_analysis"
RESULT_OPERATION: Final = "broll_analysis"


class NeedKind(StrEnum):
    """What the passage asks of supplementary footage (the "no gap" and "gap" outcomes are
    results, not needs)."""

    REQUIRED_EVIDENCE = "required_evidence"
    DEMONSTRATION = "demonstration"
    ILLUSTRATIVE = "illustrative"
    VARIETY = "variety"
    JUMP_CUT_COVER = "jump_cut_cover"
    NO_VISUAL_NEEDED = "no_visual_needed"


class TargetOrigin(StrEnum):
    STORY_VISUAL = "story_visual"
    CONTENT_OPPORTUNITY = "content_opportunity"
    PACING = "pacing"
    JUMP_CUT = "jump_cut"


class Purpose(StrEnum):
    DEMONSTRATION = "demonstration"
    EVIDENCE = "evidence"
    COMPARISON = "comparison"
    ILLUSTRATION = "illustration"
    PRODUCT_CLOSEUP = "product_closeup"
    ESTABLISHING = "establishing"
    SCREEN_CONTENT = "screen_content"
    VARIETY = "variety"
    JUMP_CUT_COVER = "jump_cut_cover"


class EvidenceRole(StrEnum):
    """What a shot's evidence supports, strongest first. Never inferred from attractiveness."""

    #: The shot shows the action itself happening (observed movement/events), and its subject.
    DIRECT_DEMONSTRATION = "direct_demonstration"
    #: The shot shows the specific thing named (identity confirmed, or text/screen content read).
    DIRECT_EVIDENCE = "direct_evidence"
    #: The shot shows the kind of subject named, but identity or action is not established.
    SPECIFIC_SUBJECT = "specific_subject"
    #: A related scene: meaning is close, nothing specific is observed.
    CONTEXTUAL_ILLUSTRATION = "contextual_illustration"
    GENERIC_ILLUSTRATION = "generic_illustration"
    UNCERTAIN = "uncertain"
    #: Evidence that the shot shows something else (a different model, a different thing).
    CONTRADICTORY = "contradictory"


ROLE_ORDER: Final = {
    EvidenceRole.CONTRADICTORY: 0,
    EvidenceRole.UNCERTAIN: 1,
    EvidenceRole.GENERIC_ILLUSTRATION: 2,
    EvidenceRole.CONTEXTUAL_ILLUSTRATION: 3,
    EvidenceRole.SPECIFIC_SUBJECT: 4,
    EvidenceRole.DIRECT_EVIDENCE: 5,
    EvidenceRole.DIRECT_DEMONSTRATION: 5,
}


class Association(StrEnum):
    """How the shot was found to relate to the passage, strongest first."""

    OBSERVED_TEXT = "observed_text"
    ENTITY_LABEL = "entity_label"
    DESCRIBED = "described"
    METADATA = "metadata"
    SEMANTIC = "semantic"
    NONE = "none"


class Identity(StrEnum):
    """Whether the shot shows the specific named thing (product model, device)."""

    NOT_APPLICABLE = "not_applicable"  # the passage names no specific thing
    CONFIRMED = "confirmed"  # the shot's own text/metadata names the same thing
    UNVERIFIED = "unverified"  # nothing in the shot says which one it is
    CONFLICT = "conflict"  # the shot names a different one of the same line
    REFERENCE_UNRESOLVED = "reference_unresolved"  # "this model" could not be resolved


class Support(StrEnum):
    """What a candidate is good for, given the passage's need."""

    SATISFIES = "satisfies"  # meets the evidence the need requires
    ILLUSTRATIVE_ONLY = "illustrative_only"  # related, but not the evidence the need requires
    REJECTED = "rejected"  # considered and refused (kept only to explain a gap)


class ScoreName(StrEnum):
    SEMANTIC_RELEVANCE = "semantic_relevance"
    EVIDENCE_STRENGTH = "evidence_strength"
    NARRATIVE_RELEVANCE = "narrative_relevance"
    VISUAL_QUALITY = "visual_quality"
    SUBJECT_VISIBILITY = "subject_visibility"
    COMPOSITION_SUITABILITY = "composition_suitability"
    VISUAL_ACTIVITY = "visual_activity"
    TEMPORAL_SUITABILITY = "temporal_suitability"
    CONTINUITY_COMPATIBILITY = "continuity_compatibility"
    DURATION_SUITABILITY = "duration_suitability"
    NOVELTY = "novelty"
    AUDIO_SUITABILITY = "audio_suitability"
    CONFIDENCE = "confidence"


class ScoreBasis(StrEnum):
    MEASURED = "measured"  # copied from an upstream measurement
    DERIVED = "derived"  # computed from stated upstream facts by a stated rule
    HEURISTIC = "heuristic"  # a design-choice rule, not calibrated
    MODEL_ESTIMATED = "model_estimated"  # includes an embedding similarity


class UseKind(StrEnum):
    """How the footage would be used (intent only: no timeline is built here)."""

    COVER_OVER_NARRATION = "cover_over_narration"
    CUTAWAY = "cutaway"
    INTERCUT_DEMONSTRATION = "intercut_demonstration"
    PRODUCT_CLOSEUP = "product_closeup"
    ESTABLISHING_SHOT = "establishing_shot"
    COMPARISON_INSERT = "comparison_insert"
    SCREEN_RECORDING = "screen_recording"
    REACTION = "reaction"
    NO_CHANGE = "no_change"


class AudioBehavior(StrEnum):
    #: The narration carries on; the footage's own sound is not used.
    RETAIN_VOICEOVER = "retain_voiceover"
    #: The footage's own sound is part of the point (a synchronised action sound).
    RETAIN_SOURCE_AUDIO = "retain_source_audio"
    #: The footage contains speech or noise that would clash with the narration.
    MUTE_SOURCE = "mute_source"
    #: Not enough is known about the footage's sound: an editorial decision.
    FLAG_FOR_EDITOR = "flag_for_editor"


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GapKind(StrEnum):
    PRODUCT_CLOSEUP = "product_closeup"
    ACTION_DEMONSTRATION = "action_demonstration"
    ESTABLISHING_SHOT = "establishing_shot"
    SCREEN_RECORDING = "screen_recording"
    COMPARISON_VISUAL = "comparison_visual"
    REACTION_SHOT = "reaction_shot"
    DIAGRAM = "diagram"
    SUPPORTING_EVIDENCE = "supporting_evidence"
    ALTERNATE_ANGLE = "alternate_angle"
    CONTEXT_SCENE = "context_scene"


class Precision(StrEnum):
    """Where the ends of a source range come from. Only a shot boundary is frame-exact."""

    SHOT_BOUNDARY = "shot_boundary"
    ESTIMATED = "estimated"


class ClockBasis(StrEnum):
    CONTAINER = "container"  # corrected with the recording's alignment: the common clock
    VIDEO_ANALYSIS = "video_analysis"  # the analysis's own clock; no container offset known


class AssetVia(StrEnum):
    """Which stored version of the footage carried the evidence (the id recommended is always the
    original's)."""

    ORIGINAL = "original"
    PROXY = "proxy"
    DERIVED = "derived"


class StageState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"
    FAILED = "failed"


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
