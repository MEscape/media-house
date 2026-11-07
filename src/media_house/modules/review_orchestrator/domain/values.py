"""Vocabulary of the review workflow: the states a review session can be in, who may decide,
and what a decision can be about."""

from enum import StrEnum

SCHEMA_VERSION = 1
#: Bumped when a rule of the state machine or of approval validity changes.
ORCHESTRATOR_VERSION = 1


class ReviewState(StrEnum):
    CREATED = "created"
    ANALYSIS_PENDING = "analysis_pending"
    ANALYSIS_COMPLETE = "analysis_complete"
    REVIEW_READY = "review_ready"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    FEEDBACK_RECEIVED = "feedback_received"
    REVISION_PROPOSED = "revision_proposed"
    AWAITING_REVISION_APPROVAL = "awaiting_revision_approval"
    REVISION_APPROVED = "revision_approved"
    REVISION_REJECTED = "revision_rejected"
    REVISION_IN_PROGRESS = "revision_in_progress"
    REVISION_COMPLETE = "revision_complete"
    REVALIDATION_PENDING = "revalidation_pending"
    REVIEW_AGAIN_REQUIRED = "review_again_required"
    FINAL_POLISH_PENDING = "final_polish_pending"
    FINAL_REVIEW_READY = "final_review_ready"
    AWAITING_FINAL_APPROVAL = "awaiting_final_approval"
    FINAL_APPROVED = "final_approved"
    EXPORT_AUTHORIZED = "export_authorized"
    EXPORT_IN_PROGRESS = "export_in_progress"
    EXPORT_VALIDATION_PENDING = "export_validation_pending"
    EXPORT_VALIDATED = "export_validated"
    EXPORT_FAILED = "export_failed"
    DELIVERY_READY = "delivery_ready"
    CANCELLED = "cancelled"


#: Nothing leaves these.
TERMINAL_STATES = frozenset({ReviewState.DELIVERY_READY, ReviewState.CANCELLED})

#: Where a change to the timeline makes everything decided about the final video void.
FINAL_STAGE_STATES = frozenset(
    {
        ReviewState.FINAL_REVIEW_READY,
        ReviewState.AWAITING_FINAL_APPROVAL,
        ReviewState.FINAL_APPROVED,
        ReviewState.EXPORT_AUTHORIZED,
        ReviewState.EXPORT_FAILED,
    }
)
#: A proposal is made against one timeline; when that changes the proposal is stale.
REVISION_STAGE_STATES = frozenset(
    {
        ReviewState.REVISION_PROPOSED,
        ReviewState.AWAITING_REVISION_APPROVAL,
        ReviewState.REVISION_APPROVED,
    }
)


class SectionStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    LOCKED = "locked"
    REVISION_REQUESTED = "revision_requested"


class ApprovalKind(StrEnum):
    #: One proposal of a revision plan (no state change by itself).
    PROPOSAL = "proposal"
    #: The decision on a whole revision plan (moves the review).
    REVISION = "revision"
    SECTION = "section"
    FINAL = "final"
    #: The step that lets the final export start (recorded by ``authorize_export``).
    EXPORT = "export"
    DELIVERY = "delivery"
    #: A person knowingly accepts an issue that would otherwise block the export.
    OVERRIDE = "override"


class Verdict(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    #: The person approved a changed version of the proposal (the change is in ``modification``).
    MODIFIED = "modified"


class ActorKind(StrEnum):
    HUMAN = "human"
    #: Automation acting on a grant the project made in advance (``PermissionPolicy``).
    PRECONFIGURED = "preconfigured"
    SYSTEM = "system"


class ArtifactKind(StrEnum):
    QUALITY_REPORT = "quality_report"
    REVISION_PLAN = "revision_plan"
    FINAL_POLISH_REPORT = "final_polish_report"
    RENDER_REPORT = "render_report"
    EXPORT_VALIDATION_REPORT = "export_validation_report"
    EXPORT_REPORT = "export_report"


class ExportStatus(StrEnum):
    NONE = "none"
    AUTHORIZED = "authorized"
    IN_PROGRESS = "in_progress"
    VALIDATING = "validating"
    #: Automated checks could not decide; only a person's decision moves it on.
    MANUAL_REVIEW = "manual_review"
    VALIDATED = "validated"
    FAILED = "failed"


class ValidationStatus(StrEnum):
    """The verdict of ``export_validation`` as the workflow needs it."""

    PASS = "pass"  # noqa: S105 - a verdict, not a credential
    WARNING = "warning"
    FAIL = "fail"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
