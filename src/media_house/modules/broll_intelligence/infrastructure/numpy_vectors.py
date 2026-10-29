"""Cosine search over shot embeddings with NumPy: one matrix, one product per query.

The rows are the library's shot embeddings (half-precision floats, base64, as the shot index
stores them). They are decoded once into a float32 matrix and normalised, so a query against 20 000
shots is a single matrix-vector product. A row that is empty (a shot without an embedding) is a
zero row and never matches anything.
"""

import base64
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from media_house.modules.broll_intelligence.application.ports import VectorHits
from media_house.shared.errors import ValidationError


class NumpyVectorIndex:
    """Structurally implements ``ports.VectorIndex``."""

    def __init__(self, matrix: NDArray[np.float32]) -> None:
        self._matrix = matrix
        self._present = np.linalg.norm(matrix, axis=1) > 0

    @property
    def size(self) -> int:
        return int(self._matrix.shape[0])

    def search(self, query: Sequence[float], k: int) -> VectorHits:
        vector = np.asarray(query, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if self.size == 0 or norm == 0.0:
            return VectorHits(top=(), mean=0.0, std=0.0, rows=0)
        scores = self._matrix @ (vector / norm)
        valid = scores[self._present]
        if valid.size == 0:
            return VectorHits(top=(), mean=0.0, std=0.0, rows=0)
        masked = np.where(self._present, scores, -np.inf)
        count = min(k, int(valid.size))
        best = np.argpartition(-masked, count - 1)[:count]
        ordered = sorted(((int(i), float(scores[i])) for i in best), key=lambda p: (-p[1], p[0]))
        return VectorHits(
            top=tuple(ordered),
            mean=float(valid.mean()),
            std=float(valid.std()),
            rows=int(valid.size),
        )

    def cosines(self, query: Sequence[float], rows: Sequence[int]) -> tuple[float | None, ...]:
        vector = np.asarray(query, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm == 0.0 or self.size == 0:
            return tuple(None for _ in rows)
        unit = vector / norm
        return tuple(
            float(self._matrix[r] @ unit) if 0 <= r < self.size and self._present[r] else None
            for r in rows
        )

    def similar_pairs(
        self, rows: Sequence[int], threshold: float
    ) -> tuple[tuple[int, int, float], ...]:
        ordered = sorted({r for r in rows if 0 <= r < self.size and self._present[r]})
        if len(ordered) < 2:
            return ()
        block = self._matrix[ordered]
        gram = block @ block.T
        pairs = []
        for a in range(len(ordered)):
            for b in range(a + 1, len(ordered)):
                if gram[a, b] >= threshold:
                    pairs.append((ordered[a], ordered[b], float(gram[a, b])))
        return tuple(pairs)


class NumpyVectorIndexer:
    """Structurally implements ``ports.VectorIndexer``."""

    def build(self, packed: Sequence[str], dimension: int) -> NumpyVectorIndex:
        matrix = np.zeros((len(packed), max(dimension, 1)), dtype=np.float32)
        for row, text in enumerate(packed):
            if not text:
                continue
            raw = base64.b64decode(text)
            if len(raw) != 2 * dimension:
                raise ValidationError(
                    f"embedding of row {row} has {len(raw) // 2} values, expected {dimension}",
                    field="embedding",
                    user_message="A stored shot embedding is damaged.",
                )
            vector = np.frombuffer(raw, dtype="<f2").astype(np.float32)
            norm = float(np.linalg.norm(vector))
            if norm > 0:
                matrix[row] = vector / norm
        return NumpyVectorIndex(matrix)
