"""Building graphics from requests: checks, layout, collisions, animation, frames, identity.

Pure: the application supplies the style facts, font metrics and image sizes; it renders and
stores. Frames are computed once here (``frame_span``) and nowhere else.
"""

import hashlib
import json
import math
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.animation import (
    CaptionMotion,
    compile_animation,
)
from media_house.modules.motion_graphics_engine.domain.layout import (
    ALTERNATE_ZONE,
    LayoutInputs,
    lay_out,
)
from media_house.modules.motion_graphics_engine.domain.model import (
    AnimationEvent,
    CompiledAnimation,
    Composition,
    FaceMetrics,
    FrameSpan,
    GraphicRequest,
    GraphicsPlan,
    PlannedGraphic,
    Provenance,
    RenderRecord,
    StyleFacts,
    Timing,
)
from media_house.modules.motion_graphics_engine.domain.qc import blocking, issue
from media_house.modules.motion_graphics_engine.domain.serialization import encode
from media_house.modules.motion_graphics_engine.domain.values import (
    COMPILER_VERSION,
    PLANNER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    EditRef,
    GraphicKind,
    InputRecord,
    ItemStatus,
    QcCategory,
    QcIssue,
    ResultState,
    Severity,
    StageReport,
    StyleBinding,
)

#: Graphics that never count as overlays for the density limit or collide with them.
_NOT_OVERLAYS = frozenset(
    {GraphicKind.CAPTION, GraphicKind.SPONSOR_LABEL, GraphicKind.TRANSITION_OVERLAY}
)


def digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _f(r: Rational) -> Fraction:
    return Fraction(r.numerator, r.denominator)


def _r(f: Fraction) -> Rational:
    return Rational(f.numerator, f.denominator)


def frame_span(timing: Timing, fps: Rational) -> FrameSpan:
    """``round(t * fps)`` (half to even) on exact values; frames = end - start."""
    rate = _f(fps)
    duration = _f(timing.local_duration)
    if timing.timeline_start is not None:
        start = round(_f(timing.timeline_start) * rate)
        end = round((_f(timing.timeline_start) + duration) * rate)
        snapped: Rational | None = _r(Fraction(start) / rate)
    else:
        start, end, snapped = 0, round(duration * rate), None
    frames = end - start
    return FrameSpan(
        fps=fps,
        start_frame=start,
        end_frame=end,
        frames=frames,
        timeline_start=snapped,
        duration=_r(Fraction(frames) / rate),
    )


def changing_frames(animation: CompiledAnimation, frames: int, fps: Rational) -> tuple[int, ...]:
    """The local frames whose picture differs from the frame before (frame 0 always).

    Every visual change is a keyframe segment: a tween from ``a`` to ``b`` with another value
    changes the frames in ``(a, b]``, a jump at ``t`` changes the frame at ``t``. Every other
    frame repeats its predecessor exactly, so it need not be rendered again (a still graphic
    with an entry and an exit renders its entry, ONE hold frame and its exit).
    """
    rate = _f(fps)
    changed = [False] * frames
    for track in animation.tracks:
        for a, b in zip(track.keyframes, track.keyframes[1:], strict=False):
            if a.value == b.value:
                continue
            start, end = _f(a.local_at) * rate, _f(b.local_at) * rate
            if start < end:
                first, last = math.floor(start) + 1, math.floor(end)
            else:  # a jump: the first frame at or after it
                first = last = math.ceil(end)
            for f in range(max(first, 0), min(last, frames - 1) + 1):
                changed[f] = True
    return tuple(f for f in range(frames) if f == 0 or changed[f])


def held_frames(samples: tuple[int, ...], frames: int) -> tuple[int, ...]:
    """For each output frame, the index into ``samples`` of the drawn frame it shows (the last
    drawn frame at or before it)."""
    return tuple(bisect_right(samples, f) - 1 for f in range(frames))


