"""The main message, the premise, and the ways the story could open.

Candidates are real passages of the recording, each with the strategy that makes it a candidate and
its own measured strengths and risks. A strong-sounding line is only recommended when it stands
on its own and nothing in the recording undercuts it; otherwise the story opens as recorded.
"""

from dataclasses import dataclass

from media_house.modules.story_architecture.domain.facts import AssetFacts, SegmentFacts
from media_house.modules.story_architecture.domain.model import (
    Criterion,
    OpeningCandidate,
    SetupPayoffLink,
    Statement,
)
from media_house.modules.story_architecture.domain.profile import FormatProfile, Thresholds
from media_house.modules.story_architecture.domain.sections import Draft, excerpt, terms
from media_house.modules.story_architecture.domain.structure import Index
from media_house.modules.story_architecture.domain.values import (
    LinkState,
    Method,
    OpeningStrategy,
    Origin,
)

#: How much each criterion counts. Criteria with no value are left out and the rest renormalised.
_WEIGHTS: dict[str, float] = {
    "clarity": 0.30,
    "relevance": 0.20,
    "promise_alignment": 0.20,
    "credibility": 0.15,
    "curiosity": 0.15,
}
_ORIGIN = {o.value: o for o in Origin}


@dataclass(frozen=True, slots=True)
class Premise:
    statement: Statement
    asset_id: str
    segment: SegmentFacts
    section_id: str
    #: Seconds from the start of the first recording to this passage; ``None`` if untimed.
    delay_seconds: float | None


def main_message(assets: tuple[AssetFacts, ...]) -> Statement | None:
    """The point of the recording as upstream summarised it; nothing is written here."""
    for asset in assets:
        if asset.summary:
            text, origin, units = asset.summary
            return Statement(
                text=text,
                origin=_ORIGIN.get(origin, Origin.INFERENCE),
                method=Method.OBSERVED,
                unit_ids=units,
            )
    for asset in assets:
        if asset.takeaways:
            text, origin, units = asset.takeaways[0]
            return Statement(
                text=text,
                origin=_ORIGIN.get(origin, Origin.INFERENCE),
                method=Method.OBSERVED,
                unit_ids=units,
            )
    best: tuple[float, SegmentFacts] | None = None
    for asset in assets:
        for segment in asset.segments:
            if (
                segment.has_role("main_point", "conclusion")
                and segment.importance is not None
                and (best is None or segment.importance > best[0])
            ):
                best = (segment.importance, segment)
    if best is None:
        return None
    return Statement(
        text=excerpt(best[1].text),
        origin=Origin.DIRECT,
        method=Method.HEURISTIC,
        unit_ids=best[1].unit_ids,
    )


def find_premise(index: Index, thresholds: Thresholds) -> Premise | None:
    """The first passage that states the point: a ``main_point``, else a clearly central one."""
    chosen: tuple[Draft, SegmentFacts] | None = None
    for draft in index.drafts:
        for segment in draft.segments:
            if segment.has_role("main_point"):
                chosen = (draft, segment)
                break
        if chosen:
            break
    if chosen is None:
        for draft in index.drafts:
            for segment in draft.segments:
                if (segment.importance or 0.0) >= thresholds.essential_importance:
                    chosen = (draft, segment)
                    break
            if chosen:
                break
    if chosen is None:
        return None
    draft, segment = chosen
    first = index.drafts[0].first
    delay = (
        round(segment.start - first.start, 3)
        if segment.start is not None
        and first.start is not None
        and draft.asset_id == index.drafts[0].asset_id
        else None
    )
    statement = Statement(
        text=segment.summary or excerpt(segment.text),
        origin=_ORIGIN.get(segment.summary_origin or "", Origin.DIRECT)
        if segment.summary
        else Origin.DIRECT,
        method=Method.OBSERVED if segment.summary else Method.HEURISTIC,
        unit_ids=segment.unit_ids,
    )
    return Premise(statement, draft.asset_id, segment, draft.section_id, delay)


def _overlap(candidate: str, message: str | None) -> float | None:
    if not message:
        return None
    a, b = terms(candidate), terms(message)
    if not a or not b:
        return None
    return round(len(a & b) / min(len(a), len(b)), 4)


def _score(criteria: tuple[Criterion, ...]) -> float | None:
    known = [(c.value, _WEIGHTS[c.name]) for c in criteria if c.value is not None]
    if not known:
        return None
    total = sum(w for _, w in known)
    return round(sum(v * w for v, w in known) / total, 4)


