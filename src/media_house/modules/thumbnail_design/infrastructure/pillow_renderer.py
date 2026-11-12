"""The channel thumbnail in pixels: Pillow + numpy, float32 compositing, one PNG per variant.

The look is the owner-approved v2 (Lidl x Nico, 2026-11-10): blue glow + grid + speed rays,
a real fireworks plate, white-glow stickers with soft shadows, the shop phone, the creator
reaction lifted and rim-lit, the two silver bars on top and a Poppins ExtraBold headline with a
black stroke and a hard comic shadow. Every colour comes from the scene (the channel style).
"""

import math
from pathlib import Path
from typing import Final

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from media_house.modules.thumbnail_design.application.ports import Colours, RenderReport, Scene
from media_house.modules.thumbnail_design.domain.brief import DeviceCard, StoryBadge
from media_house.modules.thumbnail_design.domain.errors import InputUnusable
from media_house.modules.thumbnail_design.domain.layout import (
    BAR_SLOPE,
    BAR_THICKNESS,
    BARS,
    HEADLINE_MAX_WIDTH,
    HEADLINE_ORIGIN,
    HEADLINE_TILT,
    HEIGHT,
    WIDTH,
    Slot,
)
from media_house.modules.thumbnail_design.domain.qc import Box
from media_house.modules.thumbnail_design.infrastructure import pixels as px
from media_house.shared.concurrency import CancellationToken

Array = px.Array

NAME: Final = "pillow_thumbnail"
#: Bump when the pixels change for the same scene: every stored thumbnail is then re-rendered.
VERSION: Final = "1"
_PHONE_W, _PHONE_H, _BEZEL, _RADIUS = 430, 860, 20, 64
_GLOW_BLUE: Final = (0xBF / 255, 0xE0 / 255, 1.0)
_RIM_BLUE: Final = (0x9F / 255, 0xD4 / 255, 1.0)
_WHITE_GLOW: Final = (0xFD / 255, 0xFE / 255, 0xFE / 255)
_GRID_BLUE: Final = (0x7F / 255, 0xB8 / 255, 1.0)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _rgb8(value: str) -> tuple[int, int, int]:
    r, g, b = px.hex_rgb(value)
    return round(r * 255), round(g * 255), round(b * 255)


