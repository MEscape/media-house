"""Vocabulary of the final polish: how a presentation check ended and how ready the video is."""

from enum import StrEnum

SCHEMA_VERSION = 1
#: Bumped when a check or the readiness rule changes what a report says.
POLISH_VERSION = 1
DOCUMENT_TYPE = "final_polish_report"
RESULT_OPERATION = "final_polish_report"
EVIDENCE_OPERATION = "final_polish_evidence"


class Severity(StrEnum):
    PROBLEM = "problem"
    WARNING = "warning"
    NOTE = "note"


class SectionState(StrEnum):
    OK = "ok"
    WARNING = "warning"
    PROBLEM = "problem"
    NOT_CHECKED = "not_checked"


class Readiness(StrEnum):
    """What the polish says about the final review. It is never an approval of the video."""

    READY_FOR_FINAL_REVIEW = "ready_for_final_review"
    READY_WITH_WARNINGS = "ready_with_warnings"
    NOT_READY = "not_ready"


class Source(StrEnum):
    FINAL_POLISH = "final_polish"
    QUALITY_CONTROL = "quality_control"
    AUDIO_MIX_ENGINE = "audio_mix_engine"
    REVIEW = "review"
