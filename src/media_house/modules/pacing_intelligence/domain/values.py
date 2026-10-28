"""Vocabulary, versions and score definitions of pacing intelligence.

Every score is strength of evidence or degree of a named property in [0, 1]; none is a calibrated
probability and none is a validated psychological measure. A value that could not be computed is
``None`` with a reason, never 0.

Score definitions (basis in brackets: measured / derived / heuristic):

* ``speech_rate_wpm`` [derived]: words per minute of SPEECH-ACTIVE time, i.e. elapsed time minus
  the gaps between words that are at least ``min_gap`` long. ``elapsed_rate_wpm`` keeps every gap.
* ``pause_ratio`` [derived]: share of the elapsed time spent in such gaps.
* ``information_density`` [heuristic, content intelligence]: share of content words, novelty and
  absence of filler and repeats. It is NOT how fast the speaker talks.
* ``cognitive_load_estimate`` [heuristic]: density, the pressure of density against speech rate,
  and the context a passage needs. An estimate of how much time a viewer needs, not a measured
  or validated load.
* ``visual_activity`` [derived]: share of measured frame pairs of the shots under a passage that
  carry picture or camera motion (1 - static fraction).
* ``repetition_score`` [derived]: how much of a passage repeats earlier material and adds nothing.
* ``attention_change_estimate`` [heuristic prediction]: how many attention-relevant changes (a
  visual change, an emphasised word, a density rise) happen in a stretch. Nothing about real
  viewers is measured; it is never audience retention.
* ``pacing_confidence`` [heuristic]: how much of the evidence the finding needs was present and
  how reliable its timing is.
"""

import math
from enum import StrEnum

from media_house.shared.errors import InvariantViolation

SCHEMA_VERSION = 1
#: Bump when any rule, threshold or formula changes so stored analyses are not reused.
ANALYZER_VERSION = 1
#: Bump when a member is added to an enum below (readers refuse unknown members).
VOCABULARY_VERSION = 1
DOCUMENT_TYPE = "pacing_analysis"
RESULT_OPERATION = "pacing_analysis"


class Basis(StrEnum):
    MEASURED = "measured"  # copied from an upstream measurement
    DERIVED = "derived"  # exact arithmetic on measurements
    HEURISTIC = "heuristic"  # a rule or formula whose weights are design choices


class Strategy(StrEnum):
    CONCISE = "concise"
    BALANCED = "balanced"
    DETAILED = "detailed"
    HIGH_ENERGY = "high_energy"
    CINEMATIC = "cinematic"


class PaceClass(StrEnum):
    SLOW = "slow"
    STEADY = "steady"
    FAST = "fast"
    UNKNOWN = "unknown"


class PauseVerdict(StrEnum):
    NATURAL = "natural"  # ordinary breathing room
    MEANINGFUL = (
        "meaningful"  # before emphasis / after a question / before a payoff / with a reaction
    )
    LONG = "long"  # longer than natural, but nothing says it is deliberate
    DEAD_AIR = "dead_air"  # long, confirmed silence with nothing that gives it a purpose


class RecommendationType(StrEnum):
    TIGHTEN_PAUSE = "tighten_pause"
    PRESERVE_PAUSE = "preserve_pause"
    ALLOW_PROCESSING_TIME = "allow_processing_time"
    TIGHTEN_DELIVERY = "tighten_delivery"
    REDUCE_REPETITION = "reduce_repetition"
    ADD_VISUAL_CHANGE = "add_visual_change"
    HOLD_SHOT = "hold_shot"
    HOLD_DEMONSTRATION = "hold_demonstration"
    ESTABLISH_PREMISE_EARLIER = "establish_premise_earlier"
    REDUCE_SETUP = "reduce_setup"
    HOLD_REVEAL = "hold_reveal"
    ADD_SECTION_BREATH = "add_section_breath"
    ALIGN_CUTS_TO_BEATS = "align_cuts_to_beats"
    RESTORE_ATTENTION = "restore_attention"