def resample(
    animation: CompiledAnimation, samples: tuple[int, ...], frames: int, fps: Rational
) -> CompiledAnimation:
    """``animation`` with the undrawn frames cut out: drawn frame ``samples[k]`` becomes frame
    ``k``. Every keyframe moves back by the undrawn frames before it, so a changing segment
    (only drawn frames) keeps its length and easing exactly; a constant one spanning a held
    run just gets shorter. Rendered, frame ``k`` is pixel-identical to frame ``samples[k]``."""
    rate = _f(fps)

    def moved(at: Rational) -> Rational:
        f = round(_f(at) * rate)
        # frames kept up to f, minus one; after the last frame: shifted by every undrawn one
        k = bisect_right(samples, f) - 1 if f < frames else f - (frames - len(samples))
        return _r(Fraction(k) / rate)

    tracks = tuple(
        replace(t, keyframes=tuple(replace(k, local_at=moved(k.local_at)) for k in t.keyframes))
        for t in animation.tracks
    )
    return replace(animation, tracks=tracks, duration=_r(Fraction(len(samples)) / rate))


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildSettings:
    fps: Rational
    captions: CaptionMotion
    rank_label: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Built:
    request: GraphicRequest
    frames: FrameSpan | None
    composition: Composition | None
    animation: CompiledAnimation | None
    issues: tuple[QcIssue, ...]
    fingerprint: str

    def parts(self) -> tuple[Composition, CompiledAnimation, FrameSpan]:
        """Layout, animation and frames of a usable graphic."""
        if self.composition is None or self.animation is None or self.frames is None:
            raise ValueError(f"{self.request.request_id} was not built")
        return self.composition, self.animation, self.frames

    @property
    def usable(self) -> bool:
        return (
            self.composition is not None
            and self.animation is not None
            and not blocking(self.issues)
        )


def check_request(request: GraphicRequest) -> list[QcIssue]:
    found: list[QcIssue] = []
    rid = request.request_id
    if not rid or len(rid) > 200:
        found.append(issue("request.id", Severity.ERROR, "a request id of 1-200 characters", rid))
    if _f(request.timing.local_duration) <= 0:
        found.append(issue("timing.empty", Severity.ERROR, "the duration must be positive", rid))
    if not 0.0 <= request.priority <= 1.0:
        found.append(issue("request.priority", Severity.WARNING, "priority outside 0-1", rid))
    for word in request.content.words:
        if not _f(word.local_start) < _f(word.local_end) <= _f(request.timing.local_duration):
            found.append(
                issue(
                    "caption.word_timing",
                    Severity.ERROR,
                    f"word {word.text!r} is not inside the group ({word.local_start}-"
                    f"{word.local_end})",
                    rid,
                    fix="caption_engine supplies local word times inside the group",
                )
            )
        if word.emphasis_level is not None and not 0 <= word.emphasis_level <= 3:
            found.append(issue("caption.emphasis_level", Severity.ERROR, "levels are 0-3", rid))
    words = len(request.content.words)
    for e in request.content.emojis:
        if not (
            e.emoji.strip()
            and " " not in e.emoji
            and 0 <= e.after_word < words
            and 0 <= _f(e.local_start) < _f(request.timing.local_duration)
            and e.entry in ("", "pop_in", "fade_in")
        ):
            found.append(
                issue(
                    "caption.emoji",
                    Severity.ERROR,
                    f"emoji {e.emoji!r} needs one symbol after an existing word, a start inside"
                    " the group and the entry pop_in or fade_in",
                    rid,
                )
            )
    for point in request.placement.track:
        if not (0.0 <= point.x <= 1.0 and 0.0 <= point.y <= 1.0):
            found.append(
                issue("track.outside", Severity.ERROR, "track point outside the frame", rid)
            )
    if request.kind is GraphicKind.CAPTION and request.origin.value != "caption":
        found.append(
            issue(
                "caption.origin",
                Severity.WARNING,
                "captions are decided by caption_engine (origin should be 'caption')",
                rid,
            )
        )
    return found


def item_fingerprint(request: GraphicRequest, style: StyleFacts, settings: BuildSettings) -> str:
    return digest(
        {
            "request": encode(request),
            "style": style.fingerprint,
            "fps": str(settings.fps),
            "captions": encode(settings.captions),
            "rank_label": settings.rank_label,
            "versions": [COMPILER_VERSION, VOCABULARY_VERSION],
        }
    )


