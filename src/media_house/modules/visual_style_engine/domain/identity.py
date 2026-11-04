"""The MEsc channel identity as versioned data (evidence: docs/layer4/channel-discovery.md).

Rules for this file:

* A released version is NEVER edited. Change the look by adding ``MESC_IDENTITY_V2`` (copy,
  change, bump ``version``), registering it in ``IDENTITIES`` and recording its digest in
  ``IDENTITY_DIGESTS``. Projects keep the version their style was resolved with; the identity QC
  refuses a released version whose content no longer matches its recorded digest.
* Every token is covered by a ``TokenRule`` (mandatory or preference, evidence, confidence).
* Sources name the sampled video ids of the discovery (e.g. ``t_BTkcPy5sw``).
"""

from dataclasses import replace
from types import MappingProxyType
from typing import Final

from media_house.modules.visual_style_engine.domain.model import (
    BrandAssetSlot,
    BrandRule,
    ChannelIdentity,
    ColorPair,
    ContrastRules,
    Easing,
    FontFace,
    Layout,
    TokenRule,
    ZoneSpec,
)
from media_house.modules.visual_style_engine.domain.profile import (
    Emoji,
    StyleTokens,
    Transitions,
)
from media_house.modules.visual_style_engine.domain.values import (
    Confidence,
    ElementKind,
    Evidence,
    RuleKind,
    ZoneKind,
)

_H, _M, _L = Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW
_MEAS, _OBS, _PROP = Evidence.MEASURED, Evidence.OBSERVED, Evidence.PROPOSED
_MAND, _PREF = RuleKind.MANDATORY, RuleKind.PREFERENCE
_K = ElementKind

_OFL = "OFL-1.1"
_FACES: Final = (
    FontFace(
        key="bangers_400",
        family="Bangers",
        weight=400,
        style="normal",
        resource="bangers/Bangers-Regular.ttf",
        sha256="4160a7311de9342674cce9160cde9fcbb30f48190397d86ff1b70b455af65824",
        license=_OFL,
        version="2.100",
        fallback="anton_400",
        max_stroke_ratio=0.09,
    ),
    FontFace(
        key="poppins_400",
        family="Poppins",
        weight=400,
        style="normal",
        resource="poppins/Poppins-Regular.ttf",
        sha256="7e65201e9b79159e2300267cc885e16c8dcef2424cdfa09a29bfb0980a94a7ba",
        license=_OFL,
        version="4.004",
    ),
    FontFace(
        key="poppins_700",
        family="Poppins",
        weight=700,
        style="normal",
        resource="poppins/Poppins-Bold.ttf",
        sha256="983676516167748b74de6f4771fb384c664fd913acb8b471122ecacf5da5ea6c",
        license=_OFL,
        version="4.004",
        fallback="poppins_400",
    ),
    FontFace(
        key="poppins_800",
        family="Poppins",
        weight=800,
        style="normal",
        resource="poppins/Poppins-ExtraBold.ttf",
        sha256="f2ab17c1a63a0ecc12c2461848fc8a469395e3cd2d641803e889c643d9f958e1",
        license=_OFL,
        version="4.004",
        fallback="poppins_700",
    ),
    FontFace(
        key="anton_400",
        family="Anton",
        weight=400,
        style="normal",
        resource="anton/Anton-Regular.ttf",
        sha256="a4ba3a92350ebb031da0cb47630ac49eb265082ca1bc0450442f4a83ab947cab",
        license=_OFL,
        version="2.116",
        fallback="poppins_800",
        max_stroke_ratio=0.05,
    ),
)

_EASINGS: Final = (
    Easing(
        name="pop", x1=0.34, y1=1.56, x2=0.64, y2=1.0, use="entries with overshoot (sticker pop-in)"
    ),
    Easing(
        name="enter", x1=0.16, y1=1.0, x2=0.3, y2=1.0, use="entries without overshoot (expo out)"
    ),
    Easing(name="exit", x1=0.7, y1=0.0, x2=0.84, y2=0.0, use="exits (expo in)"),
    Easing(name="move", x1=0.65, y1=0.0, x2=0.35, y2=1.0, use="moves and slides (in-out)"),
    Easing(name="linear", x1=0.0, y1=0.0, x2=1.0, y2=1.0, use="counters, tickers, progress"),
)

