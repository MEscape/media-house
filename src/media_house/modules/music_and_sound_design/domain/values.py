"""Versions, document types and the closed vocabularies of music and sound design.

Bump ``ANALYZER_VERSION`` when a measurement changes (every stored ``music_analysis`` /
``sfx_profile`` is then recomputed on demand), ``PLANNER_VERSION`` when planning changes,
``SCHEMA_VERSION`` when a stored document changes shape and ``PROFILE_VERSION`` when a knob is
renamed or removed. Vocabulary additions bump ``VOCABULARY_VERSION`` and are additive.
"""

from enum import StrEnum
from typing import Final

ANALYZER_VERSION: Final = 1
#: 2: incidental speech keeps a take's sound in front; 3: the appended end screen is an outro
#: 4: a track that ends before its cue fades out instead of a negative tail
#: 5: a loud moment inside the speech of a talking clip is the voice, not protected sound
PLANNER_VERSION: Final = 5
RENDERER_VERSION: Final = 1
SCHEMA_VERSION: Final = 1
VOCABULARY_VERSION: Final = 2  # 2: transition rules use transition_engine kind names
PROFILE_VERSION: Final = 1

#: Stored documents (top-level ``document_type``) and derived-asset operations.
MUSIC_ANALYSIS: Final = "music_analysis"
SFX_PROFILE: Final = "sfx_profile"
SOUND_DESIGN_PLAN: Final = "sound_design_plan"
STEM_RENDER: Final = "sound_stem_render"

#: Library tags of every imported sound (the library filters by tags only).
LIBRARY_TAG: Final = "sound_library"
KIND_TAG_PREFIX: Final = "sound:"
CATEGORY_TAG_PREFIX: Final = "category:"
SOURCE_TAG_PREFIX: Final = "source:"
#: The asset-metadata key that holds the manifest facts (licence, stated values).
METADATA_KEY: Final = "sound_library"

#: What ``vocal_presence`` says while no model can measure it.
NOT_AVAILABLE: Final = "not_available"


class SoundKind(StrEnum):
    MUSIC = "music"
    SFX = "sfx"


class LicenceState(StrEnum):
    """``commercial_use`` of the manifest: true / null / false."""

    CLEARED = "cleared"
    UNCONFIRMED = "unconfirmed"
    NOT_COMMERCIAL = "not_commercial"


class SectionLabel(StrEnum):
    """A music section's label; anything but ``unlabeled`` needs measured evidence."""

    INTRO = "intro"
    BUILD = "build"
    DROP = "drop"
    CLIMAX = "climax"
    OUTRO = "outro"
    UNLABELED = "unlabeled"


class EditPointKind(StrEnum):
    TRACK_START = "track_start"
    SECTION_START = "section_start"
    PHRASE_START = "phrase_start"
    LOOP_START = "loop_start"
    LOOP_END = "loop_end"
    FADE_OUT_START = "fade_out_start"
    ENDING = "ending"


class SectionRole(StrEnum):
    """What a timeline section does for the viewer (decides music presence and energy)."""

    HOOK = "hook"
    INTRO = "intro"
    BODY = "body"
    EXPLANATION = "explanation"
    DEMONSTRATION = "demonstration"
    REVEAL = "reveal"
    RECAP = "recap"
    CALL_TO_ACTION = "call_to_action"
    OUTRO = "outro"


class MusicPresence(StrEnum):
    """A profile's wish for a role: music in front, a bed under speech, none, or silence."""

    FEATURE = "feature"
    BED = "bed"
    OFF = "off"
    SILENCE = "silence"


class MusicMode(StrEnum):
    MUSIC_ENABLED = "music_enabled"
    MUSIC_DISABLED = "music_disabled"
    INTENTIONAL_SILENCE = "intentional_silence"


class ClipAudio(StrEnum):
    """What the edit does with a clip's own sound (the edit's audio behaviour)."""

    SOURCE_SYNC = "source_sync"
    VOICEOVER = "voiceover"
    MUTED = "muted"


class EventSource(StrEnum):
    GRAPHICS = "graphics"
    TRANSITION = "transition"
    CAPTION = "caption"
    EDIT = "edit"


class BridgeKind(StrEnum):
    J_CUT = "j_cut"
    L_CUT = "l_cut"
    BRIDGE = "bridge"


class SfxAlign(StrEnum):
    """Which point of the sound lands on the event: its peak, its start or its end."""

    PEAK = "peak"
    START = "start"
    END = "end"


class Need(StrEnum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class OriginalAudioMode(StrEnum):
    """How a clip's own sound is treated: it is never replaced."""

    DOMINANT = "dominant"
    UNDER_MUSIC = "under_music"
    SPEECH = "speech"
    MUTED_BY_EDIT = "muted_by_edit"
    VOICEOVER = "voiceover"


class Alignment(StrEnum):
    """How a track is laid against its cue."""

    FRONT_TIMED = "front_timed"
    BACK_TIMED = "back_timed"
    LOOPED = "looped"


class DuckKey(StrEnum):
    SPEECH = "speech"
    ORIGINAL_AUDIO = "original_audio"


class CrossfadeKind(StrEnum):
    TAIL = "tail"
    FADE_OUT = "fade_out"


class Decision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"
    NOT_AVAILABLE = "not_available"


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"


class ResultState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"


class Severity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class QcCategory(StrEnum):
    TECHNICAL = "technical"
    CREATIVE = "creative"


class Declined(StrEnum):
    """Why an event got no sound effect (restraint is recorded, never silent)."""

    NO_RULE = "no_rule"
    TOO_WEAK = "too_weak"
    SFX_DISABLED = "sfx_disabled"
    MASKS_SPEECH = "masks_speech"
    PRESERVED_AUDIO = "preserved_audio"
    TOO_CLOSE = "too_close"
    DENSITY = "density"
    NO_ASSET = "no_asset"
    PERSON_REJECTED = "person_rejected"
    OUTSIDE_TIMELINE = "outside_timeline"