def build(
    request: GraphicRequest,
    style: StyleFacts,
    metrics: Mapping[str, FaceMetrics],
    images: Mapping[str, tuple[int, int]],
    settings: BuildSettings,
    zone_override: str | None = None,
) -> Built:
    found = check_request(request)
    fingerprint = item_fingerprint(request, style, settings)
    frames = frame_span(request.timing, settings.fps)
    if frames.frames < 1:
        found.append(
            issue("timing.empty", Severity.ERROR, "shorter than one frame", request.request_id)
        )
    if blocking(found):
        return Built(
            request=request,
            frames=frames,
            composition=None,
            animation=None,
            issues=tuple(found),
            fingerprint=fingerprint,
        )
    inputs = LayoutInputs(
        style=style, metrics=metrics, images=images, rank_label=settings.rank_label
    )
    composition, layout_issues = lay_out(request, inputs, zone_override)
    found += layout_issues
    animation = None
    if composition is not None:
        animation, motion_issues = compile_animation(
            request,
            composition,
            style,
            duration=frames.duration,
            fps=settings.fps,
            timeline_start=frames.timeline_start,
            captions=settings.captions,
        )
        found += motion_issues
    return Built(
        request=request,
        frames=frames,
        composition=composition,
        animation=animation,
        issues=tuple(found),
        fingerprint=fingerprint,
    )


def _window(b: Built) -> tuple[int, int] | None:
    if b.frames is None or b.frames.timeline_start is None:
        return None
    return b.frames.start_frame, b.frames.end_frame