_TEXTS = (_K.CALLOUT, _K.PRICE_TAG, _K.STICKER, _K.CTA)
_LOWER = (_K.LOWER_THIRD, _K.RANK_CARD, _K.CTA, _K.PRICE_TAG)
_TITLE = (_K.TITLE, _K.CAPTION)  # CAPTION here = the static meme line, not speech captions


def _z(
    name: str,
    kind: ZoneKind,
    box: tuple[float, float, float, float],
    accepts: tuple[ElementKind, ...] = (),
) -> ZoneSpec:
    return ZoneSpec(
        name=name, kind=kind, x0=box[0], y0=box[1], x1=box[2], y1=box[3], accepts=accepts
    )


_G, _C, _B, _LB, _X = (
    ZoneKind.GRAPHIC,
    ZoneKind.CAPTION,
    ZoneKind.BRAND,
    ZoneKind.LABEL,
    ZoneKind.KEEP_OUT,
)
_CARD = (_K.FULL_SCREEN, _K.TITLE)

_LAYOUTS: Final = (
    Layout(
        aspect="16:9",
        zones=(
            _z("corner_badge", _B, (0.035, 0.05, 0.20, 0.17), (_K.LOGO,)),
            _z("title_band", _G, (0.22, 0.05, 0.78, 0.22), _TITLE),
            _z("sponsor_label", _LB, (0.80, 0.05, 0.965, 0.11), (_K.SPONSOR_LABEL,)),
            _z("credit_label", _LB, (0.80, 0.12, 0.965, 0.17), (_K.CREDIT,)),
            _z("callout_left", _G, (0.035, 0.24, 0.32, 0.64), _TEXTS),
            _z("callout_right", _G, (0.68, 0.24, 0.965, 0.64), _TEXTS),
            _z("lower_third_band", _G, (0.035, 0.66, 0.965, 0.80), _LOWER),
            _z("caption_band", _C, (0.10, 0.82, 0.90, 0.95), (_K.CAPTION,)),
            _z("card_safe", _G, (0.05, 0.05, 0.95, 0.95), _CARD),
        ),
    ),
    Layout(
        aspect="9:16",
        zones=(
            _z("platform_top", _X, (0.0, 0.0, 1.0, 0.07)),
            _z("platform_right", _X, (0.86, 0.38, 1.0, 0.84)),
            _z("platform_bottom", _X, (0.0, 0.84, 1.0, 1.0)),
            _z("corner_badge", _B, (0.05, 0.08, 0.25, 0.13), (_K.LOGO,)),
            _z("credit_label", _LB, (0.27, 0.08, 0.66, 0.12), (_K.CREDIT,)),
            _z("sponsor_label", _LB, (0.68, 0.08, 0.95, 0.12), (_K.SPONSOR_LABEL,)),
            _z("title_band", _G, (0.05, 0.14, 0.95, 0.30), _TITLE),
            _z("callout_left", _G, (0.05, 0.32, 0.44, 0.50), _TEXTS),
            _z("callout_right", _G, (0.46, 0.32, 0.84, 0.50), _TEXTS),
            _z("caption_band", _C, (0.05, 0.58, 0.84, 0.71), (_K.CAPTION,)),
            _z("lower_third_band", _G, (0.05, 0.72, 0.84, 0.83), _LOWER),
            _z("card_safe", _G, (0.05, 0.08, 0.84, 0.83), _CARD),
        ),
    ),
    Layout(
        aspect="1:1",
        zones=(
            _z("corner_badge", _B, (0.04, 0.04, 0.22, 0.14), (_K.LOGO,)),
            _z("title_band", _G, (0.24, 0.04, 0.76, 0.18), _TITLE),
            _z("sponsor_label", _LB, (0.78, 0.04, 0.96, 0.09), (_K.SPONSOR_LABEL,)),
            _z("credit_label", _LB, (0.78, 0.10, 0.96, 0.15), (_K.CREDIT,)),
            _z("callout_left", _G, (0.04, 0.20, 0.30, 0.58), _TEXTS),
            _z("callout_right", _G, (0.70, 0.20, 0.96, 0.58), _TEXTS),
            _z("lower_third_band", _G, (0.04, 0.60, 0.96, 0.74), _LOWER),
            _z("caption_band", _C, (0.08, 0.76, 0.92, 0.94), (_K.CAPTION,)),
            _z("card_safe", _G, (0.04, 0.04, 0.96, 0.96), _CARD),
        ),
    ),
)