class ReasonCode(StrEnum):
    DEAD_AIR = "dead_air"
    LONG_PAUSE = "long_pause"
    PAUSE_BEFORE_EMPHASIS = "pause_before_emphasis"
    PAUSE_AFTER_QUESTION = "pause_after_question"
    PAUSE_BEFORE_PAYOFF = "pause_before_payoff"
    PAUSE_WITH_REACTION = "pause_with_reaction"
    SPEAKER_TURN_GAP = "speaker_turn_gap"
    DENSE_PASSAGE = "dense_passage"
    FAST_DELIVERY = "fast_delivery"
    SLOW_DELIVERY = "slow_delivery"
    LOW_DENSITY = "low_density"
    REPEATS_EARLIER = "repeats_earlier"
    REPEAT_SERVES_PURPOSE = "repeat_serves_purpose"
    NEEDS_CONTEXT = "needs_context"
    STATIC_PICTURE = "static_picture"
    FREQUENT_CUTS = "frequent_cuts"
    DEMONSTRATION_TOO_SHORT = "demonstration_too_short"
    PREMISE_LATE = "premise_late"
    SETUP_HEAVY = "setup_heavy"
    REVEAL_RUSHED = "reveal_rushed"
    SECTION_ABRUPT = "section_abrupt"
    CUTS_OFF_PULSE = "cuts_off_pulse"
    INACTIVITY = "inactivity"
    WEAK_TIMING = "weak_timing"


class Severity(StrEnum):
    ERROR = "error"  # the analysis is invalid
    WARNING = "warning"
    INFO = "info"
    UNRESOLVED = "unresolved"  # evidence needed to judge is missing


class IssueKind(StrEnum):
    INVALID_RANGE = "invalid_range"
    UNKNOWN_REFERENCE = "unknown_reference"
    INVALID_SCORE = "invalid_score"
    CONFLICT = "conflict"
    INCOMPLETE = "incomplete"
    DURATION = "duration"
    EVIDENCE_MISSING = "evidence_missing"
    TIMING = "timing"
    NARRATIVE_LIMITED = "narrative_limited"
    TARGET = "target"


class Precision(StrEnum):
    FRAME_EXACT = "frame_exact"  # a shot boundary measured by video analysis
    WORD_ALIGNED = "word_aligned"
    SEGMENT_ALIGNED = "segment_aligned"
    SENTENCE_SPAN = "sentence_span"
    ESTIMATED = "estimated"


class Level(StrEnum):
    PASSAGE = "passage"
    PAUSE = "pause"
    SHOT = "shot"
    SECTION = "section"
    TIMELINE = "timeline"


class Safety(StrEnum):
    """Whether a recommendation could be applied without a person checking it first."""

    SAFE = "safe"  # removes or changes nothing the story needs (dead air, a plain silence)
    NEEDS_REVIEW = "needs_review"  # touches meaning, context or a deliberate effect
    EXPANDS = "expands"  # asks for more time; nothing is removed


class EvidenceKind(StrEnum):
    AUDIO_WORD = "audio_word"
    AUDIO_PAUSE = "audio_pause"
    AUDIO_EVENT = "audio_event"
    AUDIO_RHYTHM = "audio_rhythm"
    CONTENT_SEGMENT = "content_segment"
    VIDEO_SHOT = "video_shot"
    STORY_SECTION = "story_section"
    STORY_BEAT = "story_beat"
    STORY_VISUAL = "story_visual"
    ALIGNMENT = "alignment"
    PROFILE = "profile"


class StageState(StrEnum):
    OK = "ok"
    NOT_AVAILABLE = "not_available"
    SKIPPED = "skipped"
    FAILED = "failed"


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"
    NOT_AVAILABLE = "not_available"


class ResultState(StrEnum):
    OK = "ok"
    WARNINGS = "ok_with_warnings"
    PARTIAL = "partial"  # an input needed for a whole level was missing
    NOT_APPLICABLE = "not_applicable"  # nothing to analyse (no speech and no shots)


def unit_interval(value: float, label: str) -> float:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise InvariantViolation(f"{label} must be within [0, 1], got {value}")
    return value


def optional_unit_interval(value: float | None, label: str) -> float | None:
    return None if value is None else unit_interval(value, label)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
