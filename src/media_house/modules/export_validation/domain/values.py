"""Vocabulary of export validation: the four verdicts, how serious and how sure a finding is, and
which module a problem goes back to."""

from enum import StrEnum

#: Bumped when a rule, a threshold or the way findings become a verdict changes.
VALIDATOR_VERSION = 1
SCHEMA_VERSION = 1
REPORT_OPERATION = "export_validation_report"


class Status(StrEnum):
    #: Every mandatory requirement is satisfied.
    PASS = "pass"  # noqa: S105 - a verdict, not a credential
    #: Usable, with non-critical issues.
    WARNING = "warning"
    #: Not to be delivered.
    FAIL = "fail"
    #: Automated validation cannot reliably tell; a person looks. Never turned into PASS.
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Certainty(StrEnum):
    #: Measured, and it contradicts the requirement.
    CONFIRMED = "confirmed"
    #: Measured, and it probably is a problem (it could be intended).
    LIKELY = "likely"
    #: Suspicious; the evidence does not decide.
    POSSIBLE = "possible"


class Category(StrEnum):
    INTEGRITY = "integrity"
    VIDEO = "video"
    AUDIO = "audio"
    VISUAL = "visual"
    DELIVERY = "delivery"
    TIMELINE = "timeline"


class Route(StrEnum):
    """Who owns the fix. A render-setting problem never goes back to editing, and a problem of the
    timeline never to the renderer."""

    RENDER_MANAGER = "render_manager"
    RESOLVE_ADAPTER = "resolve_adapter"
    AUDIO_MIX_ENGINE = "audio_mix_engine"
    REVISION_ENGINE = "revision_engine"
    QUALITY_CONTROL = "quality_control"
    RESOLVE_EXPORT = "resolve_export"
    MEDIA_MODULES = "media_modules"
    NONE = "none"


class NextAction(StrEnum):
    DELIVER = "deliver"
    RERENDER = "rerender"
    REVISE_TIMELINE = "revise_timeline"
    REMIX_AUDIO = "remix_audio"
    RECHECK_MEDIA = "recheck_media"
    #: The cause is not known from the file alone: look at the timeline at that time first.
    INVESTIGATE = "investigate"
    MANUAL_REVIEW = "manual_review"
    REEXPORT = "reexport"


class CheckState(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"