_COMBINATIONS: Final = (
    ColorPair(fg="white", bg="black", use="text"),
    ColorPair(fg="white", bg="charcoal", use="text"),
    ColorPair(fg="white", bg="brand_red", use="text"),
    ColorPair(fg="white", bg="brand_red_neon", use="large_text"),
    ColorPair(fg="white", bg="fire_dark", use="text"),
    ColorPair(fg="white", bg="fire_hot", use="large_text"),
    ColorPair(fg="black", bg="accent_yellow", use="text"),
    ColorPair(fg="accent_yellow", bg="black", use="text"),
    ColorPair(fg="accent_gold", bg="black", use="large_text"),
    ColorPair(fg="accent_gold", bg="charcoal", use="large_text"),
    ColorPair(fg="black", bg="fire_glow", use="text"),
    ColorPair(fg="positive_green", bg="black", use="large_text"),
    ColorPair(fg="white", bg="versus_red", use="large_text"),
    ColorPair(fg="white", bg="versus_blue", use="large_text"),
    ColorPair(fg="white", bg="thumbnail_blue", use="large_text"),
    ColorPair(fg="silver_light", bg="black", use="large_text"),
)


def _t(
    path: str, rule: RuleKind, evidence: Evidence, confidence: Confidence, source: str
) -> TokenRule:
    return TokenRule(path=path, rule=rule, evidence=evidence, confidence=confidence, source=source)


