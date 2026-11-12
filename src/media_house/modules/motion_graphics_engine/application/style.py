"""Anti-corruption layer to visual_style_engine: ``ResolvedStyle`` -> ``StyleFacts`` and the
drift-validator usage of what a composition draws. No colour, font, easing or zone is defined
here: every value is read from the resolved style."""

from dataclasses import fields
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.model import (
    CompiledAnimation,
    Composition,
    EasingFacts,
    EndScreenFacts,
    FaceFacts,
    GraphicsFacts,
    LookFacts,
    MotionFacts,
    StyleFacts,
    TypeFacts,
    ZoneFacts,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    ElementType,
    GraphicKind,
    QcCategory,
    QcIssue,
    Severity,
    StyleBinding,
)
from media_house.modules.visual_style_engine.application.contracts import (
    TYPE_ROLES,
    CaptionLook,
    ColorUse,
    ElementKind,
    MotionPhase,
    MotionUse,
    PlacementUse,
    ResolvedStyle,
    Semantic,
    StyleRef,
    StyleUsage,
    TextStyle,
    TextUse,
    Titles,
)
from media_house.modules.visual_style_engine.application.contracts import (
    QcIssue as StyleIssue,
)

MODULE = "motion_graphics_engine"


def _seconds(value: float) -> Rational:
    f = Fraction(str(value))
    return Rational(f.numerator, f.denominator)


def binding(style: ResolvedStyle, ref: StyleRef | None) -> StyleBinding:
    return StyleBinding(
        document_id=ref.document_id if ref else None,
        fingerprint=style.fingerprint,
        identity_version=style.identity_version,
        profile=style.profile,
    )


def style_facts(style: ResolvedStyle) -> StyleFacts:
    t = style.tokens
    colors = [(c.name, c.hex) for c in style.palette()]
    colors += [(f.name, style.hex(f.name)) for f in fields(Semantic)]
    looks = [_title_look(style, f.name, getattr(t.titles, f.name)) for f in fields(Titles)]
    looks += [
        _caption_look(style, f"caption.{name}", getattr(t.captions, name))
        for name in ("word_group", "line")
    ]
    g, m, es = t.graphics, t.motion, t.end_screen
    return StyleFacts(
        fingerprint=style.fingerprint,
        width=style.output.width,
        height=style.output.height,
        colors=tuple(colors),
        faces=tuple(FaceFacts(key=f.key, family=f.family, weight=f.weight) for f in style.faces),
        types=tuple(
            TypeFacts(
                role=r.role,
                face=r.face.key,
                size_px=r.size_px,
                tracking_em=r.tracking_em,
                leading=r.leading,
                case=r.case,
            )
            for r in (style.type_style(role) for role in TYPE_ROLES)
        ),
        looks=tuple(looks),
        zones=tuple(
            ZoneFacts(
                name=z.name,
                kind=z.kind.value,
                accepts=tuple(a.value for a in z.accepts),
                left=z.left,
                top=z.top,
                width=z.width,
                height=z.height,
            )
            for z in style.zones
        ),
        easings=tuple(
            EasingFacts(name=e.name, x1=e.x1, y1=e.y1, x2=e.x2, y2=e.y2) for e in style.easings
        ),
        motion=MotionFacts(
            character=m.character,
            entry_easing=m.entry_easing,
            exit_easing=m.exit_easing,
            move_easing=m.move_easing,
            entry_pattern=m.entry_pattern,
            exit_pattern=m.exit_pattern,
            entry_min=_seconds(m.entry_min),
            entry_max=_seconds(m.entry_max),
            exit_min=_seconds(m.exit_min),
            exit_max=_seconds(m.exit_max),
            emphasis_min=_seconds(m.emphasis_min),
            emphasis_max=_seconds(m.emphasis_max),
            stagger=_seconds(m.stagger),
            overshoot=m.overshoot,
            min_hold=_seconds(m.min_hold),
            intensity_ceiling=m.intensity_ceiling,
            motion_blur=m.motion_blur,
        ),
        graphics=GraphicsFacts(
            text_stroke_px=style.px(g.text_stroke),
            graphic_stroke_px=style.px(g.graphic_stroke),
            rank_stroke_px=style.px(g.rank_stroke),
            radius_px=style.px(g.corner_radius),
            panel_opacity=g.panel_opacity,
            padding_px=style.px(g.panel_padding),
            spacing_px=style.px(g.spacing),
            hard_shadow_px=style.px(g.hard_shadow_offset),
            hard_shadow_opacity=g.hard_shadow_opacity,
            soft_shadow_px=style.px(g.soft_shadow_blur),
            soft_shadow_opacity=g.soft_shadow_opacity,
            sticker_glow_px=style.px(g.sticker_glow),
            sticker_glow_opacity=g.sticker_glow_opacity,
            tilt_min=g.sticker_tilt_min,
            tilt_max=g.sticker_tilt_max,
            title_glow_px=style.px(g.title_glow),
            title_glow_opacity=g.title_glow_opacity,
            pip_border_px=style.px(g.pip_border),
            max_simultaneous=g.max_simultaneous,
        ),
        end_screen=EndScreenFacts(
            seconds=_seconds(es.seconds),
            subscribe_label=es.subscribe_label,
            watch_next_label=es.watch_next_label,
            background_slot=es.background,
            membership_label=es.membership_label,
        ),
        min_text_px=style.px(t.typography.min_size),
        default_caption_look=t.captions.default_look,
        brand_assets=tuple((a.slot, a.asset_id) for a in style.brand_assets),
        emoji_face=t.emoji.face if t.emoji is not None else "",
    )


