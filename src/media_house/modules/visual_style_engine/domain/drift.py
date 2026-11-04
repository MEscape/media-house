"""Drift validation: a consumer declares what it drew, the style answers with ``QcIssue``s.

Severity follows the rule behind the check (docs/layer4/contracts.md section 6):

* breaks a MANDATORY rule (locked token or brand rule) -> ERROR, technical (a reason does not
  unlock it; only a new identity version does);
* differs from a PREFERENCE -> WARNING, creative; with a recorded ``reason`` -> INFO, creative
  (an intentional, documented deviation).

Consumers never check tokens themselves: they call this (through ``StyleValidator``).
"""

import re
from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.visual_style_engine.domain.color import contrast_ratio, is_hex
from media_house.modules.visual_style_engine.domain.model import ResolvedStyle, rule_for
from media_house.modules.visual_style_engine.domain.profile import (
    PALETTE_NAMES,
    TYPE_ROLES,
    StyleTokens,
)
from media_house.modules.visual_style_engine.domain.values import (
    ElementKind,
    MotionPhase,
    QcCategory,
    QcIssue,
    RuleKind,
    Severity,
    Surface,
    TextCase,
    ZoneKind,
)

_DOT_PRICE = re.compile(r"\d+\.\d{2}\s*(?:€|EUR)")


@dataclass(frozen=True, slots=True, kw_only=True)
class ColorUse:
    """A colour drawn. ``token`` = the palette name or semantic meaning it is meant to be."""

    element_id: str
    hex: str
    token: str | None = None
    surface: Surface = Surface.VIDEO
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class TextUse:
    """A piece of text drawn: its type role, the face actually used and its treatment (px)."""

    element_id: str
    role: str
    family: str
    weight: int
    size_px: float
    fill_hex: str
    stroke_hex: str | None = None
    stroke_px: float = 0.0
    background_hex: str | None = None
    case: TextCase | None = None
    text: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlacementUse:
    """Where an element sits: the zone it claims and its rectangle in output pixels."""

    element_id: str
    kind: ElementKind
    zone: str
    left: int
    top: int
    width: int
    height: int
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionUse:
    element_id: str
    phase: MotionPhase
    easing: str
    duration: Rational
    intensity: int | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleUsage:
    """Everything one consumer used, against one style (``fingerprint``) and output size."""

    module: str
    fingerprint: str
    width: int
    height: int
    section_id: str | None = None
    colors: tuple[ColorUse, ...] = ()
    texts: tuple[TextUse, ...] = ()
    placements: tuple[PlacementUse, ...] = ()
    motions: tuple[MotionUse, ...] = ()


def validate_usage(style: ResolvedStyle, usage: StyleUsage) -> tuple[QcIssue, ...]:
    return tuple(_Checker(style, usage).run())


