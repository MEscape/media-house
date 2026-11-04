"""Assemble the stored plan: cache key, plan id, events, metrics, QC and render outcomes."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.transition_engine.domain.facts import TransitionSequence
from media_house.modules.transition_engine.domain.model import (
    Fallback,
    Handles,
    InputRecord,
    Metrics,
    Provenance,
    QcIssue,
    StageReport,
    StyleBinding,
    Transition,
    TransitionEvent,
    TransitionPlan,
)
from media_house.modules.transition_engine.domain.planner import (
    PlanInputs,
    plan_boundaries,
)
from media_house.modules.transition_engine.domain.profile import (
    TransitionConfig,
    TransitionProfile,
)
from media_house.modules.transition_engine.domain.validation import check_plan
from media_house.modules.transition_engine.domain.values import (
    CUT_KINDS,
    LIBRARY,
    PLANNER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    DecisionStatus,
    QcCategory,
    RenderState,
    ResultState,
    Severity,
    TransitionKind,
)

LIMITATIONS: tuple[str, ...] = (
    "Only the main picture layer is planned; cover (B-roll) layer edges stay hard cuts.",
    "Transitions are never moved to music beats: cut placement is editing's, and"
    " music_and_sound_design syncs to these transitions (events).",
    "Picture facts are read on the video analysis' clock; the alignment's video correction is"
    " not applied (usually 0).",
    "Composition checks use the main subject's box and on-screen text only; faces and products"
    " under a moving transition are not checked one by one.",
    "Motion-dependent transitions (whip pan, motion match) need a stored picture analysis;"
    " without one they fall back to cuts.",
    "Only graphic transitions are rendered here (through motion_graphics_engine); dissolves,"
    " slides and the like are specifications for the timeline builder.",
)


def digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key(
    inputs: PlanInputs,
    config: TransitionConfig,
    profile: TransitionProfile,
    *,
    render: bool,
    upstream: Mapping[str, str | None],
) -> dict[str, object]:
    """Everything that changes the plan and nothing else (no time, job or device)."""
    seq = inputs.sequence
    return {
        "edit": None if seq.edit is None else asdict(seq.edit),
        "sequence": digest(asdict(seq)),
        "facts": digest(
            {
                "sources": {k: asdict(v) for k, v in sorted(inputs.sources.items())},
                "edges": sorted([list(k), asdict(v)] for k, v in inputs.edges.items()),
                "continuity": sorted([list(k), asdict(v)] for k, v in inputs.continuity.items()),
                "pacing": [asdict(c) for c in inputs.pacing],
            }
        ),
        "upstream": dict(sorted(upstream.items())),
        "style": None if inputs.style is None else inputs.style.fingerprint,
        "config": digest(config.fingerprint(profile)),
        "decisions": sorted(
            [d.transition_id, d.action.value, d.kind, d.duration_frames, d.note]
            for d in inputs.decisions.values()
        ),
        "render": render,
        "versions": {
            "planner": PLANNER_VERSION,
            "schema": SCHEMA_VERSION,
            "vocabulary": VOCABULARY_VERSION,
            "profile": profile.version,
        },
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanMeta:
    plan_version: int
    supersedes: str | None
    style: StyleBinding | None
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    warnings: tuple[str, ...] = ()
    engines: tuple[tuple[str, str], ...] = ()


def plan_transitions(
    inputs: PlanInputs,
    profile: TransitionProfile,
    config: TransitionConfig,
    *,
    key: Mapping[str, object],
    now: datetime,
    meta: PlanMeta,
) -> TransitionPlan:
    """Decide every boundary and return the (not yet rendered) plan."""
    planned = plan_boundaries(inputs, profile, config)
    transitions = tuple(planned.transitions)
    seq = inputs.sequence
    checks = (*planned.issues, *check_plan(transitions, seq, inputs.sources, profile))
    return TransitionPlan(
        plan_id=f"tp:{digest(key)[:16]}",
        plan_version=meta.plan_version,
        supersedes=meta.supersedes,
        project_id=seq.project_id,
        edit=seq.edit,
        style=meta.style,
        created_at=now,
        state=_state(checks),
        profile=profile.name,
        frame_rate=seq.frame_rate,
        frame_rate_assumed=seq.frame_rate_assumed,
        transitions=transitions,
        events=events_of(transitions),
        metrics=metrics_of(transitions, seq),
        decisions=tuple(sorted(inputs.decisions.values(), key=lambda d: d.transition_id)),
        checks=checks,
        inputs=meta.inputs,
        stages=meta.stages,
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            planner_version=PLANNER_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile=profile.name,
            profile_version=profile.version,
            configuration_fingerprint=digest(config.fingerprint(profile)),
            engines=meta.engines,
        ),
        warnings=meta.warnings,
        limitations=LIMITATIONS,
    )


def events_of(transitions: tuple[Transition, ...]) -> tuple[TransitionEvent, ...]:
    return tuple(
        TransitionEvent(
            transition_id=t.transition_id, kind=t.kind, timeline_at=t.cut_at, intensity=t.intensity
        )
        for t in transitions
        if t.kind not in CUT_KINDS
    )


def metrics_of(transitions: tuple[Transition, ...], seq: TransitionSequence) -> Metrics:
    used = [t for t in transitions if t.kind not in CUT_KINDS]
    main = seq.main()
    minutes = max(c.timeline_end for c in main) / 60 if main else Fraction(0)
    return Metrics(
        boundaries=len(transitions),
        transitions=len(used),
        hard_cuts=len(transitions) - len(used),
        fallbacks=sum(t.fallback is not None for t in transitions),
        overlays=sum(t.overlay is not None for t in transitions),
        by_intensity=tuple(sum(t.intensity == i for t in transitions) for i in range(5)),
        per_minute=round(len(used) / float(minutes), 3) if minutes > 0 else 0.0,
    )


def with_checks(plan: TransitionPlan, extra: list[QcIssue]) -> TransitionPlan:
    """``plan`` with more QC findings (e.g. the style's drift check) and its state updated."""
    checks = (*plan.checks, *extra)
    return replace(plan, checks=checks, state=_state(checks))


def _state(checks: tuple[QcIssue, ...]) -> ResultState:
    failed = any(i.severity is Severity.ERROR for i in checks)
    return ResultState.PARTIAL if failed else ResultState.OK


# --- render outcomes --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class RenderOutcome:
    request_id: str
    state: RenderState
    asset_id: str | None = None
    detail: str = ""


def apply_renders(plan: TransitionPlan, outcomes: Mapping[str, RenderOutcome]) -> TransitionPlan:
    """Record render results; a FAILED overlay falls back to a hard cut (contracts sec. 5)."""
    transitions: list[Transition] = []
    checks = list(plan.checks)
    for t in plan.transitions:
        outcome = outcomes.get(t.overlay.request_id) if t.overlay else None
        if t.overlay is None or outcome is None:
            transitions.append(t)
            continue
        if outcome.state is RenderState.FAILED:
            transitions.append(_hard_cut(t, outcome.detail))
            checks.append(
                QcIssue(
                    code="overlay.render_failed",
                    severity=Severity.ERROR,
                    category=QcCategory.TECHNICAL,
                    message=f"the {t.kind} overlay could not be rendered ({outcome.detail});"
                    " the boundary falls back to a hard cut",
                    item_id=t.transition_id,
                    timeline_start=t.timeline_start,
                    timeline_end=t.timeline_end,
                )
            )
            continue
        overlay = replace(
            t.overlay, render_state=outcome.state, asset_id=outcome.asset_id, detail=outcome.detail
        )
        transitions.append(replace(t, overlay=overlay))
        if outcome.state is RenderState.NOT_AVAILABLE:
            checks.append(
                QcIssue(
                    code="overlay.not_rendered",
                    severity=Severity.WARNING,
                    category=QcCategory.TECHNICAL,
                    message=f"the {t.kind} overlay is planned but not rendered: {outcome.detail}",
                    item_id=t.transition_id,
                )
            )
    final = tuple(transitions)
    done = tuple(checks)
    seq_metrics = replace(
        plan.metrics,
        transitions=sum(t.kind not in CUT_KINDS for t in final),
        hard_cuts=sum(t.kind in CUT_KINDS for t in final),
        fallbacks=sum(t.fallback is not None for t in final),
        overlays=sum(t.overlay is not None for t in final),
        by_intensity=tuple(sum(t.intensity == i for t in final) for i in range(5)),
    )
    return replace(
        plan,
        transitions=final,
        events=events_of(final),
        metrics=seq_metrics,
        checks=done,
        state=_state(done),
    )


def _hard_cut(t: Transition, detail: str) -> Transition:
    spec = LIBRARY[TransitionKind.HARD_CUT]
    return replace(
        t,
        status=DecisionStatus.FALLBACK,
        required=False,
        kind=TransitionKind.HARD_CUT,
        family=spec.family,
        intensity=0,
        preset_id=t.preset_id.rsplit(".", 1)[0] + ".hard_cut",
        duration_frames=0,
        duration=Rational(0, 1),
        timeline_start=t.cut_at,
        timeline_end=t.cut_at,
        start_frame=t.cut_frame,
        end_frame=t.cut_frame,
        direction="",
        handles=Handles(
            required_out=0,
            required_in=0,
            available_out=t.handles.available_out,
            available_in=t.handles.available_in,
        ),
        reasons=(*t.reasons, f"render failed ({detail}): hard cut"),
        fallback=Fallback(wanted=t.kind, used=TransitionKind.HARD_CUT, reason=detail),
        overlay=None,
    )
