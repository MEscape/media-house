"""Vocabulary, versions and small helpers of story architecture.

Nothing here names an editing action. Every member describes a narrative role, a reason or a
recommendation at the level of sections and beats; cuts and timing belong to the editing stage.
"""

import math
from enum import StrEnum

from media_house.shared.errors import InvariantViolation

SCHEMA_VERSION = 1
#: Bump when any rule, threshold or formula changes so stored plans are not reused.
PLANNER_VERSION = 2
#: Bump when a member is added to an enum below (readers refuse unknown members).
VOCABULARY_VERSION = 2
DOCUMENT_TYPE = "story_plan"
RESULT_OPERATION = "story_plan"


class Origin(StrEnum):
    """Whether text is what the speaker said or what this module wrote."""

    DIRECT = "direct"  # verbatim from the recording
    PARAPHRASE = "paraphrase"  # rewritten upstream or by the language model, and checked
    INFERENCE = "inference"  # not stated by the speaker


class Method(StrEnum):
    OBSERVED = "observed"  # copied from an upstream result
    DETERMINISTIC = "deterministic"  # exact computation from observed data
    HEURISTIC = "heuristic"  # rule or formula whose weights are design choices
    MODEL = "model"  # produced by a language model and then checked


class Basis(StrEnum):
    """A recommendation rests on a source passage, or is inferred from structure."""

    DIRECT = "direct"
    INFERRED = "inferred"


class Priority(StrEnum):
    ESSENTIAL = "essential"
    SUPPORTING = "supporting"
    OPTIONAL = "optional"
    #: Advisory only: the passage repeats other material. The editing stage decides.
    REDUNDANT_CANDIDATE = "redundant_candidate"


class BeatFunction(StrEnum):
    HOOK = "hook"
    SETUP = "setup"
    CONTEXT = "context"
    CLAIM = "claim"
    EXPLANATION = "explanation"
    EVIDENCE = "evidence"
    EXAMPLE = "example"
    DEMONSTRATION = "demonstration"
    COUNTERPOINT = "counterpoint"
    CAVEAT = "caveat"
    TRANSITION = "transition"
    QUESTION = "question"
    ANSWER = "answer"
    TURNING_POINT = "turning_point"
    PAYOFF = "payoff"
    RECAP = "recap"
    CALL_TO_ACTION = "call_to_action"
    UNCLASSIFIED = "unclassified"


class ContentMode(StrEnum):
    """What a section mostly does for the viewer; used to see whether the rhythm varies."""

    EXPLAINING = "explaining"
    DEMONSTRATING = "demonstrating"
    EVIDENCING = "evidencing"
    EXEMPLIFYING = "exemplifying"
    FRAMING = "framing"  # introduction, context, transition
    SUMMARISING = "summarising"
    UNKNOWN = "unknown"


class ConstraintKind(StrEnum):
    MUST_PRECEDE = "must_precede"  # ``first`` has to come before ``second``
    KEEP_ADJACENT = "keep_adjacent"  # ``second`` has to directly follow ``first``


class Strength(StrEnum):
    HARD = "hard"  # moving it would change the meaning
    SOFT = "soft"  # moving it would weaken the story


class LinkKind(StrEnum):
    QUESTION_ANSWER = "question_answer"
    #: The opening premise and the conclusion that pays it off (always inferred).
    SETUP_PAYOFF = "setup_payoff"


class LinkState(StrEnum):
    RESOLVED = "resolved"
    OPEN = "open"  # the setup is there, the recording never pays it off


class ClaimKind(StrEnum):
    STATED_FACT = "stated_fact"  # the speaker presents it as fact; this module cannot verify it
    CLAIM = "claim"
    OPINION = "opinion"  # a recommendation or decision
    DEMONSTRATION = "demonstration"  # shown, not only said
    QUALIFICATION = "qualification"


class SupportRelation(StrEnum):
    SUPPORTS = "supports"
    EXPLAINS = "explains"
    ILLUSTRATES = "illustrates"


class OpeningStrategy(StrEnum):
    NATURAL_START = "natural_start"  # open as recorded
    STRONGEST_HOOK = "strongest_hook"  # the most compelling self-contained statement
    PREMISE_FIRST = "premise_first"  # state the main point up front
    QUESTION_FIRST = "question_first"  # open on a question the recording later answers
    RESULT_FIRST = "result_first"  # open on the conclusion or payoff


