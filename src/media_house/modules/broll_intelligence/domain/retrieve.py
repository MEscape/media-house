"""Stage 1 of retrieval: words. Which shots of the library mention what the passage is about?

One inverted index over the shots' observations is built per run and every passage looks up its
own words in it, so the cost grows with the words of the passage, never with the library. The
shortlist is only the lexical half of retrieval: the application adds the shots the picture
embedding finds (footage nobody labelled), and the assessment that follows judges both the same way.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from media_house.modules.broll_intelligence.domain.facts import ShotFact, TargetFact
from media_house.modules.broll_intelligence.domain.text import query_terms, shot_words
from media_house.modules.broll_intelligence.domain.values import Association

#: How much a word counts, by where in the shot it was found.
PLACE_WEIGHT: dict[str, float] = {
    Association.OBSERVED_TEXT.value: 1.0,
    Association.ENTITY_LABEL.value: 0.9,
    Association.DESCRIBED.value: 0.7,
    Association.METADATA.value: 0.5,
}


@dataclass(frozen=True, slots=True)
class LexicalIndex:
    shots: tuple[ShotFact, ...]
    #: reduced word -> (shot position, place found)
    postings: Mapping[str, tuple[tuple[int, str], ...]]


def build_index(shots: Sequence[ShotFact]) -> LexicalIndex:
    postings: dict[str, list[tuple[int, str]]] = {}
    for position, shot in enumerate(shots):
        for word, place in shot_words(shot).items():
            postings.setdefault(word, []).append((position, place))
    return LexicalIndex(tuple(shots), {w: tuple(p) for w, p in postings.items()})


def lexical_candidates(
    index: LexicalIndex, targets: Sequence[TargetFact], per_target: int
) -> dict[str, tuple[str, ...]]:
    """Per passage: the keys of the shots sharing the most of its weighted words (best first)."""
    out: dict[str, tuple[str, ...]] = {}
    for target in targets:
        terms = query_terms(target)
        total = sum(weight for _, weight in terms)
        if total <= 0:
            out[target.target_id] = ()
            continue
        scores: dict[int, float] = {}
        for word, weight in terms:
            best: dict[int, float] = {}
            for position, place in index.postings.get(word, ()):
                best[position] = max(best.get(position, 0.0), PLACE_WEIGHT.get(place, 0.5))
            for position, strength in best.items():
                scores[position] = scores.get(position, 0.0) + weight * strength
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        out[target.target_id] = tuple(index.shots[p].key for p, _ in ranked[:per_target])
    return out
