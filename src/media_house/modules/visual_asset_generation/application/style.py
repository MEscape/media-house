"""Anti-corruption: the channel identity (``visual_style_engine``) -> this module's knob values.

The style owns every colour and size; this module only says WHICH token feeds WHICH knob. The
result is the "channel identity" layer of ``VisualAssetConfig.resolve`` (between the profile
defaults and the caller's overrides), so a caller can still override a single value.

| Knob | Token |
| --- | --- |
| sticker / silhouette glow colour, glow + outline preset colour | semantic ``glow`` |
| sticker / silhouette glow softness, spread | ``graphics.sticker_glow`` (px; spread = half) |
| sticker / silhouette glow opacity | ``graphics.sticker_glow_opacity`` |
| sticker tilt | minus the middle of ``graphics.sticker_tilt_min..max`` (leans right, fixed) |
| silhouette fill, shadow colour | palette ``black`` |
| glow preset softness / opacity | ``graphics.title_glow`` / ``title_glow_opacity`` |
| shadow preset blur, opacity | ``graphics.soft_shadow_blur``, ``soft_shadow_opacity`` |
| shadow preset offset | ``graphics.hard_shadow_offset`` |
| outline preset width | ``graphics.graphic_stroke`` |
"""

from media_house.modules.media_library.application.contracts import JsonValue
from media_house.modules.visual_asset_generation.domain.model import StyleRef
from media_house.modules.visual_style_engine.application.contracts import ResolvedStyle
from media_house.modules.visual_style_engine.application.contracts import (
    StyleRef as StoredStyleRef,
)


def identity_layer(style: ResolvedStyle) -> dict[str, JsonValue]:
    graphics = style.tokens.graphics
    glow = style.hex("glow")
    black = style.hex("black")
    sticker_px = float(style.px(graphics.sticker_glow))
    tilt = -(graphics.sticker_tilt_min + graphics.sticker_tilt_max) / 2.0
    layer: dict[str, JsonValue] = {
        "variants.glow.glow.color": glow,
        "variants.glow.glow.radius": float(style.px(graphics.title_glow)),
        "variants.glow.glow.opacity": max(0.01, graphics.title_glow_opacity),
        "variants.shadow.shadow.color": black,
        "variants.shadow.shadow.blur": float(style.px(graphics.soft_shadow_blur)),
        "variants.shadow.shadow.opacity": max(0.01, graphics.soft_shadow_opacity),
        "variants.shadow.shadow.offset_y": float(style.px(graphics.hard_shadow_offset)),
        "variants.outline.outline.color": glow,
        "variants.outline.outline.width": max(0.5, float(style.px(graphics.graphic_stroke))),
        "variants.sticker.tilt.degrees": tilt,
        "variants.silhouette.silhouette.color": black,
    }
    for preset in ("sticker", "silhouette"):
        layer |= {
            f"variants.{preset}.glow.color": glow,
            f"variants.{preset}.glow.radius": sticker_px,
            f"variants.{preset}.glow.spread": sticker_px / 2.0,
            f"variants.{preset}.glow.opacity": max(0.01, graphics.sticker_glow_opacity),
        }
    return layer


def style_ref(style: ResolvedStyle, stored: StoredStyleRef | None) -> StyleRef:
    return StyleRef(
        document_id=None if stored is None else stored.document_id,
        fingerprint=style.fingerprint,
        identity_version=style.identity_version,
        profile=style.profile,
    )
