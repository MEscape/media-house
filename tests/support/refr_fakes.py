"""Synthetic geometry and tracking for reframing tests.

Everything here is plain numbers: boxes in the 0-1 display picture, times in seconds. It validates
the ARITHMETIC of cropping (containment, margins, limits, keyframes) and says nothing about how a
real tracker or a real picture behaves.
"""

from fractions import Fraction

from media_house.modules.reframing_intelligence.domain.elements import select
from media_house.modules.reframing_intelligence.domain.engine import plan_reframing
from media_house.modules.reframing_intelligence.domain.facts import (
    BeatHint,
    ContinuityFacts,
    ElementTrack,
    Inputs,
    Sample,
    ShotFacts,
    SourceFacts,
    StoryFacts,
    VisualNeed,
)
from media_house.modules.reframing_intelligence.domain.geometry import (
    Box,
    SourceGeometry,
    TargetFormat,
)
from media_house.modules.reframing_intelligence.domain.model import (
    Recommendation,
    ReframingAnalysis,
)
from media_house.modules.reframing_intelligence.domain.planner import plan_shot
from media_house.modules.reframing_intelligence.domain.profile import (
    PROFILES,
    ReframingConfig,
    ReframingProfile,
    SafeArea,
)
from media_house.modules.reframing_intelligence.domain.verdict import ShotPlan

__all__ = [
    "ASSET",
    "T11",
    "T169",
    "T916",
    "BeatHint",
    "ContinuityFacts",
    "Inputs",
    "ShotPlan",
    "StoryFacts",
    "VisualNeed",
    "analyse",
    "beat",
    "geom",
    "plan",
    "profile",
    "shot",
    "source",
    "sparse",
    "still",
    "walk",
]

ASSET = "a1"
T169 = TargetFormat.of("16:9")
T916 = TargetFormat.of("9:16")
T11 = TargetFormat.of("1:1")


def f(x: float) -> Fraction:
    return Fraction(x).limit_denominator(1000)


def geom(
    width: int = 1920,
    height: int = 1080,
    *,
    rotation: int = 0,
    samples: tuple[int, int] | None = None,
    exact: bool = True,
) -> SourceGeometry:
    sw, sh = samples or (width, height)
    return SourceGeometry(width, height, sw, sh, rotation, exact)


def still(
    track_id: str,
    box: tuple[float, float, float, float],
    *,
    start: float = 0.0,
    end: float = 4.0,
    step: float = 0.2,
    kind: str = "person",
    label: str | None = None,
    confidence: float = 0.9,
    cluster: str | None = None,
) -> ElementTrack:
    """A track that does not move (plus a tiny deterministic wobble of zero)."""
    return walk(
        track_id, box, box, start=start, end=end, step=step, kind=kind, label=label,
        confidence=confidence, cluster=cluster,
    )  # fmt: skip


def walk(
    track_id: str,
    begin: tuple[float, float, float, float],
    finish: tuple[float, float, float, float],
    *,
    start: float = 0.0,
    end: float = 4.0,
    step: float = 0.2,
    kind: str = "person",
    label: str | None = None,
    confidence: float = 0.9,
    cluster: str | None = None,
    skip: tuple[float, float] | None = None,
) -> ElementTrack:
    """A track whose box moves in a straight line from ``begin`` to ``finish``; ``skip`` drops the
    observations inside ``[a, b]`` (an occlusion)."""
    samples = []
    count = round((end - start) / step)
    for i in range(count + 1):
        t = start + i * step
        if skip is not None and skip[0] <= t <= skip[1]:
            continue
        a = i / count if count else 0.0
        box = tuple(b0 + (b1 - b0) * a for b0, b1 in zip(begin, finish, strict=True))
        samples.append(Sample(f(t), Box(*box), confidence))
    return ElementTrack(track_id, kind, label or kind, tuple(samples), cluster)


def sparse(track: ElementTrack, times: list[float]) -> ElementTrack:
    keep = {f(t) for t in times}
    return ElementTrack(
        track.track_id, track.kind, track.label, tuple(s for s in track.samples if s.t in keep),
        track.cluster,
    )  # fmt: skip


def shot(
    shot_id: str = "s0",
    start: float = 0.0,
    end: float = 4.0,
    tracks: tuple[ElementTrack, ...] = (),
    main: str | None = None,
    **facts: object,
) -> ShotFacts:
    return ShotFacts(
        shot_id=shot_id,
        scene_id=facts.pop("scene_id", "sc0"),  # type: ignore[arg-type]
        start=f(start),
        end=f(end),
        tracks=tracks,
        main_track_id=main if main is not None else (tracks[0].track_id if tracks else None),
        usability=facts.pop("usability", 0.8),  # type: ignore[arg-type]
        **facts,  # type: ignore[arg-type]
    )


def source(
    *shots: ShotFacts, geometry: SourceGeometry | None = None, asset_id: str = ASSET
) -> SourceFacts:
    return SourceFacts(
        asset_id=asset_id, geometry=geometry or geom(), shots=shots, identity="video-1"
    )


def beat(
    beat_id: str = "b1", function: str = "claim", start: float = 0.0, end: float = 4.0
) -> BeatHint:
    return BeatHint(beat_id, "sec1", function, ASSET, f(start), f(end))


def profile(name: str = "generic", **changes: object) -> ReframingProfile:
    base = PROFILES[name]
    return ReframingProfile(**{**base.__dict__, **changes}) if changes else base


def plan(
    one: ShotFacts,
    *,
    target: TargetFormat = T916,
    geometry: SourceGeometry | None = None,
    prof: ReframingProfile | None = None,
    beats: tuple[BeatHint, ...] = (),
    needs: tuple[VisualNeed, ...] = (),
    safe_areas: tuple[SafeArea, ...] = (),
) -> ShotPlan:
    return plan_shot(
        one, source(one, geometry=geometry), target, prof or profile(), beats, needs, safe_areas
    )


def analyse(
    *shots: ShotFacts,
    target: TargetFormat = T916,
    geometry: SourceGeometry | None = None,
    config: ReframingConfig | None = None,
    story: StoryFacts | None = None,
    continuity: ContinuityFacts | None = None,
    previous: ReframingAnalysis | None = None,
) -> ReframingAnalysis:
    from datetime import UTC, datetime

    return plan_reframing(
        Inputs((source(*shots, geometry=geometry),), story, continuity),
        target,
        config or ReframingConfig(),
        previous=previous,
        now=datetime(2026, 10, 29, tzinfo=UTC),
    )


def only(analysis: ReframingAnalysis) -> Recommendation:
    assert len(analysis.recommendations) == 1
    return analysis.recommendations[0]


_ = select  # re-exported for tests that inspect the selection directly
