"""Format profiles: what a kind of video is expected to do, as data.

A profile changes the questions asked of the recording (which functions a story of this kind
needs, how early the premise must land, how long it should run, whether sections may move), never
the machinery. A new format is a new entry here, not a new branch elsewhere. A profile never
overrides what the recording contains: an expectation it does not meet becomes an issue in the
plan, not invented material.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from media_house.modules.story_architecture.domain.errors import InvalidStoryConfig
from media_house.modules.story_architecture.domain.values import BeatFunction

F = BeatFunction


class ReorderPolicy(StrEnum):
    #: Keep the recorded order of sections (rankings, interviews: the order is the content).
    PRESERVE = "preserve"
    #: Move a section only to satisfy a hard dependency that points forward in the recording.
    DEPENDENCY = "dependency"


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Design choices on 0-1 scores; none of them is a trained or calibrated value."""

    essential_importance: float = 0.6
    optional_importance: float = 0.25
    redundant_removal: float = 0.6
    standalone_min: float = 0.5
    hook_min: float = 0.35
    dense: float = 0.7
    slow: float = 0.2
    #: A section longer than this many seconds is "long" for pacing purposes.
    long_section_seconds: float = 90.0
    #: This many consecutive sections with one mode is a monotone run.
    monotone_run: int = 4
    #: A total this much (as a share) outside its target is flagged.
    duration_tolerance: float = 0.15
    visual_necessity_strength: float = 0.6

    def problems(self) -> list[str]:
        unit = (
            "essential_importance",
            "optional_importance",
            "redundant_removal",
            "standalone_min",
            "hook_min",
            "dense",
            "slow",
            "visual_necessity_strength",
        )
        out = [f"{n} must be in [0, 1]" for n in unit if not 0.0 <= getattr(self, n) <= 1.0]
        if self.optional_importance >= self.essential_importance:
            out.append("optional_importance must be below essential_importance")
        if self.monotone_run < 2:
            out.append("monotone_run must be at least 2")
        if self.long_section_seconds <= 0:
            out.append("long_section_seconds must be positive")
        return out


@dataclass(frozen=True, slots=True)
class FormatProfile:
    name: str
    version: int
    #: Identifier of the narrative pattern the format follows (reported, never forced).
    pattern: str
    #: What the viewer should understand or do, in the format's own terms.
    intent: str
    #: Functions a story of this kind is expected to contain; a missing one is an issue.
    expected: tuple[BeatFunction, ...]
    reorder: ReorderPolicy
    #: ``(shortest, longest)`` seconds the format aims for, or ``None`` when it has no target.
    target_seconds: tuple[float, float] | None
    #: Delivery rate used only to estimate untimed material.
    words_per_minute: float = 150.0
    #: The premise should be stated within this many seconds of the opening.
    premise_window_seconds: float = 45.0
    max_openings: int = 4

    def problems(self) -> list[str]:
        out: list[str] = []
        if not self.name:
            out.append("a format needs a name")
        if self.words_per_minute <= 0:
            out.append("words_per_minute must be positive")
        if self.premise_window_seconds <= 0:
            out.append("premise_window_seconds must be positive")
        if self.max_openings < 1:
            out.append("max_openings must be at least 1")
        if self.target_seconds is not None:
            low, high = self.target_seconds
            if not 0 < low <= high:
                out.append("target_seconds must be (low, high) with 0 < low <= high")
        return out


def _format(
    name: str,
    pattern: str,
    intent: str,
    expected: tuple[BeatFunction, ...],
    reorder: ReorderPolicy,
    *,
    target: tuple[float, float] | None = None,
    premise_window: float = 45.0,
    max_openings: int = 4,
) -> FormatProfile:
    return FormatProfile(
        name,
        1,
        pattern,
        intent,
        expected,
        reorder,
        target,
        premise_window_seconds=premise_window,
        max_openings=max_openings,
    )


_P = ReorderPolicy.PRESERVE
_D = ReorderPolicy.DEPENDENCY

