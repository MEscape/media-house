"""What one analysis run shares between its steps: the evidence, the profile and fast lookups.

Nothing here decides anything. Times stay float seconds on each recording's own timeline; the
helpers that make stored spans (``span``) convert them to exact ``Rational`` once, at the edge.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.pacing_intelligence.domain.facts import (
    AssetEvidence,
    BeatFact,
    SectionFact,
    ShotFact,
    StoryFacts,
    WordFact,
)
from media_house.modules.pacing_intelligence.domain.model import TimeSpan
from media_house.modules.pacing_intelligence.domain.profile import PacingProfile, StrategyAdjust
from media_house.modules.pacing_intelligence.domain.values import Precision, Strategy

_MICRO = 1_000_000


def exact(seconds: float) -> Rational:
    """A float second count as the nearest exact fraction (microsecond-accurate; never negative)."""
    value = Fraction(max(0.0, seconds)).limit_denominator(_MICRO)
    return Rational(value.numerator, value.denominator)


@dataclass(slots=True)
class AssetIndex:
    """One recording's words and shots, searchable by time."""

    evidence: AssetEvidence
    words: Sequence[WordFact] = field(default_factory=tuple)
    shots: Sequence[ShotFact] = field(default_factory=tuple)
    _word_starts: list[float] = field(default_factory=list)
    _word_ends: list[float] = field(default_factory=list)
    _shot_starts: list[float] = field(default_factory=list)
    _word_by_index: dict[int, WordFact] = field(default_factory=dict)

    def __post_init__(self) -> None:
        audio, video = self.evidence.audio, self.evidence.video
        self.words = audio.words if audio is not None else ()
        self.shots = video.shots if video is not None else ()
        self._word_starts = [w.start for w in self.words]
        self._word_ends = [w.end for w in self.words]
        self._shot_starts = [s.start for s in self.shots]
        self._word_by_index = {w.index: w for w in reversed(self.words)}  # first one wins

    def words_in(self, start: float, end: float) -> list[WordFact]:
        """Words whose middle lies in ``[start, end)``."""
        out: list[WordFact] = []
        for i in range(bisect_left(self._word_ends, start), len(self.words)):
            word = self.words[i]
            if word.start > end:
                break
            if start <= (word.start + word.end) / 2 < end:
                out.append(word)
        return out

    def shots_overlapping(self, start: float, end: float) -> list[ShotFact]:
        first = max(0, bisect_right(self._shot_starts, start) - 1)
        out: list[ShotFact] = []
        for i in range(first, len(self.shots)):
            shot = self.shots[i]
            if shot.start >= end:
                break
            if shot.end > start:
                out.append(shot)
        return out

    def cuts_in(self, start: float, end: float) -> list[float]:
        """Shot boundaries strictly inside the window (the first shot's start is not a cut)."""
        lo, hi = bisect_right(self._shot_starts, start), bisect_left(self._shot_starts, end)
        return [s.start for s in self.shots[lo:hi] if s.index > 0]

    def last_end_by(self, time: float) -> float | None:
        """The latest word end at or before ``time``."""
        at = bisect_right(self._word_ends, time)
        return self._word_ends[at - 1] if at else None

    def first_word_from(self, time: float) -> WordFact | None:
        at = bisect_left(self._word_starts, time)
        return self.words[at] if at < len(self.words) else None

    def word(self, number: int) -> WordFact | None:
        return self._word_by_index.get(number)


type _Spans = tuple[list[float], float, list[tuple[float, float, SectionFact]]]


