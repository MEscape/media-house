"""The analysis checks its own result before it is stored or handed to a consumer.

``ERROR`` means the result is inconsistent (a span that does not contain its boundary, an issue
for a cut that is not in the sequence, a recommendation that points at an unavailable repair, an
informational issue marked as surfaced): such an analysis is never stored.
"""

from collections.abc import Iterator
from fractions import Fraction

from media_house.modules.continuity_intelligence.domain.model import (
    ContinuityAnalysis,
    Issue,
    ValidationIssue,
)
from media_house.modules.continuity_intelligence.domain.values import (
    Availability,
    Level,
    Severity,
)


def _f(value: object) -> Fraction:
    return Fraction(value.numerator, value.denominator)  # type: ignore[attr-defined]


def validate(analysis: ContinuityAnalysis) -> list[ValidationIssue]:
    problems = list(_problems(analysis))
    return [
        ValidationIssue(issue_id=f"v{n:03d}", severity=severity, subject=subject, detail=detail)
        for n, (severity, subject, detail) in enumerate(problems, start=1)
    ]


def _problems(a: ContinuityAnalysis) -> Iterator[tuple[Severity, str, str]]:
    ids = [i.issue_id for i in a.issues]
    if len(set(ids)) != len(ids):
        yield Severity.ERROR, "issues", "issue ids are not unique"
    known = {t.transition_id for t in a.transitions}
    if len(known) != len(a.transitions):
        yield Severity.ERROR, "transitions", "transition ids are not unique"
    for u in a.unresolved:
        if u.transition_id not in known:
            yield (
                Severity.ERROR,
                u.transition_id,
                "an unresolved item names a cut that is not judged",
            )
    for p in a.compatible_pairs:
        if p.transition_id not in known:
            yield (
                Severity.ERROR,
                p.transition_id,
                "a compatible pair names a cut that is not judged",
            )
        if not 0.0 <= p.compatibility <= 1.0:
            yield Severity.ERROR, p.transition_id, "compatibility is outside [0, 1]"
    for issue in a.issues:
        yield from _issue(issue, known)
    if a.metrics.issues != len(a.issues) or a.metrics.transitions != len(a.transitions):
        yield Severity.ERROR, "metrics", "the metrics disagree with the result"


def _issue(i: Issue, known: set[str]) -> Iterator[tuple[Severity, str, str]]:
    who = i.issue_id
    if i.transition_id is not None and i.transition_id not in known:
        yield Severity.ERROR, who, "the issue names a cut that is not in the sequence"
    for span in i.spans:
        start, end, at = _f(span.start), _f(span.end), _f(span.boundary)
        if end < start or not start <= at <= end:
            yield Severity.ERROR, who, f"a span of {span.clip_id} does not contain its boundary"
    if i.level is Level.INFO and i.surfaced:
        yield Severity.ERROR, who, "an informational issue is marked as surfaced"
    ranks = [r.rank for r in i.repairs]
    if ranks != list(range(1, len(ranks) + 1)):
        yield Severity.ERROR, who, "repair ranks are not 1..n in order"
    if i.recommended is not None:
        match = next((r for r in i.repairs if r.kind is i.recommended), None)
        if match is None or match.availability is Availability.UNAVAILABLE:
            yield Severity.ERROR, who, "the recommended repair is missing or unavailable"
    for share in (i.detectability_confidence, i.intentional_style_likelihood):
        if share is not None and not 0.0 <= share <= 1.0:
            yield Severity.ERROR, who, "a confidence or style estimate is outside [0, 1]"
    if not i.evidence:
        yield Severity.WARNING, who, "the issue names no evidence"