class _Checker:
    def __init__(self, style: ResolvedStyle, usage: StyleUsage) -> None:
        self.style = style
        self.usage = usage
        self.tokens: StyleTokens = style.tokens_for(usage.section_id)
        self.issues: list[QcIssue] = []

    def run(self) -> list[QcIssue]:
        style, usage = self.style, self.usage
        if usage.fingerprint != style.fingerprint:
            self._fixed("style.stale", "used a different style version than this one", None)
        if (usage.width, usage.height) != (style.output.width, style.output.height):
            self._fixed(
                "style.output_mismatch",
                f"drawn for {usage.width}x{usage.height}, style is for"
                f" {style.output.width}x{style.output.height}",
                None,
            )
        for c in usage.colors:
            self._color(c.element_id, c.hex, c.token, c.surface, c.reason)
        for t in usage.texts:
            self._text(t)
        for p in usage.placements:
            self._placement(p)
        for m in usage.motions:
            self._motion(m)
        return self.issues

    # --- severity -------------------------------------------------------------------------

    def _fixed(self, code: str, message: str, item: str | None) -> None:
        self.issues.append(
            QcIssue(
                code=code,
                severity=Severity.ERROR,
                category=QcCategory.TECHNICAL,
                message=message,
                item_id=item,
            )
        )

    def _token(self, path: str, code: str, message: str, item: str, reason: str) -> None:
        rule = rule_for(self.style.token_rules, path)
        self._add(rule.rule if rule else RuleKind.MANDATORY, code, message, item, reason, path)

    def _brand(self, rule_code: str, code: str, message: str, item: str, reason: str) -> None:
        rule = next((r for r in self.style.brand_rules if r.code == rule_code), None)
        self._add(rule.rule if rule else RuleKind.MANDATORY, code, message, item, reason, rule_code)

    def _add(
        self, kind: RuleKind, code: str, message: str, item: str, reason: str, rule: str
    ) -> None:
        if kind is RuleKind.MANDATORY:
            severity, category = Severity.ERROR, QcCategory.TECHNICAL
            if reason:
                message += " (locked rule: a reason does not unlock it)"
        elif reason:
            severity, category = Severity.INFO, QcCategory.CREATIVE
            message += f" (intentional: {reason})"
        else:
            severity, category = Severity.WARNING, QcCategory.CREATIVE
        self.issues.append(
            QcIssue(
                code=code,
                severity=severity,
                category=category,
                message=message,
                item_id=item,
                evidence=f"rule {rule}",
            )
        )

    # --- checks ---------------------------------------------------------------------------

    def _color(
        self, item: str, value: str, token: str | None, surface: Surface, reason: str
    ) -> None:
        value = value.upper()
        palette = {name: getattr(self.tokens.palette, name) for name in PALETTE_NAMES}
        if not is_hex(value) or value not in palette.values():
            self._brand(
                "palette.closed",
                "color.off_palette",
                f"{value} is not a palette colour",
                item,
                reason,
            )
            return
        if token is not None:
            try:
                expected = self.style.hex(token, section_id=self.usage.section_id)
            except KeyError:
                self._fixed("color.unknown_token", f"no colour token {token!r}", item)
                return
            if value != expected:
                path = f"palette.{token}" if token in palette else f"semantic.{token}"
                self._token(
                    path,
                    "color.token_mismatch",
                    f"{token} is {expected}, drawn {value}",
                    item,
                    reason,
                )
        names = {n for n, h in palette.items() if h == value}
        if surface is Surface.VIDEO and names and names <= set(self.style.thumbnail_only):
            self._brand(
                "palette.thumbnail_only",
                "color.thumbnail_only",
                f"{value} is a thumbnail-only colour",
                item,
                reason,
            )

    def _text(self, t: TextUse) -> None:
        style, item = self.style, t.element_id
        if t.role not in TYPE_ROLES:
            self._fixed("text.unknown_role", f"no type role {t.role!r}", item)
            return
        role = getattr(self.tokens.typography, t.role)
        face = style.face(role.face)
        fallback = style.face(face.fallback) if face.fallback else None
        fallback_used = fallback is not None and (t.family, t.weight) == (
            fallback.family,
            fallback.weight,
        )
        if (t.family, t.weight) != (face.family, face.weight):
            if fallback is not None and fallback_used:
                self.issues.append(
                    QcIssue(
                        code="font.substituted",
                        severity=Severity.INFO,
                        category=QcCategory.TECHNICAL,
                        item_id=item,
                        message=f"{face.family} {face.weight} replaced by its fallback"
                        f" {fallback.family} {fallback.weight}",
                    )
                )
            else:
                self._token(
                    f"typography.{t.role}.face",
                    "font.mismatch",
                    f"{t.role} is {face.family} {face.weight}, drawn {t.family} {t.weight}",
                    item,
                    t.reason,
                )
        drawn = fallback if fallback is not None and fallback_used else face
        if t.stroke_px > drawn.max_stroke_ratio * t.size_px:
            self._brand(
                "text.open_counters",
                "text.stroke_heavy",
                f"a {t.stroke_px} px outline on {t.size_px} px {drawn.family} closes letter gaps"
                f" (max {drawn.max_stroke_ratio:.0%} of the size)",
                item,
                t.reason,
            )
        if t.size_px < style.px(self.tokens.typography.min_size):
            self._token(
                "typography.min_size",
                "text.too_small",
                f"{t.size_px} px is below the {style.px(self.tokens.typography.min_size)}"
                " px readability floor",
                item,
                t.reason,
            )
        if t.case is not None and t.case.value != role.case:
            self._token(
                f"typography.{t.role}.case",
                "text.case",
                f"{t.role} is {role.case}, drawn {t.case.value}",
                item,
                t.reason,
            )
        for colour in (t.fill_hex, t.stroke_hex, t.background_hex):
            if colour is not None:
                self._color(item, colour, None, Surface.VIDEO, t.reason)
        against = t.background_hex or (t.stroke_hex if t.stroke_px > 0 else None)
        if against is None:
            self._brand(
                "text.legible_on_footage",
                "text.no_outline",
                "text over footage has neither an outline nor a panel",
                item,
                t.reason,
            )
        elif is_hex(t.fill_hex.upper()) and is_hex(against.upper()):
            large = t.size_px >= style.px(style.contrast.large_text_size)
            need = style.contrast.min_large_text if large else style.contrast.min_text
            ratio = contrast_ratio(t.fill_hex.upper(), against.upper())
            if ratio < need:
                self._brand(
                    "text.legible_on_footage",
                    "text.contrast",
                    f"contrast {ratio} is below {need}",
                    item,
                    t.reason,
                )
        if _DOT_PRICE.search(t.text):
            self._brand(
                "price.decimal_comma",
                "price.format",
                f"price in {t.text!r} must use a decimal comma",
                item,
                t.reason,
            )

    def _placement(self, p: PlacementUse) -> None:
        item = p.element_id
        zone = next((z for z in self.style.zones if z.name == p.zone), None)
        if zone is None:
            self._brand(
                "layout.zones",
                "zone.unknown",
                f"no zone {p.zone!r} in {self.style.output.aspect}",
                item,
                p.reason,
            )
            return
        if zone.kind is ZoneKind.KEEP_OUT:
            self._brand(
                "layout.zones", "zone.keep_out", f"{p.zone} is a platform keep-out", item, p.reason
            )
        elif p.kind not in zone.accepts:
            self._brand(
                "layout.zones",
                "zone.wrong_kind",
                f"{p.zone} does not take {p.kind}",
                item,
                p.reason,
            )
        if not zone.contains(p.left, p.top, p.width, p.height):
            self._brand(
                "layout.zones",
                "zone.outside",
                f"rectangle {p.left},{p.top} {p.width}x{p.height} leaves {p.zone}",
                item,
                p.reason,
            )
        for keep_out in (z for z in self.style.zones if z.kind is ZoneKind.KEEP_OUT):
            if zone is not keep_out and _overlaps(
                p, keep_out.left, keep_out.top, keep_out.width, keep_out.height
            ):
                self._brand(
                    "layout.zones",
                    "zone.keep_out",
                    f"rectangle reaches into {keep_out.name}",
                    item,
                    p.reason,
                )

    def _motion(self, m: MotionUse) -> None:
        motion, item = self.tokens.motion, m.element_id
        if m.easing not in {e.name for e in self.style.easings}:
            self._brand(
                "motion.easings_closed",
                "motion.easing_unknown",
                f"easing {m.easing!r} is not an identity easing",
                item,
                m.reason,
            )
        bounds: tuple[float, float] | None
        if m.phase is MotionPhase.TRANSITION:
            t = self.tokens.transitions  # None in identity v1: no duration range to check
            path = "transitions.duration"
            bounds = None if t is None else (t.duration_min, t.duration_max)
        else:
            path = f"motion.{m.phase.value}"
            bounds = (
                getattr(motion, f"{m.phase.value}_min"),
                getattr(motion, f"{m.phase.value}_max"),
            )
        seconds = Fraction(m.duration.numerator, m.duration.denominator)
        if bounds is not None and not Fraction(str(bounds[0])) <= seconds <= Fraction(
            str(bounds[1])
        ):
            low, high = bounds
            self._token(
                f"{path}_min",
                "motion.duration",
                f"{m.phase.value} of {float(seconds):.3f} s is outside {low}-{high} s",
                item,
                m.reason,
            )
        if m.intensity is not None and m.intensity > motion.intensity_ceiling:
            self._token(
                "motion.intensity_ceiling",
                "motion.intensity",
                f"intensity {m.intensity} is above the ceiling {motion.intensity_ceiling}",
                item,
                m.reason,
            )


def _overlaps(p: PlacementUse, left: int, top: int, width: int, height: int) -> bool:
    return (
        p.left < left + width
        and left < p.left + p.width
        and p.top < top + height
        and top < p.top + p.height
    )
