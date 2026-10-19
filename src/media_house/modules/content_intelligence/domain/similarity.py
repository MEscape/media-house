"""What the segmentation and the redundancy analysis need to know about sentence similarity.

Two kinds implement it: ``LexicalSimilarity`` (pure Python, TF-IDF; always available) in the
domain, and a dense-vector one built from a sentence-embedding engine in infrastructure. The
domain never learns which one it got, only its ``kind`` (used to pick the overlap threshold).
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

#: ``neighbours`` result: for each sentence index, ``(other index, similarity)`` best first.
type Neighbours = Mapping[int, tuple[tuple[int, float], ...]]

LEXICAL = "lexical"
EMBEDDING = "embedding"


class SimilarityProvider(Protocol):
    """Similarity between sentences addressed by their index in the transcript."""

    @property
    def kind(self) -> str:
        """``lexical`` or ``embedding``."""
        ...

    @property
    def identity(self) -> str:
        """Names the method and, for embeddings, the model: part of every result's identity."""
        ...

    def similarity(self, i: int, j: int) -> float:
        """Cosine in [0, 1] (negative values are clipped)."""
        ...

    def window_similarity(self, gap: int, width: int) -> float:
        """Similarity of the ``width`` sentences before ``gap`` and those from ``gap`` on."""
        ...

    def group_similarity(self, first: Sequence[int], second: Sequence[int]) -> float:
        """Similarity of the centroids of two groups of sentences."""
        ...

    def neighbours(self, k: int, min_similarity: float) -> Neighbours:
        """Up to ``k`` most similar other sentences per sentence, at least ``min_similarity``."""
        ...
