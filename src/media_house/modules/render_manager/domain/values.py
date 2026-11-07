"""Vocabulary of rendering: what kind of render, where a job is, how sure its verification is."""

from enum import StrEnum

#: Bumped when a rule or a default of the render manager changes what a render means.
MODULE_VERSION = 1
SCHEMA_VERSION = 1
REPORT_OPERATION = "render_report"
#: Recorded for every resource the backend does not let us measure (never a made-up number).
UNMEASURED = "unmeasured"


class RenderType(StrEnum):
    #: A whole-timeline render to look at; fast, never delivered.
    REVIEW = "review"
    #: A stretch of a review render with context around it.
    SECTION_PREVIEW = "section_preview"
    #: A review-quality render kept next to an earlier one to compare the two.
    COMPARISON = "comparison"
    #: A render other work reads (a ProRes of a section); not a deliverable.
    INTERMEDIATE = "intermediate"
    #: The master / delivery file; needs the export authorization of a person.
    FINAL = "final"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_final(self) -> bool:
        return self in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED)


class Purpose(StrEnum):
    PREVIEW = "preview"
    INTERMEDIATE = "intermediate"
    MASTER = "master"
    DELIVERY = "delivery"


class CheckState(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"


class Verification(StrEnum):
    """What looking at the finished file concluded. A completed job is not a verified file."""

    VERIFIED = "verified"
    WITH_WARNINGS = "with_warnings"
    FAILED = "failed"
    NOT_VERIFIED = "not_verified"