_TOKEN_RULES: Final = (
    # palette: the brand colours are locked; values measured on 360p frames (+-5-8 per channel)
    _t("palette", _MAND, _MEAS, _M, "channel-discovery 6.2 (measured HEX)"),
    _t(
        "palette.brand_red",
        _MAND,
        _MEAS,
        _M,
        "rank-label stroke d2ChnPz3FRg; proposed canonical, owner to confirm",
    ),
    _t("palette.brand_red_neon", _MAND, _MEAS, _H, "studio neon OkjnKFjRFt4"),
    _t("palette.fire_dark", _MAND, _MEAS, _H, "end screen t_BTkcPy5sw"),
    _t("palette.fire_mid", _MAND, _MEAS, _H, "end screen t_BTkcPy5sw"),
    _t("palette.fire_hot", _MAND, _MEAS, _H, "end screen t_BTkcPy5sw"),
    _t("palette.fire_glow", _MAND, _MEAS, _H, "end screen t_BTkcPy5sw"),
    _t("palette.black", _MAND, _OBS, _H, "black outlines and spec cards (value proposed)"),
    _t("palette.white", _MAND, _MEAS, _H, "end screen text"),
    _t("palette.accent_yellow", _MAND, _MEAS, _H, "caption keywords NEfxufh6YLw, ZUSKEXTAuKI"),
    _t("palette.accent_gold", _MAND, _MEAS, _M, "title card dru73YpbQ4Y (one card)"),
    _t("palette.charcoal", _MAND, _MEAS, _M, "grid plate d2ChnPz3FRg (2025 sponsored work)"),
    _t("palette.thumbnail_blue", _MAND, _MEAS, _H, "thumbnails 2022+ (newest 30)"),
    _t("palette.thumbnail_blue_light", _MAND, _MEAS, _H, "thumbnails 2022+ (newest 30)"),
    _t("palette.positive_green", _MAND, _MEAS, _L, "price text hPWgWyzYtq8 (one Short)"),
    _t("palette.versus_red", _MAND, _MEAS, _M, "poll r8A57tnbyKA"),
    _t("palette.versus_blue", _MAND, _MEAS, _M, "poll r8A57tnbyKA"),
    _t("semantic", _PREF, _PROP, _M, "meaning -> palette mapping decided here"),
    _t("semantic.highlight", _PREF, _OBS, _H, "yellow keywords in captions and thumbnails"),
    _t("semantic.accent", _PREF, _OBS, _H, "red recurs across banner, avatar, studio, graphics"),
    # typography: faces are locked (look observed, families chosen here); sizes are preferences
    _t("typography", _PREF, _PROP, _M, "size scale proposed for 1080 short side"),
    _t(
        "typography.display.face",
        _MAND,
        _PROP,
        _M,
        "T1 comic heavy display (look observed, family proposed)",
    ),
    _t(
        "typography.headline.face",
        _MAND,
        _PROP,
        _M,
        "T2 geometric bold, Poppins-like (look observed)",
    ),
    _t("typography.body.face", _MAND, _PROP, _M, "T2 geometric bold (look observed)"),
    _t("typography.caption.face", _MAND, _PROP, _M, "T3 condensed caps (look observed)"),
    _t("typography.number.face", _MAND, _PROP, _M, "prices and rank numbers in T2"),
    _t("typography.label.face", _MAND, _PROP, _M, "'Werbung' label in T2"),
    _t("typography.display.case", _MAND, _OBS, _H, "T1 always ALL CAPS"),
    _t("typography.caption.case", _MAND, _OBS, _M, "burned-in captions ALL CAPS (2023, Shorts)"),
    _t("typography.min_size", _MAND, _PROP, _M, "phone readability floor"),
    # graphic language
    _t("graphics", _PREF, _PROP, _M, "spacing, radii and shadows proposed"),
    _t("graphics.text_stroke", _MAND, _OBS, _H, "thick black outline on text over footage"),
    _t(
        "graphics.sticker_glow", _PREF, _OBS, _H, "white outer glow 6-10 px on cut-outs (8 samples)"
    ),
    _t("graphics.sticker_glow_opacity", _PREF, _OBS, _H, "white outer glow on cut-outs"),
    _t("graphics.sticker_tilt_min", _PREF, _OBS, _M, "slight rotation of stickers"),
    _t("graphics.sticker_tilt_max", _PREF, _OBS, _M, "slight rotation of stickers"),
    _t("graphics.rank_stroke", _PREF, _OBS, _M, "PLATZ N red stroke d2ChnPz3FRg"),
    _t("graphics.title_glow", _PREF, _OBS, _M, "soft glow on the tour title card"),
    _t(
        "graphics.price_format",
        _PREF,
        _OBS,
        _H,
        "comma kept either way: '15,99 EUR' (2021-24), '2,49€' (2025)",
    ),
    # captions, titles
    _t("captions", _PREF, _OBS, _M, "word-group captions 2023 + Shorts; meme line Shorts"),
    _t("captions.word_group.highlight", _PREF, _OBS, _H, "keyword yellow #F4DE19"),
    _t("titles", _PREF, _OBS, _M, "rank card, spec card, title card, labels (2024-25)"),
    _t("titles.sponsor_label.zone", _MAND, _OBS, _H, "'Werbung' top right (6 sponsored samples)"),
    _t("titles.credit.zone", _MAND, _OBS, _H, "third-party credits top right"),
    # motion
    _t("motion", _PREF, _PROP, _M, "easing and duration values proposed from observed pop-ins"),
    _t(
        "motion.intensity_ceiling",
        _PREF,
        _OBS,
        _M,
        "transitions mark structure only (4-7 cuts/min)",
    ),
    # branding: no logo file exists
    _t("branding", _PREF, _PROP, _L, "no logo file exists; slot modelled, placement proposed"),
    # end screen
    _t("end_screen", _PREF, _OBS, _H, "~20 s fire end screen, 8 of 11 long samples"),
    _t("end_screen.subscribe_label", _MAND, _OBS, _H, "'ABONNIEREN' on the end screen"),
    _t("end_screen.watch_next_label", _MAND, _OBS, _H, "'WEITER SCHAUEN' on the end screen"),
    _t("end_screen.entry_transition", _MAND, _OBS, _H, "pixel dissolve into the end screen"),
    _t("end_screen.membership_label", _PREF, _OBS, _H, "membership plug after the hook"),
)


def _r(
    code: str, text: str, rule: RuleKind, evidence: Evidence, confidence: Confidence, source: str
) -> BrandRule:
    return BrandRule(
        code=code, text=text, rule=rule, evidence=evidence, confidence=confidence, source=source
    )


