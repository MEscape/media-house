"""Vocabulary of the content analysis: enums and versions. Names describe properties, not edits.

Extending a vocabulary (a new role, relation kind or key-item kind) is additive: add the member,
bump ``VOCABULARY_VERSION`` and mention it in the README. Readers refuse unknown members, so a
document written with a newer vocabulary is rejected instead of silently reinterpreted.
"""

from enum import StrEnum
from typing import Final

SCHEMA_VERSION: Final = 1
#: Bump when any heuristic, formula or cue list changes what a result contains.
PROCESSING_VERSION: Final = 1
VOCABULARY_VERSION: Final = 1
DOCUMENT_TYPE: Final = "content_intelligence.analysis"
RESULT_OPERATION: Final = "content_intelligence"
LLM_CHUNK_OPERATION: Final = "content_intelligence_llm_chunk"
EMBEDDINGS_OPERATION: Final = "content_intelligence_embeddings"


class TimeBasis(StrEnum):
    """How trustworthy one time (a start or an end) is. Never present an estimate as exact."""

    #: Taken unchanged from an upstream segment boundary.
    SOURCE = "source"
    #: Taken from an upstream word that forced alignment measured.
    WORDS = "words"
    #: Taken from an upstream word whose time upstream itself interpolated or estimated.
    UPSTREAM_ESTIMATE = "upstream_estimate"
    #: Interpolated by this module (proportionally to characters inside one upstream segment).
    INTERPOLATED = "interpolated"
    #: No time is known (caller supplied none, or none could be derived).
    MISSING = "missing"

    @property
    def exact(self) -> bool:
        return self in {TimeBasis.SOURCE, TimeBasis.WORDS}


class Method(StrEnum):
    """Where a value comes from."""

    OBSERVED = "observed"  # copied from an upstream result
    DETERMINISTIC = "deterministic"  # exact computation from observed data
    HEURISTIC = "heuristic"  # rule or formula whose weights are design choices
    MODEL = "model"  # produced by a language model and then validated


class Origin(StrEnum):
    """Whether text is what the speaker said or what this module wrote."""

    DIRECT = "direct"  # verbatim from the transcript
    PARAPHRASE = "paraphrase"  # rewritten, and checked against its sources
    INFERENCE = "inference"  # not stated by the speaker


class ResultState(StrEnum):
    OK = "ok"
    WARNINGS = "ok_with_warnings"
    PARTIAL = "partial"  # a stage that was asked for did not produce its result
    NOT_APPLICABLE = "not_applicable"  # nothing to analyse (for example no speech)


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"  # not requested by the profile or configuration
    NOT_AVAILABLE = "not_available"  # requested, but the engine or input is missing
    FAILED = "failed"


class CacheOutcome(StrEnum):
    HIT = "hit"
    MISS = "miss"
    NOT_USED = "not_used"


class SegmentLevel(StrEnum):
    SECTION = "section"
    TOPIC = "topic"


class ScoreName(StrEnum):
    IMPORTANCE = "importance"
    INFORMATION_DENSITY = "information_density"
    UNIQUENESS = "uniqueness"
    STANDALONE = "standalone"
    HIGHLIGHT = "highlight"
    HOOK = "hook"
    REMOVAL_CANDIDATE = "removal_candidate"


class Role(StrEnum):
    HOOK = "hook"
    INTRODUCTION = "introduction"
    CONTEXT_SETTING = "context_setting"
    MAIN_POINT = "main_point"
    EXPLANATION = "explanation"
    SUPPORTING_EVIDENCE = "supporting_evidence"
    EXAMPLE = "example"
    DEMONSTRATION = "demonstration"
    COUNTERARGUMENT = "counterargument"
    CAVEAT = "caveat"
    TRANSITION = "transition"
    RECAP = "recap"
    EMPHASIS = "emphasis"
    QUESTION = "question"
    ANSWER = "answer"
    TURNING_POINT = "turning_point"
    RESOLUTION = "resolution"
    CONCLUSION = "conclusion"
    CALL_TO_ACTION = "call_to_action"
    ANECDOTE = "anecdote"
    HUMOR = "humor"
    UNCLASSIFIED = "unclassified"


class KeyKind(StrEnum):
    CLAIM = "claim"
    FACT = "fact"
    EXPLANATION = "explanation"
    ARGUMENT = "argument"
    DECISION = "decision"
    RECOMMENDATION = "recommendation"
    EXAMPLE = "example"
    DEFINITION = "definition"
    QUESTION = "question"
    ANSWER = "answer"
    CONCLUSION = "conclusion"
    ACTION_ITEM = "action_item"
    QUOTABLE = "quotable"
    CAVEAT = "caveat"
    OPEN_QUESTION = "open_question"


class RelationKind(StrEnum):
    SUPPORTS = "supports"
    EXPLAINS = "explains"
    ILLUSTRATES = "illustrates"
    QUALIFIES = "qualifies"
    CONTRADICTS = "contradicts"
    ANSWERS = "answers"
    DEPENDS_ON = "depends_on"
    REFERENCES = "references"
    CONTINUES = "continues"
    SUMMARIZES = "summarizes"
    REPEATS = "repeats"
    OVERLAPS = "overlaps"
    TRANSITIONS_TO = "transitions_to"


class Adds(StrEnum):
    """What a later overlapping passage contributes beyond the earlier one."""

    NEW_EVIDENCE = "new_evidence"
    NEW_EXAMPLE = "new_example"
    QUALIFICATION = "qualification"
    DIFFERENT_PERSPECTIVE = "different_perspective"
    STRONGER_EXPLANATION = "stronger_explanation"
    RECAP = "recap"
    CALLBACK = "callback"
    NOTHING_NEW = "nothing_new"


class RefKind(StrEnum):
    UNIT = "unit"
    SEGMENT = "segment"
    TOPIC = "topic"
    ITEM = "item"


class OpportunityKind(StrEnum):
    BROLL = "broll"
    GRAPHIC = "graphic"


class GraphicKind(StrEnum):
    KEY_NUMBER = "key_number"
    DEFINITION = "definition"
    LIST = "list"
    PROCESS = "process"
    COMPARISON = "comparison"
    NAME_AND_ROLE = "name_and_role"
    QUOTE = "quote"
    CALL_TO_ACTION = "call_to_action"
    TERM_CLARIFICATION = "term_clarification"


class VisualCategory(StrEnum):
    OBJECT = "object_closeup"
    LOCATION = "location"
    PROCESS = "process_footage"
    ARCHIVE = "archive_or_historical"
    COMPARISON = "comparison_visual"
    DIAGRAM = "diagram"
    ACTION = "physical_action"


class RhetoricalKind(StrEnum):
    """What a statement DOES in the text. Never a claim about what the speaker feels."""

    RHETORICAL_QUESTION = "rhetorical_question"
    CONTRAST = "contrast"
    EMPHASIS = "emphasis"
    SURPRISE = "surprise"
    UNCERTAINTY = "uncertainty"
    PERSONAL_DISCLOSURE = "personal_disclosure"
    STRONG_CONCLUSION = "strong_conclusion"
    DIRECTION_CHANGE = "direction_change"
    PERSUASION = "persuasion"
    HUMOR = "humor"


class SpeechTiming(StrEnum):
    """Upstream word-time quality, mirrored so the domain does not import another module."""

    ALIGNED = "aligned"
    INTERPOLATED = "interpolated"
    SEGMENT_ESTIMATE = "segment_estimate"
