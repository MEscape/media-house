"""The shared inputs of the analysis stages, built once per run."""

from dataclasses import dataclass, field

from media_house.modules.content_intelligence.domain.config import ProfileSettings, Thresholds
from media_house.modules.content_intelligence.domain.context import AudioCues, VisualContext
from media_house.modules.content_intelligence.domain.lexical import TextStats
from media_house.modules.content_intelligence.domain.lexicon import Lexicon
from media_house.modules.content_intelligence.domain.model import Evidence, excerpt
from media_house.modules.content_intelligence.domain.similarity import (
    EMBEDDING,
    Neighbours,
    SimilarityProvider,
)
from media_house.modules.content_intelligence.domain.units import Unit
from media_house.modules.content_intelligence.domain.values import Method

#: The recording centroid is estimated from at most this many evenly spaced sentences.
_SAMPLE = 600


def scale(value: float, low: float, high: float) -> float:
    """Linear map of ``[low, high]`` onto ``[0, 1]``, clipped."""
    return max(0.0, min(1.0, (value - low) / (high - low))) if high > low else 0.0


@dataclass(frozen=True, slots=True)
class Frame:
    units: tuple[Unit, ...]
    stats: TextStats
    lexicon: Lexicon
    similarity: SimilarityProvider
    neighbours: Neighbours
    thresholds: Thresholds
    settings: ProfileSettings
    visual: VisualContext | None = None
    audio: AudioCues | None = None
    #: Segment index of every sentence (filled once segmentation is done).
    segment_of: tuple[int, ...] = field(default=())

    @property
    def overlap_threshold(self) -> float:
        t = self.thresholds
        return t.overlap_embedding if self.similarity.kind == EMBEDDING else t.overlap_lexical

    def centrality_scale(self, similarity: float) -> float:
        """Centroid similarity as 0-1: embeddings and TF-IDF live on different numeric ranges."""
        if self.similarity.kind == EMBEDDING:
            return scale(similarity, 0.25, 0.65)
        return scale(similarity, 0.05, 0.35)

    def sample(self) -> list[int]:
        n = len(self.units)
        if n <= _SAMPLE:
            return list(range(n))
        step = n / _SAMPLE
        return [int(i * step) for i in range(_SAMPLE)]

    def cue(
        self, name: str, unit: Unit, weight: float, *, method: Method = Method.HEURISTIC
    ) -> Evidence:
        return Evidence(
            kind=f"cue:{name}",
            method=method,
            unit_ids=(unit.unit_id,),
            excerpt=excerpt(unit.text, 100),
            weight=weight,
        )