_BRAND_RULES: Final = (
    _r(
        "palette.closed",
        "Graphics use palette colours only; a new colour needs a new identity version.",
        _MAND,
        _PROP,
        _H,
        "spec 5",
    ),
    _r(
        "palette.thumbnail_only",
        "thumbnail_blue and thumbnail_blue_light are for thumbnails only, never in-video graphics.",
        _MAND,
        _OBS,
        _M,
        "blue appears in thumbnails 2022+, not in video graphics",
    ),
    _r(
        "fonts.bundled_only",
        "Text uses the bundled faces of its type role; a fallback face is always reported.",
        _MAND,
        _PROP,
        _H,
        "spec 6",
    ),
    _r(
        "text.legible_on_footage",
        "Text over footage always has an outline or a panel and meets the contrast minimum.",
        _MAND,
        _OBS,
        _H,
        "white text with black outline throughout",
    ),
    _r(
        "text.open_counters",
        "Outlines stay thin enough that letters and counters stay apart (a per-face maximum"
        " outline/size ratio: Anton 5%, Bangers 9%, Poppins 11%).",
        _MAND,
        _OBS,
        _H,
        "condensed caps with a separate outline per letter (2023 captions, Shorts);"
        " ratios measured on HyperFrames renders",
    ),
    _r(
        "text.german",
        "On-screen text is German with correct umlauts and ss/ß; capital ß is written SS.",
        _MAND,
        _OBS,
        _H,
        "German speech and titles in every sample",
    ),
    _r(
        "price.decimal_comma",
        "Prices use a decimal comma and the euro unit (15,99 €).",
        _MAND,
        _OBS,
        _H,
        "rank cards 2021-25",
    ),
    _r(
        "motion.easings_closed",
        "Animations use the identity's named easings only; no module defines its own.",
        _MAND,
        _PROP,
        _H,
        "Layer 4 contract 4",
    ),
    _r(
        "layout.zones",
        "Captions only in caption zones, graphics only in graphic zones,"
        " nothing in platform keep-outs.",
        _MAND,
        _PROP,
        _H,
        "Layer 4 contract 4",
    ),
    _r(
        "sponsor.label",
        "Sponsored content shows 'Werbung' in the sponsor_label zone for the whole duration.",
        _MAND,
        _OBS,
        _H,
        "6 sponsored samples",
    ),
    _r(
        "credit.third_party",
        "Third-party footage carries a credit in the credit_label zone while it is visible.",
        _MAND,
        _OBS,
        _H,
        "ZINK-FIREWORKS, PYROMOTTEFULLHD credits",
    ),
    _r(
        "sticker.backdrop",
        "Product cut-out stickers sit over fireworks footage or a fire plate,"
        " never over a flat colour.",
        _MAND,
        _OBS,
        _H,
        "8 samples",
    ),
    _r(
        "sticker.treatment",
        "Stickers have a white outer glow and a slight tilt (5-10 degrees).",
        _MAND,
        _OBS,
        _H,
        "t_BTkcPy5sw, zIxejdVAa0E",
    ),
    _r(
        "footage.no_fake_fireworks",
        "No artificial fireworks or bursts over real test footage; the effect is the product.",
        _MAND,
        _PROP,
        _H,
        "channel-discovery 11 (vfx)",
    ),
    _r(
        "end_screen.fire",
        "Long videos end on the ~20 s fire end screen, entered by a pixel dissolve.",
        _MAND,
        _OBS,
        _H,
        "8 of 11 long samples",
    ),
    _r(
        "rank.card",
        "Rankings count down with a 'PLATZ N' card (white, red stroke).",
        _PREF,
        _OBS,
        _H,
        "t_BTkcPy5sw, d2ChnPz3FRg",
    ),
    _r(
        "restraint.structure",
        "Graphics and transitions mark structure (intro, rank change, end), not every cut.",
        _PREF,
        _OBS,
        _H,
        "4-7 cuts/min in recent long videos",
    ),
    _r(
        "emoji.punchline",
        "Emojis are single punchline stickers or full-screen pops, one at a time.",
        _PREF,
        _OBS,
        _H,
        "t_BTkcPy5sw, tDi6cJFBP0s",
    ),
    _r(
        "captions.long_form",
        "Long-form videos have no burned-in speech captions by default (caption_engine decides).",
        _PREF,
        _OBS,
        _H,
        "6 of 6 recent long samples",
    ),
    _r(
        "footage.no_grade",
        "Graphics never tint the footage; the channel has no cinematic grade.",
        _PREF,
        _OBS,
        _M,
        "channel-discovery 6.9",
    ),
)

