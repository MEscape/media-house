"""Vocabulary, versions and small helpers of editing intelligence.

Everything here names an editorial DECISION, a reason or a request to a later stage. Nothing here
names a timeline backend: the plan is the same whichever tool compiles it.

Scales (every score is strength of evidence in [0, 1], never a calibrated probability, and a
missing value is ``None`` with a reason, never 0):

* ``keep`` / ``cut`` / ``shorten``: how strongly the evidence argues for keeping, removing or
  trimming the passage. Distinct on purpose: a passage can score high on both.
* ``reorder``: how strongly the story's constraints argue for moving a section.
* ``cover_need``: how much the picture would gain from other footage over it.
* ``cut_point_confidence``: how safe the proposed boundary is (alignment quality, silence at the
  boundary, room around it). Not "how frame-accurate": see ``Precision``.
* ``continuity_risk``: how likely the cut is to read as a visible jump. ``None`` = not assessable.
* ``narrative_risk``: how much meaning could be lost if the edit is wrong.
* ``engagement``: delivery and hook evidence (emphasis, expressiveness, hook score).
"""

import math
from enum import StrEnum

from media_house.shared.errors import InvariantViolation

SCHEMA_VERSION = 1
#: Bump when any rule, threshold or formula changes so stored plans are not reused.
EDITOR_VERSION = 4
#: Bump when a member is added to an enum below (readers refuse unknown members).
VOCABULARY_VERSION = 2
DOCUMENT_TYPE = "edit_plan"
RESULT_OPERATION = "edit_plan"


class Origin(StrEnum):
    AUTOMATIC = "automatic"
    #: A person decided. Never replaced by an automatic replan unless the caller says so.
    MANUAL = "manual"


class Action(StrEnum):
    KEEP = "keep"
    CUT = "cut"
    SHORTEN = "shorten"  # keep, minus trims (pauses, fillers)
    EXTEND = "extend"  # hold past the last word (payoff, reveal)
    REORDER = "reorder"  # a section plays elsewhere than it was recorded
    EMPHASIZE = "emphasize"
    COVER = "cover"  # other footage over the picture
    REACTION = "reaction"  # keep a heard reaction (laughter, breath) deliberately
    PRESERVE_AUDIO = "preserve_audio"  # the picture changes, the sound does not
    BRIDGE_AUDIO = "bridge_audio"  # J-cut / L-cut over a picture change
    REQUEST_EFFECT = "request_effect"  # a graphic, transition, cue for a later stage


class Executability(StrEnum):
    """Whether a decision may be carried out without a person looking at it first."""

    EXECUTABLE = "executable"
    NEEDS_REVIEW = "needs_review"  # a suggestion: meaning or boundary safety is not established
    UNRESOLVED = "unresolved"  # evidence needed to decide is missing


class Precision(StrEnum):
    """Where a time value comes from. It says how much the number can be trusted, not how many
    decimals it has. Only ``FRAME_EXACT`` means a measured frame boundary."""

    FRAME_EXACT = "frame_exact"  # a shot boundary measured by video analysis
    WORD_ALIGNED = "word_aligned"  # forced-aligned word timing (tens of milliseconds)
    SEGMENT_ALIGNED = "segment_aligned"  # words estimated inside recognised segments
    SENTENCE_SPAN = "sentence_span"  # a sentence time that upstream took from word or segment times
    ESTIMATED = "estimated"  # interpolated or approximate


class TrackKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


class PlacementRole(StrEnum):
    MAIN = "main"
    COLD_OPEN = "cold_open"  # the story's recommended opening, played first
    COVER = "cover"


class AudioBehavior(StrEnum):
    SOURCE_SYNC = "source_sync"  # the clip's own sound, in sync with its picture
    VOICEOVER = "voiceover"  # the picture's own sound is muted; other audio carries on
    MUTED = "muted"


class BridgeKind(StrEnum):
    J_CUT = "j_cut"  # the next sound starts before its picture
    L_CUT = "l_cut"  # the previous sound continues over the next picture
    BRIDGE = "bridge"  # both: a cutaway spans the audio boundary


class TrimKind(StrEnum):
    PAUSE = "pause"
    FILLER = "filler"


