"""Animation compiler: composition + the style's motion tokens -> backend-neutral keyframes.

Patterns (``pop_in``, ``slide_in``, ``fade_in``, ``wipe_in``, ``cut`` / ``pop_out``,
``slide_out``, ``fade_out``, ``cut``), easings, duration ranges, stagger, overshoot, minimum
hold and the intensity ceiling all come from the style. Intensity 0-4 picks inside the style's
ranges (0 = cut, 1 = gentle/longest, 4 = snappiest/largest amplitude). Every keyframe time is
snapped to the frame grid, so each rendered frame samples the same state everywhere.
"""

import hashlib
import re
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
from media_house.modules.motion_graphics_engine.domain.presets import (
    EXIT_FOR,
    PRESETS,
    parse_number,
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
        GraphicKind.CALLOUT,
        GraphicKind.COMPARISON_MATRIX,
        GraphicKind.STRIKE_VALUE,
        GraphicKind.URL_CARD,
    }
)
#: Executors keep their own vocabulary (caption looks, transition covers).
_EXECUTORS = frozenset({GraphicKind.CAPTION, GraphicKind.TRANSITION_OVERLAY})
#: Small cards that float gently while they are held (never captions, labels or full frames).
_FLOATING = frozenset(
    {
        GraphicKind.PRODUCT_STICKER,
        GraphicKind.SILHOUETTE_TEASER,
        GraphicKind.CALLOUT,
        GraphicKind.TEXT_PANEL,
        GraphicKind.HIGHLIGHT_PANEL,
        GraphicKind.PRICE_TAG,
        GraphicKind.DEADLINE_CHIP,
        GraphicKind.URL_CARD,
        GraphicKind.CTA,
        GraphicKind.RANK_CARD,
        GraphicKind.FEATURE_LIST,
        GraphicKind.STRIKE_VALUE,
    }
)
#: Float: amplitude (px, the picture of a full-screen card: two thirds), one swing (s) and the
#: sticker's sway (degrees).
#: A held card's punch: up, held at its peak, back (long enough to be seen, not a flicker).
_BEAT = Fraction(4, 5)
_BEAT_MOVE = Fraction(1, 4)
_FLOAT_PX = 7.0
_FLOAT_HALF = Fraction(6, 5)
_FLOAT_SWAY = 0.6
#: A struck value: the strike line is drawn after the value has arrived (the gesture).
_STRIKE = ".strike"


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
        """The request's patterns, else the preset's choreography pair (the same for every
        graphic of the kind); a named entry without an exit leaves with its natural pair."""
        a = self.request.animation
        kind = self.request.kind
        if kind in _EXECUTORS:
            entry = a.entry or self._look_entry() or self.m.entry_pattern
            exit_ = a.exit or self.m.exit_pattern
        else:
            preset = PRESETS[kind]
            entry = a.entry or preset.entry
            exit_ = a.exit or (preset.exit if entry == preset.entry else EXIT_FOR.get(entry, ""))
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
            self.caption(entry, entry_s, exit_, exit_s)
        elif kind is GraphicKind.TRANSITION_OVERLAY:
            self.overlay()
        else:
            if self.comp.full_frame:
                self.full_frame_entry(entry_s)
                if self.portrait:
                    self.hold_beat(exit_s)
                    self.zoom_exit(exit_s)
                else:
                    self.exit("fade_out", exit_s, intensity)
            else:
                self.entry(entry, entry_s, intensity)
                self.exit(exit_, exit_s, intensity)
            self.emphasis(intensity)
            self.float_idle(exit_s)
            self.counter(entry_s)
            self.typing(entry_s)
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
        strikes = [e for e in elements if e.id.endswith(_STRIKE)]
        panels = [
            e for e in elements if reveal and e.type is ElementType.PANEL and e not in strikes
        ]
        content = [e for e in elements if e not in panels and e not in strikes]
        stagger = _f(self.m.stagger)
        start = Fraction(0)
        if pattern == "cut":
            self.enter_peak = Fraction(0)
            return
        for e in panels:
            self.tween(e.id, Prop.REVEAL, Fraction(0), seconds, 0.0, 1.0, self.m.move_easing)
        if panels:
            start = seconds * Fraction(2, 5)
        rows = _reading_rows(content)
        budget = seconds  # all staggers together never exceed one entry duration
        step = min(stagger, budget / max(len(rows), 1)) if len(rows) > 1 else Fraction(0)
        end = start
        for i, row in enumerate(rows):
            s = start + step * i
            end = s + seconds
            for e in row:
                self.enter_one(e, pattern, s, end, intensity, inside=bool(panels))
        self.enter_peak = max(end, seconds)
        for e in strikes:  # crossed out once the value is readable
            self.tween(
                e.id, Prop.REVEAL, self.enter_peak, self.enter_peak + seconds, 0.0, 1.0, "enter"
            )
            self.event(EventKind.EMPHASIS, self.enter_peak, max(intensity, 1) / 4, e.id)
            self.enter_peak += seconds

    def full_frame_entry(self, seconds: Fraction) -> None:
        """A full-screen card: the ground fades in fast, the pictures fly up into place one after
        another (easing out, a slight scale), then the text rows slide in, staggered."""
        if seconds == 0:
            return
        stagger = _f(self.m.stagger)
        grounds = [e for e in self.comp.elements if e.id.endswith((".ground", ".plate"))]
        pictures = [
            e for e in self.comp.elements if e.type is ElementType.IMAGE and e not in grounds
        ]
        rest = [e for e in self.comp.elements if e not in grounds and e not in pictures]
        for e in grounds:
            self.tween(
                e.id, Prop.OPACITY, Fraction(0), seconds * Fraction(3, 5), 0.0, 1.0, "linear"
            )
        lift = self.style.graphics.spacing_px * 3
        # a Short: the cut-out pops from smaller, the rows rise from below (a slide + scale)
        low = 0.6 if self.portrait else 0.9
        end = seconds
        for i, e in enumerate(pictures):
            # a Short: the cut-out arrives with the ground (no empty dark frame between)
            s = (Fraction(0) if self.portrait else seconds * Fraction(1, 5)) + stagger * 3 * i
            end = max(end, s + seconds * 2)
            self.tween(e.id, Prop.Y, s, s + seconds * 2, lift, 0.0, "enter")
            self.tween(e.id, Prop.SCALE, s, s + seconds * 2, low, 1.0, "enter")
            self.tween(e.id, Prop.OPACITY, s, s + seconds / 2, 0.0, 1.0, "linear")
        rows = _reading_rows(rest)
        first = seconds * Fraction(2, 5)
        for i, row in enumerate(rows):
            s = first + stagger * 2 * i
            end = max(end, s + seconds * Fraction(3, 2))
            for e in row:
                if e.type is ElementType.PANEL and e.id.endswith(".rule"):
                    self.tween(e.id, Prop.REVEAL, s, s + seconds, 0.0, 1.0, self.m.move_easing)
                    continue
                if self.portrait:
                    dy = self.style.graphics.spacing_px * 2
                    self.tween(e.id, Prop.Y, s, s + seconds * Fraction(3, 2), dy, 0.0, "enter")
                    self.tween(e.id, Prop.SCALE, s, s + seconds * Fraction(3, 2), 0.8, 1.0, "enter")
                else:
                    dx = -self.style.graphics.spacing_px
                    self.tween(e.id, Prop.X, s, s + seconds * Fraction(3, 2), dx, 0.0, "enter")
                self.tween(e.id, Prop.OPACITY, s, s + seconds / 2, 0.0, 1.0, "linear")
        self.enter_peak = end

    @property
    def portrait(self) -> bool:
        return self.style.height > self.style.width

    def hold_beat(self, exit_s: Fraction) -> None:
        """A Short's full-screen card is never still for long: halfway through its hold the card
        bumps, its pictures punch in and the price pops (an emphasis a sound can land on)."""
        start, end = self.enter_peak, self.duration - exit_s
        if end - start < _BEAT + Fraction(1, 5):
            return
        at = self.snap((start + end) / 2 - _BEAT / 2)
        values = [e.id for e in self.comp.elements if re.search(r"\.v\d+(\.count)?$", e.id)]
        last = max(values, key=lambda i: int(re.findall(r"\.v(\d+)", i)[-1]), default=None)
        price = {e.id for e in self.comp.elements if e.id.endswith(".price") or e.id == last}
        pops = [e for e in self.comp.elements if not e.id.endswith((".ground", ".plate"))]
        for e in pops:
            # the whole card bumps; the price (a data card's last value) and the pictures harder
            peak = 1.28 if e.id in price else 1.22 if e.type is ElementType.IMAGE else 1.125
            self.tween(e.id, Prop.SCALE, at, at + _BEAT_MOVE, 1.0, peak, "enter")
            self.tween(e.id, Prop.SCALE, at + _BEAT - _BEAT_MOVE, at + _BEAT, peak, 1.0, "enter")
        if pops:
            self.event(EventKind.EMPHASIS, at, 0.5, next(iter(price), pops[0].id))

    def zoom_exit(self, seconds: Fraction) -> None:
        """A Short's full-screen card leaves by zooming through: its content grows towards the
        viewer while everything fades (the next shot feels pushed into)."""
        if seconds == 0:
            return
        start = self.duration - seconds
        for e in self.comp.elements:
            if not e.id.endswith((".ground", ".plate")):
                self.tween(e.id, Prop.SCALE, start, self.duration, 1.0, 1.25, self.m.exit_easing)
            self.tween(e.id, Prop.OPACITY, start, self.duration, 1.0, 0.0, self.m.exit_easing)

    def float_idle(self, exit_s: Fraction) -> None:
        """While held, a small card floats up and down (a sticker also sways a little); on a
        full-screen card only its pictures float. Each graphic has its own phase, so cards on
        screen together never move in step. Deterministic."""
        kind = self.request.kind
        if self.comp.full_frame:
            targets = [
                e
                for e in self.comp.elements
                if e.type is ElementType.IMAGE and not e.id.endswith(".plate")
            ]
            amplitude = _FLOAT_PX * 2 / 3
        elif kind in _FLOATING:
            targets = list(self.comp.elements)
            amplitude = _FLOAT_PX
        else:
            return
        start, end = self.enter_peak, self.duration - exit_s
        if not targets or end - start < _FLOAT_HALF:
            return
        digest = hashlib.sha256(self.request.request_id.encode()).digest()
        sign = 1.0 if digest[0] % 2 else -1.0
        sway = kind in (GraphicKind.PRODUCT_STICKER, GraphicKind.SILHOUETTE_TEASER)
        for i, e in enumerate(targets):
            phase = sign if not self.comp.full_frame or i % 2 == 0 else -sign
            t, value = start + _FLOAT_HALF / 2, phase
            self.key(e.id, Prop.Y, start, 0.0, "linear")
            self.key(e.id, Prop.Y, t, value * amplitude, self.m.move_easing)
            while t + _FLOAT_HALF <= end:
                t, value = t + _FLOAT_HALF, -value
                self.key(e.id, Prop.Y, t, value * amplitude, self.m.move_easing)
            self.key(e.id, Prop.Y, end, 0.0, self.m.move_easing)
            if sway and e.type is ElementType.IMAGE:
                self.key(e.id, Prop.ROTATION, start, 0.0, "linear")
                self.key(e.id, Prop.ROTATION, end, -phase * _FLOAT_SWAY, self.m.move_easing)

    def enter_one(
        self,
        e: Element,
        pattern: str,
        s: Fraction,
        end: Fraction,
        intensity: int,
        *,
        inside: bool = False,
    ) -> None:
        """``inside``: content of a card whose panel wipes open: it slides a short way in the
        reading direction (the card itself already moved)."""
        fade_end = s + (end - s) * Fraction(2, 5)
        easing = self.m.entry_easing
        if pattern == "pop_in":
            low = min(max(0.6 - 0.1 * (intensity - 2), 0.3), 0.85)
            self.tween(e.id, Prop.SCALE, s, end, low, 1.0, easing)
            self.tween(e.id, Prop.OPACITY, s, fade_end, 0.0, 1.0, "linear")
            if intensity >= 4 and e.type is not ElementType.TEXT:
                self.tween(e.id, Prop.ROTATION, s, end, -self.style.graphics.tilt_min, 0.0, easing)
        elif pattern == "slide_in":
            distance = self.style.graphics.spacing_px * (1 if inside else 1 + intensity)
            sign = -1 if inside or (e.rect.left + e.rect.width / 2) < self.style.width / 2 else 1
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
            self.count_figures(entry_s)
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

    def count_figures(self, entry_s: Fraction) -> None:
        """Card figures marked ``.count`` (a Short's facts) count up from zero while the card
        arrives, landing on the exact value a little after the entry."""
        hold_end = self.duration - _f(self.m.exit_max)
        end = min(entry_s + Fraction(3, 5), hold_end)
        for e in self.comp.elements:
            if e.type is not ElementType.TEXT or e.text is None or not e.id.endswith(".count"):
                continue
            point = parse_number(e.text.text)
            if point is None or _f(point.value) <= 0 or end <= entry_s / 2:
                continue
            self.tween(e.id, Prop.COUNT, entry_s / 2, end, 0.0, float(_f(point.value)), "enter")

    def typing(self, entry_s: Fraction) -> None:
        """A typed address: its characters appear one by one once the card is in."""
        c = self.request.content
        if self.request.kind is not GraphicKind.URL_CARD or c.variant != "typing":
            return
        target = next((e for e in self.comp.elements if e.id.endswith(".head")), None)
        if target is None or target.text is None:
            return
        chars = len(target.text.text)
        hold_end = self.duration - _f(self.m.exit_max)
        end = min(entry_s + min(max(Fraction(chars, 20), Fraction(4, 5)), Fraction(9, 5)), hold_end)
        self.tween(target.id, Prop.TYPE, entry_s, end, 0.0, float(chars), "linear")

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

    def caption(self, entry: str, entry_s: Fraction, exit_: str, exit_s: Fraction) -> None:
        """Words pop in as spoken; the line itself pops / slides in and out with the group's
        patterns; sticker emoji fly in and out as their own elements."""
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
        for e in self.comp.elements:
            if ".emoji" in e.id:
                k = int(e.id.rsplit(".emoji", 1)[1])
                self.sticker(e, self.request.content.emojis[k])
        lift = float(self.style.graphics.spacing_px)
        if entry in ("pop_in", "slide_in") and entry_s:
            self.tween(text.id, Prop.SCALE, Fraction(0), entry_s, 0.86, 1.0, self.m.entry_easing)
            rise = lift * (2 if entry == "slide_in" else 1)
            self.tween(text.id, Prop.Y, Fraction(0), entry_s, rise, 0.0, self.m.entry_easing)
            if not self.captions.word_reveal:
                self.tween(text.id, Prop.OPACITY, Fraction(0), entry_s / 2, 0.0, 1.0, "linear")
        if exit_s:
            leave = self.duration - exit_s
            self.tween(text.id, Prop.OPACITY, leave, self.duration, 1.0, 0.0, self.m.exit_easing)
            if exit_ in ("pop_out", "slide_out"):
                move = self.m.move_easing
                self.tween(text.id, Prop.SCALE, leave, self.duration, 1.0, 0.9, move)
                self.tween(text.id, Prop.Y, leave, self.duration, 0.0, -lift, move)
        self.enter_peak = Fraction(0)

    def sticker(self, element: Element, emoji: TimedEmoji) -> None:
        """A sticker emoji flies in on its word (up from below, scale overshoot, turning into a
        slight tilt), holds, and flies out (up, shrinking, fading) as its group ends."""
        start = _f(emoji.local_start)
        room = self.duration - start
        fly_in = min(Fraction(1, 4), room * Fraction(2, 5))
        fly_out = min(Fraction(1, 5), room * Fraction(3, 10))
        # gone one frame before the clip ends, so its last drawn frame shows it left
        gone = max(self.duration - 1 / self.fps, start + fly_in + fly_out)
        peak, settle, out = start + fly_in * Fraction(3, 5), start + fly_in, gone - fly_out
        # the tilt side alternates between groups, decided by the group id (deterministic)
        tilt = 8.0 if _digest(self.request.request_id)[0] % 2 else -8.0
        lift = element.rect.height * 0.6
        # leaving moves from its first frame (the style's exit easing barely moves in 5 frames)
        eid, leave = element.id, self.m.move_easing
        self.tween(eid, Prop.SCALE, start, peak, 0.2, 1.25, "enter")
        self.key(eid, Prop.SCALE, settle, 1.0, self.m.move_easing)
        self.tween(eid, Prop.SCALE, out, gone, 1.0, 0.4, leave)
        self.tween(eid, Prop.OPACITY, start, start + fly_in * Fraction(2, 5), 0.0, 1.0, "linear")
        self.tween(eid, Prop.OPACITY, out, gone, 1.0, 0.0, "linear")
        self.tween(eid, Prop.ROTATION, start, settle, -3 * tilt, tilt, "enter")
        self.tween(eid, Prop.ROTATION, out, gone, tilt, 3 * tilt, leave)
        self.tween(eid, Prop.Y, start, settle, lift, 0.0, "enter")
        self.tween(eid, Prop.Y, out, gone, 0.0, -lift, leave)
        self.event(EventKind.EMPHASIS, start, 0.5, eid)

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


def _reading_rows(elements: list[Element]) -> list[list[Element]]:
    """Elements grouped into visual rows (overlapping vertical spans), top to bottom, each row
    left to right: the stagger follows the reading order, not the element ids."""
    rows: list[list[Element]] = []
    for e in sorted(elements, key=lambda e: (e.rect.top, e.rect.left, e.id)):
        first = rows[-1][0].rect if rows else None
        if first is not None and first.top <= e.rect.top + e.rect.height // 2 < first.bottom:
            rows[-1].append(e)
        else:
            rows.append([e])
    return [sorted(r, key=lambda e: (e.rect.left, e.id)) for r in rows]


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
