"""Caption segments -> motion graphics CAPTION requests, and the render results back.

One request per caption GROUP (not per sentence): motion graphics lays out one text block per
request with one run per timed word, so a group is exactly what appears and leaves together.
Each group is its own short ProRes 4444 clip placed at its timeline start, and motion's library
cache reuses every unchanged group on a replan: only edited groups render again.
"""

from collections.abc import Sequence
from dataclasses import replace

from media_house.modules.caption_engine.domain.engine import EMOJI_NOT_RENDERED
from media_house.modules.caption_engine.domain.facts import frac, rat
from media_house.modules.caption_engine.domain.model import CaptionSegment, EmojiDecision, RenderRef
from media_house.modules.caption_engine.domain.profile import CaptionProfile
from media_house.modules.caption_engine.domain.values import (
    LEVEL_RANK,
    EmphasisLevel,
    QcCategory,
    QcIssue,
    RenderStatus,
    Severity,
)
from media_house.modules.motion_graphics_engine.application.contracts import (
    Anchor,
    AnimationRequest,
    GraphicContent,
    GraphicKind,
    GraphicRequest,
    ItemStatus,
    MotionGraphicsConfig,
    Origin,
    Placement,
    RenderedGraphics,
    SyncPoint,
    TimedEmoji,
    TimedWord,
    Timing,
)
from media_house.modules.motion_graphics_engine.application.contracts import (
    QcIssue as MotionIssue,
)


def _timed(segment: CaptionSegment) -> tuple[tuple[TimedWord, ...], tuple[TimedEmoji, ...]]:
    """Motion takes one word per run: a token with inner spaces is split, time shared evenly.
    Each planned emoji follows the last run of its word, at its own planned time."""
    out: list[TimedWord] = []
    last: dict[str, int] = {}
    for w in segment.words:
        parts = w.display.split()
        start, end = frac(w.local_start), frac(w.local_end)
        step = (end - start) / max(1, len(parts))
        for k, part in enumerate(parts):
            out.append(
                TimedWord(
                    text=part,
                    local_start=rat(start + k * step),
                    local_end=rat(start + (k + 1) * step),
                    emphasis=w.emphasis is not EmphasisLevel.NORMAL,
                    emphasis_level=LEVEL_RANK[w.emphasis],
                )
            )
            last[w.word_id] = len(out) - 1
    emojis = tuple(
        TimedEmoji(
            emoji=e.emoji, after_word=last[e.word_id], local_start=e.local_start, entry=e.animation
        )
        for e in segment.emojis
        if e.word_id in last
    )
    return tuple(out), emojis


def request_for(segment: CaptionSegment) -> GraphicRequest:
    a = segment.animation
    start, end = frac(segment.timeline_start), frac(segment.timeline_end)
    emphasised = [w for w in segment.words if w.emphasis is not EmphasisLevel.NORMAL]
    words, emojis = _timed(segment)
    return GraphicRequest(
        request_id=segment.segment_id,
        kind=GraphicKind.CAPTION,
        content=GraphicContent(
            words=words,
            emojis=emojis,
            look=segment.layout.look,
            source=f"caption_engine {segment.mode.value}: {', '.join(segment.reasons)}",
        ),
        timing=Timing(local_duration=rat(end - start), timeline_start=segment.timeline_start),
        placement=Placement(zone=segment.layout.zone, anchor=Anchor(segment.layout.anchor)),
        animation=AnimationRequest(
            entry=a.entry, exit=a.exit, emphasis_at=a.emphasis_at, intensity=a.intensity
        ),
        sync=tuple(
            SyncPoint(ref=w.word_id, local_at=w.local_start, label=w.text) for w in emphasised
        ),
        reason=", ".join(segment.reasons),
        origin=Origin.CAPTION,
        section_id=segment.section_id,
    )


def motion_config(profile: CaptionProfile) -> MotionGraphicsConfig:
    """The caption execution knobs motion owns, set from this module's animation request."""
    a = profile.animation
    return MotionGraphicsConfig(
        overrides={
            "captions.word_reveal": a.reveal == "word_by_word",
            "captions.active_word": "highlight" if a.active_word else "none",
        }
    )


def _issue(found: MotionIssue) -> QcIssue:
    return QcIssue(
        code=f"render.{found.code}",
        severity=Severity(found.severity.value),
        category=QcCategory(found.category.value),
        message=found.message,
        item_id=found.item_id,
        timeline_start=found.timeline_start,
        timeline_end=found.timeline_end,
        evidence=found.evidence or found.fix,
    )


def apply_renders(
    segments: Sequence[CaptionSegment], rendered: RenderedGraphics
) -> tuple[list[CaptionSegment], list[QcIssue]]:
    by_id = {i.request_id: i for i in rendered.items}
    out: list[CaptionSegment] = []
    issues: list[QcIssue] = []
    for s in segments:
        item = by_id.get(s.segment_id)
        if item is None:
            out.append(s)
            continue
        issues += [_issue(q) for q in item.qc]
        if item.status is ItemStatus.OK and item.asset_id is not None:
            status = RenderStatus.RENDERED if item.created else RenderStatus.REUSED
        elif item.status is ItemStatus.NOT_AVAILABLE:
            status = RenderStatus.NOT_AVAILABLE
        else:
            status = RenderStatus.FAILED
        frames = item.frames
        drawn = not any(q.code == "emoji.not_available" for q in item.qc)
        out.append(
            replace(
                s,
                emojis=_emojis(s, status if drawn else RenderStatus.NOT_AVAILABLE, item.detail),
                render=RenderRef(
                    status=status,
                    asset_id=item.asset_id,
                    start_frame=frames.start_frame if frames else None,
                    end_frame=frames.end_frame if frames else None,
                    detail=item.detail,
                    asset_start_frame=item.render.asset_start_frame if item.render else 0,
                ),
            )
        )
    return out, issues


def not_rendered(
    segments: Sequence[CaptionSegment], status: RenderStatus, detail: str
) -> list[CaptionSegment]:
    return [
        s
        if s.render.status is RenderStatus.FAILED
        else replace(
            s, emojis=_emojis(s, status, detail), render=RenderRef(status=status, detail=detail)
        )
        for s in segments
    ]


def _emojis(
    segment: CaptionSegment, status: RenderStatus, detail: str
) -> tuple[EmojiDecision, ...]:
    """The group's emoji share its render; one the style cannot draw stays planned."""
    if status is RenderStatus.NOT_AVAILABLE and not detail:
        detail = EMOJI_NOT_RENDERED
    shown = "" if status in (RenderStatus.RENDERED, RenderStatus.REUSED) else detail
    return tuple(replace(e, render=status, detail=shown) for e in segment.emojis)