def _title_look(style: ResolvedStyle, name: str, ts: TextStyle) -> LookFacts:
    return LookFacts(
        name=name,
        role=ts.role,
        size_px=style.px(ts.size),
        case=ts.case,
        fill=style.hex(ts.fill),
        stroke=style.hex(ts.stroke),
        stroke_px=style.px(ts.stroke_width),
        background=ts.background,
        panel=ts.panel,
        glow=ts.glow,
        zone=ts.zone,
        max_lines=ts.max_lines,
        max_chars=ts.max_chars_per_line,
        entry=ts.entry,
    )


def _caption_look(style: ResolvedStyle, name: str, cl: CaptionLook) -> LookFacts:
    return LookFacts(
        name=name,
        role=cl.role,
        size_px=style.px(cl.size),
        case=cl.case,
        fill=style.hex(cl.fill),
        stroke=style.hex(cl.stroke),
        stroke_px=style.px(cl.stroke_width),
        background="none",
        panel="panel",
        glow=False,
        zone=cl.zone,
        max_lines=cl.max_lines,
        max_chars=cl.max_chars_per_line,
        entry="pop_in",
        highlight=style.hex(cl.highlight),
    )


def usage(
    style: ResolvedStyle, composition: Composition, animation: CompiledAnimation | None
) -> StyleUsage:
    """What one composition draws, declared to the style's drift validator."""
    texts: list[TextUse] = []
    colors: list[ColorUse] = []
    panels = [
        e
        for e in composition.elements
        if e.type is ElementType.PANEL and e.fill is not None and e.opacity >= 0.5
    ]
    for e in composition.elements:
        if e.text is not None:
            # the smallest panel around the text is the one it is read against (a chip on a ground)
            behind = min(
                (p for p in panels if e.rect.inside(p.rect)),
                key=lambda p: p.rect.width * p.rect.height,
                default=None,
            )
            texts.append(
                TextUse(
                    element_id=e.id,
                    role=e.text.role,
                    family=e.text.family,
                    weight=e.text.weight,
                    size_px=e.text.size_px,
                    fill_hex=e.text.fill,
                    stroke_hex=e.text.stroke,
                    stroke_px=e.text.stroke_px,
                    background_hex=behind.fill if behind else None,
                    text=e.text.text,
                )
            )
        elif e.fill is not None:
            colors.append(ColorUse(element_id=e.id, hex=e.fill, token=e.colour_name or None))
        if e.stroke is not None:
            colors.append(ColorUse(element_id=e.id, hex=e.stroke))
    placements: tuple[PlacementUse, ...] = ()
    if composition.zone and composition.element_kind and not composition.full_frame:
        r = composition.rect
        placements = (
            PlacementUse(
                element_id=composition.request_id,
                kind=ElementKind(composition.element_kind),
                zone=composition.zone,
                left=r.left,
                top=r.top,
                width=r.width,
                height=r.height,
            ),
        )
    motions: list[MotionUse] = []
    if animation is not None and composition.kind not in (
        GraphicKind.CAPTION,
        GraphicKind.TRANSITION_OVERLAY,
    ):
        m = animation.motion
        easing = {"pop_in": m.entry_easing, "fade_in": "enter"}.get(m.entry, "move")
        if m.entry != "cut":
            motions.append(
                MotionUse(
                    element_id=composition.request_id,
                    phase=MotionPhase.ENTRY,
                    easing=easing,
                    duration=m.entry_seconds,
                    intensity=m.intensity,
                )
            )
        if m.exit != "cut":
            motions.append(
                MotionUse(
                    element_id=composition.request_id,
                    phase=MotionPhase.EXIT,
                    easing=m.exit_easing,
                    duration=m.exit_seconds,
                    intensity=m.intensity,
                )
            )
    return StyleUsage(
        module=MODULE,
        fingerprint=style.fingerprint,
        width=style.output.width,
        height=style.output.height,
        colors=tuple(colors),
        texts=tuple(texts),
        placements=placements,
        motions=tuple(motions),
    )


def from_style_issue(found: StyleIssue) -> QcIssue:
    return QcIssue(
        code=f"style.{found.code}",
        severity=Severity(found.severity.value),
        category=QcCategory(found.category.value),
        message=found.message,
        item_id=found.item_id,
        evidence=found.evidence,
    )
