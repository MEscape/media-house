"""Versions, document identity and the small vocabularies of the visual style engine."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from media_house.core.domain import Rational

#: Shape of a stored ``visual_style`` document. Bump on any field change, except a field marked
#: ``ADDED_IN`` (left out while absent, so every older document keeps reading unchanged).
SCHEMA_VERSION: Final = 1
#: Field metadata key of a token group / field added by a later identity version (value: that
#: version). While its value is ``None`` the field is left out of the stored and canonical form,
#: so released identities, their digests and every stored fingerprint stay byte-identical.
ADDED_IN: Final = "added_in_identity"
#: Schema of the configurable knobs in ``profile.py``. Renaming or removing a knob bumps it.
PROFILE_VERSION: Final = 1
DOCUMENT_TYPE: Final = "visual_style"
RESULT_OPERATION: Final = "visual_style"
#: Library tag on every stored style, so all of them can be listed (identity upgrades).
STYLE_TAG: Final = "visual_style"
#: Sizes in the token tree are "reference pixels": pixels on an output whose SHORT side is 1080
#: (the height of 1920x1080, the width of 1080x1920). A 4K output scales them by 2.
REFERENCE_SHORT_SIDE: Final = 1080


class RuleKind(StrEnum):
    """Mandatory = brand-defining and locked (only a new identity version changes it).
    Preference = the default look; format profiles, projects and sections may change it with
    a reason."""

    MANDATORY = "mandatory"
    PREFERENCE = "preference"


class Evidence(StrEnum):
    MEASURED = "measured"  # a value measured on channel material (HEX from frames)
    OBSERVED = "observed"  # a recurring pattern seen in the samples, value by eye
    PROPOSED = "proposed"  # a design decision made here, not seen on the channel


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TextCase(StrEnum):
    UPPER = "upper"
    SENTENCE = "sentence"
    AS_WRITTEN = "as_written"


class ZoneKind(StrEnum):
    CAPTION = "caption"  # speech captions only
    GRAPHIC = "graphic"  # titles, lower thirds, callouts, cards
    BRAND = "brand"  # logo / badge
    LABEL = "label"  # sponsor label, footage credit
    KEEP_OUT = "keep_out"  # platform interface: nothing may be drawn here


class ElementKind(StrEnum):
    """What a consumer draws; each zone accepts a fixed set of kinds."""

    CAPTION = "caption"
    TITLE = "title"
    LOWER_THIRD = "lower_third"
    RANK_CARD = "rank_card"
    CALLOUT = "callout"
    PRICE_TAG = "price_tag"
    STICKER = "sticker"
    LOGO = "logo"
    SPONSOR_LABEL = "sponsor_label"
    CREDIT = "credit"
    CTA = "cta"
    FULL_SCREEN = "full_screen"  # a card that replaces the picture (spec card, end screen, poll)


class OverrideLayer(StrEnum):
    FORMAT_PROFILE = "format_profile"
    PROJECT = "project"
    SECTION = "section"


class MotionPhase(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    EMPHASIS = "emphasis"
    #: A transition between clips (duration range: ``transitions.duration_*``, identity v2+).
    TRANSITION = "transition"


class Surface(StrEnum):
    VIDEO = "video"
    THUMBNAIL = "thumbnail"


class Severity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class QcCategory(StrEnum):
    TECHNICAL = "technical"
    CREATIVE = "creative"


@dataclass(frozen=True, slots=True, kw_only=True)
class QcIssue:
    """One finding (Layer 4 convention, docs/layer4/contracts.md section 6)."""

    code: str
    severity: Severity
    category: QcCategory
    message: str
    item_id: str | None = None
    timeline_start: Rational | None = None
    timeline_end: Rational | None = None
    evidence: str = ""
