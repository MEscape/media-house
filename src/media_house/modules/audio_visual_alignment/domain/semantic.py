"""Which shots relate to a spoken passage, and how specific the link is.

Grades say what KIND of evidence links them (``EvidenceGrade``); none says the picture proves the
sentence. There is no grade for a demonstrated action and none for a contradiction: nothing
upstream measures either, and a missing match is only ever "none found".

Terms are plain word forms, matched exactly after case folding. Object labels and generated
descriptions are English, so for speech in another language a match can only come through text
read off the screen or a shared word; that limit is reported rather than hidden. Embedding
similarities, when an engine supplied them, add ``SEMANTIC_CANDIDATE`` for a shot that stands out
from the other shots of the same passage (a z-score, not an absolute threshold).
"""

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace

from media_house.modules.audio_visual_alignment.domain.clock import Clock
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.inputs import (
    ContentFacts,
    Passage,
    ShotFacts,
)
from media_house.modules.audio_visual_alignment.domain.model import (
    Evidence,
    Scores,
    TextMatch,
    VideoRef,
)
from media_house.modules.audio_visual_alignment.domain.scoring import (
    combine,
    coverage,
    gap,
    overlap,
)
from media_house.modules.audio_visual_alignment.domain.values import EvidenceGrade, EvidenceKind

_WORDS = re.compile(r"\w+", re.UNICODE)
_SNIPPET = 160
#: Words in more than this share of the passages carry no information about one passage.
_COMMON_SHARE = 0.3
_MIN_PASSAGES_FOR_COMMON = 10
_GRADE_WEIGHT: Mapping[EvidenceGrade, float] = {
    EvidenceGrade.OBSERVED_TEXT: 0.9,
    EvidenceGrade.DESCRIBED: 0.6,
    EvidenceGrade.ENTITY_LABEL: 0.5,
}
_ORDER = (
    EvidenceGrade.OBSERVED_TEXT,
    EvidenceGrade.DESCRIBED,
    EvidenceGrade.ENTITY_LABEL,
    EvidenceGrade.SEMANTIC_CANDIDATE,
    EvidenceGrade.NONE_FOUND,
    EvidenceGrade.UNKNOWN,
)


def words(text: str, minimum: int) -> set[str]:
    return {w for w in _WORDS.findall(text.casefold()) if len(w) >= minimum and not w.isdigit()}


def passage_terms(passages: Sequence[Passage], profile: AlignmentProfile) -> dict[str, set[str]]:
    """Distinctive words per passage; words common to many passages are dropped."""
    per_passage = {p.passage_id: words(p.text, profile.min_term_length) for p in passages}
    if len(passages) >= _MIN_PASSAGES_FOR_COMMON:
        frequency = Counter(w for terms in per_passage.values() for w in terms)
        common = {w for w, n in frequency.items() if n / len(passages) > _COMMON_SHARE}
        per_passage = {pid: terms - common for pid, terms in per_passage.items()}
    return per_passage


def _similarity_z(
    passage_id: str, similarities: Mapping[tuple[str, str], float], minimum: int
) -> dict[str, tuple[float, float]]:
    """``{shot_id: (similarity, z)}`` for one passage; empty when too few shots were compared."""
    values = {shot: v for (pid, shot), v in similarities.items() if pid == passage_id}
    if len(values) < minimum:
        return {}
    mean = sum(values.values()) / len(values)
    deviation = math.sqrt(sum((v - mean) ** 2 for v in values.values()) / len(values))
    if deviation <= 0:
        return {}
    return {shot: (v, (v - mean) / deviation) for shot, v in values.items()}


def text_matches(
    content: ContentFacts,
    shots: Sequence[ShotFacts],
    clock: Clock,
    similarities: Mapping[tuple[str, str], float],
    profile: AlignmentProfile,
) -> tuple[TextMatch, ...]:
    terms = passage_terms(content.passages, profile)
    spans = [(shot, clock.video_interval(shot.range)) for shot in shots]
    out: list[TextMatch] = []
    for passage in content.passages:
        start, end = clock.audio(passage.start), clock.audio(passage.end)
        z_by_shot = _similarity_z(passage.passage_id, similarities, profile.semantic_min_shots)
        wanted = terms[passage.passage_id]
        candidates: list[TextMatch] = []
        for shot, (s0, s1) in spans:
            distance = gap(start, end, s0, s1)
            before = s1 <= start and start - s1 <= profile.text_lead_seconds
            after = s0 >= end and s0 - end <= profile.text_lag_seconds
            inside = overlap(start, end, s0, s1) > 0
            if not (inside or before or after):
                continue
            candidates.append(
                _match(passage, wanted, shot, (s0, s1), (start, end), distance, z_by_shot, profile)
            )
        matched = [
            m
            for m in candidates
            if m.grade not in (EvidenceGrade.NONE_FOUND, EvidenceGrade.UNKNOWN)
        ]
        matched.sort(key=lambda m: (_ORDER.index(m.grade), m.gap_seconds, m.shot.shot_id))
        kept = matched[: profile.max_candidates]
        kept_ids = {m.shot.shot_id for m in kept}
        unmatched = [
            m
            for m in candidates
            if m.shot.shot_id not in kept_ids and m.gap_seconds == 0.0 and m not in matched
        ]
        for rank, m in enumerate((*kept, *sorted(unmatched, key=lambda m: m.shot.shot_id)), 1):
            out.append(replace(m, rank=rank))
    return tuple(out)


