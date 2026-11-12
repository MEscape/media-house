"""What the caption engine reads: the caller's timeline and facts the application fills.

``CaptionTimeline`` is the module's OWN input (the pipeline maps the edit plan to it; this
module never knows the edit plan). Every other fact here is built by
``application/views.py`` from upstream contracts (audio transcript, content sentences and
commerce facts, video tracks through reframing crops, the visual style), so the domain imports
no other module. Source times are on the recording's common clock, timeline times from the
start of the edit; they meet only through a clip.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.caption_engine.domain.values import EditRef, FactKind, StyleBinding


def frac(r: Rational) -> Fraction:
    return Fraction(r.numerator, r.denominator)


def rat(f: Fraction) -> Rational:
    if f < 0:
        raise ValueError(f"a time is never negative ({f})")
    return Rational(f.numerator, f.denominator)


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionClip:
    """One range of a recording placed on the timeline at speed 1.

    ``picture``: its picture is on screen (subject-aware layout reads it). ``audio``: its own
    sound is heard, so its speech is captioned (a muted cover clip has ``audio=False``; a
    voice-over is an ``audio`` clip with ``picture=False``).
    """

    clip_id: str
    asset_id: str
    source_start: Rational
    source_end: Rational
    timeline_start: Rational
    picture: bool = True
    audio: bool = True
    section_id: str | None = None
    #: Story beat function of the clip (``hook``, ``payoff``, ...), when the story knows it.
    beat_function: str | None = None

    @property
    def timeline_end(self) -> Rational:
        return rat(frac(self.timeline_start) + frac(self.source_end) - frac(self.source_start))

    def to_timeline(self, source: Fraction) -> Fraction:
        return frac(self.timeline_start) + source - frac(self.source_start)

    def to_source(self, timeline: Fraction) -> Fraction:
        return frac(self.source_start) + timeline - frac(self.timeline_start)


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionTimeline:
    """The edit as the caption engine needs it (``edit=None`` on the no-speech path: one clip
    per source asset, timeline = source, no sections)."""

    edit: EditRef | None
    project_id: str | None
    frame_rate: Rational
    width: int
    height: int
    clips: tuple[CaptionClip, ...]
    story_format: str | None = None
    #: The frame rate is assumed (VFR source): seconds stay authoritative.
    frame_rate_assumed: bool = False

    @property
    def aspect(self) -> str:
        ratio = Fraction(self.width, self.height)
        return f"{ratio.numerator}:{ratio.denominator}"

    @property
    def anchor_asset_id(self) -> str:
        return self.clips[0].asset_id

    def problems(self) -> list[str]:
        found: list[str] = []
        if not self.clips:
            found.append("no clips")
        if self.width <= 0 or self.height <= 0:
            found.append("output size must be positive")
        if frac(self.frame_rate) <= 0:
            found.append("frame rate must be positive")
        ids = [c.clip_id for c in self.clips]
        if len(ids) != len(set(ids)):
            found.append("clip ids must be unique")
        for c in self.clips:
            if frac(c.source_end) <= frac(c.source_start):
                found.append(f"clip {c.clip_id}: empty source range")
        return found


# --- facts filled by the application ------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SpokenWord:
    """One transcript word (audio_intelligence) on the SOURCE clock."""

    asset_id: str
    index: int
    text: str
    start: Fraction
    end: Fraction
    confidence: float | None
    speaker: str | None
    #: audio_intelligence flagged it (low confidence inside a bang): never shown as certain.
    suspect: bool
    #: ``aligned`` | ``interpolated`` | ``segment_estimate``
    timing: str
    #: Delivery emphasis 0-1 from audio_intelligence (``None`` = no evidence).
    vocal: float | None
    sentence_id: str
    sentence_end: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Sentence:
    sentence_id: str
    asset_id: str
    #: Content key-item kinds that cover the sentence (``claim``, ``caveat``, ...).
    key_kinds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SpokenFact:
    """A fact content_intelligence recognised in the words ``first..last`` (inclusive)."""

    asset_id: str
    kind: FactKind
    first: int
    last: int
    text: str
    #: The rank number for a ``rank`` fact.
    rank: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptFacts:
    asset_id: str
    #: Library id of the stored audio timeline (traceability).
    document_id: str | None
    language: str | None
    #: ``forced_alignment`` | ``whisper_segment`` | ``none``
    alignment: str
    words: tuple[SpokenWord, ...]
    sentences: tuple[Sentence, ...]
    facts: tuple[SpokenFact, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SubjectBox:
    """A face / person / product in OUTPUT-normalised coordinates while it is visible
    (source clock of ``asset_id``)."""

    asset_id: str
    start: Fraction
    end: Fraction
    x0: float
    y0: float
    x1: float
    y1: float
    label: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CoveredSpan:
    """A planned graphic already shows this fact (key information is not repeated)."""

    timeline_start: Fraction
    timeline_end: Fraction
    kind: FactKind
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    def overlaps(self, other: "Rect") -> bool:
        return (
            self.left < other.left + other.width
            and other.left < self.left + self.width
            and self.top < other.top + other.height
            and other.top < self.top + self.height
        )

    def inside(self, other: "Rect") -> bool:
        return (
            self.left >= other.left
            and self.top >= other.top
            and self.left + self.width <= other.left + other.width
            and self.top + self.height <= other.top + other.height
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ZoneFacts:
    name: str
    rect: Rect
    takes_captions: bool
    keep_out: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleFacts:
    """The resolved visual style as captions need it (filled from ``ResolvedStyle``)."""

    binding: StyleBinding
    profile: str
    width: int
    height: int
    look: str
    case: str
    size_px: int
    line_px: int
    stroke_px: int
    max_chars: int
    max_lines: int
    strip_trailing_period: bool
    highlight_hex: str
    zone: ZoneFacts
    zones: tuple[ZoneFacts, ...]
    min_text_px: int
    entry_pattern: str
    exit_pattern: str
    intensity_ceiling: int
    calm: bool
    #: Longest exit animation of the style (a caption stays readable until it starts).
    exit_seconds: Fraction = Fraction(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class BurnedInSpan:
    """Text the source picture already shows (video OCR; source clock of ``asset_id``).

    ``caption``: video analysis judged it a burned-in caption; any other overlay counts only
    where its text is the words being spoken (a fast-cut Short's captions read as lower thirds).
    """

    asset_id: str
    start: Fraction
    end: Fraction
    text: str = ""
    caption: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionInputs:
    timeline: CaptionTimeline
    transcripts: tuple[TranscriptFacts, ...]
    style: StyleFacts
    subjects: tuple[SubjectBox, ...] = ()
    covered: tuple[CoveredSpan, ...] = ()
    burned_in: tuple[BurnedInSpan, ...] = ()
    #: Where a full-screen graphic fills the picture (timeline seconds): no caption is drawn over
    #: it (the card carries the facts; a Short's caption band runs through its text).
    full_screen: tuple[tuple[Fraction, Fraction], ...] = ()

    def transcript(self, asset_id: str) -> TranscriptFacts | None:
        return next((t for t in self.transcripts if t.asset_id == asset_id), None)
