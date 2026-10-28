"""Editing profiles and the run configuration.

A profile is DATA: it changes preferences and constraints (how long a pause may be, whether
fillers go, how much cover footage the style wants, which story functions are never cut), never
the algorithms. A style is a table entry; ``EditConfig`` picks one, optionally sets a target
duration and says whether structural alternatives may be applied.

Hard constraints (never a preference): a story's ordering constraints, key information and its
qualifications, context a kept passage needs, source ranges inside the recording. Soft
preferences: pause length, filler removal, cover appetite, effects density, a soft target
duration. A profile can lower or raise a soft preference; it can never switch a hard constraint
off (only a person's explicit override can, and the plan then says so).
"""

from dataclasses import dataclass, replace
from types import MappingProxyType

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.errors import InvalidEditConfig
from media_house.modules.editing_intelligence.domain.values import DurationMode

DEFAULT_PROFILE = "generic"


@dataclass(frozen=True, slots=True, kw_only=True)
class EditingProfile:
    name: str
    version: int = 1
    description: str = ""
    # --- pauses (seconds) ---
    #: A silent gap inside kept speech longer than this is shortened.
    pause_max: float = 0.9
    #: What remains of a shortened gap.
    pause_keep: float = 0.4
    #: Silence ratio from which a gap counts as confirmed silence.
    pause_confirm: float = 0.8
    #: Emphasis score of the next word from which a pause is treated as deliberate.
    hold_before_emphasis: float = 0.7
    # --- content ---
    cut_fillers: bool = True
    cut_redundant: bool = True
    trim_greeting: bool = False
    #: Cut score from which an unprotected passage is proposed for removal, and the margin by which
    #: it must beat the keep score.
    cut_threshold: float = 0.65
    cut_margin: float = 0.25
    #: Story beat functions whose passages are never proposed for removal.
    protected_functions: frozenset[str] = frozenset({"hook", "payoff", "caveat"})
    # --- boundaries (seconds) ---
    lead_in: float = 0.12
    tail_out: float = 0.2
    #: Boundary confidence below which a cut is only a suggestion.
    min_auto_confidence: float = 0.6
    #: Hold past the last word of a payoff / turning point, when the recording has the room.
    payoff_hold: float = 0.0
    # --- visual coverage ---
    cover_appetite: float = 0.5
    cover_threshold: float = 0.45
    #: Seconds of unchanged picture before repetition starts to count (scaled by importance).
    static_seconds: float = 8.0
    min_cover_seconds: float = 1.0
    max_cover_seconds: float = 6.0
    #: A cut inside one continuous shot is part of the style (talking head, short form).
    jump_cuts_ok: bool = True
    # --- audio and effects ---
    bridge_audio: bool = True
    audio_fade: float = 0.012
    #: Most requests for later stages (transitions, cues, reframes) per minute of edit.
    effects_per_minute: float = 4.0
    section_transitions: bool = False
    reveal_cue: bool = False
    emphasis_reframe: bool = False
    continuity_review: float = 0.7
    #: Duration mode when the caller sets none and the story names a target length.
    story_target_mode: DurationMode = DurationMode.SOFT

    def problems(self) -> list[str]:
        out: list[str] = []
        for label, value in (
            ("pause_confirm", self.pause_confirm),
            ("hold_before_emphasis", self.hold_before_emphasis),
            ("cut_threshold", self.cut_threshold),
            ("cut_margin", self.cut_margin),
            ("min_auto_confidence", self.min_auto_confidence),
            ("cover_appetite", self.cover_appetite),
            ("cover_threshold", self.cover_threshold),
            ("continuity_review", self.continuity_review),
        ):
            if not 0.0 <= value <= 1.0:
                out.append(f"{label} must be within [0, 1]")
        for label, value in (
            ("pause_max", self.pause_max),
            ("pause_keep", self.pause_keep),
            ("lead_in", self.lead_in),
            ("tail_out", self.tail_out),
            ("payoff_hold", self.payoff_hold),
            ("audio_fade", self.audio_fade),
            ("static_seconds", self.static_seconds),
            ("min_cover_seconds", self.min_cover_seconds),
            ("max_cover_seconds", self.max_cover_seconds),
            ("effects_per_minute", self.effects_per_minute),
        ):
            if value < 0:
                out.append(f"{label} must not be negative")
        if self.pause_keep > self.pause_max:
            out.append("pause_keep must not exceed pause_max")
        if self.min_cover_seconds > self.max_cover_seconds:
            out.append("min_cover_seconds must not exceed max_cover_seconds")
        return out

    def fingerprint(self) -> dict[str, object]:
        return {
            name: sorted(value) if isinstance(value, frozenset) else getattr(value, "value", value)
            for name in self.__dataclass_fields__
            for value in [getattr(self, name)]
        }