FORMATS: Mapping[str, FormatProfile] = {
    p.name: p
    for p in (
        _format(
            "generic",
            "premise_and_support",
            "Understand the main point and why it holds.",
            (F.CLAIM,),
            _D,
        ),
        _format(
            "top_n",
            "ranked_reveal",
            "Learn the criteria and see each entry earn its place.",
            (F.SETUP, F.PAYOFF),
            _P,
            premise_window=30.0,
        ),
        _format(
            "product_introduction",
            "need_feature_proof",
            "See what the product is for and what it does.",
            (F.SETUP, F.DEMONSTRATION),
            _D,
        ),
        _format(
            "review",
            "need_evidence_verdict",
            "Decide whether the product fits, with its limits stated.",
            (F.SETUP, F.EVIDENCE, F.CAVEAT, F.PAYOFF),
            _D,
        ),
        _format(
            "unboxing",
            "reveal_sequence",
            "See what is in the box and the first impression.",
            (F.DEMONSTRATION,),
            _P,
        ),
        _format(
            "talking_head",
            "premise_and_support",
            "Follow one speaker's argument to its conclusion.",
            (F.CLAIM, F.PAYOFF),
            _D,
        ),
        _format(
            "tutorial",
            "prerequisites_steps_result",
            "Be able to repeat the procedure and avoid its mistakes.",
            (F.SETUP, F.DEMONSTRATION, F.PAYOFF),
            _P,
        ),
        _format(
            "interview",
            "question_answer",
            "Hear each answer in the context of its question.",
            (F.QUESTION, F.ANSWER),
            _P,
            premise_window=90.0,
        ),
        _format(
            "podcast",
            "conversation",
            "Follow the conversation and take away its main points.",
            (F.QUESTION, F.ANSWER),
            _P,
            premise_window=120.0,
        ),
        _format(
            "documentary",
            "question_evidence_resolution",
            "See a question developed through evidence to a resolution.",
            (F.QUESTION, F.EVIDENCE, F.TURNING_POINT, F.PAYOFF),
            _D,
            premise_window=120.0,
        ),
        _format(
            "short",
            "premise_then_payoff",
            "Get the point and its payoff fast.",
            (F.PAYOFF,),
            _D,
            target=(15.0, 60.0),
            premise_window=5.0,
            max_openings=3,
        ),
    )
}

DEFAULT_FORMAT = "generic"


@dataclass(frozen=True, slots=True)
class StoryConfig:
    #: A registered format name, or a profile built for this run.
    format: str | FormatProfile = DEFAULT_FORMAT
    thresholds: Thresholds = field(default_factory=Thresholds)
    #: Free text the caller knows and the recording cannot say; passed through, never inferred.
    audience: str | None = None

    def resolve(self) -> FormatProfile:
        if isinstance(self.format, FormatProfile):
            profile = self.format
        else:
            found = FORMATS.get(self.format)
            if found is None:
                raise InvalidStoryConfig(
                    f"Unknown format {self.format!r}; known: {', '.join(sorted(FORMATS))}"
                )
            profile = found
        problems = [*profile.problems(), *self.thresholds.problems()]
        if problems:
            raise InvalidStoryConfig("; ".join(problems))
        return profile

    def fingerprint(self) -> dict[str, object]:
        """Everything of the configuration that can change a plan, as JSON-safe data."""
        profile = self.resolve()
        return {
            "format": {
                "name": profile.name,
                "version": profile.version,
                "pattern": profile.pattern,
                "expected": [f.value for f in profile.expected],
                "reorder": profile.reorder.value,
                "target": list(profile.target_seconds) if profile.target_seconds else None,
                "wpm": profile.words_per_minute,
                "premise_window": profile.premise_window_seconds,
                "max_openings": profile.max_openings,
            },
            "thresholds": {
                n: getattr(self.thresholds, n) for n in self.thresholds.__dataclass_fields__
            },
            "audience": self.audience,
        }
