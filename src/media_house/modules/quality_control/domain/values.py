"""Vocabulary of quality control: how serious a finding is, what it is about, how it was found."""

from enum import StrEnum

SCHEMA_VERSION = 1
#: Bumped when a check, a threshold or a classification changes what a report says.
QC_VERSION = 1
DOCUMENT_TYPE = "quality_report"
RESULT_OPERATION = "quality_report"
EVIDENCE_OPERATION = "quality_evidence"


class Severity(StrEnum):
    #: The video cannot safely go on to export, or a serious failure.
    CRITICAL = "critical"
    #: A clearly noticeable problem that needs attention.
    HIGH = "high"
    #: Noticeable, may be acceptable in context.
    MEDIUM = "medium"
    LOW = "low"
    #: A subjective creative suggestion.
    OPTIONAL = "optional"


SEVERITY_RANK = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.OPTIONAL: 0,
}


class Category(StrEnum):
    STRUCTURE = "structure"
    TECHNICAL_VIDEO = "technical_video"
    VISUAL = "visual"
    EDITING = "editing"
    AUDIO = "audio"
    CAPTIONS = "captions"
    GRAPHICS = "graphics"
    STORY = "story"
    CONTINUITY = "continuity"
    RESOLVE_SYNC = "resolve_sync"


class Basis(StrEnum):
    #: Measured or counted: it is so, within the stated confidence.
    OBJECTIVE = "objective"
    #: A creative opinion: another editor may decide differently. Never blocks an export.
    SUBJECTIVE = "subjective"


class Area(StrEnum):
    TECHNICAL = "technical"
    VISUAL = "visual"
    AUDIO = "audio"
    EDITING = "editing"
    STORY = "story"


class AreaState(StrEnum):
    OK = "ok"
    WARNING = "warning"
    PROBLEM = "problem"
    #: Nothing could be checked here (no render, no plan): says nothing about the video.
    NOT_CHECKED = "not_checked"


class CheckState(StrEnum):
    RAN = "ran"
    NOT_RUN = "not_run"
    FAILED = "failed"


class TechnicalStatus(StrEnum):
    OK = "ok"
    WARNINGS = "warnings"
    PROBLEMS = "problems"
    #: An export-blocking issue is open.
    BLOCKED = "blocked"


class ReviewStatus(StrEnum):
    """What the report says about the next step. It is never an approval: a person decides."""

    READY_FOR_HUMAN_REVIEW = "ready_for_human_review"
    NEEDS_ATTENTION = "needs_attention"