class SignalName(StrEnum):
    KEEP = "keep"
    CUT = "cut"
    SHORTEN = "shorten"
    REORDER = "reorder"
    COVER_NEED = "cover_need"
    CUT_POINT_CONFIDENCE = "cut_point_confidence"
    CONTINUITY_RISK = "continuity_risk"
    NARRATIVE_RISK = "narrative_risk"
    ENGAGEMENT = "engagement"


class SignalBasis(StrEnum):
    MEASURED = "measured"  # copied or computed exactly from an upstream measurement
    HEURISTIC = "heuristic"  # a rule or formula whose weights are design choices


class EvidenceKind(StrEnum):
    STORY_SECTION = "story_section"
    STORY_BEAT = "story_beat"
    STORY_KEY_INFORMATION = "story_key_information"
    STORY_CONSTRAINT = "story_constraint"
    STORY_VISUAL = "story_visual"
    STORY_RELATION = "story_relation"
    CONTENT_UNIT = "content_unit"
    CONTENT_SEGMENT = "content_segment"
    AUDIO_WORD = "audio_word"
    AUDIO_PAUSE = "audio_pause"
    AUDIO_EVENT = "audio_event"
    VIDEO_SHOT = "video_shot"
    ALIGNMENT_SPEECH = "alignment_speech"
    ALIGNMENT_MATCH = "alignment_match"
    INSPECTION = "inspection"
    PACING_ANALYSIS = "pacing_analysis"
    PROFILE_RULE = "profile_rule"
    OVERRIDE = "override"


class DurationMode(StrEnum):
    NONE = "none"
    SOFT = "soft"  # a preference: reported when missed
    HARD = "hard"  # a constraint: a plan that misses it says so as an unmet constraint


class ConstraintKind(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class ConstraintState(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    NOT_CHECKED = "not_checked"


class Severity(StrEnum):
    ERROR = "error"  # the plan is invalid
    WARNING = "warning"  # merits review
    RECOMMENDATION = "recommendation"  # subjective, optional
    UNRESOLVED = "unresolved"  # evidence needed to judge is missing


class IssueKind(StrEnum):
    INVALID_RANGE = "invalid_range"
    UNKNOWN_REFERENCE = "unknown_reference"
    ORDER_VIOLATED = "order_violated"
    ESSENTIAL_MISSING = "essential_missing"
    QUALIFICATION_SEPARATED = "qualification_separated"
    CONTEXT_MISSING = "context_missing"
    OVERLAP = "overlap"
    GAP = "gap"
    DURATION_INCONSISTENT = "duration_inconsistent"
    TARGET_NOT_MET = "target_not_met"
    UNSUPPORTED_CONTENT = "unsupported_content"
    MANUAL_OVERRIDE_RISK = "manual_override_risk"
    TIME_BASIS_LIMITED = "time_basis_limited"
    EVIDENCE_MISSING = "evidence_missing"
    CONTINUITY = "continuity"
    COVER_NEEDED = "cover_needed"
    COVER_RISK = "cover_risk"
    PACING = "pacing"
    STRUCTURAL = "structural"
    RELATED_RECORDINGS = "related_recordings"


class CheckState(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"  # the evidence to check it does not exist; never a pass


class EffectKind(StrEnum):
    TRANSITION = "transition"
    GRAPHIC = "graphic"
    MUSIC_CUE = "music_cue"
    SOUND_CUE = "sound_cue"
    REFRAME = "reframe"


class EffectIntent(StrEnum):
    SECTION_CHANGE = "section_change"
    REVEAL = "reveal"
    EMPHASIS = "emphasis"
    EXPLAIN = "explain"  # a graphic that explains what is said
    SHOW_TEXT = "show_text"
    COVERED_CUT = "covered_cut"


class StructuralKind(StrEnum):
    OMIT_SECTION = "omit_section"
    COLD_OPEN = "cold_open"


class PlanState(StrEnum):
    OK = "ok"
    WARNINGS = "ok_with_warnings"
    #: Best effort, but a hard constraint (for example a hard target duration) is not met.
    UNMET_CONSTRAINTS = "unmet_constraints"


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"
    FAILED = "failed"


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"
    REQUESTED = "requested"
    NOT_AVAILABLE = "not_available"


def unit_interval(value: float, label: str) -> float:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise InvariantViolation(f"{label} must be within [0, 1], got {value}")
    return value


def optional_unit_interval(value: float | None, label: str) -> float | None:
    return None if value is None else unit_interval(value, label)
