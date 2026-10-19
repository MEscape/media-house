"""Sentence similarity from dense vectors (NumPy). Implements the domain's ``SimilarityProvider``.

Neighbour search is exact and bounded: rows are processed in blocks so memory stays at
``block x n`` floats, and only the top ``k`` per row are kept, in a deterministic order.
"""

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from media_house.modules.content_intelligence.domain.similarity import (
    EMBEDDING,
    Neighbours,
)

_BLOCK = 512

type Matrix = npt.NDArray[np.float32]


class DenseSimilarity:
    kind = EMBEDDING

    def __init__(self, vectors: Matrix, identity: str) -> None:
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        self._vectors: Matrix = (vectors / np.maximum(norms, 1e-12)).astype(np.float32)
        self._identity = identity

    @property
    def identity(self) -> str:
        return self._identity

    def __len__(self) -> int:
        return int(self._vectors.shape[0])

    def similarity(self, i: int, j: int) -> float:
        return max(0.0, float(self._vectors[i] @ self._vectors[j]))

    def _centroid(self, indices: Sequence[int]) -> Matrix | None:
        if not len(indices):
            return None
        mean = self._vectors[list(indices)].mean(axis=0)
        norm = float(np.linalg.norm(mean))
        return (mean / norm).astype(np.float32) if norm > 1e-12 else None

    def group_similarity(self, first: Sequence[int], second: Sequence[int]) -> float:
        a, b = self._centroid(first), self._centroid(second)
        return 0.0 if a is None or b is None else max(0.0, float(a @ b))

    def window_similarity(self, gap: int, width: int) -> float:
        n = len(self)
        return self.group_similarity(
            range(max(0, gap - width), gap), range(gap, min(n, gap + width))
        )

    def neighbours(self, k: int, min_similarity: float) -> Neighbours:
        n = len(self)
        result: dict[int, tuple[tuple[int, float], ...]] = {}
        if n < 2 or k < 1:
            return result
        top = min(k, n - 1)
        for start in range(0, n, _BLOCK):
            block = self._vectors[start : start + _BLOCK]
            sims = block @ self._vectors.T
            rows = np.arange(block.shape[0])
            sims[rows, start + rows] = -np.inf  # never a sentence's own neighbour
            candidates = np.argpartition(-sims, top - 1, axis=1)[:, :top]
            for r in range(block.shape[0]):
                picked = sorted(
                    ((int(c), float(sims[r, c])) for c in candidates[r]),
                    key=lambda item: (-item[1], item[0]),
                )
                kept = tuple((c, s) for c, s in picked if s >= min_similarity)
                if kept:
                    result[start + r] = kept
        return result
