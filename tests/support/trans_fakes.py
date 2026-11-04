"""Builders for transition engine tests: sequences, recording facts and a one-call planner."""

from collections.abc import Mapping
from datetime import UTC, datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.transition_engine.domain.engine import PlanMeta, plan_transitions
from media_house.modules.transition_engine.domain.facts import (
    AudioBridgeMark,
    Bookend,
    ContinuityFact,
    EdgeFact,
    EditRef,
    PacingCue,
    SourceFacts,
    StyleLimits,
    TransitionClip,
    TransitionRequest,
    TransitionSequence,
    WordSpan,
)
from media_house.modules.transition_engine.domain.model import Transition, TransitionPlan
from media_house.modules.transition_engine.domain.planner import PlanInputs
from media_house.modules.transition_engine.domain.profile import (
    JsonValue,
    SectionOverride,
    TransitionConfig,
    TransitionDecision,
)

FPS = Rational(30, 1)
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
EDIT = EditRef(edit_plan_id="edit-1", plan_version=1)


def r(value: float | str | Fraction) -> Rational:
    f = value if isinstance(value, Fraction) else Fraction(str(value))
    return Rational(f.numerator, f.denominator)


def clip(
    clip_id: str,
    asset_id: str,
    source_start: float | str | Fraction,
    source_end: float | str | Fraction,
    timeline_start: float | str | Fraction,
    **changes: object,
) -> TransitionClip:
    return TransitionClip(
        clip_id=clip_id,
        asset_id=asset_id,
        source_start=r(source_start),
        source_end=r(source_end),
        timeline_start=r(timeline_start),
        **changes,  # type: ignore[arg-type]
    )


def chain(
    *specs: tuple[str, str, float, float], **per_clip: Mapping[str, object]
) -> tuple[TransitionClip, ...]:
    """Clips back to back: ``(clip_id, asset_id, source_start, source_end)``; ``per_clip`` adds
    fields per clip id (``c2={"section_id": "s2"}``)."""
    out: list[TransitionClip] = []
    at = Fraction(0)
    for cid, asset, start, end in specs:
        out.append(clip(cid, asset, start, end, at, **dict(per_clip.get(cid, {}))))
        at += Fraction(str(end)) - Fraction(str(start))
    return tuple(out)


def sequence(
    clips: tuple[TransitionClip, ...],
    *,
    requests: tuple[TransitionRequest, ...] = (),
    bridges: tuple[AudioBridgeMark, ...] = (),
    bookends: tuple[Bookend, ...] = (),
    frame_rate: Rational = FPS,
    edit: EditRef | None = EDIT,
    format_hint: str | None = None,
) -> TransitionSequence:
    return TransitionSequence(
        edit=edit,
        project_id=None if edit is None else "project-1",
        frame_rate=frame_rate,
        clips=clips,
        requests=requests,
        bridges=bridges,
        bookends=bookends,
        format=format_hint,
    )


def source(asset_id: str, duration: float | None = 600.0, **changes: object) -> SourceFacts:
    fields: dict[str, object] = {
        "duration": None if duration is None else Fraction(str(duration)),
        "duration_basis": "inspection" if duration is not None else "unknown",
        "frame_rate": Fraction(30),
        "sample_rate": 48_000,
        **changes,
    }
    return SourceFacts(asset_id=asset_id, **fields)  # type: ignore[arg-type]


def words(*spans: tuple[float, float]) -> tuple[WordSpan, ...]:
    return tuple(WordSpan(start=Fraction(str(a)), end=Fraction(str(b))) for a, b in spans)


def edge(shot_id: str = "s1", **changes: object) -> EdgeFact:
    return EdgeFact(shot_id=shot_id, **changes)  # type: ignore[arg-type]


def style(ceiling: int = 3, **changes: object) -> StyleLimits:
    return StyleLimits(
        document_id="style-doc",
        fingerprint="f" * 64,
        identity_version=1,
        profile="generic",
        intensity_ceiling=ceiling,
        **changes,  # type: ignore[arg-type]
    )


def plan(
    seq: TransitionSequence,
    *,
    profile: str | None = "clean_default",
    sources: Mapping[str, SourceFacts] | None = None,
    edges: Mapping[tuple[str, str], EdgeFact] | None = None,
    continuity: Mapping[tuple[str, str], ContinuityFact] | None = None,
    pacing: tuple[PacingCue, ...] = (),
    style_limits: StyleLimits | None = None,
    decisions: tuple[TransitionDecision, ...] = (),
    overrides: Mapping[str, JsonValue] | None = None,
    section_overrides: tuple[SectionOverride, ...] = (),
) -> TransitionPlan:
    """Plan ``seq``; every recording defaults to a 10-minute source with no words."""
    config = TransitionConfig(
        profile=profile, overrides=dict(overrides or {}), section_overrides=section_overrides
    )
    resolved = config.resolve(seq.format, style_limits.profile if style_limits else None)
    assets = {c.asset_id for c in seq.clips}
    inputs = PlanInputs(
        sequence=seq,
        sources=dict(sources) if sources is not None else {a: source(a) for a in assets},
        edges=dict(edges or {}),
        continuity=dict(continuity or {}),
        pacing=pacing,
        style=style_limits,
        decisions={d.transition_id: d for d in decisions},
    )
    return plan_transitions(
        inputs,
        resolved,
        config,
        key={"test": True},
        now=T0,
        meta=PlanMeta(plan_version=1, supersedes=None, style=None, inputs=(), stages=()),
    )


def cut(p: TransitionPlan, out_id: str, in_id: str) -> Transition:
    found = p.between(out_id, in_id)
    assert found is not None, f"no boundary {out_id}>{in_id}"
    return found


def codes(p: TransitionPlan) -> set[str]:
    return {i.code for i in p.checks}