_PROHIBITED: Final = (
    "colours outside the palette",
    "thumbnail blue in in-video graphics",
    "fonts other than the bundled faces (unreported substitution)",
    "graphics or text inside platform keep-out zones",
    "artificial fireworks or bursts over real test footage",
    "stickers over a flat colour background",
    "a logo invented by automation (no logo file exists yet)",
    "a cinematic grade or colour tint applied by graphics",
    "dot decimal prices (15.99) or a missing euro unit",
)

_BRAND_ASSETS: Final = (
    BrandAssetSlot(
        name="logo",
        purpose="channel logo (variants: full_color, white, black)",
        required=False,
        evidence="none exists; avatar wolf is the de-facto mark",
    ),
    BrandAssetSlot(
        name="wolf_mascot",
        purpose="mascot cut-out for end screen and plug",
        required=True,
        evidence="avatar.jpg, end screen",
    ),
    BrandAssetSlot(
        name="wordmark",
        purpose="'MESC' chrome comic wordmark",
        required=False,
        evidence="banner.png",
    ),
    BrandAssetSlot(
        name="neon_script",
        purpose="red neon 'MEsc' script",
        required=False,
        evidence="studio OkjnKFjRFt4",
    ),
    BrandAssetSlot(
        name="fire_plate",
        purpose="fire/lava end-screen background",
        required=True,
        evidence="t_BTkcPy5sw end screen",
    ),
    BrandAssetSlot(
        name="fire_blur_plate",
        purpose="orange fire blur behind product stickers",
        required=False,
        evidence="t_BTkcPy5sw 8:27",
    ),
    BrandAssetSlot(
        name="charcoal_grid",
        purpose="charcoal grid plate (sponsor templates, polls)",
        required=False,
        evidence="d2ChnPz3FRg, r8A57tnbyKA",
    ),
    BrandAssetSlot(
        name="membership_plate",
        purpose="membership plug plate (fire + wolf)",
        required=False,
        evidence="6 long samples",
    ),
    BrandAssetSlot(
        name="thumbnail_background",
        purpose="blue speed-line warp (thumbnails only)",
        required=False,
        evidence="thumbnails 2022+",
    ),
    BrandAssetSlot(
        name="corner_stripes",
        purpose="grey-white diagonal corner stripes",
        required=False,
        evidence="banner, thumbnails",
    ),
)

MESC_IDENTITY_V1: Final = ChannelIdentity(
    id="mesc",
    version=1,
    name="MEsc",
    handle="@MEscapYT",
    description=(
        "German fireworks channel: retail intelligence (brochures, ranges, rankings: what, where,"
        " how much), shopping tours, unboxings, midnight footage; Shorts for memes and hauls."
    ),
    personality=("loud", "playful", "comic", "hands-on", "trustworthy on prices"),
    audience="German-speaking fireworks fans preparing for New Year's Eve",
    emotions=("anticipation", "excitement", "fun", "confidence in the buying decision"),
    language="de-DE",
    required_glyphs=(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        "ÄÖÜäöüß€.,:;!?-'\"%&/()+"
        "\N{EN DASH}\N{HORIZONTAL ELLIPSIS}\N{DOUBLE LOW-9 QUOTATION MARK}"
        "\N{LEFT DOUBLE QUOTATION MARK}\N{SINGLE LOW-9 QUOTATION MARK}"
        "\N{LEFT SINGLE QUOTATION MARK}"
    ),
    references=(
        "t_BTkcPy5sw",
        "dru73YpbQ4Y",
        "d2ChnPz3FRg",
        "bw7fkdSK1Vk",
        "9etWVDZnyOs",
        "OkjnKFjRFt4",
        "_Ka5c6jMYRg",
        "ZUSKEXTAuKI",
        "tDi6cJFBP0s",
        "y_mj09m0XB4",
        "zIxejdVAa0E",
        "nsIVE20ixMM",
        "NEfxufh6YLw",
        "r8A57tnbyKA",
        "xCoOYkqzgDo",
        "hPWgWyzYtq8",
    ),
    tokens=StyleTokens(),
    faces=_FACES,
    easings=_EASINGS,
    layouts=_LAYOUTS,
    combinations=_COMBINATIONS,
    contrast=ContrastRules(),
    thumbnail_only=("thumbnail_blue", "thumbnail_blue_light"),
    token_rules=_TOKEN_RULES,
    brand_rules=_BRAND_RULES,
    prohibited=_PROHIBITED,
    brand_assets=_BRAND_ASSETS,
)