class IssueKind(StrEnum):
    UNANSWERED_QUESTION = "unanswered_question"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    CONTRADICTION = "contradiction"
    CONTEXT_REMOVAL_RISK = "context_removal_risk"
    REDUNDANT_MATERIAL = "redundant_material"
    MISSING_EXPECTED_ROLE = "missing_expected_role"
    DELAYED_PREMISE = "delayed_premise"
    OVER_TARGET_DURATION = "over_target_duration"
    UNDER_TARGET_DURATION = "under_target_duration"
    UNTIMED_MATERIAL = "untimed_material"
    DISTORTION_RISK = "distortion_risk"
    #: Recordings of the plan are related (a copy, alternate takes, parallel coverage ...).
    RELATED_RECORDINGS = "related_recordings"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"


class Certainty(StrEnum):
    """How far a relation between two recordings can be relied on. Ordinal, never a probability."""

    #: Stated by an authoritative record (library derivation, checksum, proxy provenance).
    RECORDED = "recorded"
    #: A person confirmed it.
    CONFIRMED = "confirmed"
    #: Detected from concrete evidence (corroborated or indicated) and not confirmed.
    EVIDENCE_SUPPORTED = "evidence_supported"
    #: Circumstantial evidence only: a hypothesis, never a fact.
    UNVERIFIED = "unverified"
    #: A person rejected it, or its basis is out of date: kept in the plan, never used.
    NOT_USABLE = "not_usable"


class Treatment(StrEnum):
    """What the plan did with a relation."""

    #: One recording is an exact copy of another: its sections are redundant candidates.
    REDUNDANT_COPY = "redundant_copy"
    #: One recording is derived from another (same content): its sections are redundant candidates.
    SAME_RECORDING = "same_recording"
    #: A person confirmed they are alternate takes: flagged so one is chosen; nothing is dropped.
    ALTERNATE_TAKES = "alternate_takes"
    #: A person confirmed they cover the same time span: flagged as parallel coverage.
    PARALLEL_COVERAGE = "parallel_coverage"
    #: Mentioned as a possibility only; it changed neither order nor priority.
    NOTED = "noted"
    #: Rejected or out of date: listed, not used.
    NOT_USED = "not_used"


class DurationBasis(StrEnum):
    OBSERVED = "observed"  # the span between the first and last timed word
    ESTIMATED_FROM_WORDS = "estimated_from_words"  # words at the format's delivery rate
    UNKNOWN = "unknown"


class PacingKind(StrEnum):
    DENSE = "dense"
    SLOW = "slow"
    REPETITIVE = "repetitive"
    UNDER_EXPLAINED = "under_explained"
    MONOTONE_RUN = "monotone_run"
    LONG_WITHOUT_BREAK = "long_without_break"


class PacingAction(StrEnum):
    ADD_EXAMPLE = "add_example"
    ADD_DEMONSTRATION = "add_demonstration"
    ADD_CONTEXT = "add_context"
    ADD_RECAP = "add_recap"
    BREAK_UP = "break_up"
    VISUAL_BREAK = "visual_break"
    TIGHTEN = "tighten"


class VisualPurpose(StrEnum):
    DEMONSTRATION = "demonstration"
    EVIDENCE = "evidence"
    COMPARISON = "comparison"
    EXPLAINER_GRAPHIC = "explainer_graphic"
    ILLUSTRATION = "illustration"  # b-roll that shows what is being said
    TEXT_ON_SCREEN = "text_on_screen"


class Necessity(StrEnum):
    ESSENTIAL = "essential"  # the passage says "as you can see": without the picture it fails
    OPTIONAL = "optional"


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"
    FAILED = "failed"


class ResultState(StrEnum):
    OK = "ok"
    WARNINGS = "ok_with_warnings"
    PARTIAL = "partial"  # a requested stage did not produce its result
    NOT_APPLICABLE = "not_applicable"  # nothing to plan (no speech)


def unit_interval(value: float, label: str) -> float:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise InvariantViolation(f"{label} must be within [0, 1], got {value}")
    return value


def optional_unit_interval(value: float | None, label: str) -> float | None:
    return None if value is None else unit_interval(value, label)
