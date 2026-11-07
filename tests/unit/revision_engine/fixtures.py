"""A small timeline for the planner and the feedback reader: two 4 s shots at 25 fps in two
sections, a cover clip, two dialogue clips, music and a sound effect."""

from dataclasses import replace
from fractions import Fraction
from typing import Any

from media_house.modules.revision_engine.domain.facts import (
    ClipView,
    IssueFacts,
    MixView,
    ReviewView,
    SectionView,
    SoundView,
    TimelineView,
    TransitionView,
)
from media_house.modules.revision_engine.domain.planner import PlanningInput
from media_house.modules.revision_engine.domain.values import Origin

PA, PB, PC = "v:pA", "v:pB", "v:pC"


def clip(
    clip_id: str, start: int, end: int, role: str = "primary", section: str | None = "s1", **kw: Any
) -> ClipView:
    return ClipView(
        clip_id=clip_id, track_id="V1" if role == "primary" else "V2", role=role, kind=role,
        name=f"Shot {clip_id}", start_frame=start, end_frame=end, section_id=section,
        enabled=True, origin=kw.pop("origin", Origin.MEDIA_HOUSE),
    )  # fmt: skip


def sound(
    clip_id: str, role: str, start: float, end: float, section: str | None = "s1"
) -> SoundView:
    return SoundView(
        clip_id=clip_id, track_id="A1", role=role, start=Fraction(start).limit_denominator(100),
        end=Fraction(end).limit_denominator(100), section_id=section, origin=Origin.MEDIA_HOUSE,
    )  # fmt: skip


def timeline(**changes: Any) -> TimelineView:
    base = TimelineView(
        timeline_id="tl", version=1, content_hash="h1", anchor_asset_id="anchor", project_id="p",
        fps=Fraction(25), duration_frames=200,
        clips=(
            clip(PA, 0, 100),
            clip(PB, 100, 200, section="s2"),
            clip(PC, 125, 150, "cover", "s2"),
        ),
        sounds=(
            sound("a:aV", "dialogue", 0, 4), sound("a:aW", "dialogue", 4, 8, "s2"),
            sound("a:music", "music", 0, 8), sound("a:sfx", "sfx", 3.9, 4.3),
        ),
        transitions=(TransitionView("cut:pA>pB", "dissolve", 100, PA, PB),),
        sections=(SectionView("s1", "Hook", 0, 100), SectionView("s2", "Body", 100, 200)),
    )  # fmt: skip
    return replace(base, **changes)


def review(**changes: Any) -> ReviewView:
    base = ReviewView(
        session_id="sess", locked_sections=(), approved_sections=(), rejected_keys=frozenset(),
        authority=(
            ("technical_repair", "suggested"), ("asset_reconnection", "suggested"),
            ("local_creative_revision", "suggested"), ("render_preparation", "automatic"),
            ("override_decision", "manual_only"),
        ),
    )  # fmt: skip
    return replace(base, **changes)


def issue(code: str, **kw: Any) -> IssueFacts:
    base = IssueFacts(
        issue_id=f"q_{code}", fingerprint=f"fp_{code}", code=code, severity="medium",
        basis="objective",
        confidence=0.9, description=f"{code} happened.", suggested_correction="Fix it.",
        owner_hint="timeline_builder", start_frame=10, end_frame=20, clips=(), tracks=(),
        sections=("s1",),
        measurements=(), blocks_export=False,
    )  # fmt: skip
    return replace(base, **kw)


def planning(*issues: IssueFacts, **kw: Any) -> PlanningInput:
    return PlanningInput(
        timeline=kw.pop("timeline", timeline()), issues=tuple(issues),
        review=kw.pop("review", review()), mix=kw.pop("mix", MixView()), **kw,
    )  # fmt: skip
