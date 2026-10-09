"""Anonymous identity clusters: tracks that look like the same person get the same letter.

Evidence is the appearance embedding of the person in each track. Nothing identifies anybody:
clusters are named ``person_A``, ``person_B`` ... in order of first appearance, and no attribute
of a person is inferred. Two tracks that are on screen together in the same shot can never be the
same person (cannot-link), whatever they look like.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True, slots=True)
class TrackAppearance:
    track_id: str
    shot_index: int
    first: int
    last: int
    #: Mean, L2-normalised appearance embedding of the person in this track.
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Cluster:
    cluster_id: str
    track_ids: tuple[str, ...]
    #: Mean similarity between the members (1.0 for a single track).
    similarity: float


def mean_vector(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """The normalised mean of ``vectors`` (an empty input gives an empty vector)."""
    if not vectors:
        return ()
    total = [sum(column) for column in zip(*vectors, strict=True)]
    norm = math.sqrt(sum(v * v for v in total)) or 1.0
    return tuple(v / norm for v in total)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _coexist(a: TrackAppearance, b: TrackAppearance) -> bool:
    return a.shot_index == b.shot_index and a.first <= b.last and b.first <= a.last


def _name(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, len(_LETTERS))
        letters = _LETTERS[remainder] + letters
    return f"person_{letters}"


def cluster_tracks(tracks: Sequence[TrackAppearance], threshold: float) -> list[Cluster]:
    """Average-linkage agglomeration: merge the most similar pair while it reaches ``threshold``."""
    groups: list[list[TrackAppearance]] = [[t] for t in tracks if t.vector]
    while len(groups) > 1:
        best: tuple[float, int, int] | None = None
        for i, left in enumerate(groups):
            for j in range(i + 1, len(groups)):
                right = groups[j]
                if any(_coexist(a, b) for a in left for b in right):
                    continue
                sims = [cosine(a.vector, b.vector) for a in left for b in right]
                score = sum(sims) / len(sims)
                if score >= threshold and (best is None or score > best[0]):
                    best = (score, i, j)
        if best is None:
            break
        _, i, j = best
        groups[i] = groups[i] + groups[j]
        del groups[j]

    ordered = sorted(groups, key=lambda g: min((t.first, t.track_id) for t in g))
    clusters: list[Cluster] = []
    for index, group in enumerate(ordered):
        pairs = [cosine(a.vector, b.vector) for n, a in enumerate(group) for b in group[n + 1 :]]
        clusters.append(
            Cluster(
                _name(index),
                tuple(sorted(t.track_id for t in group)),
                sum(pairs) / len(pairs) if pairs else 1.0,
            )
        )
    return clusters