_BASE = EditingProfile(name=DEFAULT_PROFILE, description="Neutral defaults for any recording.")


def _profile(name: str, description: str, **changes: object) -> EditingProfile:
    return replace(_BASE, name=name, description=description, **changes)  # type: ignore[arg-type]


_PROFILES: tuple[EditingProfile, ...] = (
    _BASE,
    _profile(
        "top_n",
        "Ranking: entries stay in order, each reveal gets room, the last one is protected.",
        protected_functions=frozenset({"hook", "payoff", "caveat", "turning_point", "claim"}),
        pause_max=1.2,
        pause_keep=0.5,
        cover_appetite=0.7,
        reveal_cue=True,
        section_transitions=True,
        payoff_hold=0.4,
    ),
    _profile(
        "product_introduction",
        "A product shown and explained: claims need their demonstration.",
        protected_functions=frozenset({"hook", "payoff", "caveat", "demonstration", "claim"}),
        cover_appetite=0.6,
    ),
    _profile(
        "review",
        "Claims, evidence, pros, cons and a verdict stay balanced and in context.",
        protected_functions=frozenset(
            {"hook", "payoff", "caveat", "demonstration", "claim", "evidence", "counterpoint"}
        ),
        cover_appetite=0.6,
        trim_greeting=True,
    ),
    _profile(
        "unboxing",
        "The reveal, the inspection and the reactions are the content: little is trimmed.",
        protected_functions=frozenset({"hook", "payoff", "caveat", "demonstration", "example"}),
        pause_max=1.6,
        pause_keep=0.7,
        cut_fillers=False,
        cover_appetite=0.4,
        reveal_cue=True,
        payoff_hold=0.5,
    ),
    _profile(
        "talking_head",
        "One person to camera: dead air and fillers go, natural delivery stays.",
        pause_max=0.7,
        pause_keep=0.3,
        trim_greeting=True,
        cover_appetite=0.5,
        jump_cuts_ok=True,
    ),
    _profile(
        "tutorial",
        "Prerequisites, steps in order, demonstrations and the check that it worked.",
        protected_functions=frozenset(
            {
                "hook",
                "payoff",
                "caveat",
                "setup",
                "context",
                "demonstration",
                "explanation",
                "evidence",
            }
        ),
        pause_max=1.2,
        pause_keep=0.5,
        cut_redundant=False,
        cover_appetite=0.7,
        static_seconds=12.0,
    ),
    _profile(
        "interview",
        "A conversation: context, intent and reactions are kept; speech is rarely trimmed.",
        protected_functions=frozenset({"hook", "payoff", "caveat", "question", "answer"}),
        pause_max=1.6,
        pause_keep=0.8,
        cut_fillers=False,
        cut_redundant=False,
        cover_appetite=0.3,
        jump_cuts_ok=False,
    ),
    _profile(
        "podcast",
        "Long form conversation: like an interview, with the same restraint.",
        protected_functions=frozenset({"hook", "payoff", "caveat", "question", "answer"}),
        pause_max=1.8,
        pause_keep=0.9,
        cut_fillers=False,
        cut_redundant=False,
        cover_appetite=0.2,
        jump_cuts_ok=False,
    ),
    _profile(
        "documentary",
        "Evidence, coherence and atmosphere: deliberate pace, plenty of cover.",
        protected_functions=frozenset(
            {"hook", "payoff", "caveat", "evidence", "context", "turning_point"}
        ),
        pause_max=2.0,
        pause_keep=1.0,
        cut_fillers=False,
        cover_appetite=0.8,
        jump_cuts_ok=False,
        static_seconds=10.0,
        section_transitions=True,
    ),
    _profile(
        "short",
        "Vertical short form: the premise lands fast, the payoff is delivered, nothing idles.",
        pause_max=0.4,
        pause_keep=0.2,
        cut_threshold=0.55,
        cut_margin=0.2,
        trim_greeting=True,
        cover_appetite=0.6,
        static_seconds=4.0,
        max_cover_seconds=3.0,
        emphasis_reframe=True,
        effects_per_minute=8.0,
    ),
    _profile(
        "montage",
        "Cinematic montage: rhythm, movement and atmosphere; speech is not the spine.",
        protected_functions=frozenset({"hook", "payoff", "turning_point"}),
        pause_max=3.0,
        pause_keep=1.5,
        cut_fillers=False,
        cut_redundant=False,
        cover_appetite=1.0,
        jump_cuts_ok=False,
        reveal_cue=True,
        section_transitions=True,
        static_seconds=3.0,
    ),
)

