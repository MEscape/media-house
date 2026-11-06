"""Versions and vocabulary of the Resolve adapter.

A vocabulary addition bumps ``VOCABULARY_VERSION`` and is additive; a change of shape bumps
``SCHEMA_VERSION`` (older stored documents are then refused, never reinterpreted).
"""

from enum import StrEnum

ADAPTER_VERSION = 1
SCHEMA_VERSION = 1
VOCABULARY_VERSION = 1

BINDING_DOCUMENT_TYPE = "resolve_binding"
BINDING_OPERATION = "resolve_binding"
REPORT_DOCUMENT_TYPE = "resolve_execution_report"
REPORT_OPERATION = "resolve_execution_report"
RENDER_OPERATION = "resolve_render"

#: The built-timeline schema versions this adapter reads (anything else is refused).
SUPPORTED_BUILT_SCHEMAS = frozenset({1})

#: Written into every marker this adapter makes, so a person's own markers are never touched.
TAG_PREFIX = "mh:"


class ConnectionState(StrEnum):
    NOT_INSTALLED = "not_installed"
    NOT_RUNNING = "not_running"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    UNSUPPORTED_VERSION = "unsupported_version"
    API_UNAVAILABLE = "api_unavailable"
    CONNECTION_FAILED = "connection_failed"
    DISCONNECTED = "disconnected"


class CapabilityState(StrEnum):
    SUPPORTED = "supported"  # confirmed by a read-back in this Resolve (version + edition)
    PARTIALLY_SUPPORTED = "partially_supported"  # confirmed, with a documented limit
    UNSUPPORTED = "unsupported"  # no scripting method exists (a known gap) or it is missing
    VERSION_DEPENDENT = "version_dependent"  # this Resolve is older than the operation needs
    EDITION_DEPENDENT = "edition_dependent"  # this edition lacks it (Studio only)
    PLUGIN_DEPENDENT = "plugin_dependent"  # a plugin or component is missing
    NOT_VERIFIED = "not_verified"  # the method exists; nothing has confirmed the whole operation


class ExecutionState(StrEnum):
    QUEUED = "queued"
    INITIALIZING = "initializing"
    CONNECTING = "connecting"
    CONFIGURING_PROJECT = "configuring_project"
    IMPORTING_MEDIA = "importing_media"
    BUILDING_TIMELINE = "building_timeline"
    APPLYING_EFFECTS = "applying_effects"
    VERIFYING_PROJECT = "verifying_project"
    READY_TO_RENDER = "ready_to_render"
    RENDERING = "rendering"
    VERIFYING_RENDER = "verifying_render"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            ExecutionState.COMPLETED,
            ExecutionState.PARTIALLY_COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
            ExecutionState.INTERRUPTED,
        }


class Outcome(StrEnum):
    """What the pipeline is told. Never one generic success."""

    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class ExecutionPolicy(StrEnum):
    """What the adapter may do to a project that already holds work."""

    #: A new timeline, next to whatever exists. Nothing existing is touched.
    CREATE_NEW = "create_new"
    #: In place. A conflict (both sides changed a field) is resolved for Media House.
    UPDATE_MANAGED_ELEMENTS = "update_managed_elements"
    #: In place. A conflict is resolved for the person; the change from Media House is dropped.
    PRESERVE_MANUAL_EDITS = "preserve_manual_edits"
    #: In place. A conflict keeps the person's version AND stays open; rendering is blocked.
    REQUIRE_CONFLICT_REVIEW = "require_conflict_review"
    #: Back up, keep the old timeline under a new name, then build a fresh one.
    FULL_REBUILD_WITH_BACKUP = "full_rebuild_with_backup"


class OperationStatus(StrEnum):
    EXECUTED = "executed"
    PARTIAL = "partial"  # done, with something left for a person
    SKIPPED = "skipped"  # nothing to do (already in place), or not allowed
    UNSUPPORTED = "unsupported"  # the capability does not exist; kept and reported
    FAILED = "failed"


class Verification(StrEnum):
    VERIFIED = "verified"  # read back and equal
    PARTIALLY_VERIFIED = "partially_verified"
    NOT_VERIFIABLE = "not_verifiable"  # the API cannot show it
    FAILED = "failed"
    NOT_CHECKED = "not_checked"


class Strategy(StrEnum):
    """How an operation was carried out."""

    NATIVE_API = "native_api"
    PROJECT_SETTING = "project_setting"
    IMPORTED_FILE = "imported_file"  # a rendered file or a subtitle file imported into the pool
    MANUAL_MARKER = "manual_marker"  # a marker tells a person what to do in Resolve (one step)
    NOT_APPLIED = "not_applied"


class Area(StrEnum):
    CONNECTION = "connection"
    PROJECT = "project"
    SETTING = "setting"
    MEDIA = "media"
    PROXY = "proxy"
    TRACK = "track"
    CLIP = "clip"
    AUDIO = "audio"
    TRANSITION = "transition"
    EFFECT = "effect"
    CAPTION = "caption"
    MARKER = "marker"
    IDENTITY = "identity"
    SYNC = "sync"
    RENDER = "render"


class ChangeOrigin(StrEnum):
    MEDIA_HOUSE = "media_house"
    RESOLVE = "resolve"  # a person (or another tool) changed it in Resolve
    BOTH = "both"
    UNKNOWN = "unknown"


class Resolution(StrEnum):
    """How a conflict between the two sides was settled."""

    KEPT_PERSON = "kept_person"
    APPLIED_MEDIA_HOUSE = "applied_media_house"
    OPEN = "open"  # held for review


class CaptionStrategy(StrEnum):
    """How the caption text meets Resolve. The compiler decided the look; this only chooses
    where the person edits the words."""

    #: The rendered caption clips the plan made (the designed, animated look). Not editable text.
    RENDERED = "rendered"
    #: Also import the cues as a subtitle file, so a wrong word is a text edit in Resolve.
    #: The rendered clips stay; the person decides which one the delivery shows.
    RENDERED_WITH_EDITABLE_CUES = "rendered_with_editable_cues"


class RetimePlacement(StrEnum):
    """Scripting cannot retime a clip. What happens to a clip that needs it."""

    PLACE_WITH_MARKER = "place_with_marker"  # at real speed, a marker says what to apply
    SKIP = "skip"  # leave a gap and report it
