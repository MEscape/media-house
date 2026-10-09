"""Visual meaning and relations from embeddings: content type, environment, scenes, retakes.

One shared embedding pass feeds all of it: shot and scene embeddings, zero-shot labels (content
type, environment, unusable-footage indicators), scene grouping and retake / near-duplicate
detection. Labels only describe what a picture looks like; nothing here infers speech, intent or
story.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from media_house.modules.video_intelligence.domain.identity import cosine, mean_vector
from media_house.modules.video_intelligence.domain.observations import (
    ContentTypeObservation,
    Evidence,
    IndicatorObservation,
    RetakeGroup,
)
from media_house.modules.video_intelligence.domain.profiles import MeaningSettings
from media_house.modules.video_intelligence.domain.signals import EmbeddingSignals, ShotSignals
from media_house.modules.video_intelligence.domain.values import AnalyzerState, RelationKind
from media_house.modules.video_intelligence.domain.vocabulary import (
    CONTENT_PREFIX,
    CONTENT_PROFILE_OF,
    ENVIRONMENT_PREFIX,
    INDICATOR_PREFIX,
)

_FULL_CONFIDENCE_FRAMES = 3
_ENVIRONMENT_PLACES = ("office", "studio", "home", "street", "nature")
#: A shot whose every analysed frame is black is flagged on this share of black frames.
_BLACK_SHARE = 0.9


def frames_in(signals: EmbeddingSignals, first: int, end: int) -> list[int]:
    """Row indices of the analysed frames inside ``first..end-1``."""
    return [i for i, f in enumerate(signals.frames) if first <= f < end]


def shot_embedding(signals: EmbeddingSignals, first: int, end: int) -> tuple[float, ...]:
    """The normalised mean of the frame embeddings inside the shot (empty if none)."""
    return mean_vector([signals.vectors[i] for i in frames_in(signals, first, end)])


def _softmax_scores(
    vector: Sequence[float], signals: EmbeddingSignals, prefix: str, temperature: float
) -> dict[str, float]:
    indices = [i for i, n in enumerate(signals.label_names) if n.startswith(prefix)]
    logits = [temperature * cosine(vector, signals.label_vectors[i]) for i in indices]
    top = max(logits)
    exp = [math.exp(v - top) for v in logits]
    total = sum(exp)
    return {
        signals.label_names[i][len(prefix) :]: e / total for i, e in zip(indices, exp, strict=True)
    }


def shot_label_scores(
    signals: EmbeddingSignals, first: int, end: int, prefix: str, settings: MeaningSettings
) -> dict[str, float]:
    """Mean zero-shot score of each label of one group over the analysed frames of the shot."""
    rows = frames_in(signals, first, end)
    if not rows or not signals.label_names:
        return {}
    totals: dict[str, float] = {}
    for i in rows:
        for name, score in _softmax_scores(
            signals.vectors[i], signals, prefix, settings.label_temperature
        ).items():
            totals[name] = totals.get(name, 0.0) + score
    return {name: total / len(rows) for name, total in sorted(totals.items())}


def _evidence(signals: EmbeddingSignals, timeline: ShotSignals, rows: Sequence[int]) -> Evidence:
    return Evidence(
        frames=tuple(timeline.time_of(signals.frames[i]) for i in rows[:3]), metric="embedding"
    )


def classify_content(
    signals: EmbeddingSignals,
    timeline: ShotSignals,
    first: int,
    end: int,
    settings: MeaningSettings,
) -> ContentTypeObservation:
    rows = frames_in(signals, first, end)
    if not rows:
        return ContentTypeObservation(
            state=AnalyzerState.NOT_ANALYZED, reasons=("no_embedded_frame_in_shot",)
        )
    scores = shot_label_scores(signals, first, end, CONTENT_PREFIX, settings)
    if not scores:
        return ContentTypeObservation(
            state=AnalyzerState.NOT_AVAILABLE, reasons=("no_content_vocabulary",)
        )
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    (best, best_score), runner = ranked[0], ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < settings.content_min_score or best_score - runner < settings.label_margin:
        return ContentTypeObservation(
            state=AnalyzerState.UNKNOWN,
            reasons=("no_content_type_stands_out",),
            scores=scores,
        )
    return ContentTypeObservation(
        state=AnalyzerState.OK,
        confidence=min(1.0, best_score) * min(1.0, len(rows) / _FULL_CONFIDENCE_FRAMES),
        evidence=(_evidence(signals, timeline, rows),),
        label=best,
        content_profile=CONTENT_PROFILE_OF.get(best, "generic"),
        scores=scores,
        margin=best_score - runner,
    )


@dataclass(frozen=True, slots=True)
class Environment:
    indoor_outdoor: str | None
    place: str | None
    scores: dict[str, float]
    confidence: float


def describe_environment(
    signals: EmbeddingSignals, first: int, end: int, settings: MeaningSettings
) -> Environment | None:
    scores = shot_label_scores(signals, first, end, ENVIRONMENT_PREFIX, settings)
    if not scores:
        return None
    inside, outside = scores.get("indoor", 0.0), scores.get("outdoor", 0.0)
    indoor_outdoor = None
    if inside + outside > 0 and abs(inside - outside) >= settings.label_margin:
        indoor_outdoor = "indoor" if inside > outside else "outdoor"
    places = {k: v for k, v in scores.items() if k in _ENVIRONMENT_PLACES}
    place = max(places, key=lambda k: (places[k], k)) if places else None
    if place is not None and places[place] < settings.content_min_score:
        place = None
    return Environment(indoor_outdoor, place, scores, max(inside, outside))


def find_indicators(
    signals: EmbeddingSignals | None,
    timeline: ShotSignals,
    black: Sequence[bool],
    first: int,
    end: int,
    settings: MeaningSettings,
) -> IndicatorObservation:
    """Signs the footage may be unusable (a black shot, a slate, a covered lens). Flags only."""
    flags: list[str] = []
    length = end - first
    if length and sum(1 for b in black[first:end] if b) / length >= _BLACK_SHARE:
        flags.append("black_picture")
    rows: list[int] = []
    if signals is not None:
        rows = frames_in(signals, first, end)
        if rows and signals.label_names:
            scores = shot_label_scores(signals, first, end, INDICATOR_PREFIX, settings)
            flags.extend(sorted(n for n, s in scores.items() if s >= settings.indicator_min_score))
    frames = (
        _evidence(signals, timeline, rows)
        if signals is not None and rows
        else Evidence(
            frames=(timeline.time_of(first), timeline.time_of(max(first, end - 1))),
            metric="brightness",
        )
    )
    return IndicatorObservation(
        state=AnalyzerState.OK,
        confidence=1.0 if flags else 0.8,
        evidence=(frames,),
        flags=tuple(flags),
    )


# --- scenes and retakes ------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SceneGroup:
    shot_indices: tuple[int, ...]
    similarity: float


def group_scenes(
    vectors: Sequence[tuple[float, ...]], settings: MeaningSettings
) -> list[SceneGroup]:
    """Consecutive shots whose embeddings stay similar to their scene so far form one scene.

    A shot without an embedding starts a scene of its own (nothing says it belongs elsewhere).
    """
    groups: list[list[int]] = []
    for index, vector in enumerate(vectors):
        if groups and vector:
            members = [vectors[i] for i in groups[-1] if vectors[i]]
            if members and cosine(vector, mean_vector(members)) >= settings.scene_similarity:
                groups[-1].append(index)
                continue
        groups.append([index])
    result: list[SceneGroup] = []
    for group in groups:
        members = [vectors[i] for i in group if vectors[i]]
        centre = mean_vector(members)
        similarity = sum(cosine(m, centre) for m in members) / len(members) if members else 0.0
        result.append(SceneGroup(tuple(group), similarity))
    return result


def find_retakes(
    ids: Sequence[str],
    vectors: Sequence[tuple[float, ...]],
    framing: Sequence[tuple[float, float, float] | None],
    timeline: ShotSignals,
    starts: Sequence[int],
    settings: MeaningSettings,
) -> list[RetakeGroup]:
    """Shots that show the same framing and action again: retakes and near duplicates.

    ``framing`` per shot is (subject height, subject x, subject y) or ``None`` when unknown;
    two shots must also agree on it (when both know it) to count as the same take.
    """
    count = len(ids)
    parent = list(range(count))
    best: dict[int, float] = {}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(count):
        for j in range(i + 1, count):
            if not vectors[i] or not vectors[j]:
                continue
            similarity = cosine(vectors[i], vectors[j])
            if similarity < settings.retake_similarity:
                continue
            a, b = framing[i], framing[j]
            if (
                a is not None
                and b is not None
                and any(
                    abs(x - y) > settings.retake_framing_tolerance
                    for x, y in zip(a, b, strict=True)
                )
            ):
                continue
            parent[find(j)] = find(i)
            for k in (i, j):
                best[k] = max(best.get(k, 0.0), similarity)

    members: dict[int, list[int]] = {}
    for i in best:
        members.setdefault(find(i), []).append(i)
    groups: list[RetakeGroup] = []
    for shots in sorted(members.values(), key=min):
        shots.sort()
        similarity = min(best[i] for i in shots)
        kind = (
            RelationKind.NEAR_DUPLICATE
            if similarity >= settings.near_duplicate_similarity
            else RelationKind.RETAKE
        )
        marks = tuple(timeline.time_of(starts[i]) for i in shots[:3])
        groups.append(
            RetakeGroup(
                state=AnalyzerState.OK,
                confidence=min(1.0, similarity),
                evidence=(Evidence(frames=marks, metric="embedding_similarity"),),
                group_id=f"{kind.value}_{starts[shots[0]]:07d}",
                kind=kind,
                shot_ids=tuple(ids[i] for i in shots),
                similarity=similarity,
            )
        )
    return groups
