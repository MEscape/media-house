"""Optional outside opinions on three judgements the editor makes with baseline evidence.

Specialised analysis may later answer these better than the editor's baseline rules: ranking
cover footage, assessing continuity at a cut, advising on pacing. The editor asks through these
small callables when one is supplied, and falls back to its own evidence when it is not (or when
the answer is ``None``). An answer is data like any other: shots it names must exist in the video
analysis, ranges are validated, nothing it says is trusted blindly. No hook is required, none is
implemented here, and a plan made without them is complete.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from media_house.modules.editing_intelligence.domain.facts import ShotChoice


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverageQuery:
    request_id: str
    section_id: str
    purpose: str
    concept: str
    asset_id: str
    #: The passage the footage should accompany, on the recording's timeline.
    start: float
    end: float
    #: The candidates the editor already has (alignment's ranking), best first.
    baseline: tuple[ShotChoice, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CutQuery:
    asset_id: str
    #: Where the picture leaves one moment and resumes at another, on the recording's timeline.
    leaves_at: float
    resumes_at: float


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuityVerdict:
    risk: float | None
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionPace:
    section_id: str
    label: str
    seconds: float
    words: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PaceAdvice:
    section_id: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Hooks:
    #: Replaces the candidate list for a place that needs cover (best first); ``None`` = no opinion.
    coverage: Callable[[CoverageQuery], Sequence[ShotChoice] | None] | None = None
    continuity: Callable[[CutQuery], ContinuityVerdict | None] | None = None
    pacing: Callable[[Sequence[SectionPace]], Sequence[PaceAdvice]] | None = None
