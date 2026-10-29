"""Check an analysis before it is stored or handed to a consumer: a crop that is not valid must
never get past here.

A crop is already guaranteed to lie inside the picture by its own constructor; what only the whole
analysis can check is that its aspect matches the target for the source it was planned on, that
keyframes lie inside the range in strict time order, and that the claims a recommendation makes
about itself agree with each other. Any ``ERROR`` means the analyser has a bug: the analysis is
never stored.
"""

from collections.abc import Callable
from itertools import pairwise

from media_house.modules.reframing_intelligence.domain.geometry import (
    SourceGeometry,
    crop_problems,
)
from media_house.modules.reframing_intelligence.domain.model import (
    Issue,
    Recommendation,
    ReframingAnalysis,
)
from media_house.modules.reframing_intelligence.domain.values import (
    Feasibility,
    IssueKind,
    Severity,
    Strategy,
)


def validate_analysis(analysis: ReframingAnalysis) -> list[Issue]:
    geometries = {
        s.asset_id: SourceGeometry(
            s.display_width,
            s.display_height,
            s.sample_width,
            s.sample_height,
            s.rotation,
            s.geometry_exact,
        )
        for s in analysis.sources
    }
    found: list[Issue] = []

    def error(detail: str, subject: str | None = None) -> None:
        found.append(
            Issue(
                kind=IssueKind.INVALID_GEOMETRY,
                severity=Severity.ERROR,
                detail=detail,
                subject=subject,
            )
        )

    ids = [r.recommendation_id for r in analysis.recommendations]
    if len(set(ids)) != len(ids):
        error("recommendation ids are not unique")
    for r in analysis.recommendations:
        geometry = geometries.get(r.asset_id)
        if geometry is None:
            error(f"{r.recommendation_id}: its recording is not among the sources", r.asset_id)
            continue
        _recommendation(r, geometry, analysis, error)
    known = set(ids)
    for p in analysis.pairs:
        if p.first_id not in known or p.second_id not in known:
            error(f"a pair names an unknown recommendation ({p.first_id}, {p.second_id})")
    return found


def _recommendation(
    r: Recommendation,
    geometry: SourceGeometry,
    analysis: ReframingAnalysis,
    error: Callable[..., None],
) -> None:
    where = r.recommendation_id
    if r.end.value <= r.start.value:
        error(f"{where}: the range is empty or reversed", r.asset_id)
    times = [k.time.value for k in r.keyframes]
    if any(b <= a for a, b in pairwise(times)):
        error(f"{where}: keyframes are not in strict time order")
    if times and (times[0] < r.start.value or times[-1] > r.end.value):
        error(f"{where}: a keyframe lies outside the range [start, end]")
    for k in r.keyframes:
        for problem in crop_problems(k.crop, geometry, analysis.target):
            error(f"{where}: {problem}")
    for a in r.alternatives:
        for crop in a.crops:
            for problem in crop_problems(crop, geometry, analysis.target):
                error(f"{where}: alternative {a.strategy.value}: {problem}")
    if r.executable and not (r.strategy.has_crop and r.feasibility is not Feasibility.UNCERTAIN):
        error(f"{where}: marked executable without a usable crop")
    if r.executable and r.feasibility is Feasibility.IMPOSSIBLE:
        error(f"{where}: an impossible crop is marked executable")
    if r.strategy in (Strategy.UNREFRAMEABLE, Strategy.FILL_TREATMENT) and (
        r.feasibility is not Feasibility.IMPOSSIBLE
    ):
        error(f"{where}: {r.strategy.value} must be impossible, not {r.feasibility.value}")