PROFILES = MappingProxyType({p.name: p for p in _PROFILES})


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetDuration:
    """A wanted length in seconds (either end may be open)."""

    low: float | None = None
    high: float | None = None
    mode: DurationMode = DurationMode.SOFT

    def problems(self) -> list[str]:
        out: list[str] = []
        for label, value in (("low", self.low), ("high", self.high)):
            if value is not None and value <= 0:
                out.append(f"target {label} must be positive")
        if self.low is not None and self.high is not None and self.low > self.high:
            out.append("target low must not exceed high")
        if self.mode is not DurationMode.NONE and self.low is None and self.high is None:
            out.append("a target duration needs a low or a high end")
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class EditConfig:
    #: A profile name, a profile built for this run, or ``None`` = the one named like the story's
    #: format (else ``generic``).
    profile: str | EditingProfile | None = None
    #: ``None`` = the story's own target length as a soft preference (none if the story has none).
    target: TargetDuration | None = None
    #: Apply structural alternatives (dropping a supporting section to meet a target). Off: they
    #: are only proposed.
    allow_structural: bool = False
    #: Play the story's recommended opening first when it is not the natural start.
    use_recommended_opening: bool = True
    #: The timeline's frame rate; ``None`` = the most common constant rate of the recordings.
    timeline_frame_rate: Rational | None = None

    def resolve(self, story_format: str) -> EditingProfile:
        if isinstance(self.profile, EditingProfile):
            profile = self.profile
        else:
            name = self.profile or (story_format if story_format in PROFILES else DEFAULT_PROFILE)
            found = PROFILES.get(name)
            if found is None:
                raise InvalidEditConfig(
                    f"Unknown profile {name!r}; known: {', '.join(sorted(PROFILES))}"
                )
            profile = found
        problems = [*profile.problems(), *(self.target.problems() if self.target else [])]
        if problems:
            raise InvalidEditConfig("; ".join(problems))
        return profile

    def fingerprint(self, profile: EditingProfile) -> dict[str, object]:
        """Everything of the configuration that can change a plan, as JSON-safe data."""
        rate = self.timeline_frame_rate
        return {
            "profile": profile.fingerprint(),
            "target": None
            if self.target is None
            else [self.target.low, self.target.high, self.target.mode.value],
            "allow_structural": self.allow_structural,
            "recommended_opening": self.use_recommended_opening,
            "timeline_rate": None if rate is None else str(rate),
        }
