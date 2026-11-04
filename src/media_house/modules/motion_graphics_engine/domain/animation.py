"""Animation compiler: composition + the style's motion tokens -> backend-neutral keyframes.

Patterns (``pop_in``, ``slide_in``, ``fade_in``, ``wipe_in``, ``cut`` / ``pop_out``,
``slide_out``, ``fade_out``, ``cut``), easings, duration ranges, stagger, overshoot, minimum
hold and the intensity ceiling all come from the style. Intensity 0-4 picks inside the style's
ranges (0 = cut, 1 = gentle/longest, 4 = snappiest/largest amplitude). Every keyframe time is
snapped to the frame grid, so each rendered frame samples the same state everywhere.
"""

import hashlib
from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.model import (
    AnimationEvent,
    CompiledAnimation,
    Composition,
    Element,
    GraphicRequest,
    Keyframe,
    MotionSpec,
    StyleFacts,
    TimedEmoji,
    Track,
)
from media_house.modules.motion_graphics_engine.domain.qc import issue
from media_house.modules.motion_graphics_engine.domain.values import (
    ElementType,
    EventKind,
    GraphicKind,
    OverlayDirection,
    OverlayKind,
    Prop,
    QcCategory,
    QcIssue,
    Severity,
)

ENTRY_PATTERNS = ("pop_in", "slide_in", "fade_in", "wipe_in", "cut")
EXIT_PATTERNS = ("pop_out", "slide_out", "fade_out", "cut")
#: Kinds whose panels wipe open first and whose content follows.
_REVEAL_KINDS = frozenset(
    {
        GraphicKind.LOWER_THIRD,
        GraphicKind.TEXT_PANEL,
        GraphicKind.HIGHLIGHT_PANEL,
        GraphicKind.FEATURE_LIST,
        GraphicKind.STEP_LIST,
        GraphicKind.CHART,
        GraphicKind.TIMELINE,
        GraphicKind.CTA,
        GraphicKind.COMPARISON,
        GraphicKind.SPEC_CARD,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionMotion:
    active_word: bool
    word_reveal: bool


def _f(r: Rational) -> Fraction:
    return Fraction(r.numerator, r.denominator)


def _r(f: Fraction) -> Rational:
    return Rational(max(f, Fraction(0)).numerator, max(f, Fraction(0)).denominator)


class _Compiler:
    def __init__(
        self,
        request: GraphicRequest,
        composition: Composition,
        style: StyleFacts,
        duration: Fraction,
        fps: Fraction,
        timeline_start: Fraction | None,
        captions: CaptionMotion,
    ) -> None:
        self.request = request
        self.comp = composition
        self.style = style
        self.m = style.motion
        self.duration = duration
        self.fps = fps
        self.timeline_start = timeline_start
        self.captions = captions
        self.issues: list[QcIssue] = []
        self.tracks: dict[tuple[str, Prop], list[Keyframe]] = {}
        self.events: list[AnimationEvent] = []
        self.enter_peak = Fraction(0)

    # --- helpers ----------------------------------------------------------------------------

    def snap(self, t: Fraction) -> Fraction:
        t = min(max(t, Fraction(0)), self.duration)
        return Fraction(round(t * self.fps), 1) / self.fps

    def key(self, target: str, prop: Prop, at: Fraction, value: float, easing: str) -> None:
        frames = self.tracks.setdefault((target, prop), [])
        snapped = self.snap(at)
        if frames and _f(frames[-1].local_at) > snapped:
            snapped = _f(frames[-1].local_at)
        frames.append(Keyframe(local_at=_r(snapped), value=round(value, 4), easing=easing))

    def tween(
        self,
        target: str,
        prop: Prop,
        start: Fraction,
        end: Fraction,
        a: float,
        b: float,
        easing: str,
    ) -> None:
        frames = self.tracks.get((target, prop))
        if not frames:
            self.key(target, prop, Fraction(0), a, "linear")
        self.key(target, prop, start, a, "linear")
        self.key(target, prop, end, b, easing)

    def event(self, kind: EventKind, at: Fraction, strength: float, ref: str = "") -> None:
        at = self.snap(at)
        self.events.append(
            AnimationEvent(
                graphic_id=self.request.request_id,
                kind=kind,
                local_at=_r(at),
                timeline_at=_r(self.timeline_start + at)
                if self.timeline_start is not None
                else None,
                strength=round(strength, 3),
                ref=ref,
            )
        )

    def warn(self, code: str, message: str, creative: bool = True) -> None:
        self.issues.append(
            issue(
                code,
                Severity.WARNING,
                message,
                self.request.request_id,
                category=QcCategory.CREATIVE if creative else QcCategory.TECHNICAL,
            )
        )

    # --- motion choice ----------------------------------------------------------------------

    def intensity(self) -> int:
        wanted = self.request.animation.intensity
        if wanted is None:
            wanted = {"calm": 1, "snappy": 2, "punchy": 2}.get(self.m.character, 2)
        if not 0 <= wanted <= 4:
            self.warn("motion.intensity_range", f"intensity {wanted} is outside 0-4", False)
            wanted = min(max(wanted, 0), 4)
        if wanted > self.m.intensity_ceiling:
            self.warn(
                "motion.intensity",
                f"intensity {wanted} is above the style ceiling {self.m.intensity_ceiling}:"
                " lowered",
            )
            wanted = self.m.intensity_ceiling
        return wanted

    def span(self, low: Rational, high: Rational, intensity: int) -> Fraction:
        lo, hi = _f(low), _f(high)
        if intensity <= 1:
            return hi
        return hi - (hi - lo) * Fraction(intensity - 1, 3)

    def patterns(self, intensity: int) -> tuple[str, str]:
        a = self.request.animation
        entry = a.entry or self._look_entry() or self.m.entry_pattern
        exit_ = a.exit or self.m.exit_pattern
        if entry not in ENTRY_PATTERNS:
            self.warn("motion.pattern", f"unknown entry {entry!r}: the style's entry is used")
            entry = self.m.entry_pattern
        if exit_ not in EXIT_PATTERNS:
            self.warn("motion.pattern", f"unknown exit {exit_!r}: the style's exit is used")
            exit_ = self.m.exit_pattern
        if intensity == 0:
            return "cut", "cut"
        if self.m.character == "calm" or intensity == 1:
            entry = "fade_in" if entry in ("pop_in", "slide_in") else entry
            exit_ = "fade_out" if exit_ in ("pop_out", "slide_out") else exit_
        return entry, exit_

    def _look_entry(self) -> str | None:
        texts = [e.text for e in self.comp.elements if e.text is not None]
        if not texts:
            return None
        try:
            return self.style.look(texts[0].look).entry
        except KeyError:
            return None

    # --- compile ----------------------------------------------------------------------------

    def run(self) -> CompiledAnimation:
        kind = self.request.kind
        intensity = self.intensity()
        entry, exit_ = self.patterns(intensity)
        entry_s = (
            Fraction(0)
            if entry == "cut"
            else self.span(self.m.entry_min, self.m.entry_max, intensity)
        )
        exit_s = (
            Fraction(0)
            if exit_ == "cut"
            else self.span(self.m.exit_min, self.m.exit_max, intensity)
        )
        if kind is GraphicKind.TRANSITION_OVERLAY:
            entry_s = exit_s = self.duration / 2
        hold = self.duration - entry_s - exit_s
        min_hold = _f(self.m.min_hold)
        texts = any(e.type is ElementType.TEXT for e in self.comp.elements)
        if (
            texts
            and kind not in (GraphicKind.CAPTION, GraphicKind.TRANSITION_OVERLAY)
            and hold < min_hold
        ):
            self.warn(
                "timing.short_hold",
                f"text stays fully visible {float(hold):.2f} s, the style asks for"
                f" {float(min_hold):.2f} s",
            )
            if hold < self.duration / 2:
                factor = (
                    (self.duration / 2) / (entry_s + exit_s) if entry_s + exit_s else Fraction(1)
                )
                entry_s, exit_s = entry_s * factor, exit_s * factor
        strength = max(intensity, 1) / 4
        if kind is GraphicKind.CAPTION:
            self.caption(exit_s if exit_ != "cut" else Fraction(0))
        elif kind is GraphicKind.TRANSITION_OVERLAY:
            self.overlay()
        else:
            self.entry(entry, entry_s, intensity)
            self.exit(exit_, exit_s, intensity)
            self.emphasis(intensity)
            self.counter(entry_s)
            self.tracking()
        self.event(EventKind.ENTER_START, Fraction(0), strength)
        peak = self.enter_peak if kind is not GraphicKind.TRANSITION_OVERLAY else self.duration / 2
        self.event(
            EventKind.ENTER_PEAK, peak if kind is not GraphicKind.CAPTION else Fraction(0), strength
        )
        self.event(EventKind.EXIT_START, self.duration - exit_s, strength / 2)
        self.event(EventKind.EXIT_END, self.duration, strength / 2)
        for point in self.request.sync:
            self.event(EventKind.EMPHASIS, _f(point.local_at), strength, point.ref)
        events = tuple(sorted(self.events, key=lambda e: (_f(e.local_at), e.kind.value, e.ref)))
        tracks = tuple(
            Track(target=target, prop=prop, keyframes=tuple(frames))
            for (target, prop), frames in sorted(
                self.tracks.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
            )
        )
        motion = MotionSpec(
            entry=entry,
            exit=exit_,
            intensity=intensity,
            entry_seconds=_r(entry_s),
            exit_seconds=_r(exit_s),
            entry_easing=self.m.entry_easing,
            exit_easing=self.m.exit_easing,
        )
        return CompiledAnimation(
            duration=_r(self.duration), motion=motion, tracks=tracks, events=events
        )

    def entry(self, pattern: str, seconds: Fraction, intensity: int) -> None:
        elements = list(self.comp.elements)
        reveal = self.request.kind in _REVEAL_KINDS
        panels = [e for e in elements if reveal and e.type is ElementType.PANEL]
        content = [e for e in elements if e not in panels]
        stagger = _f(self.m.stagger)
        start = Fraction(0)
        if pattern == "cut":
            self.enter_peak = Fraction(0)
            return
        for e in panels:
            self.tween(e.id, Prop.REVEAL, Fraction(0), seconds, 0.0, 1.0, self.m.move_easing)
        if panels:
            start = seconds * Fraction(2, 5)
        budget = seconds  # all staggers together never exceed one entry duration
        step = min(stagger, budget / max(len(content), 1)) if len(content) > 1 else Fraction(0)
        end = start
        for i, e in enumerate(content):
            s = start + step * i
            end = s + seconds
            self.enter_one(e, pattern, s, end, intensity)
        self.enter_peak = max(end, seconds)

    def enter_one(
        self, e: Element, pattern: str, s: Fraction, end: Fraction, intensity: int
    ) -> None:
        fade_end = s + (end - s) * Fraction(2, 5)
        easing = self.m.entry_easing
        if pattern == "pop_in":
            low = min(max(0.6 - 0.1 * (intensity - 2), 0.3), 0.85)
            self.tween(e.id, Prop.SCALE, s, end, low, 1.0, easing)
            self.tween(e.id, Prop.OPACITY, s, fade_end, 0.0, 1.0, "linear")
            if intensity >= 4 and e.type is not ElementType.TEXT:
                self.tween(e.id, Prop.ROTATION, s, end, -self.style.graphics.tilt_min, 0.0, easing)
        elif pattern == "slide_in":
            distance = self.style.graphics.spacing_px * (1 + intensity)
            sign = -1 if (e.rect.left + e.rect.width / 2) < self.style.width / 2 else 1
            self.tween(e.id, Prop.X, s, end, sign * distance, 0.0, self.m.move_easing)
            self.tween(e.id, Prop.OPACITY, s, fade_end, 0.0, 1.0, "linear")
            if self.m.motion_blur and intensity >= 3:
                self.tween(
                    e.id, Prop.BLUR, s, end, self.style.graphics.spacing_px / 6, 0.0, "linear"
                )
        elif pattern == "wipe_in":
            self.tween(e.id, Prop.REVEAL, s, end, 0.0, 1.0, self.m.move_easing)
        else:  # fade_in
            self.tween(e.id, Prop.OPACITY, s, end, 0.0, 1.0, "enter")

    def exit(self, pattern: str, seconds: Fraction, intensity: int) -> None:
        if pattern == "cut" or seconds == 0:
            return
        start = self.duration - seconds
        easing = self.m.exit_easing
        for e in self.comp.elements:
            if pattern == "pop_out":
                self.tween(e.id, Prop.SCALE, start, self.duration, 1.0, 0.7, easing)
                self.tween(e.id, Prop.OPACITY, start, self.duration, 1.0, 0.0, easing)
            elif pattern == "slide_out":
                distance = self.style.graphics.spacing_px * (1 + intensity)
                sign = -1 if (e.rect.left + e.rect.width / 2) < self.style.width / 2 else 1
                self.tween(e.id, Prop.X, start, self.duration, 0.0, sign * distance, easing)
                self.tween(e.id, Prop.OPACITY, start, self.duration, 1.0, 0.0, easing)
            else:
                self.tween(e.id, Prop.OPACITY, start, self.duration, 1.0, 0.0, easing)

    def emphasis(self, intensity: int) -> None:
        moments = self.request.animation.emphasis_at
        if not moments:
            return
        seconds = self.span(self.m.emphasis_min, self.m.emphasis_max, intensity)
        peak = 1.0 + self.m.overshoot * max(intensity, 1) / 2
        targets = [e for e in self.comp.elements if e.type is ElementType.TEXT]
        for at in moments:
            t = _f(at)
            if not self.enter_peak <= t <= self.duration - seconds:
                self.warn(
                    "motion.emphasis_outside", f"emphasis at {float(t):.2f} s is not in the hold"
                )
                continue
            for e in targets:
                self.tween(e.id, Prop.SCALE, t, t + seconds / 2, 1.0, peak, self.m.move_easing)
                self.key(e.id, Prop.SCALE, t + seconds, 1.0, self.m.move_easing)
            self.event(EventKind.EMPHASIS, t, max(intensity, 1) / 4)

    def counter(self, entry_s: Fraction) -> None:
        c = self.request.content
        if not (
            self.request.kind is GraphicKind.CHART
            and c.chart is not None
            and c.chart.value == "counter"
        ):
            return
        target = next((e for e in self.comp.elements if e.id.endswith(".count")), None)
        if target is None:
            return
        value = float(_f(c.data[0].value))
        hold_end = self.duration - _f(self.m.exit_max)
        end = (
            _f(self.request.sync[0].local_at)
            if self.request.sync
            else entry_s + (hold_end - entry_s) / 2
        )
        end = min(max(end, entry_s), hold_end)
        self.tween(target.id, Prop.COUNT, entry_s / 2, end, 0.0, value, "linear")

    def tracking(self) -> None:
        track = self.request.placement.track
        if self.request.kind is not GraphicKind.TRACKING_LABEL or len(track) < 2:
            return
        zone = self.style.zone(self.comp.zone or "")
        x0, y0 = track[0].x * self.style.width, track[0].y * self.style.height
        rect = self.comp.rect
        clamped = False
        for point in track:
            dx = point.x * self.style.width - x0
            dy = point.y * self.style.height - y0
            if zone is not None:
                ndx = min(max(dx, zone.left - rect.left), zone.left + zone.width - rect.right)
                ndy = min(max(dy, zone.top - rect.top), zone.top + zone.height - rect.bottom)
                clamped = clamped or (ndx, ndy) != (dx, dy)
                dx, dy = ndx, ndy
            for e in self.comp.elements:
                self.key(e.id, Prop.X, _f(point.local_at), dx, "linear")
                self.key(e.id, Prop.Y, _f(point.local_at), dy, "linear")
        if clamped:
            self.warn("track.clamped", "the label is kept inside its zone", False)
        if _f(track[-1].local_at) < self.duration - _f(self.m.exit_max):
            self.warn(
                "track.lost", "the track ends before the label: it holds its last position", False
            )

    def caption(self, exit_s: Fraction) -> None:
        words = self.request.content.words
        text = next((e for e in self.comp.elements if e.text is not None), None)
        if text is None or text.text is None:
            return
        runs = [r for line in text.text.lines for r in line.runs]
        emojis = [r for r in runs if r.id.rsplit(".", 1)[1].startswith("e")]
        runs = [r for r in runs if r not in emojis]
        pop = _f(self.m.entry_min)
        for run, word in zip(runs, words, strict=False):
            start, end = _f(word.local_start), _f(word.local_end)
            if self.captions.word_reveal:
                self.tween(
                    run.id,
                    Prop.OPACITY,
                    start,
                    start + min(pop, end - start) / 2,
                    0.0,
                    1.0,
                    "linear",
                )
                self.tween(
                    run.id,
                    Prop.SCALE,
                    start,
                    start + min(pop, end - start),
                    0.8,
                    1.0,
                    self.m.entry_easing,
                )
            if self.captions.active_word and not word.emphasis:
                self.tween(
                    run.id,
                    Prop.HIGHLIGHT,
                    start,
                    start + Fraction(1) / self.fps,
                    0.0,
                    1.0,
                    "linear",
                )
                self.tween(
                    run.id, Prop.HIGHLIGHT, end, end + Fraction(1) / self.fps, 1.0, 0.0, "linear"
                )
            level = word.emphasis_level
            strength = 0.25 * (1 + level) if level is not None else 0.5 if word.emphasis else 0.25
            self.event(EventKind.EMPHASIS, start, strength, run.id)
        for run in emojis:
            self.emoji(run.id, self.request.content.emojis[int(run.id.rsplit(".e", 1)[1])])
        if exit_s:
            self.tween(
                text.id,
                Prop.OPACITY,
                self.duration - exit_s,
                self.duration,
                1.0,
                0.0,
                self.m.exit_easing,
            )
        self.enter_peak = Fraction(0)

    def emoji(self, run_id: str, emoji: TimedEmoji) -> None:
        """An emoji punchline pops (or fades) in at its own time and stays with the group."""
        start = _f(emoji.local_start)
        seconds = min(_f(self.m.entry_max), self.duration - start)
        if emoji.entry == "fade_in":
            self.tween(run_id, Prop.OPACITY, start, start + seconds, 0.0, 1.0, "enter")
        else:
            self.tween(run_id, Prop.SCALE, start, start + seconds, 0.3, 1.0, self.m.entry_easing)
            self.tween(
                run_id, Prop.OPACITY, start, start + seconds * Fraction(2, 5), 0.0, 1.0, "linear"
            )
        self.event(EventKind.EMPHASIS, start, 0.5, run_id)

    def overlay(self) -> None:
        overlay = self.request.content.overlay
        mid = self.duration / 2
        enter, leave = "enter", self.m.exit_easing  # no overshoot on full-frame covers
        elements = list(self.comp.elements)
        w = self.style.width
        if overlay is OverlayKind.WIPE:
            way = self.request.content.direction or OverlayDirection.RIGHT
            horizontal = way in (OverlayDirection.RIGHT, OverlayDirection.LEFT)
            prop = Prop.X if horizontal else Prop.Y
            span = w if horizontal else self.style.height
            span = span if way in (OverlayDirection.RIGHT, OverlayDirection.DOWN) else -span
            for i, e in enumerate(elements):
                lag = _f(self.m.stagger) * i
                self.tween(e.id, prop, lag, mid, -span, 0.0, enter)
                self.tween(e.id, prop, mid + lag, self.duration, 0.0, span, leave)
        elif overlay in (OverlayKind.SHAPE, OverlayKind.MASK):
            for e in elements:
                self.tween(e.id, Prop.SCALE, Fraction(0), mid, 0.0, 1.0, enter)
                self.tween(e.id, Prop.OPACITY, mid, self.duration, 1.0, 0.0, leave)
        elif overlay is OverlayKind.ZOOM_TUNNEL:
            step = self.duration / (len(elements) + 2)
            for i, e in enumerate(elements):
                self.tween(e.id, Prop.SCALE, step * i, step * (i + 3), 0.05, 1.2, "linear")
                self.tween(e.id, Prop.OPACITY, step * i, step * i + step / 2, 0.0, 1.0, "linear")
                self.key(e.id, Prop.OPACITY, step * (i + 3), 0.0, "linear")
        else:
            order = sorted(elements, key=lambda e: _digest(e.id))
            leave_order = sorted(elements, key=lambda e: _digest(e.id + "out"))
            frame = Fraction(1) / self.fps
            n = len(elements)
            for i, e in enumerate(order):
                t = mid * Fraction(9, 10) * i / n
                self.tween(e.id, Prop.OPACITY, t, t + frame, 0.0, 1.0, "linear")
            for i, e in enumerate(leave_order):
                t = mid + mid * Fraction(9, 10) * i / n
                self.tween(e.id, Prop.OPACITY, t, t + frame, 1.0, 0.0, "linear")


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


def compile_animation(
    request: GraphicRequest,
    composition: Composition,
    style: StyleFacts,
    *,
    duration: Rational,
    fps: Rational,
    timeline_start: Rational | None,
    captions: CaptionMotion,
) -> tuple[CompiledAnimation, tuple[QcIssue, ...]]:
    compiler = _Compiler(
        request,
        composition,
        style,
        _f(duration),
        _f(fps),
        _f(timeline_start) if timeline_start is not None else None,
        captions,
    )
    compiled = compiler.run()
    return compiled, tuple(compiler.issues)
