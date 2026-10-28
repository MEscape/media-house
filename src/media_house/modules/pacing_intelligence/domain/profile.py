"""Pacing profiles, strategies and the run configuration.

A profile is DATA. It sets what pace, pause length, cut rate and hold time suit a format; the
engine is the same for all nine. A strategy then scales the profile's tolerances (``concise``
tolerates less delay, ``cinematic`` more) so the five strategies differ in what they recommend,
not by a global speed factor. The default profile, used whenever nothing stronger is known, is the
neutral ``generic``: a specific format is chosen only from the story plan's own format name.
"""

from dataclasses import dataclass, replace
from types import MappingProxyType

from media_house.modules.pacing_intelligence.domain.errors import InvalidPacingConfig
from media_house.modules.pacing_intelligence.domain.values import Strategy

DEFAULT_PROFILE = "generic"
#: The story format names that map to a pacing profile. Anything else gets the neutral profile.
STORY_FORMAT_PROFILE: dict[str, str] = {
    "top_n": "top_10_ranking",
    "review": "product_review",
    "product_introduction": "product_review",
    "unboxing": "unboxing",
    "talking_head": "talking_head",
    "tutorial": "tutorial",
    "interview": "interview_podcast",
    "podcast": "interview_podcast",
    "documentary": "documentary",
    "short": "shorts_reels",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingProfile:
    name: str
    version: int = 1
    description: str = ""
    # --- speech (words per minute of speech-active time) ---
    wpm_low: float = 120.0
    wpm_high: float = 175.0
    #: Speech is never recommended faster than this, whatever the target length.
    wpm_cap: float = 200.0
    #: A gap between words at least this long (seconds) is a pause, not a breath between words.
    min_gap: float = 0.25
    # --- pauses (seconds) ---
    natural_pause: float = 0.8
    dead_air: float = 1.6
    keep_pause: float = 0.4
    #: A pause this long before emphasis, after a question or before a payoff is deliberate.
    meaningful_pause: float = 0.5
    #: Share of a gap that must be confirmed silent for it to count as dead air.
    silence_confirm: float = 0.8
    # --- information ---
    load_high: float = 0.65
    density_low: float = 0.3
    #: Extra seconds suggested per second of speech, per unit of load above ``load_high``.
    processing_factor: float = 0.25
    #: Share of a passage that may repeat earlier material before it is called repetitive.
    repetition_tolerance: float = 0.5
    # --- picture ---
    max_cut_rate: float = 14.0  # cuts per minute
    static_shot_seconds: float = 10.0
    demo_min_seconds: float = 4.0
    # --- narrative ---
    #: Seconds by which the first main point should have arrived; ``None`` = no deadline.
    premise_deadline: float | None = 45.0
    setup_max_share: float = 0.35
    reveal_hold: float = 0.8
    section_breath: float = 0.3
    # --- rhythm ---
    prefer_beats: bool = False
    beat_tolerance: float = 0.15
    min_rhythm_strength: float = 0.4
    # --- attention ---
    inactivity_seconds: float = 12.0
    attention_bin: float = 5.0

    def problems(self) -> list[str]:
        out: list[str] = []
        if not 0 < self.wpm_low < self.wpm_high <= self.wpm_cap:
            out.append("need 0 < wpm_low < wpm_high <= wpm_cap")
        if not 0 < self.keep_pause <= self.dead_air or self.natural_pause > self.dead_air:
            out.append("need keep_pause <= dead_air and natural_pause <= dead_air")
        for label, value in (
            ("silence_confirm", self.silence_confirm),
            ("load_high", self.load_high),
            ("density_low", self.density_low),
            ("repetition_tolerance", self.repetition_tolerance),
            ("setup_max_share", self.setup_max_share),
            ("min_rhythm_strength", self.min_rhythm_strength),
        ):
            if not 0.0 <= value <= 1.0:
                out.append(f"{label} must be within [0, 1]")
        for label, value in (
            ("min_gap", self.min_gap),
            ("natural_pause", self.natural_pause),
            ("meaningful_pause", self.meaningful_pause),
            ("processing_factor", self.processing_factor),
            ("max_cut_rate", self.max_cut_rate),
            ("static_shot_seconds", self.static_shot_seconds),
            ("demo_min_seconds", self.demo_min_seconds),
            ("reveal_hold", self.reveal_hold),
            ("section_breath", self.section_breath),
            ("beat_tolerance", self.beat_tolerance),
            ("inactivity_seconds", self.inactivity_seconds),
            ("attention_bin", self.attention_bin),
        ):
            if value < 0:
                out.append(f"{label} must not be negative")
        if self.premise_deadline is not None and self.premise_deadline <= 0:
            out.append("premise_deadline must be positive")
        return out

    def fingerprint(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


_BASE = PacingProfile(name=DEFAULT_PROFILE, description="Neutral defaults for any recording.")


def _p(name: str, description: str, **changes: object) -> PacingProfile:
    return replace(_BASE, name=name, description=description, **changes)  # type: ignore[arg-type]


_PROFILES = (
    _BASE,
    _p(
        "top_10_ranking",
        "Forward momentum; each entry varies; reveals get room.",
        wpm_low=140.0,
        wpm_high=190.0,
        wpm_cap=215.0,
        dead_air=1.2,
        max_cut_rate=20.0,
        static_shot_seconds=8.0,
        premise_deadline=20.0,
        reveal_hold=1.2,
        section_breath=0.4,
        inactivity_seconds=9.0,
    ),
    _p(
        "product_review",
        "Concise commentary with time for demonstrations, comparisons and evidence.",
        wpm_low=130.0,
        wpm_high=180.0,
        demo_min_seconds=5.0,
        premise_deadline=30.0,
        max_cut_rate=16.0,
    ),
    _p(
        "unboxing",
        "Anticipation, inspection, reveals and reactions are the content.",
        wpm_low=110.0,
        wpm_high=170.0,
        natural_pause=1.2,
        dead_air=2.5,
        keep_pause=0.7,
        reveal_hold=1.5,
        static_shot_seconds=14.0,
        max_cut_rate=12.0,
        demo_min_seconds=3.0,
        premise_deadline=40.0,
    ),
    _p(
        "talking_head",
        "One person to camera: no dead air, natural delivery, a static picture is normal.",
        wpm_low=135.0,
        wpm_high=185.0,
        wpm_cap=205.0,
        natural_pause=0.7,
        dead_air=1.2,
        keep_pause=0.3,
        static_shot_seconds=18.0,
        inactivity_seconds=15.0,
        max_cut_rate=12.0,
        premise_deadline=20.0,
    ),
    _p(
        "tutorial",
        "Viewers follow each step and see its result; dense steps get processing time.",
        wpm_low=100.0,
        wpm_high=155.0,
        wpm_cap=175.0,
        natural_pause=1.0,
        dead_air=2.5,
        keep_pause=0.5,
        demo_min_seconds=6.0,
        load_high=0.55,
        processing_factor=0.4,
        static_shot_seconds=20.0,
        max_cut_rate=8.0,
        premise_deadline=40.0,
        inactivity_seconds=18.0,
    ),
    _p(
        "interview_podcast",
        "Conversational rhythm, reactions and speaker turns are preserved.",
        wpm_low=125.0,
        wpm_high=180.0,
        natural_pause=1.2,
        dead_air=3.0,
        keep_pause=0.6,
        static_shot_seconds=30.0,
        inactivity_seconds=25.0,
        max_cut_rate=6.0,
        premise_deadline=60.0,
    ),
    _p(
        "documentary",
        "Exposition, atmosphere, tension and payoff on a deliberate pace.",
        wpm_low=110.0,
        wpm_high=165.0,
        natural_pause=1.3,
        dead_air=3.0,
        keep_pause=0.8,
        static_shot_seconds=14.0,
        max_cut_rate=10.0,
        reveal_hold=1.8,
        section_breath=0.6,
        premise_deadline=60.0,
        inactivity_seconds=16.0,
    ),
    _p(
        "shorts_reels",
        "The premise lands at once; clarity survives the speed.",
        wpm_low=150.0,
        wpm_high=210.0,
        wpm_cap=230.0,
        natural_pause=0.4,
        dead_air=0.7,
        keep_pause=0.2,
        max_cut_rate=30.0,
        static_shot_seconds=4.0,
        premise_deadline=4.0,
        reveal_hold=0.5,
        section_breath=0.15,
        inactivity_seconds=5.0,
        attention_bin=2.0,
        demo_min_seconds=2.5,
    ),
    _p(
        "cinematic_montage",
        "Visual rhythm, movement and music; speech is not the spine.",
        wpm_low=100.0,
        wpm_high=170.0,
        natural_pause=2.0,
        dead_air=5.0,
        keep_pause=1.2,
        prefer_beats=True,
        beat_tolerance=0.12,
        max_cut_rate=40.0,
        static_shot_seconds=6.0,
        premise_deadline=None,
        inactivity_seconds=10.0,
        reveal_hold=1.5,
    ),
)

PROFILES = MappingProxyType({p.name: p for p in _PROFILES})


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyAdjust:
    """How a strategy scales a profile's tolerances (1 = as the profile says)."""

    #: Pause tolerances (natural, dead air). Below 1 tightens, above 1 allows more silence.
    pause: float = 1.0
    #: How much processing time a dense passage is given.
    processing: float = 1.0
    #: Repetition tolerance: below 1, more repeats are called repetitive.
    repetition: float = 1.0
    #: Time a picture may stay static or a shot may run.
    static: float = 1.0
    #: Cuts per minute tolerated.
    cut_rate: float = 1.0
    #: Hold time of reveals, demonstrations and section breaths.
    hold: float = 1.0


STRATEGIES = MappingProxyType(
    {
        Strategy.CONCISE: StrategyAdjust(
            pause=0.7, processing=0.6, repetition=0.7, static=0.8, cut_rate=1.1, hold=0.8
        ),
        Strategy.BALANCED: StrategyAdjust(),
        Strategy.DETAILED: StrategyAdjust(
            pause=1.3, processing=1.4, repetition=1.3, static=1.2, cut_rate=0.9, hold=1.2
        ),
        Strategy.HIGH_ENERGY: StrategyAdjust(
            pause=0.6, processing=0.7, repetition=0.8, static=0.6, cut_rate=1.4, hold=0.7
        ),
        Strategy.CINEMATIC: StrategyAdjust(
            pause=1.6, processing=1.2, repetition=1.2, static=1.5, cut_rate=0.6, hold=1.5
        ),
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetDuration:
    """A wanted length of the edit in seconds. Every end is optional."""

    minimum: float | None = None
    maximum: float | None = None
    preferred_low: float | None = None
    preferred_high: float | None = None

    def problems(self) -> list[str]:
        values = [self.minimum, self.preferred_low, self.preferred_high, self.maximum]
        known = [v for v in values if v is not None]
        out = ["a target length must be positive" for v in known if v <= 0][:1]
        if known != sorted(known):
            out.append("need minimum <= preferred_low <= preferred_high <= maximum")
        if not known:
            out.append("a target needs at least one end")
        return out


@dataclass(frozen=True, slots=True, kw_only=True)
class PacingConfig:
    #: A profile name, a profile built for this run, or ``None`` = by the story's format, else the
    #: neutral ``generic``.
    profile: str | PacingProfile | None = None
    strategy: Strategy = Strategy.BALANCED
    target: TargetDuration | None = None

    def resolve(self, story_format: str | None = None) -> PacingProfile:
        if isinstance(self.profile, PacingProfile):
            profile = self.profile
        else:
            name = self.profile or STORY_FORMAT_PROFILE.get(story_format or "", DEFAULT_PROFILE)
            found = PROFILES.get(name)
            if found is None:
                raise InvalidPacingConfig(
                    f"Unknown profile {name!r}; known: {', '.join(sorted(PROFILES))}"
                )
            profile = found
        problems = [*profile.problems(), *(self.target.problems() if self.target else [])]
        if problems:
            raise InvalidPacingConfig("; ".join(problems))
        return profile

    def adjust(self) -> StrategyAdjust:
        return STRATEGIES[self.strategy]

    def fingerprint(self, profile: PacingProfile) -> dict[str, object]:
        t = self.target
        return {
            "profile": profile.fingerprint(),
            "strategy": self.strategy.value,
            "target": None
            if t is None
            else [t.minimum, t.preferred_low, t.preferred_high, t.maximum],
        }
