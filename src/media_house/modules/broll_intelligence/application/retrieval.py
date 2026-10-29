"""Staged retrieval: cheap filters first, models only where they can change the answer.

1. **Words** (domain, one inverted index): shots whose observations share the passage's words.
2. **Pictures**: the passage's words in the shots' embedding space (CLIP text tower); one matrix
   product per passage over every embedded shot, so footage nobody labelled can still be found. The
   similarity is also expressed against the same passage's similarity to the whole pool (a standard
   score), because a raw CLIP cosine alone says little.
3. **Descriptions**: the sentence-embedding similarity of the passage and the shortlisted shots'
   scene descriptions (a few per passage; never the whole library).
4. **Repeats**: near-identical pictures among the shortlist.

A model that is unavailable (library or files missing, a different embedding model than the one that
embedded the footage) costs only its own signal; the result records why.
"""

from dataclasses import dataclass, field

from media_house.modules.broll_intelligence.application.pool import PoolBuild
from media_house.modules.broll_intelligence.application.ports import VectorIndex, VectorIndexer
from media_house.modules.broll_intelligence.domain.facts import (
    Match,
    RetrievalFacts,
    ShotFact,
    TargetFact,
)
from media_house.modules.broll_intelligence.domain.profile import BrollProfile
from media_house.modules.broll_intelligence.domain.retrieve import (
    build_index,
    lexical_candidates,
)
from media_house.modules.broll_intelligence.domain.select import SIMILAR
from media_house.modules.content_intelligence.application.contracts import TextEncoder
from media_house.modules.video_intelligence.application.contracts import VisualTextEncoder
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import MediaHouseError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Shortlisted shots per passage whose description is compared by sentence embedding.
TEXT_REFINE = 12
_QUERY_CHARS = 200


