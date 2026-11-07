"""Vocabulary of the final export: how an export stands and what stops one from starting."""

from enum import StrEnum

SCHEMA_VERSION = 1
MODULE_VERSION = 1
DOCUMENT_TYPE = "export_report"
REPORT_OPERATION = "export_report"


class ExportStatus(StrEnum):
    AUTHORIZED = "authorized"
    RENDERING = "rendering"
    VALIDATING = "validating"
    #: The file passed (or passed with warnings): technically ready, not yet delivered.
    VALIDATED = "validated"
    #: Automated validation could not decide; a person looks at the file.
    MANUAL_REVIEW = "manual_review"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeliveryStatus(StrEnum):
    NOT_DELIVERED = "not_delivered"
    AWAITING_APPROVAL = "awaiting_delivery_approval"
    DELIVERED = "delivered"
    BLOCKED = "blocked"


class BlockerKind(StrEnum):
    """Why a final export cannot (yet) start. A hard blocker is never overridden."""

    NO_QUALITY_REPORT = "no_quality_report"
    NO_POLISH_REPORT = "no_polish_report"
    NOT_CURRENT = "not_current"
    CRITICAL_ISSUE = "critical_issue"
    HIGH_ISSUE = "high_issue"
    POLISH_PROBLEM = "polish_problem"
    UNFINISHED = "unfinished"
    MIX_NOT_READY = "mix_not_ready"
    MEDIA_OFFLINE = "media_offline"
    NOT_IN_RESOLVE = "not_in_resolve"
    RESOLVE_STALE = "resolve_stale"
    RESOLVE_CONFLICT = "resolve_conflict"
    PROFILE_UNUSABLE = "profile_unusable"
    UNKNOWN_PROFILE = "unknown_profile"
