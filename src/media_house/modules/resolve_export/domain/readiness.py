"""Whether the final export may be authorized: the conditions of the final review, as rules.

A hard blocker is a fact that makes an export wrong (a critical problem, a file that is offline, a
report of another version): it is fixed, never overridden. A soft blocker (a high-severity issue,
a problem the final polish found) stops the export until it is resolved or a person knowingly
accepts it. Nothing here reads a score: a high automated score is never a reason to export.
"""

from dataclasses import dataclass

from media_house.modules.resolve_export.domain.values import BlockerKind


@dataclass(frozen=True, slots=True)
class IssueFact:
    issue_id: str
    code: str
    severity: str
    description: str
    blocks_export: bool


@dataclass(frozen=True, slots=True)
class PolishProblemFact:
    code: str
    message: str
    #: The quality issue it restates (empty: a finding of the polish itself).
    reference: str
    start_frame: int | None


@dataclass(frozen=True, slots=True)
class ReadinessFacts:
    timeline_hash: str
    #: The version the review is at.
    review_hash: str
    quality_report_id: str
    quality_hash: str
    quality_issues: tuple[IssueFact, ...]
    polish_report_id: str
    polish_hash: str
    polish_readiness: str
    polish_problems: tuple[PolishProblemFact, ...]
    mix_state: str
    mix_errors: tuple[str, ...]
    offline_sources: tuple[str, ...]
    #: ``None``: the timeline was never carried out in Resolve.
    resolve_hash: str | None
    resolve_conflicts: tuple[str, ...]
    profile_known: bool
    profile_problems: tuple[str, ...]
    high_issues_block: bool


@dataclass(frozen=True, slots=True)
class Blocker:
    #: Stable id: the issue's own id, or ``polish:<code>:<frame>`` for the polish's.
    blocker_id: str
    kind: BlockerKind
    message: str
    hard: bool


@dataclass(frozen=True, slots=True)
class ExportReadiness:
    hard: tuple[Blocker, ...]
    soft: tuple[Blocker, ...]
    warnings: tuple[str, ...]

    @property
    def can_authorize(self) -> bool:
        """Nothing that can never be overridden stands in the way (a soft blocker may still need
        a person's explicit override, which the review checks)."""
        return not self.hard

    @property
    def clear(self) -> bool:
        return not self.hard and not self.soft


_HARD_POLISH = frozenset(
    {
        "validation.not_current",
        "validation.no_quality_report",
        "unfinished.element",
        "validation.rejected_applied",
    }
)


def assess(facts: ReadinessFacts) -> ExportReadiness:
    hard: list[Blocker] = []
    soft: list[Blocker] = []
    warnings: list[str] = []
    if not facts.quality_report_id:
        hard.append(
            Blocker(
                "quality:none",
                BlockerKind.NO_QUALITY_REPORT,
                "Quality control has not checked this version.",
                True,
            )
        )
    elif facts.quality_hash != facts.timeline_hash:
        hard.append(
            Blocker(
                "quality:stale",
                BlockerKind.NOT_CURRENT,
                "The quality report is of another version of the timeline.",
                True,
            )
        )
    if not facts.polish_report_id:
        hard.append(
            Blocker(
                "polish:none",
                BlockerKind.NO_POLISH_REPORT,
                "The final polish has not been run on this version.",
                True,
            )
        )
    elif facts.polish_hash != facts.timeline_hash:
        hard.append(
            Blocker(
                "polish:stale",
                BlockerKind.NOT_CURRENT,
                "The final polish report is of another version of the timeline.",
                True,
            )
        )
    if facts.review_hash != facts.timeline_hash:
        hard.append(
            Blocker(
                "review:stale",
                BlockerKind.NOT_CURRENT,
                "The review is not at the current version of the timeline.",
                True,
            )
        )
    for issue in facts.quality_issues:
        if issue.blocks_export or issue.severity == "critical":
            hard.append(
                Blocker(issue.issue_id, BlockerKind.CRITICAL_ISSUE, issue.description, True)
            )
        elif issue.severity == "high" and facts.high_issues_block:
            soft.append(Blocker(issue.issue_id, BlockerKind.HIGH_ISSUE, issue.description, False))
    for problem in facts.polish_problems:
        place = "" if problem.start_frame is None else problem.start_frame
        ident = problem.reference or f"polish:{problem.code}:{place}"
        if any(b.blocker_id == ident for b in (*hard, *soft)):
            continue  # the same finding, already listed through quality control
        is_hard = problem.code in _HARD_POLISH
        kind = (
            BlockerKind.UNFINISHED
            if problem.code == "unfinished.element"
            else BlockerKind.POLISH_PROBLEM
        )
        (hard if is_hard else soft).append(Blocker(ident, kind, problem.message, is_hard))
    if facts.mix_state not in ("completed", "partial", "ok", ""):
        hard.append(
            Blocker(
                "mix:state",
                BlockerKind.MIX_NOT_READY,
                f"The audio mix is '{facts.mix_state}'.",
                True,
            )
        )
    hard.extend(
        Blocker(f"mix:{i}", BlockerKind.MIX_NOT_READY, message, True)
        for i, message in enumerate(facts.mix_errors)
    )
    hard.extend(
        Blocker(f"media:{name}", BlockerKind.MEDIA_OFFLINE, f"'{name}' is offline.", True)
        for name in facts.offline_sources
    )
    if facts.resolve_hash is None:
        hard.append(
            Blocker(
                "resolve:none",
                BlockerKind.NOT_IN_RESOLVE,
                "The timeline has not been carried out in Resolve.",
                True,
            )
        )
    elif facts.resolve_hash != facts.timeline_hash:
        hard.append(
            Blocker(
                "resolve:stale",
                BlockerKind.RESOLVE_STALE,
                "Resolve holds another version of the timeline than the one reviewed.",
                True,
            )
        )
    hard.extend(
        Blocker(
            f"resolve:conflict:{c}",
            BlockerKind.RESOLVE_CONFLICT,
            f"{c} is changed on both sides in Resolve and undecided.",
            True,
        )
        for c in facts.resolve_conflicts
    )
    if not facts.profile_known:
        hard.append(
            Blocker(
                "profile:unknown",
                BlockerKind.UNKNOWN_PROFILE,
                "The output profile is not one the render manager knows.",
                True,
            )
        )
    hard.extend(
        Blocker(f"profile:{i}", BlockerKind.PROFILE_UNUSABLE, problem, True)
        for i, problem in enumerate(facts.profile_problems)
    )
    if facts.polish_readiness == "ready_with_warnings":
        warnings.append("The final polish ended with warnings; look at them in the final review.")
    return ExportReadiness(tuple(hard), tuple(soft), tuple(warnings))
