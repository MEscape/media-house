"""Word statistics of a transcript: terms, TF-IDF vectors, key terms and the lexical similarity.

Stemming is a crude prefix cut (first six letters), good enough to match inflections in the
languages we carry cues for and honest about being crude. Everything is deterministic: ties
break on index or alphabetical order.
"""

import itertools
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from media_house.modules.content_intelligence.domain.lexicon import Lexicon
from media_house.modules.content_intelligence.domain.similarity import (
    LEXICAL,
    Neighbours,
)

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_STEM_LENGTH = 6
#: Postings longer than this (terms in more sentences) are too common to name a neighbour.
_MAX_POSTING = 200

type Sparse = dict[str, float]


def tokens(text: str) -> list[str]:
    return [t.casefold() for t in _TOKEN.findall(text)]


def stem(token: str) -> str:
    return token if token.isdigit() or len(token) <= _STEM_LENGTH else token[:_STEM_LENGTH]


def content_tokens(text: str, lexicon: Lexicon) -> list[str]:
    """Words that carry meaning: stop words, one-letter words and fillers removed."""
    out: list[str] = []
    for token in tokens(text):
        if token in lexicon.stopwords or token in lexicon.fillers:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        out.append(token)
    return out


def cosine(a: Sparse, b: Sparse) -> float:
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(w * large[t] for t, w in small.items() if t in large)
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    return max(0.0, dot / (na * nb)) if na and nb else 0.0


def containment(first: Sequence[str], second: Sequence[str], n: int = 3) -> float:
    """Share of the shorter text's word n-grams found in the other: near-verbatim repetition."""
    size = n if min(len(first), len(second)) >= n else 1
    grams_a = {tuple(first[i : i + size]) for i in range(len(first) - size + 1)}
    grams_b = {tuple(second[i : i + size]) for i in range(len(second) - size + 1)}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / min(len(grams_a), len(grams_b))


@dataclass(frozen=True, slots=True)
class TextStats:
    """Per-sentence terms plus recording-wide document frequencies."""

    #: Casefolded words of each sentence (all of them, in order).
    words: tuple[tuple[str, ...], ...]
    #: Content word stems of each sentence.
    stems: tuple[tuple[str, ...], ...]
    idf: dict[str, float]
    vectors: tuple[Sparse, ...]
    #: Most common surface form per stem, for readable labels.
    surface: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, texts: Sequence[str], lexicon: Lexicon) -> "TextStats":
        words = tuple(tuple(tokens(t)) for t in texts)
        content = [content_tokens(t, lexicon) for t in texts]
        stems = tuple(tuple(stem(w) for w in c) for c in content)
        document_frequency: Counter[str] = Counter()
        for unit in stems:
            document_frequency.update(set(unit))
        n = len(texts)
        idf = {t: math.log((n + 1) / (df + 1)) + 1.0 for t, df in document_frequency.items()}
        vectors: list[Sparse] = []
        for unit in stems:
            counts = Counter(unit)
            vectors.append({t: (1 + math.log(c)) * idf[t] for t, c in counts.items()})
        surface_counts: dict[str, Counter[str]] = {}
        for c in content:
            for w in c:
                surface_counts.setdefault(stem(w), Counter())[w] += 1
        surface = {
            s: min(forms.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            for s, forms in surface_counts.items()
        }
        return cls(words, stems, idf, tuple(vectors), surface)

    def __len__(self) -> int:
        return len(self.words)

    def centroid(self, indices: Iterable[int]) -> Sparse:
        total: Sparse = {}
        for i in indices:
            for t, w in self.vectors[i].items():
                total[t] = total.get(t, 0.0) + w
        return total

    def key_terms(self, indices: Sequence[int], count: int, min_count: int = 1) -> list[str]:
        """Readable key terms of a group of sentences, best first (bigrams win when repeated).

        Only words used at least ``min_count`` times in the group qualify, so that a label made of
        terms is a statement about repetition, not an arbitrary pick among single mentions.
        """
        totals = self.centroid(indices)
        if min_count > 1:
            seen: Counter[str] = Counter()
            for i in indices:
                seen.update(self.stems[i])
            totals = {t: w for t, w in totals.items() if seen[t] >= min_count}
        bigrams: Counter[tuple[str, str]] = Counter()
        for i in indices:
            s = self.stems[i]
            bigrams.update(itertools.pairwise(s))
        scored: list[tuple[float, str]] = []
        for (a, b), c in bigrams.items():
            if c >= 2 and a != b:
                score = c * (totals.get(a, 0.0) + totals.get(b, 0.0)) / max(1, len(indices))
                scored.append((score * 1.5, f"{self.surface.get(a, a)} {self.surface.get(b, b)}"))
        scored += [(w, self.surface.get(t, t)) for t, w in totals.items()]
        out: list[str] = []
        used: set[str] = set()
        for _, label in sorted(scored, key=lambda x: (-x[0], x[1])):
            parts = set(label.split())
            if parts & used:
                continue
            out.append(label)
            used |= parts
            if len(out) == count:
                break
        return out


class LexicalSimilarity:
    """Sentence similarity from TF-IDF vectors (no model, no download, fully deterministic)."""

    kind = LEXICAL
    identity = "lexical-tfidf-v1"

    def __init__(self, stats: TextStats) -> None:
        self._stats = stats

    def similarity(self, i: int, j: int) -> float:
        return cosine(self._stats.vectors[i], self._stats.vectors[j])

    def window_similarity(self, gap: int, width: int) -> float:
        n = len(self._stats)
        left = self._stats.centroid(range(max(0, gap - width), gap))
        right = self._stats.centroid(range(gap, min(n, gap + width)))
        return cosine(left, right)

    def group_similarity(self, first: Sequence[int], second: Sequence[int]) -> float:
        return cosine(self._stats.centroid(first), self._stats.centroid(second))

    def neighbours(self, k: int, min_similarity: float) -> Neighbours:
        n = len(self._stats)
        cap = _MAX_POSTING if n >= 400 else n
        postings: dict[str, list[int]] = {}
        for i, vector in enumerate(self._stats.vectors):
            for t in vector:
                postings.setdefault(t, []).append(i)
        norms = [math.sqrt(sum(w * w for w in v.values())) for v in self._stats.vectors]
        result: dict[int, tuple[tuple[int, float], ...]] = {}
        for i, vector in enumerate(self._stats.vectors):
            if not vector or not norms[i]:
                continue
            scores: dict[int, float] = {}
            for t, w in vector.items():
                posting = postings[t]
                if len(posting) > cap:
                    continue
                for j in posting:
                    if j != i:
                        scores[j] = scores.get(j, 0.0) + w * self._stats.vectors[j][t]
            ranked = sorted(
                ((j, dot / (norms[i] * norms[j])) for j, dot in scores.items() if norms[j]),
                key=lambda item: (-item[1], item[0]),
            )
            kept = tuple((j, s) for j, s in ranked[:k] if s >= min_similarity)
            if kept:
                result[i] = kept
        return result
