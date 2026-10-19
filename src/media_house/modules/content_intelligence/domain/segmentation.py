"""Where the subject changes: depth-score segmentation with cue evidence, at three granularities.

The similarity of the sentences before and after every gap forms a curve. A gap is a boundary
candidate when it is a deep valley in that curve (TextTiling). Discourse markers, a long pause,
a speaker change and a scene cut each ADD evidence; none is required, and a visual cut alone can
never make a boundary because a topic may span scenes and a scene may hold several topics.
Boundaries chosen at a stricter level are always a subset of the finer level, so segments nest
into topics and topics into sections.
"""

import itertools
import statistics
from dataclasses import dataclass

from media_house.modules.content_intelligence.domain.config import Thresholds
from media_house.modules.content_intelligence.domain.context import VisualContext
from media_house.modules.content_intelligence.domain.lexicon import Lexicon
from media_house.modules.content_intelligence.domain.model import Boundary, Evidence, excerpt
from media_house.modules.content_intelligence.domain.similarity import SimilarityProvider
from media_house.modules.content_intelligence.domain.units import Unit
from media_house.modules.content_intelligence.domain.values import Method

#: A scene cut within this many seconds of a sentence start counts as nearby.
_SCENE_WINDOW = 0.75
_WEAK = 0.35


@dataclass(frozen=True, slots=True)
class Candidate:
    #: Index of the first sentence after the boundary.
    gap: int
    level_score: float
    strength: float
    evidence: tuple[Evidence, ...]

    def to_boundary(self, units: tuple[Unit, ...]) -> Boundary:
        return Boundary(
            before_unit=units[self.gap].unit_id,
            strength=self.strength,
            evidence=self.evidence,
            weak=self.strength < _WEAK,
        )


@dataclass(frozen=True, slots=True)
class Segmentation:
    """Boundary gaps per level; each level is a subset of the finer one."""

    leaf: tuple[Candidate, ...]
    topic: tuple[Candidate, ...]
    section: tuple[Candidate, ...]
    #: Every examined gap's depth z-score, for diagnostics and tests.
    candidates: int


def _depths(curve: list[float]) -> list[float]:
    """Valley depth at each gap (index 0 and the last are never valleys)."""
    depths = [0.0] * len(curve)
    for g in range(1, len(curve) - 1):
        left = g
        while left - 1 >= 1 and curve[left - 1] >= curve[left]:
            left -= 1
        right = g
        while right + 1 < len(curve) - 1 and curve[right + 1] >= curve[right]:
            right += 1
        depths[g] = (curve[left] - curve[g]) + (curve[right] - curve[g])
    return depths


def _cue_evidence(
    units: tuple[Unit, ...],
    gap: int,
    lexicon: Lexicon,
    thresholds: Thresholds,
    visual: VisualContext | None,
    cuts: tuple[float, ...],
) -> tuple[float, list[Evidence]]:
    unit, previous = units[gap], units[gap - 1]
    found: list[Evidence] = []
    bonus = 0.0
    for name, factor in (("transition", 0.5), ("intro", 0.5), ("recap", 0.4), ("context", 0.3)):
        weight = lexicon.weight(name, unit.text)
        if weight:
            found.append(
                Evidence(
                    kind=f"cue:{name}",
                    method=Method.HEURISTIC,
                    unit_ids=(unit.unit_id,),
                    excerpt=excerpt(unit.text, 80),
                    weight=weight,
                )
            )
            bonus = max(bonus, weight * factor * 2.0)
    if unit.gap_before is not None and unit.gap_before >= thresholds.pause_evidence_seconds:
        weight = min(1.0, unit.gap_before / 4.0)
        found.append(
            Evidence(
                kind="pause",
                method=Method.OBSERVED,
                unit_ids=(unit.unit_id,),
                weight=weight,
                detail=f"{unit.gap_before:.1f}s of silence before the sentence",
            )
        )
        bonus += 0.6 * weight
    if unit.speaker and previous.speaker and unit.speaker != previous.speaker:
        found.append(
            Evidence(
                kind="speaker_change",
                method=Method.OBSERVED,
                unit_ids=(unit.unit_id,),
                weight=0.3,
                detail=f"{previous.speaker} -> {unit.speaker}",
            )
        )
        bonus += 0.3
    if visual is not None and unit.span.start is not None:
        start = unit.span.start
        near = next((c for c in cuts if abs(c - start) <= _SCENE_WINDOW), None)
        if near is not None:
            found.append(
                Evidence(
                    kind="scene_cut",
                    method=Method.OBSERVED,
                    unit_ids=(unit.unit_id,),
                    weight=0.3,
                    detail=f"scene change at {near:.2f}s",
                )
            )
            bonus += 0.3
    return bonus, found