@dataclass(slots=True)
class Context:
    profile: PacingProfile
    adjust: StrategyAdjust
    strategy: Strategy
    assets: Mapping[str, AssetEvidence]
    story: StoryFacts | None
    asset_order: tuple[str, ...]
    index: dict[str, AssetIndex] = field(default_factory=dict)
    _section_of_unit: dict[tuple[str, str], str] = field(default_factory=dict)
    _beats_by_asset: dict[str, list[BeatFact]] = field(default_factory=dict)
    #: Per recording: section source intervals sorted by start, with the longest length.
    _section_spans: dict[str, _Spans] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {a: AssetIndex(e) for a, e in self.assets.items()}
        if self.story is None:
            return
        for section in self.story.sections:
            for ref in section.sources:
                for unit in ref.unit_ids:
                    self._section_of_unit.setdefault((ref.asset_id, unit), section.section_id)
        for beat in self.story.beats:
            self._beats_by_asset.setdefault(beat.source.asset_id, []).append(beat)
        grouped: dict[str, list[tuple[float, float, SectionFact]]] = {}
        for section in self.story.sections:
            for ref in section.sources:
                if ref.start is not None and ref.end is not None and ref.end > ref.start:
                    grouped.setdefault(ref.asset_id, []).append((ref.start, ref.end, section))
        for asset_id, items in grouped.items():
            items.sort(key=lambda item: (item[0], item[1]))
            self._section_spans[asset_id] = (
                [i[0] for i in items],
                max(i[1] - i[0] for i in items),
                items,
            )

    # --- scaled tolerances -------------------------------------------------------------------
    @property
    def natural_pause(self) -> float:
        return self.profile.natural_pause * self.adjust.pause

    @property
    def dead_air(self) -> float:
        return self.profile.dead_air * self.adjust.pause

    @property
    def keep_pause(self) -> float:
        return self.profile.keep_pause * self.adjust.pause

    @property
    def static_seconds(self) -> float:
        return self.profile.static_shot_seconds * self.adjust.static

    @property
    def cut_rate(self) -> float:
        return self.profile.max_cut_rate * self.adjust.cut_rate

    # --- story lookups -----------------------------------------------------------------------
    def section_of_units(self, asset_id: str, unit_ids: Sequence[str]) -> SectionFact | None:
        if self.story is None:
            return None
        for unit in unit_ids:
            found = self._section_of_unit.get((asset_id, unit))
            if found is not None:
                return self.story.section(found)
        return None

    def section_at(self, asset_id: str, start: float, end: float) -> SectionFact | None:
        """The section whose source passages cover most of ``[start, end)`` (by time)."""
        spans = self._section_spans.get(asset_id)
        if not spans:
            return None
        starts, longest, items = spans
        covered: dict[str, float] = {}
        winner: SectionFact | None = None
        best = 0.0
        for i in range(max(0, bisect_left(starts, start - longest)), len(items)):
            lo, hi, section = items[i]
            if lo >= end:
                break
            overlap = min(end, hi) - max(start, lo)
            if overlap <= 0:
                continue
            covered[section.section_id] = covered.get(section.section_id, 0.0) + overlap
            if covered[section.section_id] > best:
                best, winner = covered[section.section_id], section
        return winner

    def beat_at(self, asset_id: str, start: float, end: float) -> BeatFact | None:
        best: tuple[float, BeatFact] | None = None
        for beat in self._beats_by_asset.get(asset_id, ()):
            ref = beat.source
            if ref.start is None or ref.end is None:
                continue
            covered = max(0.0, min(end, ref.end) - max(start, ref.start))
            if covered > 0 and (best is None or covered > best[0]):
                best = (covered, beat)
        return None if best is None else best[1]

    # --- stored spans ------------------------------------------------------------------------
    def span(
        self,
        asset_id: str,
        start: float,
        end: float,
        precision: Precision,
        *,
        shot: ShotFact | None = None,
    ) -> TimeSpan:
        video = self.assets[asset_id].video if asset_id in self.assets else None
        rate = video.frame_rate if video is not None else None
        if shot is not None:
            return TimeSpan(
                asset_id=asset_id,
                start=exact(start),
                end=exact(max(start, end)),
                precision=Precision.FRAME_EXACT,
                start_frame=shot.start_frame,
                end_frame=shot.end_frame,
                frame_rate=rate,
            )
        return TimeSpan(
            asset_id=asset_id,
            start=exact(start),
            end=exact(max(start, end)),
            precision=precision,
        )

    def word_precision(self, asset_id: str) -> Precision:
        audio = self.assets[asset_id].audio if asset_id in self.assets else None
        if audio is None:
            return Precision.ESTIMATED
        return Precision.WORD_ALIGNED if audio.word_level else Precision.SEGMENT_ALIGNED


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def is_finite_range(start: float | None, end: float | None) -> bool:
    return start is not None and end is not None and end > start
