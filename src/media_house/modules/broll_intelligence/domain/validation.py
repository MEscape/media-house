"""The analysis checks its own result before it is stored or handed to a consumer.

``ERROR`` issues mean the result is inconsistent (a range outside its shot, a candidate marked as
satisfying a need with evidence that contradicts it, a gap without a missing recommendation): such
an analysis is never stored. ``WARNING`` issues are kept for a person to read. The checks guard the
claims that could mislead a viewer: a contradicted or unproven candidate must never be presented
as satisfying a need, and a recommendation must name the original asset and a range inside the
shot it came from.
"""

from collections.abc import Iterator
from fractions import Fraction

from media_house.modules.broll_intelligence.domain.model import (
    BrollAnalysis,
    Candidate,
    Issue,
    Recommendation,
    SourceInterval,
)
from media_house.modules.broll_intelligence.domain.values import (
    AssetVia,
    EvidenceRole,
    Identity,
    NeedKind,
    Severity,
    Support,
)

_TOLERANCE = Fraction(1, 1000)


def _f(value: object) -> Fraction:
    return Fraction(value.numerator, value.denominator)  # type: ignore[attr-defined]


def validate(analysis: BrollAnalysis) -> list[Issue]:
    problems = list(_problems(analysis))
    return [
        Issue(issue_id=f"iss:{n:03d}", severity=severity, subject=subject, detail=detail)
        for n, (severity, subject, detail) in enumerate(problems, start=1)
    ]


def _problems(analysis: BrollAnalysis) -> Iterator[tuple[Severity, str, str]]:
    ids = [r.recommendation_id for r in analysis.recommendations]
    if len(set(ids)) != len(ids):
        yield Severity.ERROR, "recommendations", "recommendation ids are not unique"
    gap_ids = {g.gap_id for g in analysis.gaps}
    if len(gap_ids) != len(analysis.gaps):
        yield Severity.ERROR, "gaps", "gap ids are not unique"
    recommended = {r.target_id for r in analysis.recommendations}
    for change in analysis.no_change:
        if change.target_id in recommended:
            yield Severity.ERROR, change.target_id, "a passage is both recommended and left alone"
    for gap in analysis.gaps:
        rec = next(
            (r for r in analysis.recommendations if r.recommendation_id == gap.recommendation_id),
            None,
        )
        if rec is None:
            yield Severity.ERROR, gap.gap_id, "a gap names a recommendation that does not exist"
        elif not rec.missing_coverage:
            yield Severity.ERROR, gap.gap_id, "a gap is reported for a passage that has coverage"
        elif rec.gap_id != gap.gap_id:
            yield Severity.ERROR, gap.gap_id, "the recommendation does not point back at its gap"
        if _f(gap.end) < _f(gap.start):
            yield Severity.ERROR, gap.gap_id, "a gap ends before it starts"
    for rec in analysis.recommendations:
        yield from _recommendation(rec, gap_ids)


def _recommendation(rec: Recommendation, gap_ids: set[str]) -> Iterator[tuple[Severity, str, str]]:
    who = rec.recommendation_id
    placement = rec.placement
    if _f(placement.target_end) < _f(placement.target_start):
        yield Severity.ERROR, who, "the passage ends before it starts"
    if placement.insert_min_seconds > placement.insert_max_seconds:
        yield Severity.ERROR, who, "the insert length range is inverted"
    satisfying = [c for c in rec.candidates if c.support is Support.SATISFIES]
    if rec.missing_coverage == bool(satisfying):
        yield Severity.ERROR, who, "missing_coverage disagrees with the candidates"
    if rec.gap_id is not None and rec.gap_id not in gap_ids:
        yield Severity.ERROR, who, "the recommendation names a gap that does not exist"
    ranks = [c.rank for c in rec.candidates]
    if ranks != list(range(1, len(ranks) + 1)):
        yield Severity.ERROR, who, "candidate ranks are not 1..n in order"
    seen_support = [c.support for c in rec.candidates]
    if seen_support != sorted(seen_support, key=lambda s: 0 if s is Support.SATISFIES else 1):
        yield Severity.ERROR, who, "an illustrative candidate is ranked above a satisfying one"
    seen: set[tuple[str, str, Fraction, Fraction]] = set()
    for c in [*rec.candidates, *rec.rejected]:
        yield from _candidate(rec, c)
        key = (c.asset_id, c.shot_id, _f(c.source.start), _f(c.source.end))
        if c.rank > 0:
            if key in seen:
                yield Severity.ERROR, c.candidate_id, "the same range is recommended twice"
            seen.add(key)


def _candidate(rec: Recommendation, c: Candidate) -> Iterator[tuple[Severity, str, str]]:
    who = c.candidate_id
    if not _inside(c.source, c.shot_range) or _f(c.source.end) <= _f(c.source.start):
        yield Severity.ERROR, who, "the source range is empty or lies outside its shot"
    if _f(c.shot_range.end) <= _f(c.shot_range.start):
        yield Severity.ERROR, who, "the shot has no length"
    if (c.via is AssetVia.ORIGINAL) != (c.asset_id == c.analyzed_asset_id):
        yield Severity.ERROR, who, "the asset and the analysed version disagree about lineage"
    if c.support is Support.SATISFIES:
        if c.role is EvidenceRole.CONTRADICTORY or c.identity is Identity.CONFLICT:
            yield Severity.ERROR, who, "a contradicted candidate is marked as satisfying the need"
        if rec.need in (NeedKind.REQUIRED_EVIDENCE, NeedKind.DEMONSTRATION) and c.role in (
            EvidenceRole.UNCERTAIN,
            EvidenceRole.GENERIC_ILLUSTRATION,
        ):
            yield (
                Severity.ERROR,
                who,
                "a candidate with no specific evidence satisfies a need that requires it",
            )
        if c.rank == 0:
            yield Severity.ERROR, who, "a satisfying candidate is not ranked"
    elif c.rank > 0 and c.support is Support.REJECTED:
        yield Severity.ERROR, who, "a refused candidate is ranked"
    if c.support is Support.SATISFIES and c.overall is None:
        yield Severity.WARNING, who, "no ranking score could be stated for a satisfying candidate"


def _inside(inner: SourceInterval, outer: SourceInterval) -> bool:
    return (
        _f(inner.start) >= _f(outer.start) - _TOLERANCE
        and _f(inner.end) <= _f(outer.end) + _TOLERANCE
    )