class PillowThumbnailRenderer:
    def identity(self) -> dict[str, str]:
        return {"name": NAME, "version": VERSION}

    def render(
        self, scene: Scene, destination: Path, cancellation: CancellationToken
    ) -> RenderReport:
        boxes: dict[str, Box] = {}
        c = scene.colours
        canvas = _background(c, scene.plate)
        cancellation.raise_if_cancelled()
        layout = scene.layout
        if scene.logo is not None and layout.logo is not None:
            rgb, a = _picture(scene.logo, "logo", crop=False)
            boxes["logo"] = _sticker(canvas, rgb, a, layout.logo, glow=(6, 10, 0.85), shadow=False)
        yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
        burst = np.exp(-(((xx - 1300) / 420) ** 2 + ((yy - 330) / 300) ** 2)).astype(np.float32)
        canvas = px.screen(canvas, (1.0, 0.95, 0.8), burst * 0.35)

        phone: tuple[Array, Array] | None = None
        rows: dict[str, Box] = {}
        if (
            scene.device is not None
            and scene.device_product is not None
            and layout.device is not None
        ):
            prgb, pa, rows = _phone(scene, scene.device, scene.device_product)
            if layout.device.width != _PHONE_W:
                prgb, pa = px.resize(prgb, pa, layout.device.width)
            prgb, pa = px.rotate(prgb, pa, layout.device.rotation)
            prgb, pa, _ = px.glow(prgb, pa, spread=4, sigma=22, opacity=0.75, colour=_GLOW_BLUE)
            phone = (prgb, pa)
            px.darken(
                canvas, px.shadow(pa, dx=18, dy=26, sigma=26, opacity=0.6), *_xy(layout.device)
            )
        for placed in layout.products:
            cancellation.raise_if_cancelled()
            rgb, a = _picture(scene.products[placed.asset_id], "product")
            boxes[f"product:{placed.asset_id}"] = _sticker(
                canvas, px.contrast(rgb, 0.06), a, placed.slot, glow=(8, 9, 0.95), shadow=True
            )
        if phone is not None and layout.device is not None:
            drawn = px.place(canvas, *phone, *_xy(layout.device))
            boxes["device"] = drawn
            boxes |= _device_boxes(layout.device, drawn, rows)
        if scene.badge is not None and layout.badge is not None:
            rgb, a = _badge(scene, scene.badge)
            boxes["badge"] = _sticker(canvas, rgb, a, layout.badge, glow=(8, 9, 0.95), shadow=True)
        cancellation.raise_if_cancelled()
        boxes["reaction"] = _reaction(canvas, scene, layout.reaction)
        _bars(canvas, c)
        sizes, boxes["headline"] = _headline(canvas, scene)

        out = Image.fromarray((np.clip(canvas, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
        destination.parent.mkdir(parents=True, exist_ok=True)
        out.save(destination, format="PNG", optimize=True)
        return RenderReport(out.width, out.height, boxes, sizes)

    def export(self, source: Path, destination: Path, width: int, max_bytes: int) -> Path:
        with Image.open(source) as im:
            rgb = im.convert("RGB")
        height = round(rgb.height * width / rgb.width)
        small = rgb.resize((width, height), Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        for quality in (95, 92, 88, 84, 80, 75):
            small.save(destination, format="JPEG", quality=quality, subsampling=0, optimize=True)
            if destination.stat().st_size <= max_bytes:
                break
        return destination


# --- layers ------------------------------------------------------------------------------------


def _xy(slot: Slot) -> tuple[int, int]:
    return slot.x, slot.y


def _picture(path: Path, role: str, *, crop: bool = True) -> tuple[Array, Array]:
    try:
        rgb, a = px.load_rgba(path)
    except OSError as exc:
        raise InputUnusable(role, "the file cannot be read") from exc
    if crop:
        rgb, a = px.crop_visible(rgb, a)
    if a.max() <= 0:
        raise InputUnusable(role, "nothing is left after background removal")
    return rgb, a


def _sticker(
    canvas: Array,
    rgb: Array,
    a: Array,
    slot: Slot,
    *,
    glow: tuple[float, float, float],
    shadow: bool,
) -> Box:
    rgb, a = px.resize(rgb, a, slot.width)
    rgb, a = px.rotate(rgb, a, slot.rotation)
    rgb, a, _ = px.glow(rgb, a, spread=glow[0], sigma=glow[1], opacity=glow[2], colour=_WHITE_GLOW)
    x, y = slot.x, slot.y  # slots are the top-left of the finished (glowing) layer
    if shadow:
        px.darken(canvas, px.shadow(a, dx=14, dy=20, sigma=18, opacity=0.5), x, y)
    return px.place(canvas, rgb, a, x, y)


def _background(c: Colours, plate: Path | None) -> Array:
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    cx, cy = 1180.0, 560.0
    r = np.hypot((xx - cx) / WIDTH, (yy - cy) / HEIGHT)
    blue, light = np.array(px.hex_rgb(c.blue)), np.array(px.hex_rgb(c.blue_light))
    deep = np.array([0.02, 0.05, 0.13])  # navy rim of the channel's blue warp
    t = np.clip(r / 0.75, 0, 1)[..., None]
    canvas = (
        light * (1 - t) ** 3 + blue * 3 * t * (1 - t) ** 2 + deep * (1 - (1 - t) ** 3)
    ).astype(np.float32)
    angle = np.arctan2(yy - cy, xx - cx)
    rays = (0.5 + 0.5 * np.cos(angle * 36)) ** 8 * np.clip(r / 0.35, 0, 1) * np.clip(1.2 - r, 0, 1)
    canvas = px.screen(canvas, px.hex_rgb(c.blue_light), (rays * 0.22).astype(np.float32))
    grid = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(grid)
    for gx in range(-30, WIDTH, 96):
        draw.line([(gx, 0), (gx, HEIGHT)], fill=255, width=3)
    for gy in range(-20, HEIGHT, 96):
        draw.line([(0, gy), (WIDTH, gy)], fill=255, width=3)
    lines = np.asarray(grid, np.float32) / 255 * (0.10 + 0.25 * np.clip(1 - r / 0.6, 0, 1))
    canvas = px.screen(canvas, _GRID_BLUE, lines.astype(np.float32))
    if plate is not None:
        try:
            with Image.open(plate) as im:
                fw = np.asarray(im.convert("RGB").resize((WIDTH, HEIGHT)), np.float32) / 255
        except OSError as exc:
            raise InputUnusable("plate", "the file cannot be read") from exc
        fade = np.clip((xx - 700) / 600, 0, 1) * np.clip((900 - yy) / 500, 0, 1)
        canvas = 1 - (1 - canvas) * (1 - np.roll(fw, -260, axis=1) * 0.95 * fade[..., None])
        fade2 = np.clip((xx - 900) / 500, 0, 1) * np.clip((700 - yy) / 400, 0, 1) * 0.55
        canvas = 1 - (1 - canvas) * (1 - np.roll(fw, 380, axis=1) * fade2[..., None])
    vignette = np.hypot((xx - WIDTH / 2) / (WIDTH / 2), (yy - HEIGHT / 2) / (HEIGHT / 2))
    canvas = canvas * (1 - 0.45 * np.clip((vignette - 0.55) / 0.85, 0, 1) ** 1.5)[..., None]
    return np.asarray(np.clip(canvas, 0, 1), dtype=np.float32)


def _reaction(canvas: Array, scene: Scene, slot: Slot) -> Box:
    rgb, a = _picture(scene.reaction, "reaction")
    if scene.flip_reaction:
        rgb, a = rgb[:, ::-1].copy(), a[:, ::-1].copy()
    rgb, a = px.resize(rgb, a, slot.width)
    my, mx = np.mgrid[0 : a.shape[0], 0 : a.shape[1]].astype(np.float32)
    # Lift the head most: the upper part of the cut-out, around where its pixels are.
    head = a[: max(1, a.shape[0] * 2 // 5)]
    cols = head.sum(axis=0)
    centre = float((cols * np.arange(cols.size)).sum() / max(float(cols.sum()), 1e-6)) / a.shape[1]
    face = np.exp(
        -(((mx / a.shape[1] - centre) / 0.22) ** 2 + ((my / a.shape[0] - 0.30) / 0.20) ** 2)
    )
    rgb = np.clip(rgb ** (0.92 - 0.10 * face[..., None]), 0, 1).astype(np.float32)
    lum = rgb.mean(axis=2, keepdims=True)
    rgb = px.contrast(np.clip(lum + (rgb - lum) * 1.12, 0, 1).astype(np.float32), 0.10)
    rim = px.inner_rim(a, 14) * np.clip((mx / a.shape[1] - 0.35) / 0.4, 0, 1)
    rgb = px.screen(rgb, _RIM_BLUE, (rim * 0.85).astype(np.float32))
    rgb, a, _ = px.glow(rgb, a, spread=7, sigma=14, opacity=0.9, colour=_WHITE_GLOW)
    x, y = slot.x, slot.y
    px.darken(canvas, px.shadow(a, dx=20, dy=10, sigma=30, opacity=0.5), x, y)
    return px.place(canvas, rgb, a, x, y)


def _bars(canvas: Array, c: Colours) -> None:
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    light, mid, dark = (
        np.array(px.hex_rgb(v)) for v in (c.silver_light, c.silver_mid, c.silver_dark)
    )
    for p0, p1 in BARS:
        mask = px.bar_mask((WIDTH, HEIGHT), p0, p1, BAR_THICKNESS)
        t = ((yy - (p0[1] + (xx - p0[0]) * BAR_SLOPE)) / BAR_THICKNESS + 0.5).clip(0, 1)[..., None]
        silver = np.where(
            t < 0.5, light * (1 - t * 2) + mid * t * 2, mid * (2 - t * 2) + dark * (t * 2 - 1)
        ).astype(np.float32)
        canvas *= 1 - px.shadow(mask, dx=0, dy=10, sigma=12, opacity=0.55)[..., None]
        canvas[:] = silver * mask[..., None] + canvas * (1 - mask[..., None])


def _headline(canvas: Array, scene: Scene) -> tuple[tuple[int, ...], Box]:
    c = scene.colours
    lines = scene.headline.caps
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    sizes = []
    for line, size in zip(lines, scene.layout.headline_sizes, strict=True):
        while (
            size > 60 and probe.textlength(line, font=_font(scene.font, size)) > HEADLINE_MAX_WIDTH
        ):
            size -= 4
        sizes.append(size)
    x0, y = HEADLINE_ORIGIN
    placed = []
    for i, (line, size) in enumerate(zip(lines, sizes, strict=True)):
        placed.append((line, size, (x0 - (4 if i else 0), y)))
        y += round(size * 0.9)

    def draw(d: ImageDraw.ImageDraw, _im: Image.Image) -> None:
        for i, (line, size, xy) in enumerate(placed):
            colour = c.highlight if i == scene.headline.accent_line else c.white
            d.text(
                xy,
                line,
                font=_font(scene.font, size),
                fill=(*_rgb8(colour), 255),
                stroke_width=max(4, round(size * 0.04)),
                stroke_fill=(*_rgb8(c.black), 255),
            )

    rgb, a = px.rgba_layer((WIDTH, HEIGHT), draw)
    layer = Image.fromarray((np.dstack([rgb, a]) * 255).astype(np.uint8), "RGBA")
    layer = layer.rotate(HEADLINE_TILT, resample=Image.Resampling.BICUBIC, center=(500, 220))
    t = np.asarray(layer, dtype=np.float32) / np.float32(255)
    rgb, a = t[..., :3].copy(), t[..., 3].copy()
    hard = px.shadow(a, dx=8, dy=10, sigma=0.5, opacity=0.9)  # hard comic shadow
    soft = px.shadow(a, dx=0, dy=8, sigma=22, opacity=0.6)
    canvas *= 1 - np.maximum(hard, soft)[..., None]
    px.place(canvas, rgb, a, 0, 0)
    ys, xs = np.nonzero(a > 0.05)
    box = (
        (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        if ys.size
        else (0, 0, 0, 0)
    )
    return tuple(sizes), box


# --- the phone ---------------------------------------------------------------------------------


def _phone(
    scene: Scene, device: DeviceCard, product_file: Path
) -> tuple[Array, Array, dict[str, Box]]:
    """The phone layer and where its price and button are (phone px), for the checks."""
    c = scene.colours
    phone = Image.new("RGBA", (_PHONE_W, _PHONE_H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(phone)
    pd.rounded_rectangle([0, 0, _PHONE_W - 1, _PHONE_H - 1], _RADIUS, fill=(12, 12, 14, 255))
    pd.rounded_rectangle(
        [3, 3, _PHONE_W - 4, _PHONE_H - 4], _RADIUS - 3, outline=(70, 72, 78, 255), width=3
    )
    sw, sh = _PHONE_W - 2 * _BEZEL, _PHONE_H - 2 * _BEZEL
    screen = Image.new("RGB", (sw, sh), (0xF4, 0xF5, 0xF7))
    sd = ImageDraw.Draw(screen)
    small, price, button = _font(scene.font, 24), _font(scene.font, 46), _font(scene.font, 27)
    top = 0
    if scene.logo is not None:  # shop header with the retailer's logo and a search field
        sd.rectangle([0, 0, sw, 118], fill=(255, 255, 255))
        with Image.open(scene.logo) as im:
            logo = im.convert("RGBA").resize((66, 66), Image.Resampling.LANCZOS)
        screen.paste(logo, (20, 40), logo)
        sd.rounded_rectangle([102, 52, sw - 22, 96], 22, fill=(0xEC, 0xEE, 0xF1))
        sd.text((122, 60), "Feuerwerk", font=small, fill=(110, 112, 118))
        top = 118
    if scene.device_screen is not None:  # the real shop page, cover-fit into a banner
        with Image.open(scene.device_screen) as im:
            page = im.convert("RGB")
        banner_h = 184
        scale = max(sw / page.width, banner_h / page.height)
        page = page.resize(
            (round(page.width * scale), round(page.height * scale)), Image.Resampling.LANCZOS
        )
        left, upper = (page.width - sw) // 2, (page.height - banner_h) // 2
        screen.paste(page.crop((left, upper, left + sw, upper + banner_h)), (0, top))
        top += banner_h
    y = top + 18
    sd.rounded_rectangle([18, y, sw - 18, sh - 24], 26, fill=(255, 255, 255))
    with Image.open(product_file) as im:
        product = im.convert("RGBA")
    bbox = product.getchannel("A").getbbox()
    if bbox:
        product = product.crop(bbox)
    product.thumbnail((sw - 70, 200), Image.Resampling.LANCZOS)
    screen.paste(product, ((sw - product.width) // 2, y + 16), product)
    row = y + 16 + product.height + 8
    sd.text((40, row), device.product_name, font=small, fill=(40, 40, 46))
    sd.text((40, row + 30), device.price, font=price, fill=_rgb8(c.price))
    price_w = round(sd.textlength(device.price, font=price))
    by = row + 98
    sd.rounded_rectangle([34, by, sw - 34, by + 70], 35, fill=_rgb8(c.highlight))
    width = sd.textlength(device.button, font=button)
    sd.text(((sw - width) / 2, by + 17), device.button, font=button, fill=(20, 20, 24))
    mask = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, sw - 1, sh - 1], _RADIUS - _BEZEL, fill=255)
    phone.paste(screen, (_BEZEL, _BEZEL), mask)
    pd.rounded_rectangle(
        [_PHONE_W // 2 - 62, _BEZEL + 12, _PHONE_W // 2 + 62, _BEZEL + 44], 16, fill=(8, 8, 10, 255)
    )
    a = np.asarray(phone, dtype=np.float32) / np.float32(255)
    rows: dict[str, Box] = {
        "price": (_BEZEL + 40, _BEZEL + row + 30, _BEZEL + 40 + price_w, _BEZEL + row + 86),
        "button": (_BEZEL + 34, _BEZEL + by, _BEZEL + sw - 34, _BEZEL + by + 70),
    }
    return a[..., :3].copy(), a[..., 3].copy(), rows


def _device_boxes(slot: Slot, drawn: Box, rows: dict[str, Box]) -> dict[str, Box]:
    """Price and button boxes in the picture: the phone's local rows, scaled and rotated."""
    scale = slot.width / _PHONE_W
    cx, cy = (drawn[0] + drawn[2]) / 2, (drawn[1] + drawn[3]) / 2
    angle = math.radians(slot.rotation)

    def to_canvas(x: float, y: float) -> tuple[float, float]:
        dx, dy = (x - _PHONE_W / 2) * scale, (y - _PHONE_H / 2) * scale
        # PIL rotates counter-clockwise on screen (y down): x' = x cos + y sin, y' = -x sin + y cos
        return cx + dx * math.cos(angle) + dy * math.sin(angle), cy - dx * math.sin(
            angle
        ) + dy * math.cos(angle)

    out: dict[str, Box] = {}
    for name, (x0, y0, x1, y1) in rows.items():
        corners = [to_canvas(x, y) for x in (x0, x1) for y in (y0, y1)]
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        out[name] = (round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys)))
    return out


# --- the story badge ---------------------------------------------------------------------------


def _badge(scene: Scene, badge: StoryBadge) -> tuple[Array, Array]:
    """A digital alarm clock showing the badge text, struck through in the alert red."""
    c = scene.colours
    red = _rgb8(c.strike)
    w, h = 500, 220

    def draw(d: ImageDraw.ImageDraw, _im: Image.Image) -> None:
        y = 44
        for bx in (70, w - 150):  # bells
            d.ellipse(
                [bx, y - 40, bx + 80, y + 40], fill=(*red, 255), outline=(0, 0, 0, 255), width=6
            )
        d.rounded_rectangle(
            [0, y, w, y + h], 44, fill=(14, 14, 16, 255), outline=(0, 0, 0, 255), width=8
        )
        d.rounded_rectangle([10, y + 10, w - 10, y + h - 10], 36, outline=(*red, 255), width=8)
        size = 172
        while size > 80 and d.textlength(badge.text.upper(), font=_font(scene.font, size)) > w - 60:
            size -= 6
        d.text(
            (w / 2, y + h / 2 + 8),
            badge.text.upper(),
            font=_font(scene.font, size),
            anchor="mm",
            fill=(0xFF, 0x3B, 0x30, 255),
        )
        d.rectangle([60, y + h, 110, y + h + 26], fill=(0, 0, 0, 255))
        d.rectangle([w - 110, y + h, w - 60, y + h + 26], fill=(0, 0, 0, 255))
        if badge.struck:
            line = [(30, 300 - 40), (w + 10 - 30, 40)]
            d.line(line, fill=(255, 255, 255, 255), width=36)
            d.line(line, fill=(*red, 255), width=20)

    return px.rgba_layer((w + 10, 300), draw)
