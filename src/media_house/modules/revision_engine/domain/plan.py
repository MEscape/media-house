"""Assembling a revision plan, and checking afterwards what the revision really did.

A plan's identity comes from what it was made from (the timeline version, the report, the changes'
keys, the instructions), never from the clock: asking again for the same plan gives the same plan.
After a revision the check is on the next report, not on the command that carried it out: a change
is "resolved" only when the finding it answered is gone.
"""

from collections.abc import Sequence
from dataclasses import replace

from media_house.core.domain import json_codec
from media_house.modules.revision_engine.domain.facts import IssueFacts, ReviewView, TimelineView
from media_house.modules.revision_engine.domain.model import (
    ImprovementCheck,
    Instruction,
    ProposedChange,
    RevisionPlan,
    Skipped,
    ValidationResult,
)
from media_house.modules.revision_engine.domain.planner import strictest
from media_house.modules.revision_engine.domain.values import (
    REVISION_VERSION,
    ChangeStatus,
    Level,
    PlanStatus,
)

_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "optional": 0}
_UNIQUE = dict.fromkeys


def plan_identity(
    *,
    session_id: str,
    timeline_hash: str,
    report_id: str,
    changes: Sequence[ProposedChange],
    instructions: Sequence[Instruction],
) -> str:
    return (
        "rp_"
        + json_codec.digest(
            [
                session_id,
                timeline_hash,
                report_id,
                sorted(c.proposal_key for c in changes),
                sorted(i.instruction_id for i in instructions),
            ]
        )[:20]
    )


def assemble_plan(
    *,
    timeline: TimelineView,
    review: ReviewView,
    report_id: str,
    anchor_asset_id: str,
    changes: Sequence[ProposedChange],
    instructions: Sequence[Instruction],
    skipped: Sequence[Skipped],
    created_at: str,
    previous: RevisionPlan | None,
    protected_clips: tuple[str, ...],
    warnings: tuple[str, ...],
    provenance: tuple[tuple[str, str], ...],
) -> RevisionPlan:
    plan_id = plan_identity(
        session_id=review.session_id,
        timeline_hash=timeline.content_hash,
        report_id=report_id,
        changes=changes,
        instructions=instructions,
    )
    level = strictest([c.level for c in changes])
    conflicts = any(c.status is ChangeStatus.CONFLICT for c in changes)
    plan = RevisionPlan(
        revision_plan_id=plan_id,
        revision_id="rev_" + json_codec.digest(plan_id)[:12],
        plan_version=(previous.plan_version + 1) if previous else 1,
        previous_revision_reference=previous.revision_plan_id if previous else None,
        project_id=timeline.project_id,
        timeline_id=timeline.timeline_id,
        source_quality_report_id=report_id,
        source_timeline_version=timeline.version,
        source_timeline_hash=timeline.content_hash,
        target_timeline_version=None,
        review_session_id=review.session_id,
        created_at=created_at,
        anchor_asset_id=anchor_asset_id,
        proposed_changes=tuple(changes),
        instructions=tuple(instructions),
        skipped=tuple(skipped),
        affected_sections=tuple(_UNIQUE(s for c in changes for s in c.section_ids)),
        affected_clips=tuple(_UNIQUE(c for ch in changes for c in ch.clip_ids)),
        problem_references=tuple(_UNIQUE(i for c in changes for i in c.issue_ids)),
        responsible_modules=tuple(_UNIQUE(c.owner_module for c in changes)),
        expected_improvements=tuple(
            c.expected_improvement for c in changes if c.expected_improvement
        ),
        potential_side_effects=tuple(_UNIQUE(s for c in changes for s in c.side_effects)),
        dependencies=tuple(_UNIQUE(d for c in changes for d in c.invalidates)),
        approval_requirement=level,
        approval_status="blocked_by_conflicts"
        if conflicts
        else ("pending" if changes else "nothing_to_approve"),
        execution_status=PlanStatus.PROPOSED,
        checkpoint_reference="",
        new_revision_reference=None,
        validation_results=None,
        warnings=warnings,
        provenance=provenance,
        protected_clips=protected_clips,
        revision_version=REVISION_VERSION,
    )
    return seal(plan)


def seal(plan: RevisionPlan) -> RevisionPlan:
    """The content hash covers what the plan says and did, not when it was made."""
    return replace(
        plan, content_hash=json_codec.digest(replace(plan, created_at="", content_hash=""))
    )


def validate_revision(
    plan: RevisionPlan,
    before: Sequence[IssueFacts],
    after: Sequence[IssueFacts],
    *,
    before_report_id: str,
    after_report_id: str,
) -> ValidationResult:
    old = {i.fingerprint: i for i in before}
    new = {i.fingerprint: i for i in after}
    resolved = [i for fp, i in old.items() if fp not in new]
    worsened = [
        i for fp, i in new.items() if fp in old and _RANK[i.severity] > _RANK[old[fp].severity]
    ]
    introduced = [i for fp, i in new.items() if fp not in old]
    persisting = [i for fp, i in new.items() if fp in old and i not in worsened]
    regressions = tuple(
        i.issue_id for i in (*introduced, *worsened) if _RANK[i.severity] >= _RANK["medium"]
    )
    checks: list[ImprovementCheck] = []
    for change in plan.proposed_changes:
        if change.status is ChangeStatus.HANDED_OFF:
            checks.append(
                ImprovementCheck(
                    proposal_id=change.proposal_id,
                    result="not_checked",
                    detail=f"handed to {change.owner_module}; nothing was carried out here",
                )
            )
        elif change.status is not ChangeStatus.EXECUTED:
            checks.append(
                ImprovementCheck(
                    proposal_id=change.proposal_id,
                    result="not_checked",
                    detail=f"not carried out ({change.status.value})",
                )
            )
        elif not change.issue_fingerprints:
            checks.append(
                ImprovementCheck(
                    proposal_id=change.proposal_id,
                    result="not_checked",
                    detail="asked for by a person: it answers no finding, so look at it",
                )
            )
        else:
            left = [fp for fp in change.issue_fingerprints if fp in new]
            checks.append(
                ImprovementCheck(
                    proposal_id=change.proposal_id,
                    result="still_there" if left else "resolved",
                    detail=f"{len(left)} of {len(change.issue_fingerprints)} findings remain"
                    if left
                    else "every finding it answered is gone",
                )
            )
    summary = (
        f"{len(resolved)} findings resolved, {len(persisting)} still there, "
        f"{len(worsened)} worse, {len(introduced)} new."
    )
    return ValidationResult(
        quality_report_id=after_report_id,
        previous_report_id=before_report_id,
        resolved=len(resolved),
        persisting=len(persisting),
        worsened=len(worsened),
        introduced=len(introduced),
        regressions=regressions,
        checks=tuple(checks),
        summary=summary,
    )


def level_of(changes: Sequence[ProposedChange]) -> Level:
    return strictest([c.level for c in changes])
