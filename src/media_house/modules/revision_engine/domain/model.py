"""A revision plan: what to change, why, who owns it, who may approve it, what it touches."""

from dataclasses import dataclass, field

from media_house.modules.revision_engine.domain.values import (
    SCHEMA_VERSION,
    ChangeKind,
    ChangeStatus,
    InstructionKind,
    Level,
    PlanStatus,
    TargetKind,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Target:
    kind: TargetKind
    clip_ids: tuple[str, ...] = ()
    section_ids: tuple[str, ...] = ()
    role: str = ""
    transition_ids: tuple[str, ...] = ()
    start_frame: int | None = None
    end_frame: int | None = None
    #: How the reference was resolved (kept next to what it resolved to).
    resolved_by: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Clarification:
    question: str
    #: Concrete choices, each a short description and the target it would mean.
    options: tuple[tuple[str, Target], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Instruction:
    """A person's words and what they were understood to mean. The original is never replaced."""

    instruction_id: str
    original: str
    author: str
    kind: InstructionKind
    intent: str
    target: Target
    #: Named values taken from the words (``amount_db``, ``seconds``...).
    params: tuple[tuple[str, str], ...] = ()
    confidence: float = 0.0
    #: Set when the words cannot be acted on without asking.
    clarification: Clarification | None = None
    feedback_id: str = ""
    #: What was understood, in one sentence a person can check.
    interpretation: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposedChange:
    proposal_id: str
    #: Stable for the same problem in the same place: a rejected change is recognised again.
    proposal_key: str
    kind: ChangeKind
    owner_module: str
    #: The permission this change falls under (a ``review_orchestrator`` operation name).
    operation: str
    level: Level
    title: str
    #: Section, problem, change, owner and expected result, in one readable paragraph.
    description: str
    issue_ids: tuple[str, ...] = ()
    issue_fingerprints: tuple[str, ...] = ()
    instruction_ids: tuple[str, ...] = ()
    clip_ids: tuple[str, ...] = ()
    track_ids: tuple[str, ...] = ()
    section_ids: tuple[str, ...] = ()
    #: What the change is, as named values the owner's contract can act on.
    params: tuple[tuple[str, str], ...] = ()
    expected_improvement: str = ""
    side_effects: tuple[str, ...] = ()
    #: Outputs that become stale because of it (and nothing else).
    invalidates: tuple[str, ...] = ()
    #: Problems that stop it from going ahead as it is (a locked section, a person's own work).
    conflicts: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    status: ChangeStatus = ChangeStatus.PROPOSED
    #: What happened when it was carried out (or why it was not).
    outcome: str = ""
    #: The elements the change touched, once carried out.
    changed_elements: tuple[str, ...] = ()

    def param(self, name: str, default: str = "") -> str:
        return next((v for k, v in self.params if k == name), default)


@dataclass(frozen=True, slots=True, kw_only=True)
class Skipped:
    """A finding that did not become a proposal, and why: a person can see what was left out."""

    issue_id: str
    code: str
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ImprovementCheck:
    proposal_id: str
    #: ``resolved`` (the finding is gone), ``still_there``, ``not_checked`` (nothing to compare).
    result: str
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationResult:
    #: The report made of the revised timeline.
    quality_report_id: str
    previous_report_id: str
    resolved: int
    persisting: int
    worsened: int
    introduced: int
    #: New or worsened findings that matter, by issue id.
    regressions: tuple[str, ...]
    checks: tuple[ImprovementCheck, ...]
    summary: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionRecord:
    plan_id: str
    started_at: str
    finished_at: str
    checkpoint_id: str
    from_version: int
    to_version: int | None
    #: The audio mix overrides in effect after the revision (``bus:music=-3`` ...), so a rollback
    #: can restore them.
    mix_overrides_after: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class RevisionPlan:
    revision_plan_id: str
    #: Identifies one revision across its plan and its execution (spec: ``revision_id``).
    revision_id: str
    plan_version: int
    previous_revision_reference: str | None
    project_id: str | None
    timeline_id: str
    source_quality_report_id: str
    source_timeline_version: int
    source_timeline_hash: str
    #: Set when the revision was carried out.
    target_timeline_version: int | None
    review_session_id: str
    created_at: str
    anchor_asset_id: str
    proposed_changes: tuple[ProposedChange, ...]
    instructions: tuple[Instruction, ...]
    skipped: tuple[Skipped, ...]
    affected_sections: tuple[str, ...]
    affected_clips: tuple[str, ...]
    problem_references: tuple[str, ...]
    responsible_modules: tuple[str, ...]
    expected_improvements: tuple[str, ...]
    potential_side_effects: tuple[str, ...]
    dependencies: tuple[str, ...]
    #: ``A``, ``B`` or ``C``: the strictest level among its changes.
    approval_requirement: Level
    approval_status: str
    execution_status: PlanStatus
    checkpoint_reference: str
    new_revision_reference: str | None
    validation_results: ValidationResult | None
    warnings: tuple[str, ...]
    #: Where the timeline came from (counts of elements by origin) when the plan was made.
    provenance: tuple[tuple[str, str], ...]
    #: Elements a person asked to keep as they are (this plan and every earlier one).
    protected_clips: tuple[str, ...] = ()
    execution: ExecutionRecord | None = None
    revision_version: int = 1
    content_hash: str = ""
    schema_version: int = field(default=SCHEMA_VERSION)

    def change(self, proposal_id: str) -> ProposedChange | None:
        return next((c for c in self.proposed_changes if c.proposal_id == proposal_id), None)