def _overlap(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    return a is not None and b is not None and a[0] < b[1] and b[0] < a[1]


def resolve_collisions(
    built: list[Built],
    style: StyleFacts,
    metrics: Mapping[str, FaceMetrics],
    images: Mapping[str, tuple[int, int]],
    settings: BuildSettings,
) -> list[Built]:
    """No two simultaneous graphics overlap; the style's overlay density is kept.

    Higher priority wins (ties: earlier request id). A loser first moves to the alternate
    zone (callout left <-> right); if it still overlaps it falls back (ERROR, not drawn).
    Over the density limit, the lowest-priority overlay is not drawn (WARNING, creative).
    """
    order = sorted(
        range(len(built)), key=lambda i: (-built[i].request.priority, built[i].request.request_id)
    )
    accepted: list[Built] = []
    result: dict[int, Built] = {}
    for i in order:
        b = built[i]
        if not b.usable:
            result[i] = b
            continue
        clash = _clash(b, accepted)
        if clash is not None and b.composition is not None and b.composition.zone in ALTERNATE_ZONE:
            moved = build(
                b.request, style, metrics, images, settings, ALTERNATE_ZONE[b.composition.zone]
            )
            if moved.usable and _clash(moved, accepted) is None:
                b = replace(
                    moved,
                    issues=(
                        *moved.issues,
                        issue(
                            "layout.moved",
                            Severity.INFO,
                            f"moved: {clash} uses {b.composition.zone} at the same time",
                            b.request.request_id,
                            category=QcCategory.CREATIVE,
                        ),
                    ),
                )
                clash = None
        if clash is not None:
            b = replace(
                b,
                issues=(
                    *b.issues,
                    issue(
                        "layout.overlap",
                        Severity.ERROR,
                        f"overlaps {clash} on screen at the same time",
                        b.request.request_id,
                        prop="rect",
                        fix="move one of them in time or zone",
                    ),
                ),
            )
            result[i] = b
            continue
        if b.request.kind not in _NOT_OVERLAYS:
            limit = style.graphics.max_simultaneous
            for frame in _starts(b, accepted):
                live = [
                    a
                    for a in accepted
                    if a.request.kind not in _NOT_OVERLAYS and _contains(a, frame)
                ]
                if len(live) + 1 > limit:
                    b = replace(
                        b,
                        issues=(
                            *b.issues,
                            issue(
                                "density.max_simultaneous",
                                Severity.WARNING,
                                f"more than {limit} graphics at once: lowest priority not drawn",
                                b.request.request_id,
                                category=QcCategory.CREATIVE,
                            ),
                        ),
                    )
                    break
            if any(i.code == "density.max_simultaneous" for i in b.issues):
                result[i] = b
                continue
        accepted.append(b)
        result[i] = b
    return [result[i] for i in range(len(built))]


def _starts(b: Built, accepted: list[Built]) -> list[int]:
    window = _window(b)
    if window is None:
        return []
    return [window[0]] + [
        w[0] for a in accepted if (w := _window(a)) is not None and window[0] <= w[0] < window[1]
    ]


def _contains(a: Built, frame: int) -> bool:
    w = _window(a)
    return w is not None and w[0] <= frame < w[1]


def _clash(b: Built, accepted: list[Built]) -> str | None:
    if b.composition is None or b.request.kind is GraphicKind.TRANSITION_OVERLAY:
        return None
    for a in accepted:
        if a.composition is None or a.request.kind is GraphicKind.TRANSITION_OVERLAY:
            continue
        if _overlap(_window(a), _window(b)) and a.composition.rect.overlaps(b.composition.rect):
            return a.request.request_id
    return None


def planned(
    built: Built,
    render: RenderRecord | None,
    extra: tuple[QcIssue, ...] = (),
    status: ItemStatus | None = None,
    detail: str = "",
) -> PlannedGraphic:
    issues = (*built.issues, *extra)
    if status is None:
        status = ItemStatus.OK if built.usable and not blocking(extra) else ItemStatus.FALLBACK
    usable = status is ItemStatus.OK
    return PlannedGraphic(
        graphic_id=built.request.request_id,
        request=built.request,
        status=status,
        frames=built.frames,
        composition=built.composition if usable else None,
        animation=built.animation if usable else None,
        render=render if usable else None,
        checks=issues,
        fingerprint=built.fingerprint,
        detail=detail,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanMeta:
    plan_id: str
    plan_version: int
    supersedes: str | None
    created_at: datetime
    profile: str
    profile_version: int
    configuration_fingerprint: str
    engines: tuple[tuple[str, str], ...]


def assemble_plan(
    *,
    meta: PlanMeta,
    project_id: str | None,
    edit: EditRef | None,
    style: StyleBinding | None,
    anchor_asset_id: str,
    width: int,
    height: int,
    fps: Rational,
    graphics: tuple[PlannedGraphic, ...],
    checks: tuple[QcIssue, ...],
    inputs: tuple[InputRecord, ...],
    stages: tuple[StageReport, ...],
    warnings: tuple[str, ...],
    limitations: tuple[str, ...],
) -> GraphicsPlan:
    events: list[AnimationEvent] = []
    for g in graphics:
        if g.status is ItemStatus.OK and g.animation is not None:
            events += g.animation.events
    events.sort(
        key=lambda e: (
            _f(e.timeline_at) if e.timeline_at else _f(e.local_at),
            e.graphic_id,
            e.kind.value,
        )
    )
    partial = any(
        g.status is not ItemStatus.OK and g.status is not ItemStatus.REJECTED for g in graphics
    ) or blocking(checks)
    # The last rendered frame, not start + nominal seconds: 20 s at 29.97 fps is 599 frames, and a
    # sub-frame overhang made the music tail under the end screen run past the timeline.
    appended = [
        Fraction(frame_span(t, fps).end_frame) / _f(fps)
        for g in graphics
        if g.status is not ItemStatus.REJECTED
        and g.request.kind is GraphicKind.END_SCREEN
        and (t := g.request.timing).timeline_start is not None
    ]
    return GraphicsPlan(
        plan_id=meta.plan_id,
        plan_version=meta.plan_version,
        supersedes=meta.supersedes,
        project_id=project_id,
        edit=edit,
        style=style,
        created_at=meta.created_at,
        state=ResultState.PARTIAL if partial else ResultState.OK,
        anchor_asset_id=anchor_asset_id,
        width=width,
        height=height,
        fps=fps,
        graphics=graphics,
        events=tuple(events),
        checks=checks,
        inputs=inputs,
        stages=stages,
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            planner_version=PLANNER_VERSION,
            compiler_version=COMPILER_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile=meta.profile,
            profile_version=meta.profile_version,
            configuration_fingerprint=meta.configuration_fingerprint,
            engines=meta.engines,
        ),
        warnings=warnings,
        limitations=limitations,
        schema_version=SCHEMA_VERSION,
        timeline_end_with_bookends=_r(max(appended)) if appended else None,
    )


def validate_plan(plan: GraphicsPlan) -> list[QcIssue]:
    """CRITICAL findings: the plan is inconsistent and must never be stored."""
    found: list[QcIssue] = []
    ids = [g.graphic_id for g in plan.graphics]
    if len(ids) != len(set(ids)):
        found.append(issue("plan.duplicate_ids", Severity.CRITICAL, "graphic ids are not unique"))
    for g in plan.graphics:
        if g.status is ItemStatus.OK and (
            g.composition is None or g.animation is None or g.frames is None
        ):
            found.append(
                issue(
                    "plan.incomplete",
                    Severity.CRITICAL,
                    "an OK graphic lacks its layout, animation or frames",
                    g.graphic_id,
                )
            )
        if g.frames is not None and g.frames.frames != g.frames.end_frame - g.frames.start_frame:
            found.append(
                issue(
                    "plan.frames", Severity.CRITICAL, "frame count is not end - start", g.graphic_id
                )
            )
    return found
