"""Versions, document identity and the vocabularies of the caption engine."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from media_house.core.domain import Rational

#: Shape of a stored ``caption_plan`` document. Bump on any field change.
SCHEMA_VERSION: Final = 1
#: Selection, segmentation, emphasis, emoji and layout rules. Bump when the same inputs plan
#: differently (it changes every cache key, so old plans are not reused for new rules).
# 7: Shorts sticker emoji (wider vocabulary, longest stem wins, denser); 6: no caption over a
# full-screen card; 5: OCR caption spans must read heard words
PLANNER_VERSION: Final = 7  # 4: groups render in batches (asset_start_frame); 3: burned-in, end
#: Vocabularies (modes, reasons, QC codes; additions are additive).
VOCABULARY_VERSION: Final = 1
#: Schema of the configurable knobs in ``profile.py``.
PROFILE_VERSION: Final = 3  # 3: emoji.display, exact emoji rules; 2: Short emoji, SHORT_OF
DOCUMENT_TYPE: Final = "caption_plan"
RESULT_OPERATION: Final = "caption_plan"


class CaptionMode(StrEnum):
    """What spoken content is burned in (selection; presentation is decided separately)."""

    FULL_CAPTIONS = "full_captions"
    SELECTIVE_CAPTIONS = "selective_captions"
    EMPHASIS_ONLY = "emphasis_only"
    KEY_INFORMATION = "key_information"
    DISABLED = "disabled"


class EmphasisLevel(StrEnum):
    NORMAL = "normal"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


#: Order of the levels (higher = stronger).
LEVEL_RANK: Final = {
    EmphasisLevel.NORMAL: 0,
    EmphasisLevel.MEDIUM: 1,
    EmphasisLevel.HIGH: 2,
    EmphasisLevel.CRITICAL: 3,
}


class FactKind(StrEnum):
    """Spoken facts recognised upstream (content_intelligence) that captions protect/emphasise."""

    PRICE = "price"
    RANK = "rank"
    SPEC = "spec"
    RETAILER = "retailer"
    PRODUCT = "product"
    NUMBER = "number"


class Cue(StrEnum):
    """Why a word or sentence matters (evidence names, stored with each decision)."""

    PRICE = "price"
    RANK = "rank"
    SPEC = "spec"
    RETAILER = "retailer"
    PRODUCT = "product"
    NUMBER = "number"
    KEY_ITEM = "key_item"
    WARNING = "warning"
    CONTRAST = "contrast"
    STRONG_CLAIM = "strong_claim"
    VOCAL = "vocal"
    BEAT = "beat"


class WordFate(StrEnum):
    """What happened to a transcript word on its way into the edit's captions."""

    SHOWN = "shown"
    NOT_SELECTED = "not_selected"
    SUSPECT = "suspect"
    LOW_CONFIDENCE = "low_confidence"
    WRONG_LANGUAGE = "wrong_language"
    SPLIT_BY_CUT = "split_by_cut"
    REJECTED = "rejected"
    OVERLAP = "overlap"


class Action(StrEnum):
    """A person's decision about one caption item (ids are stable across replans)."""

    REJECT = "reject"
    NO_EMPHASIS = "no_emphasis"
    NO_EMOJI = "no_emoji"
    #: Replace one word's text (a transcription fix); ``item_id`` is the word id.
    CORRECT = "correct"


class Severity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class QcCategory(StrEnum):
    TECHNICAL = "technical"
    CREATIVE = "creative"


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


class RenderStatus(StrEnum):
    RENDERED = "rendered"
    REUSED = "reused"
    NOT_REQUESTED = "not_requested"
    NOT_AVAILABLE = "not_available"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class QcIssue:
    code: str
    severity: Severity
    category: QcCategory
    message: str
    item_id: str | None = None
    timeline_start: Rational | None = None
    timeline_end: Rational | None = None
    evidence: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    kind: str
    how: InputHow
    asset_id: str | None = None
    identity: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StageReport:
    stage: str
    state: StageState
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class EditRef:
    """The edit a plan was made for (``None`` on the no-speech path)."""

    edit_plan_id: str
    plan_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleBinding:
    """The visual style a plan was made with (copied from ``visual_style_engine.StyleRef``)."""

    document_id: str | None
    fingerprint: str
    identity_version: int
    profile: str
