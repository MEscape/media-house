"""Vocabulary of revisions: what kind of change, who may approve it, where it stands."""

from enum import StrEnum

SCHEMA_VERSION = 1
#: Bumped when the mapping from findings to changes, or a level of authority, changes.
REVISION_VERSION = 1
DOCUMENT_TYPE = "revision_plan"
RESULT_OPERATION = "revision_plan"


class ChangeKind(StrEnum):
    """What a proposed change does. Every kind but ``HANDOFF`` is carried out by this module through
    the owner's own contract; ``HANDOFF`` is a request to an owner whose re-planning this module
    does not drive (it says so and never pretends the change was made)."""

    TRIM_CLIP = "trim_clip"
    DELETE_CLIP = "delete_clip"
    DISABLE_CLIP = "disable_clip"
    SET_OPACITY = "set_opacity"
    REBUILD_TIMELINE = "rebuild_timeline"
    MIX_BUS_GAIN = "mix_bus_gain"
    MIX_CLIP_GAIN = "mix_clip_gain"
    REMIX = "remix"
    RESYNC_RESOLVE = "resync_resolve"
    ROLLBACK = "rollback"
    HANDOFF = "handoff"


class Level(StrEnum):
    #: Reversible technical operation the project authorized in advance.
    AUTOMATIC = "A"
    #: A creative change a person approves.
    SUGGESTED = "B"
    #: Only on a person's explicit instruction.
    MANUAL_ONLY = "C"


class ChangeStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    #: Passed to the module that owns it; this module did not (and cannot) carry it out.
    HANDED_OFF = "handed_off"
    SKIPPED = "skipped"
    FAILED = "failed"
    #: Would overwrite a person's work or touch a locked section: waits for an explicit decision.
    CONFLICT = "conflict"


class PlanStatus(StrEnum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    PARTIAL = "partial"
    FAILED = "failed"
    #: The timeline changed after the plan was made: it describes another video.
    STALE = "stale"


class Origin(StrEnum):
    """Who made an element, as far as the timeline's record shows."""

    MEDIA_HOUSE = "media_house"
    AUTOMATED_REVISION = "automated_revision"
    HUMAN_MANUAL = "human_manual"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class InstructionKind(StrEnum):
    DIRECTIVE = "directive"
    PREFERENCE = "preference"
    QUESTION = "question"
    REJECTION = "rejection"
    APPROVAL = "approval"
    CLARIFICATION = "clarification"
    LOCK = "lock"
    KEEP = "keep"


class TargetKind(StrEnum):
    SECTION = "section"
    CLIPS = "clips"
    ROLE = "role"
    TRANSITION = "transition"
    GLOBAL = "global"
    NONE = "none"
    AMBIGUOUS = "ambiguous"


class Role(StrEnum):
    DIALOGUE = "dialogue"
    MUSIC = "music"
    SFX = "sfx"
    CAPTIONS = "captions"
    GRAPHICS = "graphics"
    PICTURE = "picture"


#: The closed vocabulary of what a person can ask for (anything else is a clarification).
INTENTS = (
    "shorten",
    "lengthen",
    "remove",
    "quieter",
    "louder",
    "music_later",
    "original_audio",
    "less_processed",
    "softer_style",
    "more_dynamic",
    "more_of",
    "change_transition",
    "abrupt_ending",
)