@dataclass(slots=True)
class Retrieved:
    lexical: dict[str, tuple[str, ...]] = field(default_factory=dict)
    facts: RetrievalFacts = field(default_factory=RetrievalFacts)
    shortlist: list[ShotFact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def query_text(target: TargetFact) -> str:
    base = target.concept or target.text or target.reason
    names = " ".join(n.text for n in target.names if n.text not in base)
    return f"{base} {names}".strip()[:_QUERY_CHARS]


class Retriever:
    def __init__(
        self, visual: VisualTextEncoder, text: TextEncoder, vectors: VectorIndexer
    ) -> None:
        self._visual = visual
        self._text = text
        self._vectors = vectors

    def run(
        self,
        targets: list[TargetFact],
        pool: PoolBuild,
        profile: BrollProfile,
        cancellation: CancellationToken,
    ) -> Retrieved:
        out = Retrieved()
        lexical = lexical_candidates(build_index(pool.shots), targets, profile.max_candidates)
        out.lexical = lexical
        rows = {s.key: i for i, s in enumerate(pool.shots)}
        matches: dict[tuple[str, str], Match] = {}
        similar: dict[frozenset[str], float] = {}
        index: VectorIndex | None = None
        visual_model: str | None = None
        visual_keys: dict[str, list[str]] = {}

        reason = self._visual_unavailable(pool)
        if reason is None:
            try:
                index = self._vectors.build(pool.packed, pool.dimension)
                visual_model = pool.embedding_model
                visual_keys = self._visual_matches(
                    targets, pool, index, profile, matches, lexical, rows, cancellation
                )
            except (MediaHouseError, RuntimeError, OSError) as exc:
                _log.warning("Picture search failed", reason=str(exc))
                reason = f"the picture search failed: {exc}"
                index, visual_model, visual_keys, matches = None, None, {}, {}
        if reason is not None:
            out.notes.append(f"visual_unavailable: {reason}")

        keys = {
            t.target_id: dict.fromkeys(
                [*lexical.get(t.target_id, ()), *visual_keys.get(t.target_id, ())]
            )
            for t in targets
        }
        text_model = self._text_matches(
            targets, pool, keys, rows, matches, profile, out.notes, cancellation
        )

        union = list(dict.fromkeys(k for ks in keys.values() for k in ks))
        if index is not None and union:
            row_to_key = {rows[k]: k for k in union}
            for a, b, similarity in index.similar_pairs(sorted(row_to_key), SIMILAR):
                similar[frozenset({row_to_key[a], row_to_key[b]})] = round(similarity, 4)
        out.shortlist = [pool.shots[rows[k]] for k in union]
        out.facts = RetrievalFacts(
            matches=matches,
            similar=similar,
            visual_model=visual_model,
            text_model=text_model,
            notes=tuple(out.notes),
        )
        return out

    # --- pictures --------------------------------------------------------------------------
    def _visual_unavailable(self, pool: PoolBuild) -> str | None:
        if not pool.dimension or pool.embedding_model is None:
            return "no shot of the searched footage has a picture embedding"
        problem = self._visual.unavailable()
        if problem is not None:
            return problem
        if self._visual.identity() != pool.embedding_model:
            return (
                f"the footage was embedded by {pool.embedding_model}, the encoder is "
                f"{self._visual.identity()}"
            )
        return None

    def _visual_matches(
        self,
        targets: list[TargetFact],
        pool: PoolBuild,
        index: VectorIndex,
        profile: BrollProfile,
        matches: dict[tuple[str, str], Match],
        lexical: dict[str, tuple[str, ...]],
        rows: dict[str, int],
        cancellation: CancellationToken,
    ) -> dict[str, list[str]]:
        askers = [(t, query_text(t)) for t in targets]
        askers = [(t, q) for t, q in askers if q]
        vectors = self._visual.embed([q for _, q in askers], cancellation)
        found: dict[str, list[str]] = {}
        for (target, _), vector in zip(askers, vectors, strict=True):
            hits = index.search(vector, profile.max_candidates)
            keys: list[str] = []
            for row, cosine in hits.top:
                key = pool.shots[row].key
                matches[(target.target_id, key)] = Match(
                    visual_similarity=round(cosine, 4), visual_z=_round(hits.z(cosine))
                )
                keys.append(key)
            seen = [k for k in lexical.get(target.target_id, ()) if k not in keys]
            for key, other in zip(
                seen, index.cosines(vector, [rows[k] for k in seen]), strict=True
            ):
                if other is not None:
                    matches[(target.target_id, key)] = Match(
                        visual_similarity=round(other, 4), visual_z=_round(hits.z(other))
                    )
            found[target.target_id] = keys
        return found

    # --- descriptions ----------------------------------------------------------------------
    def _text_matches(
        self,
        targets: list[TargetFact],
        pool: PoolBuild,
        keys: dict[str, dict[str, None]],
        rows: dict[str, int],
        matches: dict[tuple[str, str], Match],
        profile: BrollProfile,
        notes: list[str],
        cancellation: CancellationToken,
    ) -> str | None:
        wanted: dict[str, list[str]] = {}
        for target in targets:
            described = [k for k in keys[target.target_id] if pool.shots[rows[k]].description][
                :TEXT_REFINE
            ]
            if described and query_text(target):
                wanted[target.target_id] = described
        if not wanted:
            return None
        problem = self._text.unavailable()
        if problem is not None:
            notes.append(f"text_unavailable: {problem}")
            return None
        try:
            asks = {t.target_id: query_text(t) for t in targets if t.target_id in wanted}
            descriptions = sorted(
                {pool.shots[rows[k]].description or "" for ks in wanted.values() for k in ks}
            )
            vectors = self._text.embed([*asks.values(), *descriptions], cancellation)
        except (MediaHouseError, RuntimeError, OSError) as exc:
            notes.append(f"text_unavailable: {exc}")
            return None
        query_vectors = dict(zip(asks, vectors[: len(asks)], strict=True))
        description_vectors = dict(zip(descriptions, vectors[len(asks) :], strict=True))
        for target_id, shot_keys in wanted.items():
            for key in shot_keys:
                shot = pool.shots[rows[key]]
                similarity = _dot(
                    query_vectors[target_id], description_vectors[shot.description or ""]
                )
                previous = matches.get((target_id, key), Match())
                matches[(target_id, key)] = Match(
                    visual_similarity=previous.visual_similarity,
                    visual_z=previous.visual_z,
                    text_similarity=round(similarity, 4),
                )
        return self._text.identity()


def _dot(a: list[float], b: list[float]) -> float:
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)
