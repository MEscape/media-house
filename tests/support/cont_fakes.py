"""Builders for continuity tests: the evidence at the two sides of a cut, and a one-call run."""

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

from media_house.modules.continuity_intelligence.domain.engine import (
    AnalysisMeta,
    analyse_continuity,
)
from media_house.modules.continuity_intelligence.domain.facts import (
    AudioEdge,
    EdgeFact,
    NameFact,
    SideFact,
    SubjectFact,
    TransitionFact,
)
from media_house.modules.continuity_intelligence.domain.model import (
    ContinuityAnalysis,
    Issue,
    IssueDecision,
)
from media_house.modules.continuity_intelligence.domain.profile import ContinuityConfig
from media_house.modules.continuity_intelligence.domain.values import IssueType, TransitionKind

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
#: ``side(..., edge_=None)`` means NO picture evidence; leaving it out means a plain default frame.
_DEFAULT: Any = object()


def secs(value: float) -> Fraction:
    return Fraction(value).limit_denominator(1000)


def name(text: str) -> NameFact:
    family, _, value = text.partition(" ")
    return NameFact(family=family.casefold(), value=value.casefold(), text=text)


def subject(cx: float = 0.5, cy: float = 0.5, height: float = 0.6, **changes: Any) -> SubjectFact:
    base = SubjectFact(
        track_id="t", identity_cluster="person_A", cx=cx, cy=cy, height=height, vx=None, vy=None
    )
    return replace(base, **changes)


def edge(**changes: Any) -> EdgeFact:
    base = EdgeFact(
        subject=subject(),
        head_yaw=None,
        framing="medium",
        environment="studio",
        indoor_outdoor="indoor",
        luma=0.5,
        color_balance=0.1,
    )
    return replace(base, **changes)


def side(
    clip: str,
    asset: str = "a1",
    *,
    start: float = 0.0,
    end: float = 10.0,
    moment: float | None = None,
    outgoing: bool = True,
    shot: str = "s1",
    scene: str | None = "sc1",
    section: str | None = "sec1",
    edge_: EdgeFact | None = _DEFAULT,
    audio: AudioEdge | None = None,
    sync: str = "consistent",
) -> SideFact:
    return SideFact(
        clip_id=clip,
        asset_id=asset,
        section_id=section,
        beat_id=None,
        clip_start=secs(start),
        clip_end=secs(end),
        moment=secs(moment if moment is not None else (end if outgoing else start)),
        shot_id=shot,
        scene_id=scene,
        edge=edge() if edge_ is _DEFAULT else edge_,
        audio=audio,
        sync=sync,
    )


def transition(
    tid: str = "tr1",
    *,
    a: SideFact | None = None,
    b: SideFact | None = None,
    kind: TransitionKind = TransitionKind.SAME_RECORDING,
    covered: bool = False,
    bridged: bool = False,
    trimmed: bool = False,
    section_change: bool = False,
    reordered: bool = False,
    gap: float | None = None,
    cold_open: bool = False,
    related: Sequence[str] = (),
    cover_available: bool | None = None,
    at: float = 10.0,
    fingerprint: str | None = None,
) -> TransitionFact:
    left = a or side("c1")
    right = b or side("c2", outgoing=False, start=12.0, end=20.0)
    return TransitionFact(
        transition_id=tid,
        kind=kind,
        outgoing=left,
        incoming=right,
        timeline_at=secs(at),
        covered=covered,
        bridged=bridged,
        speech_trimmed=trimmed,
        section_change=section_change,
        reordered=reordered,
        gap=None if gap is None else secs(gap),
        cold_open=cold_open,
        fingerprint=fingerprint
        or hashlib.sha256(repr((left, right, kind, gap)).encode()).hexdigest()[:20],
        related_assets=tuple(related),
        cover_available=cover_available,
    )


def run(
    transitions: Sequence[TransitionFact],
    *,
    profile: str = "generic",
    strictness: float = 1.0,
    previous: ContinuityAnalysis | None = None,
    decisions: Sequence[IssueDecision] = (),
) -> ContinuityAnalysis:
    meta = AnalysisMeta(
        continuity_analysis_id="ca_test",
        analysis_version=1,
        supersedes=None,
        project_id=None,
        created_at=NOW,
        edit_plan_id="ep1",
        edit_plan_version=1,
        configuration_fingerprint="cfg",
        story_format=None,
        sources=("a1",),
        inputs=(),
        stages=(),
        warnings=(),
        decisions=tuple(decisions),
    )
    return analyse_continuity(
        transitions, ContinuityConfig(profile=profile, strictness=strictness), meta, previous
    )


def issue_of(analysis: ContinuityAnalysis, kind: IssueType) -> Issue | None:
    return next((i for i in analysis.issues if i.type is kind), None)


def jump_analysis() -> ContinuityAnalysis:
    """A small finished analysis (one jump cut) for tests of consumers."""
    a = side("c1", edge_=edge(subject=subject(cx=0.40)))
    b = side("c2", outgoing=False, start=12.0, end=20.0, edge_=edge(subject=subject(cx=0.50)))
    return run(
        [transition(a=a, b=b, kind=TransitionKind.SAME_SHOT, gap=2.0)],
        profile="cinematic_dialogue",
    )
