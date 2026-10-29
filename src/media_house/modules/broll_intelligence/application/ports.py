"""Ports the use case needs from the outside world: vector arithmetic only.

Everything else B-roll reads comes through other modules' public contracts. Vector search is a port
because the domain and application are plain Python and a library of footage holds tens of
thousands of 512-number embeddings: the adapter (NumPy) does the arithmetic, once per run.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VectorHits:
    """The best rows for one query, and how the query scores against ALL rows."""

    #: ``(row, cosine)`` best first.
    top: tuple[tuple[int, float], ...]
    mean: float
    std: float
    rows: int

    def z(self, cosine: float) -> float | None:
        """Standard score of ``cosine`` against every row (``None`` when it cannot be stated)."""
        if self.rows < 8 or self.std <= 1e-9:
            return None
        return (cosine - self.mean) / self.std


class VectorIndex(Protocol):
    @property
    def size(self) -> int: ...

    def search(self, query: Sequence[float], k: int) -> VectorHits:
        """Cosine similarity of ``query`` to every row (unit-length rows and query)."""
        ...

    def cosines(self, query: Sequence[float], rows: Sequence[int]) -> tuple[float | None, ...]:
        """Cosine similarity of ``query`` to each of ``rows`` (``None``: the row has no vector)."""
        ...

    def similar_pairs(
        self, rows: Sequence[int], threshold: float
    ) -> tuple[tuple[int, int, float], ...]:
        """Pairs among ``rows`` whose cosine is at least ``threshold`` (lower row first)."""
        ...


class VectorIndexer(Protocol):
    def build(self, packed: Sequence[str], dimension: int) -> VectorIndex:
        """An index over embeddings stored as base64 half-precision floats (``IndexedShot``)."""
        ...
