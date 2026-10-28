"""The validation boundary: is this analysis consistent before it is stored or handed on?

* ``ERROR``: the analysis is invalid and is never stored (a range outside its recording, a
  recommendation without evidence or text, duplicate ids, a runtime that does not add up, a
  preserved pause that is also asked to be tightened).
* ``WARNING``: recommendations that pull in opposite directions over the same material, a target
  that cannot be met.

Time ranges are checked against the recording's known length; when none is known the check is
reported as unresolved, never passed.
"""

from collections.abc import Mapping

from media_house.modules.pacing_intelligence.domain.facts import AssetEvidence
from media_house.modules.pacing_intelligence.domain.model import (
    Issue,
    PacingAnalysis,
    Recommendation,
)
from media_house.modules.pacing_intelligence.domain.values import (
    IssueKind,
    RecommendationType,
    Severity,
)

_TOLERANCE = 0.1


def validate(analysis: PacingAnalysis, evidence: Mapping[str, AssetEvidence]) -> list[Issue]:
    out: list[Issue] = []

    def add(kind: IssueKind, severity: Severity, detail: str, subject: str | None = None) -> None:
        out.append(Issue(issue_id="", kind=kind, severity=severity, detail=detail, subject=subject))

    spans = (
        [(p.passage_id, p.span) for p in analysis.passages]
        + [(p.pause_id, p.span) for p in analysis.pauses]
        + [(s.shot_id, s.span) for s in analysis.shots]
        + [(r.recommendation_id, r.target) for r in analysis.recommendations]
    )
    unchecked = 0
    for subject, span in spans:
        if span.end.value < span.start.value:
            add(
                IssueKind.INVALID_RANGE,
                Severity.ERROR,
                f"{subject}: range ends before it starts",
                subject,
            )
            continue
        known = evidence.get(span.asset_id)
        if known is None:
            add(
                IssueKind.UNKNOWN_REFERENCE,
                Severity.ERROR,
                f"{subject}: unknown recording {span.asset_id}",
                subject,
            )
        elif known.source.duration is None:
            unchecked += 1
        elif span.end.value > known.source.duration + _TOLERANCE:
            add(
                IssueKind.INVALID_RANGE,
                Severity.ERROR,
                f"{subject}: ends at {span.end.value:.2f} s, after the recording ends "
                f"({known.source.duration:.2f} s)",
                subject,
            )
    if unchecked:
        add(
            IssueKind.INCOMPLETE,
            Severity.UNRESOLVED,
            f"{unchecked} range(s) could not be checked against a recording length",
        )
    recs = analysis.recommendations
    ids = [r.recommendation_id for r in recs]
    if len(set(ids)) != len(ids):
        add(IssueKind.INCOMPLETE, Severity.ERROR, "duplicate recommendation ids")
    if sorted(r.priority for r in recs) != list(range(1, len(recs) + 1)):
        add(IssueKind.INCOMPLETE, Severity.ERROR, "recommendation priorities are not 1..n")
    sections = {s.section_id for s in analysis.sections}
    for rec in recs:
        _recommendation(rec, sections, add)
    _conflicts(recs, add)
    _runtime(analysis, add)
    return out


def _recommendation(rec: Recommendation, sections: set[str], add) -> None:  # type: ignore[no-untyped-def]
    subject = rec.recommendation_id
    if not rec.evidence:
        add(
            IssueKind.INCOMPLETE,
            Severity.ERROR,
            f"{subject}: a recommendation without evidence",
            subject,
        )
    if not (rec.observed and rec.proposal and rec.benefit):
        add(
            IssueKind.INCOMPLETE,
            Severity.ERROR,
            f"{subject}: observed, proposal and benefit are required",
            subject,
        )
    if not rec.reason_codes:
        add(IssueKind.INCOMPLETE, Severity.ERROR, f"{subject}: no reason code", subject)
    if rec.section_id is not None and sections and rec.section_id not in sections:
        add(
            IssueKind.UNKNOWN_REFERENCE,
            Severity.ERROR,
            f"{subject}: unknown section {rec.section_id}",
            subject,
        )
    if rec.delta_seconds is not None and not -1e6 < rec.delta_seconds < 1e6:
        add(
            IssueKind.INVALID_SCORE,
            Severity.ERROR,
            f"{subject}: implausible runtime change",
            subject,
        )
    if (
        rec.delta_seconds is not None
        and rec.delta_seconds < 0
        and rec.target.seconds + rec.delta_seconds < -_TOLERANCE
    ):
        add(
            IssueKind.DURATION,
            Severity.ERROR,
            f"{subject}: removes more time than the passage has",
            subject,
        )


def _conflicts(recs: tuple[Recommendation, ...], add) -> None:  # type: ignore[no-untyped-def]
    for i, a in enumerate(recs):
        for b in recs[i + 1 :]:
            if a.target.asset_id != b.target.asset_id:
                continue
            if not (
                a.target.start.value < b.target.end.value
                and b.target.start.value < a.target.end.value
            ):
                continue
            kinds = {a.type, b.type}
            if kinds == {RecommendationType.PRESERVE_PAUSE, RecommendationType.TIGHTEN_PAUSE}:
                add(
                    IssueKind.CONFLICT,
                    Severity.ERROR,
                    f"{a.recommendation_id} and {b.recommendation_id}: a pause is both preserved "
                    "and tightened",
                )
            elif (a.delta_seconds or 0) * (
                b.delta_seconds or 0
            ) < 0 and a.target.start != b.target.start:
                add(
                    IssueKind.CONFLICT,
                    Severity.WARNING,
                    f"{a.recommendation_id} and {b.recommendation_id} pull in opposite directions "
                    "over the same material",
                    a.recommendation_id,
                )


def _runtime(analysis: PacingAnalysis, add) -> None:  # type: ignore[no-untyped-def]
    d = analysis.duration
    if d.current_seconds is None or d.proposed_seconds is None:
        return
    delta = sum(r.delta_seconds or 0.0 for r in analysis.recommendations)
    if abs(d.current_seconds + delta - d.proposed_seconds) > 0.01 + 0.001 * len(
        analysis.recommendations
    ):
        add(
            IssueKind.DURATION,
            Severity.ERROR,
            "the proposed runtime is not the current runtime plus the recommendations' changes",
        )
    if d.proposed_seconds < 0:
        add(IssueKind.DURATION, Severity.ERROR, "the proposed runtime is negative")