# --- v2: v1 unchanged plus emoji punchlines and the transition language ------------------------
# Why a new version (not v1 data): styles resolved under v1 are stored and pinned in project
# libraries; their fingerprints are part of every downstream cache key. v1 stays byte-identical.

#: Emojis the identity must be able to draw: the channel's punchlines (channel-discovery 6.5)
#: plus caption_engine's controlled vocabulary. Variation selectors are not glyphs.
EMOJI_GLYPHS: Final = (
    "\N{SMILING FACE WITH HEART-SHAPED EYES}\N{SHOCKED FACE WITH EXPLODING HEAD}"
    "\N{FACE SCREAMING IN FEAR}\N{THINKING FACE}\N{EYES}"
    "\N{BANKNOTE WITH EURO SIGN}\N{TROPHY}\N{COLLISION SYMBOL}\N{FIRE}"
    "\N{HIGH VOLTAGE SIGN}\N{SPEAKER WITH THREE SOUND WAVES}\N{MONEY WITH WINGS}"
    "\N{WARNING SIGN}\N{CROSS MARK}\N{HEAVY BLACK HEART}"
)
_EMOJI_FACE: Final = FontFace(
    key="noto_color_emoji",
    family="Noto Color Emoji",
    weight=400,
    style="normal",
    resource="noto_emoji/Noto-COLRv1-noflags.ttf",
    sha256="469e3b92d63cfc203789f8742f1835b8672c7b5995ab4a832f1699b712a5afcc",
    license=_OFL,
    version="2.051",
    coverage=EMOJI_GLYPHS,
)
_V2_TOKEN_RULES: Final = (
    _t(
        "emoji",
        _MAND,
        _PROP,
        _M,
        "emoji punchlines (channel-discovery 6.5); face chosen here: Google Noto Color Emoji"
        " (COLRv1, OFL-1.1)",
    ),
    _t("transitions", _PREF, _OBS, _M, "channel-discovery 6.6 and 11 (transition_engine)"),
    _t("transitions.default", _MAND, _OBS, _H, "hard cuts dominate (4-7 cut events/min)"),
    _t(
        "transitions.prohibited",
        _MAND,
        _PROP,
        _M,
        "never seen in 11 long samples and 8 Shorts; decorative (shape, mask, glitch)",
    ),
    _t(
        "transitions.bookend_only",
        _MAND,
        _OBS,
        _H,
        "zoom tunnel into the membership plug (6 samples), pixel dissolve into the end screen"
        " (8 of 11)",
    ),
    _t("transitions.duration_min", _PREF, _PROP, _M, "short, punchy transitions (proposed)"),
    _t("transitions.duration_max", _PREF, _PROP, _M, "short, punchy transitions (proposed)"),
)
_V2_BRAND_RULES: Final = (
    _r(
        "emoji.bundled_face",
        "Emojis are drawn with the bundled emoji face only, never a system emoji font.",
        _MAND,
        _PROP,
        _H,
        "reproducible renders on every machine",
    ),
    _r(
        "transitions.closed",
        "Transitions come from the approved set; prohibited ones are never used and the brand"
        " bookend transitions appear only at their bookend.",
        _MAND,
        _OBS,
        _H,
        "channel-discovery 6.6",
    ),
)

MESC_IDENTITY_V2: Final = replace(
    MESC_IDENTITY_V1,
    version=2,
    tokens=StyleTokens(emoji=Emoji(), transitions=Transitions()),
    faces=(*_FACES, _EMOJI_FACE),
    token_rules=(*_TOKEN_RULES, *_V2_TOKEN_RULES),
    brand_rules=(*_BRAND_RULES, *_V2_BRAND_RULES),
)

IDENTITIES: Final = MappingProxyType({i.version: i for i in (MESC_IDENTITY_V1, MESC_IDENTITY_V2)})
CURRENT_IDENTITY_VERSION: Final = max(IDENTITIES)
#: sha256 of each released identity's canonical content (``validation.identity_digest``).
IDENTITY_DIGESTS: Final = MappingProxyType(
    {
        1: "a394236163c04499c2b077e5ff0d785ff9d1724a3f19632afcdb01e27c1f93f5",
        2: "39887916a9c5cb843368dae5517a977a4e8b3cd84d8bc0965614a875bff39c57",
    }
)
