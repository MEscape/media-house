"""Narrative pacing, from the AUTHORITATIVE story plan only.

Sections, beats, their order and lengths are the story plan's. Pacing does not invent a structure:
with no story plan this module returns nothing and the analysis says narrative-level findings are
unavailable. The story-wide clock is the sum of the sections' lengths in story order; recordings
are never placed on a shared clock.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from media_house.modules.pacing_intelligence.domain.context import Context
from media_house.modules.pacing_intelligence.domain.facts import RefFact
from media_house.modules.pacing_intelligence.domain.model import (
    Measure,
    PassageAnalysis,
    SectionAnalysis,
)
from media_house.modules.pacing_intelligence.domain.values import Basis, ReasonCode

#: Beats that state or deliver the point; hooks promise it, claims and answers make it.
PREMISE_FUNCTIONS = frozenset({"hook", "claim", "answer"})
SETUP_FUNCTIONS = frozenset({"setup", "context"})


@dataclass(slots=True)
class Narrative:
    sections: list[SectionAnalysis] = field(default_factory=list)
    #: Seconds from the start of the story to the first beat that states the point.
    premise_at: float | None = None
    premise_section: str | None = None
    #: Share of the story's runtime before the premise that is setup or context.
    setup_share: float | None = None
    total_seconds: float | None = None
    starts: dict[str, float] = field(default_factory=dict)


def analyse_narrative(ctx: Context, passages: list[PassageAnalysis]) -> Narrative:
    story = ctx.story
    if story is None:
        return Narrative()
    out = Narrative()
    ordered = story.ordered_sections()
    seconds: dict[str, float | None] = {}
    for section in ordered:
        mine = [p for p in passages if p.section_id == section.section_id]
        seconds[section.section_id] = (
            section.duration_seconds
            if section.duration_seconds is not None
            else (sum(p.elapsed_seconds for p in mine) if mine else None)
        )
    known = [v for v in seconds.values() if v is not None]
    out.total_seconds = sum(known) if known and len(known) == len(ordered) else None
    cursor = 0.0
    for section in ordered:
        out.starts[section.section_id] = cursor
        cursor += seconds[section.section_id] or 0.0
    out.premise_at, out.premise_section = _premise(ctx, out.starts)
    out.setup_share = _setup_share(ctx, seconds, out)
    for section in ordered:
        mine = [p for p in passages if p.section_id == section.section_id]
        reasons: list[ReasonCode] = []
        deadline = ctx.profile.premise_deadline
        if (
            section.section_id == out.premise_section
            and deadline is not None
            and out.premise_at is not None
            and out.premise_at > deadline * ctx.adjust.static
        ):
            reasons.append(ReasonCode.PREMISE_LATE)
        if (
            section.order == ordered[0].order
            and out.setup_share is not None
            and out.setup_share > ctx.profile.setup_max_share
        ):
            reasons.append(ReasonCode.SETUP_HEAVY)
        total = out.total_seconds
        own = seconds[section.section_id]
        words = sum(p.words for p in mine)
        timed = [p for p in mine if p.speech_rate_wpm is not None]
        rate_words = sum(p.words for p in timed)
        rate_time = sum(p.speech_seconds for p in timed)
        out.sections.append(
            SectionAnalysis(
                section_id=section.section_id,
                label=section.label,
                order=section.order,
                priority=section.priority,
                seconds=None if own is None else round(own, 3),
                words=words,
                speech_rate_wpm=round(rate_words / rate_time * 60, 1) if rate_time > 0 else None,
                information_density=_weighted(mine, "information_density"),
                cognitive_load=_weighted(mine, "cognitive_load"),
                share=None if total is None or own is None or total <= 0 else round(own / total, 3),
                first_main_point_at=out.premise_at
                if section.section_id == out.premise_section
                else None,
                reason_codes=tuple(reasons),
            )
        )
    return out


def _weighted(passages: list[PassageAnalysis], name: str) -> Measure:
    pairs = [
        (getattr(p, name).value, p.words)
        for p in passages
        if getattr(p, name).value is not None and p.words > 0
    ]
    if not pairs:
        return Measure(value=None, basis=Basis.HEURISTIC, note="no passage of the section has it")
    weight = sum(w for _, w in pairs)
    return Measure(
        value=round(sum(v * w for v, w in pairs) / weight, 3),
        basis=Basis.HEURISTIC,
        note="mean of the section's passages, weighted by words",
    )


def _premise(ctx: Context, starts: dict[str, float]) -> tuple[float | None, str | None]:
    story = ctx.story
    if story is None:
        return None, None
    for section in story.ordered_sections():
        for beat in story.beats:
            if beat.section_id != section.section_id or beat.function not in PREMISE_FUNCTIONS:
                continue
            offset = _offset(section.sources, beat.source.asset_id, beat.source.start)
            return starts[section.section_id] + (offset or 0.0), section.section_id
    return None, None


def _offset(sources: Sequence[RefFact], asset_id: str, start: float | None) -> float | None:
    first = [r.start for r in sources if r.asset_id == asset_id and r.start is not None]
    if start is None or not first:
        return None
    return max(0.0, start - min(first))


def _setup_share(ctx: Context, seconds: dict[str, float | None], n: Narrative) -> float | None:
    story = ctx.story
    if (
        story is None
        or n.total_seconds is None
        or n.total_seconds <= 0
        or n.premise_section is None
    ):
        return None
    setup = 0.0
    for section in story.ordered_sections():
        if section.section_id == n.premise_section:
            break
        functions = {b.function for b in story.beats if b.section_id == section.section_id}
        if functions and functions <= SETUP_FUNCTIONS:
            setup += seconds[section.section_id] or 0.0
    return round(setup / n.total_seconds, 3)
