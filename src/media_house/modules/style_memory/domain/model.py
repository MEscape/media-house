"""The editorial style as data: rules with provenance, a versioned profile, events, exemplars.

Everything is a frozen dataclass tree of tuples (the strict JSON codec of ``core.domain`` reads and
writes it). Nothing here reads a clock or a file: time is passed in.
"""

from dataclasses import dataclass
from datetime import datetime

from media_house.modules.style_memory.domain.values import (
    Category,
    EventKind,
    Origin,
    Scope,
    Status,
    Strength,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a rule comes from: a video, a file, a learned event or a person's decision."""

    source: str
    quote: str = ""


@dataclass(frozen=True, slots=True)
class StyleRule:
    rule_id: str
    text: str
    category: Category
    strength: Strength
    scope: Scope
    #: The format profile, client or project the scope names ("" for a global rule).
    scope_key: str
    origin: Origin
    status: Status
    confidence: float
    evidence: tuple[Provenance, ...]
    #: A learned rule is a mere candidate until a person approved it.
    approved: bool
    #: A locked rule is changed only after it is unlocked: neither learning nor edits touch it.
    locked: bool
    #: Groups events that say the same thing (the learning key); "" for a hand-written rule.
    topic: str
    created_at: datetime
    updated_at: datetime
    note: str = ""

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise InvariantViolation("A rule needs a text", user_message="Die Regel ist leer.")
        if not 0.0 <= self.confidence <= 1.0:
            raise InvariantViolation(f"confidence {self.confidence} is outside 0..1")
        if self.scope is not Scope.GLOBAL and not self.scope_key:
            raise InvariantViolation(f"A {self.scope} rule needs a scope key")
        if (
            self.origin is Origin.LEARNED
            and self.strength is Strength.MANDATORY
            and not self.approved
        ):
            raise InvariantViolation(
                "A learned rule becomes mandatory only with a person's approval",
                user_message="Gelernte Regeln werden erst nach deiner Freigabe verbindlich.",
            )
        if self.status is Status.ACTIVE and self.origin is Origin.LEARNED and not self.approved:
            raise InvariantViolation("An unapproved learned rule cannot be active")

    @property
    def applies(self) -> bool:
        return self.status is Status.ACTIVE


@dataclass(frozen=True, slots=True)
class HookPattern:
    name: str
    description: str
    evidence: tuple[Provenance, ...]


@dataclass(frozen=True, slots=True)
class PacingNorm:
    """Measured norms of one format: what a typical video of it looks like."""

    format: str
    cuts_per_minute_min: float
    cuts_per_minute_max: float
    #: Most overlays (graphic cards, stickers, pop-ins) per minute before it gets busy.
    overlays_per_minute_max: float
    words_per_minute: float
    evidence: tuple[Provenance, ...] = ()


@dataclass(frozen=True, slots=True)
class CaptionPolicy:
    burned_in: bool
    note: str
    evidence: tuple[Provenance, ...] = ()


@dataclass(frozen=True, slots=True)
class FormatProfile:
    """What a kind of video (ranking, tour, short ...) is expected to be."""

    name: str
    title: str
    structure: tuple[str, ...]
    audience_expects: tuple[str, ...]
    target_minutes_min: float
    target_minutes_max: float
    evidence: tuple[Provenance, ...] = ()


@dataclass(frozen=True, slots=True)
class StyleProfile:
    """The channel's editorial style (voice, structure, pacing). The look (colour, type, graphic
    language) stays with ``visual_style_engine``: ``visual_identity`` only names that source."""

    version: int
    channel: str
    language: str
    audience: str
    #: The tone every generated text is written in (German, informal "du").
    address: str
    greeting: str
    catchphrases: tuple[str, ...]
    hook_patterns: tuple[HookPattern, ...]
    pacing: tuple[PacingNorm, ...]
    allowed_transitions: tuple[str, ...]
    forbidden_transitions: tuple[str, ...]
    #: Most overlays on screen at once, per profile name ("sponsored", "default").
    overlay_limits: tuple[tuple[str, int], ...]
    caption_policy: CaptionPolicy
    recurring_layouts: tuple[str, ...]
    never: tuple[str, ...]
    formats: tuple[FormatProfile, ...]
    visual_identity: str
    updated_at: datetime
    provenance: tuple[Provenance, ...] = ()

    def format(self, name: str) -> FormatProfile | None:
        return next((f for f in self.formats if f.name == name), None)

    def pacing_of(self, name: str) -> PacingNorm | None:
        return next((p for p in self.pacing if p.format == name), None)

    def overlay_limit(self, profile: str) -> int:
        limits = dict(self.overlay_limits)
        return limits.get(profile, limits.get("default", 0))


@dataclass(frozen=True, slots=True)
class PreferenceEvent:
    """One thing the creator did that says something about his taste. Append-only."""

    event_id: str
    kind: EventKind
    at: datetime
    project_id: str
    scene_id: str
    #: The rule the event was about, when it was (an override of a mandatory rule).
    rule_id: str
    format: str
    client: str
    category: Category
    #: A short normalised statement of the preference ("intro_flows_from_context"), or "".
    signal: str
    #: The person's words: an instruction, a reason, a comment.
    note: str
    before: str = ""
    after: str = ""
    #: 0 = no rating, else 1 to 5.
    rating: int = 0
    scope_hint: Scope = Scope.PROJECT

    def __post_init__(self) -> None:
        if not 0 <= self.rating <= 5:
            raise InvariantViolation(f"rating {self.rating} is outside 0..5")


@dataclass(frozen=True, slots=True)
class Annotation:
    """Why a passage of an exemplar is good (what a few-shot reader should copy)."""

    quote: str
    why: str


@dataclass(frozen=True, slots=True)
class Exemplar:
    exemplar_id: str
    title: str
    format: str
    kind: str
    body: str
    annotations: tuple[Annotation, ...]
    tags: tuple[str, ...]
    approved_at: datetime
    source: str = ""


@dataclass(frozen=True, slots=True)
class PacingStats:
    cuts_per_minute: float
    overlays_per_minute: float
    words_per_minute: float
    average_shot_seconds: float


@dataclass(frozen=True, slots=True)
class ReferenceCard:
    """Another creator's style, measured and named in raw terms (never copied)."""

    card_id: str
    name: str
    source: str
    stats: PacingStats
    #: What the reference does, in its own words ("glitch transitions", "jump cuts").
    techniques: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StyleMemoryDocument:
    """Everything exported and imported as one file."""

    profile: StyleProfile
    rules: tuple[StyleRule, ...]
    events: tuple[PreferenceEvent, ...]
    exemplars: tuple[Exemplar, ...]
    reference_cards: tuple[ReferenceCard, ...] = ()
