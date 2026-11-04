"""Plan assembly: the cache key, the plan envelope and the application of render results."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from media_house.modules.vfx_engine.domain.facts import SourceFacts, StyleLimits, VfxShots
from media_house.modules.vfx_engine.domain.model import (
    Capability,
    InputRecord,
    OutputRef,
    Provenance,
    QcIssue,
    RenderQc,
    StageReport,
    StyleBinding,
    Treatment,
    VfxPlan,
)
from media_house.modules.vfx_engine.domain.planner import NEEDS, PlanInputs, plan_treatments
from media_house.modules.vfx_engine.domain.profile import VfxConfig, VfxProfile
from media_house.modules.vfx_engine.domain.values import (
    PLANNER_VERSION,
    RENDER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Backend,
    EffectStatus,
    EffectType,
    ResultState,
    Severity,
    TreatmentStatus,
)

LIMITATIONS: tuple[str, ...] = (
    "No video matting model ships: background blur/replacement and subject isolation are "
    "not_available (never faked with a rectangle).",
    "No inpainting model ships: object removal is not_available; concealment (blur/mosaic) is "
    "offered instead and is recorded as concealment, not removal.",
    "Video intelligence tracks boxes, not planes: screen replacement is not_available.",
    "No lens-flare element library: lens flares are not_available.",
    "Picture times are the analysis' stream clock; an audio/video alignment correction is not "
    "applied here.",
    "Audio is never processed: each treatment states an audio intent for the audio stage.",
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def cache_key(
    shots: VfxShots,
    sources: Mapping[str, SourceFacts],
    style: StyleLimits | None,
    available: frozenset[EffectType],
    config: VfxConfig,
    profile: VfxProfile,
    render: bool,
    renderer: str,
) -> dict[str, object]:
    """Everything that changes the plan, nothing that cannot (time, job, device)."""
    return {
        "shots": digest(asdict(shots)),
        "sources": {k: digest(asdict(v)) for k, v in sorted(sources.items())},
        "style": None if style is None else style.fingerprint,
        "available": sorted(e.value for e in available),
        "config": digest(config.fingerprint(profile)),
        "render": render,
        "renderer": renderer if render else None,
        "versions": {
            "planner": PLANNER_VERSION,
            "schema": SCHEMA_VERSION,
            "vocabulary": VOCABULARY_VERSION,
            "render": RENDER_VERSION,
        },
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanMeta:
    plan_version: int
    supersedes: str | None
    anchor_asset_id: str
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    engines: tuple[tuple[str, str], ...] = ()


def capabilities(available: frozenset[EffectType]) -> tuple[Capability, ...]:
    out = []
    for effect in EffectType:
        if effect is EffectType.NO_EFFECT:
            continue
        backend, why = NEEDS.get(effect, (Backend.FFMPEG, ""))
        out.append(
            Capability(
                effect=effect,
                backend=backend,
                available=effect in available,
                detail="" if effect in available else (why or "renderer unavailable"),
            )
        )
    return tuple(out)


def build_plan(
    inputs: PlanInputs,
    profile: VfxProfile,
    *,
    now: datetime,
    meta: PlanMeta,
) -> VfxPlan:
    planned = plan_treatments(inputs, profile)
    shots = inputs.shots
    style = inputs.style
    binding = (
        None
        if style is None
        else StyleBinding(
            document_id=style.document_id,
            fingerprint=style.fingerprint,
            identity_version=style.identity_version,
            profile=style.profile,
            intensity_ceiling=style.intensity_ceiling,
        )
    )
    plan = VfxPlan(
        plan_id="vfx:" + digest([meta.anchor_asset_id, meta.plan_version, now.isoformat()])[:16],
        plan_version=meta.plan_version,
        supersedes=meta.supersedes,
        project_id=shots.project_id,
        edit=shots.edit,
        style=binding,
        created_at=now,
        state=ResultState.OK,
        anchor_asset_id=meta.anchor_asset_id,
        frame_rate=shots.frame_rate,
        output_width=shots.output_width,
        output_height=shots.output_height,
        rendered=False,
        treatments=planned.treatments,
        capabilities=capabilities(inputs.available),
        checks=planned.checks,
        inputs=meta.inputs,
        stages=meta.stages,
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            planner_version=PLANNER_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile=profile.name,
            profile_version=profile.version,
            render_version=RENDER_VERSION,
            configuration_fingerprint=digest(inputs.config.fingerprint(profile)),
            engines=meta.engines,
        ),
        warnings=planned.warnings
        + (
            ("the frame rate was assumed (VFR source): seconds stay authoritative",)
            if shots.frame_rate_assumed
            else ()
        ),
        limitations=LIMITATIONS,
    )
    return with_state(plan)


def with_state(plan: VfxPlan) -> VfxPlan:
    """PARTIAL as soon as anything is ERROR (a treatment failed, a check found an error)."""
    failing = any(c.severity is Severity.ERROR for c in plan.checks) or any(
        t.status is TreatmentStatus.ERROR for t in plan.treatments
    )
    return replace(plan, state=ResultState.PARTIAL if failing else ResultState.OK)


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderOutcome:
    """What rendering one treatment produced (``output`` None = failed: the fallback applies)."""

    treatment_id: str
    render_fingerprint: str
    output: OutputRef | None
    qc: RenderQc | None
    issues: tuple[QcIssue, ...]
    reused: bool = False
    #: Replaces the treatment's effects (e.g. a privacy fallback to the whole picture).
    replaced: Treatment | None = None


def apply_renders(plan: VfxPlan, outcomes: Mapping[str, RenderOutcome]) -> VfxPlan:
    treatments = []
    checks = list(plan.checks)
    for t in plan.treatments:
        o = outcomes.get(t.treatment_id)
        if o is None:
            treatments.append(t)
            continue
        base = o.replaced or t
        failed = o.output is None or any(
            i.severity in {Severity.ERROR, Severity.CRITICAL} for i in o.issues
        )
        effect_status = (
            EffectStatus.ERROR
            if failed
            else EffectStatus.REUSED
            if o.reused
            else EffectStatus.RENDERED
        )
        active = {e.effect_id for e in base.active()}
        effects = tuple(
            replace(e, status=effect_status) if e.effect_id in active else e for e in base.effects
        )
        status = (
            TreatmentStatus.ERROR
            if failed
            else TreatmentStatus.REUSED
            if o.reused
            else TreatmentStatus.RENDERED
        )
        treatments.append(
            replace(
                base,
                effects=effects,
                status=status,
                render_fingerprint=o.render_fingerprint,
                output=None if failed else o.output,
                qc=o.qc,
                checks=base.checks + o.issues,
            )
        )
        checks += list(o.issues)
    return with_state(
        replace(plan, treatments=tuple(treatments), checks=tuple(checks), rendered=True)
    )