def build_candidates(
    index: Index,
    *,
    message: Statement | None,
    premise: Premise | None,
    links: tuple[SetupPayoffLink, ...],
    credibility: dict[tuple[str, str], tuple[float, str]],
    profile: FormatProfile,
    thresholds: Thresholds,
) -> tuple[OpeningCandidate, ...]:
    """Distinct ways to open, best first. The natural start is always among them."""
    picks: list[tuple[OpeningStrategy, Draft, SegmentFacts]] = []
    first = index.drafts[0]
    picks.append((OpeningStrategy.NATURAL_START, first, first.first))

    if premise is not None:
        picks.append(
            (OpeningStrategy.PREMISE_FIRST, index.draft(premise.section_id), premise.segment)
        )

    hooks = [
        (s.hook or 0.0, d, s)
        for d in index.drafts
        for s in d.segments
        if s.hook is not None
        and s.hook >= thresholds.hook_min
        and (s.standalone is None or s.standalone >= thresholds.standalone_min)
    ]
    if hooks:
        _, d, s = max(hooks, key=lambda h: (h[0], -h[1].source_index))
        picks.append((OpeningStrategy.STRONGEST_HOOK, d, s))

    answered = {k.setup.segment_ids[0] for k in links if k.state is LinkState.RESOLVED}
    for d in index.drafts:
        found = next(
            (
                s
                for s in d.segments
                if s.segment_id in answered and "rhetorical_question" not in s.rhetoric
            ),
            None,
        )
        if found is not None:
            picks.append((OpeningStrategy.QUESTION_FIRST, d, found))
            break

    for d in reversed(index.drafts):
        found = next(
            (s for s in reversed(d.segments) if s.has_role("conclusion", "resolution")), None
        )
        if found is not None:
            picks.append((OpeningStrategy.RESULT_FIRST, d, found))
            break

    chosen: dict[tuple[str, str], tuple[OpeningStrategy, Draft, SegmentFacts, list[str]]] = {}
    for strategy, draft, segment in picks:
        key = (draft.asset_id, segment.segment_id)
        if key in chosen:
            chosen[key][3].append(f"also qualifies as {strategy.value.replace('_', ' ')}")
        else:
            chosen[key] = (strategy, draft, segment, [])

    qualified = {
        (a.asset_id, i.segment_id) for a in index.assets for i in a.items if i.qualified_by
    }
    built: list[tuple[float | None, int, OpeningCandidate]] = []
    for strategy, draft, segment, notes in chosen.values():
        candidate = _candidate(
            index,
            strategy,
            draft,
            segment,
            notes,
            message=message,
            premise=premise,
            credibility=credibility,
            qualified=qualified,
        )
        if candidate is not None:
            position = draft.source_index * 100_000 + draft.segments.index(segment)
            built.append((candidate.score, position, candidate))
    built.sort(key=lambda b: (b[0] is None, -(b[0] or 0.0), b[1]))
    return tuple(
        OpeningCandidate(
            candidate_id=f"op_{i + 1}",
            strategy=c.strategy,
            source=c.source,
            excerpt=c.excerpt,
            section_id=c.section_id,
            criteria=c.criteria,
            score=c.score,
            rank=i + 1,
            context_required=c.context_required,
            risks=c.risks,
            delay_to_premise_seconds=c.delay_to_premise_seconds,
            notes=c.notes,
        )
        for i, (_, _, c) in enumerate(_keep_natural(built, profile.max_openings))
    )


def _keep_natural(
    built: list[tuple[float | None, int, OpeningCandidate]], limit: int
) -> list[tuple[float | None, int, OpeningCandidate]]:
    """The first ``limit`` candidates, never losing the natural start (the safe way to open)."""
    top = built[:limit]
    if any(c.strategy is OpeningStrategy.NATURAL_START for _, _, c in top):
        return top
    natural = next((b for b in built if b[2].strategy is OpeningStrategy.NATURAL_START), None)
    return [*top[:-1], natural] if natural is not None else top


def _candidate(
    index: Index,
    strategy: OpeningStrategy,
    draft: Draft,
    segment: SegmentFacts,
    notes: list[str],
    *,
    message: Statement | None,
    premise: Premise | None,
    credibility: dict[tuple[str, str], tuple[float, str]],
    qualified: set[tuple[str, str | None]],
) -> OpeningCandidate | None:
    asset_id = draft.asset_id
    ref = index.ref_of_segment(asset_id, segment.segment_id)
    if ref is None:
        return None
    cred_value, cred_reason = credibility.get((asset_id, segment.segment_id), (1.0, ""))
    curiosity: float | None = segment.hook
    curiosity_reason = "" if curiosity is not None else "no hook score upstream"
    if strategy is OpeningStrategy.QUESTION_FIRST:
        curiosity = max(curiosity or 0.0, 0.5)
        curiosity_reason = "raises a question the recording answers later"
    criteria = (
        Criterion(
            name="clarity",
            value=segment.standalone,
            reason="" if segment.standalone is not None else "no standalone score upstream",
        ),
        Criterion(
            name="relevance",
            value=segment.importance,
            reason="" if segment.importance is not None else "no importance score upstream",
        ),
        Criterion(
            name="promise_alignment",
            value=_overlap(segment.text, message.text if message else None),
            reason="" if message else "no main message to compare with",
        ),
        Criterion(name="credibility", value=cred_value, reason=cred_reason),
        Criterion(name="curiosity", value=curiosity, reason=curiosity_reason),
    )
    own = set(segment.unit_ids)
    missing = tuple(u for u in segment.requires_context if u not in own)
    context = index.ref_of_units(asset_id, missing) if missing else None
    risks: list[str] = []
    if context is not None:
        risks.append("needs earlier sentences to be understood")
    if (asset_id, segment.segment_id) in qualified:
        risks.append("the claim it makes is qualified in later sentences")
    if cred_value < 1.0 and cred_reason:
        risks.append(cred_reason)
    if strategy is not OpeningStrategy.NATURAL_START and segment.standalone is None:
        risks.append("whether it stands alone is unknown")
    delay = premise.delay_seconds if premise is not None else None
    return OpeningCandidate(
        candidate_id="",
        strategy=strategy,
        source=ref,
        excerpt=excerpt(segment.text),
        section_id=draft.section_id,
        criteria=criteria,
        score=_score(criteria),
        rank=0,
        context_required=context,
        risks=tuple(risks),
        delay_to_premise_seconds=delay,
        notes=tuple(notes),
    )


def recommend(candidates: tuple[OpeningCandidate, ...]) -> OpeningCandidate | None:
    """The best-ranked candidate with no risk, else the natural start, else the best ranked."""
    safe = next((c for c in candidates if not c.risks), None)
    if safe is not None:
        return safe
    natural = next((c for c in candidates if c.strategy is OpeningStrategy.NATURAL_START), None)
    return natural or (candidates[0] if candidates else None)