def _match(
    passage: Passage,
    wanted: set[str],
    shot: ShotFacts,
    shot_span: tuple[float, float],
    passage_span: tuple[float, float],
    distance: float,
    z_by_shot: Mapping[str, tuple[float, float]],
    profile: AlignmentProfile,
) -> TextMatch:
    minimum = profile.min_term_length
    on_screen = (
        set().union(*(words(t, minimum) for t in shot.screen_text)) if shot.screen_text else set()
    )
    described = words(shot.description, minimum) if shot.description else set()
    labels = {w for label in shot.objects for w in words(label, minimum)}
    evidence: list[Evidence] = []
    hits_text, hits_described, hits_label = wanted & on_screen, wanted & described, wanted & labels
    similarity = z = None
    if shot.shot_id in z_by_shot:
        similarity, z = z_by_shot[shot.shot_id]

    if hits_text:
        grade, found = EvidenceGrade.OBSERVED_TEXT, hits_text
        kind = EvidenceKind.TEXT_TERM
    elif hits_described:
        grade, found = EvidenceGrade.DESCRIBED, hits_described
        kind = EvidenceKind.TEXT_TERM
    elif hits_label:
        grade, found = EvidenceGrade.ENTITY_LABEL, hits_label
        kind = EvidenceKind.OBJECT_LABEL
    elif z is not None and z >= profile.semantic_min_z:
        grade, found, kind = (
            EvidenceGrade.SEMANTIC_CANDIDATE,
            set(),
            EvidenceKind.EMBEDDING_SIMILARITY,
        )
    elif on_screen or described or labels or z is not None:
        grade, found, kind = EvidenceGrade.NONE_FOUND, set(), EvidenceKind.TEXT_TERM
    else:
        grade, found, kind = EvidenceGrade.UNKNOWN, set(), EvidenceKind.TEXT_TERM

    for term in sorted(found):
        evidence.append(
            Evidence(kind=kind, source="video_intelligence", ref=shot.shot_id, detail=term)
        )
    if grade is EvidenceGrade.SEMANTIC_CANDIDATE and similarity is not None:
        evidence.append(
            Evidence(
                kind=EvidenceKind.EMBEDDING_SIMILARITY,
                source="video_intelligence",
                ref=shot.shot_id,
                value=similarity,
                detail=f"z={z:.2f} against the other shots" if z is not None else "",
            )
        )
    shared = overlap(*passage_span, *shot_span)
    weight = _GRADE_WEIGHT.get(grade)
    if grade is EvidenceGrade.SEMANTIC_CANDIDATE and z is not None:
        weight = min(0.6, 0.6 * z / (2 * profile.semantic_min_z))
    horizon = max(profile.text_lead_seconds, profile.text_lag_seconds)
    proximity = 1.0 if distance == 0 else max(0.0, 1.0 - distance / horizon) if horizon else 0.0
    strength = combine([weight, proximity]) if weight is not None else None
    evidence.append(
        Evidence(
            kind=EvidenceKind.TEMPORAL_OVERLAP,
            source="audio_visual_alignment",
            ref=shot.shot_id,
            value=shared,
            detail="overlap" if distance == 0 else f"{distance:.2f}s apart",
        )
    )
    text = passage.text if len(passage.text) <= _SNIPPET else passage.text[: _SNIPPET - 1] + "…"
    return TextMatch(
        match_id=f"tm_{passage.passage_id}_{shot.shot_id}",
        passage_id=passage.passage_id,
        start=passage_span[0],
        end=passage_span[1],
        text=text,
        shot=VideoRef(shot_id=shot.shot_id, range=shot.range),
        gap_seconds=distance,
        grade=grade,
        terms=tuple(sorted(found)),
        rank=0,
        similarity=similarity,
        similarity_z=z,
        scores=Scores(
            overlap_seconds=shared,
            coverage_audio=coverage(shared, passage_span[1] - passage_span[0]),
            coverage_video=coverage(shared, shot_span[1] - shot_span[0]),
            strength=strength,
        ),
        evidence=tuple(evidence),
    )