def _enforce_minimum(chosen: list[Candidate], total: int, minimum: int) -> list[Candidate]:
    """Drop the weakest boundary next to a segment shorter than ``minimum`` until none is."""
    chosen = sorted(chosen, key=lambda c: c.gap)
    while chosen:
        edges = [0, *(c.gap for c in chosen), total]
        short = [i for i in range(len(edges) - 1) if edges[i + 1] - edges[i] < minimum]
        if not short:
            break
        adjacent = {j for i in short for j in (i - 1, i) if 0 <= j < len(chosen)}
        weakest = min(adjacent, key=lambda j: (chosen[j].level_score, -chosen[j].gap))
        chosen.pop(weakest)
    return chosen


def segment(
    units: tuple[Unit, ...],
    similarity: SimilarityProvider,
    lexicon: Lexicon,
    thresholds: Thresholds,
    width: int,
    visual: VisualContext | None = None,
) -> Segmentation:
    n = len(units)
    minimum = thresholds.min_segment_units
    if n < 2 * minimum or n < 4:
        return Segmentation((), (), (), 0)

    raw = [0.0] + [similarity.window_similarity(g, width) for g in range(1, n)]
    depths = _depths(raw)  # the windows already average over several sentences
    interior = depths[1:]
    mean = statistics.fmean(interior)
    spread = statistics.pstdev(interior)
    cuts = visual.scene_cuts() if visual else ()

    candidates: list[Candidate] = []
    for g in range(1, n):
        is_peak = depths[g] >= depths[g - 1] and (g == n - 1 or depths[g] > depths[g + 1])
        z = (depths[g] - mean) / spread if spread > 1e-9 else 0.0
        bonus, evidence = _cue_evidence(units, g, lexicon, thresholds, visual, cuts)
        if not is_peak and bonus < 0.5:
            continue
        score = z + bonus
        strength = max(0.0, min(1.0, (score + 1.0) / 3.5))
        valley = Evidence(
            kind="similarity_valley",
            method=Method.DETERMINISTIC,
            unit_ids=(units[g].unit_id,),
            weight=max(0.0, min(1.0, (z + 1.0) / 3.0)),
            detail=f"depth {depths[g]:.3f} ({z:+.1f} sd); similarity {raw[g]:.2f}",
        )
        candidates.append(Candidate(g, score, strength, (valley, *evidence)))

    def at(level_sigma: float, minimum_units: int) -> list[Candidate]:
        chosen = [c for c in candidates if c.level_score >= level_sigma]
        return _enforce_minimum(chosen, n, minimum_units)

    leaf = at(thresholds.leaf_depth_sigma, minimum)
    topic = _enforce_minimum(
        [c for c in leaf if c.level_score >= thresholds.topic_depth_sigma], n, minimum * 2
    )
    section = _enforce_minimum(
        [c for c in topic if c.level_score >= thresholds.section_depth_sigma], n, minimum * 4
    )
    return Segmentation(tuple(leaf), tuple(topic), tuple(section), len(candidates))


def ranges(total: int, boundaries: tuple[Candidate, ...]) -> list[range]:
    """The sentence ranges the boundaries cut ``[0, total)`` into."""
    edges = [0, *(c.gap for c in boundaries), total]
    return [range(a, b) for a, b in itertools.pairwise(edges) if b > a]
