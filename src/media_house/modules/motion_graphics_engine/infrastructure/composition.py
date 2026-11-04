"""HyperFrames composition writer: composition + keyframes -> one deterministic HTML document.

Same scene in, byte-identical HTML out: no clock, no randomness, fixed number formatting,
element ids generated in order. Everything is local (GSAP and the fonts are copied next to the
document; nothing is fetched at render time). The root stays unpainted so the MOV keeps alpha.

Animated properties map to GSAP: x/y/scale/rotation/opacity directly; reveal, highlight and
blur through CSS variables (``--rv``, ``--hl``, ``--bl``); counters through a proxy object.
Easings are the style's cubic beziers, evaluated by a small solver (same curve as CSS).
"""

import hashlib
import html
import re
from fractions import Fraction
from itertools import pairwise

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.application.ports import Scene, ScenePart
from media_house.modules.motion_graphics_engine.domain.model import Element, Keyframe, TextBlock
from media_house.modules.motion_graphics_engine.domain.values import ElementType, Prop

COMPOSITION_ID = "mg"
GSAP_FILE = "gsap.min.js"

_PROPS = {
    Prop.X: "x",
    Prop.Y: "y",
    Prop.SCALE: "scale",
    Prop.ROTATION: "rotation",
    Prop.OPACITY: "opacity",
    Prop.BLUR: "--bl",
    Prop.REVEAL: "--rv",
    Prop.HIGHLIGHT: "--hl",
}
#: Reveal clips: the moving edge goes from fully clipped (--rv 0) to 50 % outside the box
#: (--rv 1), so shadows and glows are never cut once revealed.
_EDGE = "calc((1 - var(--rv)) * 150% - 50%)"
_CLIP = {
    "left": f"inset(-50% {_EDGE} -50% -50%)",
    "right": f"inset(-50% -50% -50% {_EDGE})",
    "top": f"inset(-50% -50% {_EDGE} -50%)",
    "bottom": f"inset({_EDGE} -50% -50% -50%)",
    "center": "inset(-50% calc((1 - var(--rv)) * 75% - 25%))",
}
_NUMBER = re.compile(r"^(?P<num>[\d.]+(?:,(?P<dec>\d+))?)\s*(?P<unit>.*)$")

_BEZIER_JS = """function bez(x1, y1, x2, y2) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = (t) => ((ax * t + bx) * t + cx) * t;
  const sy = (t) => ((ay * t + by) * t + cy) * t;
  const dx = (t) => (3 * ax * t + 2 * bx) * t + cx;
  return function (x) {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let t = x;
    for (let i = 0; i < 8; i++) {
      const e = sx(t) - x, d = dx(t);
      if (Math.abs(e) < 1e-7) return sy(t);
      if (Math.abs(d) < 1e-6) break;
      t -= e / d;
    }
    let lo = 0, hi = 1;
    t = x;
    for (let i = 0; i < 40; i++) {
      const v = sx(t);
      if (Math.abs(v - x) < 1e-7) break;
      if (v < x) lo = t; else hi = t;
      t = (lo + hi) / 2;
    }
    return sy(t);
  };
}
function fmt(v, d, unit) {
  const fixed = Math.abs(v).toFixed(d).split(".");
  const whole = fixed[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ".");
  return (d ? whole + "," + fixed[1] : whole) + (unit ? " " + unit : "");
}"""


def num(value: float) -> str:
    """Fixed, locale-free number text (deterministic)."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def seconds(value: Rational | Fraction) -> str:
    f = Fraction(value.numerator, value.denominator)
    text = f"{float(f):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _duration_text(frames: int, fps: Rational) -> str:
    """Seconds of ``frames`` frames, truncated (never rounded up) to 6 decimals, so the
    renderer's frame count is exactly ``frames``."""
    exact = Fraction(frames) * Fraction(fps.denominator, fps.numerator)
    micro = exact.numerator * 1_000_000 // exact.denominator
    return f"{micro // 1_000_000}.{micro % 1_000_000:06d}".rstrip("0").rstrip(".")


def _rgba(hex_colour: str, alpha: float) -> str:
    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {num(alpha)})"


class _Writer:
    def __init__(self, scene: Scene) -> None:
        self.scene = scene
        self.ids: dict[str, str] = {}
        self.bases: dict[str, float] = {}
        self.counters: dict[str, tuple[int, str]] = {}
        #: A batch shows each part only in its own frames; one graphic is its own only part.
        self.parts = scene.parts or (
            ScenePart(
                composition=scene.composition,
                animation=scene.animation,
                start_frame=0,
                frames=scene.frames,
            ),
        )
        self.revealed = {
            t.target for p in self.parts for t in p.animation.tracks if t.prop is Prop.REVEAL
        }
        self.offset = Fraction(0)

    def at(self, frame: int) -> Fraction:
        return Fraction(frame) * Fraction(self.scene.fps.denominator, self.scene.fps.numerator)

    def html_id(self, element_id: str) -> str:
        if element_id not in self.ids:
            self.ids[element_id] = f"e{len(self.ids)}"
        return self.ids[element_id]

    # --- markup -----------------------------------------------------------------------------

    def element(self, e: Element) -> str:
        r = e.rect
        style = [
            f"left:{r.left}px",
            f"top:{r.top}px",
            f"width:{r.width}px",
            f"height:{r.height}px",
            f"z-index:{e.z}",
            f"transform-origin:{num(e.origin_x * 100)}% {num(e.origin_y * 100)}%",
        ]
        if e.id in self.revealed:
            style.append(f"clip-path:{_CLIP.get(e.reveal_from, _CLIP['left'])}")
        filters = ["blur(calc(var(--bl) * 1px))"]
        if e.glow and e.glow_px:
            glow = _rgba(e.glow, e.glow_opacity)
            filters += [
                f"drop-shadow(0 0 {e.glow_px}px {glow})",
                f"drop-shadow(0 0 {e.glow_px // 2}px {glow})",
            ]
        if e.shadow_color and e.shadow_px:
            shade = _rgba(e.shadow_color, e.shadow_opacity)
            filters.append(f"drop-shadow({e.shadow_px}px {e.shadow_px}px 0 {shade})")
        if e.shadow_color and e.shadow_blur_px:
            soft = _rgba(e.shadow_color, e.shadow_opacity)
            blur = e.shadow_blur_px
            filters.append(f"drop-shadow(0 {blur // 4}px {blur}px {soft})")
        style.append("filter:" + " ".join(filters))
        self.bases[e.id] = e.rotation
        inner = ""
        if e.type is ElementType.PANEL:
            if e.fill:
                style.append(f"background:{_rgba(e.fill, e.opacity)}")
            if e.stroke and e.stroke_px:
                style.append(f"border:{e.stroke_px}px solid {e.stroke}")
            if e.radius_px:
                style.append(f"border-radius:{e.radius_px}px")
        elif e.type is ElementType.IMAGE and e.image_asset_id:
            inner = f'<img src="images/{html.escape(_file_id(e.image_asset_id))}.png" alt="">'
        elif e.type is ElementType.SHAPE:
            fill = e.fill or "none"
            stroke = (
                f' stroke="{e.stroke}" stroke-width="{e.stroke_px * 2 if e.fill else e.stroke_px}"'
                if e.stroke
                else ""
            )
            inner = (
                f'<svg width="{r.width}" height="{r.height}" viewBox="0 0 {r.width} {r.height}"'
                f' overflow="visible"><path d="{e.path}" fill="{fill}"{stroke}'
                ' stroke-linejoin="round" stroke-linecap="round" paint-order="stroke"/></svg>'
            )
        elif e.type is ElementType.TEXT and e.text is not None:
            inner = self.text(e.id, e.text)
            style += self.text_style(e.text)
        css = ";".join(style)
        return f'<div id="{self.html_id(e.id)}" class="el" style="{css}">{inner}</div>'

    def text_style(self, t: TextBlock) -> list[str]:
        family = f"'mh-{t.face}'" + (f",'mh-{t.emoji_face}'" if t.emoji_face else "")
        style = [
            f"font-family:{family}",
            f"font-weight:{t.weight}",
            f"font-size:{t.size_px}px",
            f"line-height:{t.line_px}px",
            f"letter-spacing:{num(t.tracking_em)}em",
            f"text-align:{t.align}",
            f"padding:{t.stroke_px}px",
            f"word-spacing:{t.stroke_px}px",
            f"--fill:{t.fill}",
            f"--hlc:{t.highlight}",
        ]
        if t.stroke and t.stroke_px:
            style.append(f"-webkit-text-stroke:{t.stroke_px * 2}px {t.stroke}")
        return style

    def text(self, element_id: str, t: TextBlock) -> str:
        lines = []
        for line in t.lines:
            runs = []
            for run in line.runs:
                css = ["--hl:1"] if run.emphasis else []
                if run.emoji and t.emoji_face:  # colour glyphs: no outline around the emoji
                    css += [f"font-family:'mh-{t.emoji_face}'", "-webkit-text-stroke:0"]
                hl = f' style="{";".join(css)}"' if css else ""
                word = html.escape(run.text)
                runs.append(f'<span id="{self.html_id(run.id)}" class="run"{hl}>{word}</span>')
            lines.append(f'<div class="ln">{" ".join(runs)}</div>')
        return "".join(lines)

    # --- animation --------------------------------------------------------------------------

    def tracks(self) -> list[str]:
        out: list[str] = []
        for i, part in enumerate(self.parts):
            self.offset = self.at(part.start_frame)
            if self.scene.parts:
                group = f'el("g{i}")'
                out.append(f'tl.set({group}, {{ display: "block" }}, {seconds(self.offset)});')
                end = self.at(part.start_frame + part.frames)
                out.append(f'tl.set({group}, {{ display: "none" }}, {seconds(end)});')
            out += self.part_tracks(part)
        self.offset = Fraction(0)
        return out

    def part_tracks(self, part: ScenePart) -> list[str]:
        out: list[str] = []
        for e in part.composition.elements:
            if e.rotation:
                out.append(
                    f'gsap.set(el("{self.html_id(e.id)}"), {{ rotation: {num(e.rotation)} }});'
                )
        for track in part.animation.tracks:
            target = f'el("{self.html_id(track.target)}")'
            if track.prop is Prop.COUNT:
                out += self.counter(track.target, target, track.keyframes)
                continue
            name = _PROPS[track.prop]
            base = self.bases.get(track.target, 0.0) if track.prop is Prop.ROTATION else 0.0
            key = f'"{name}"' if name.startswith("--") else name
            frames = track.keyframes
            first = seconds(self.offset) if self.scene.parts else "0"
            out.append(f"tl.set({target}, {{ {key}: {num(frames[0].value + base)} }}, {first});")
            for a, b in pairwise(frames):
                out.append(self.segment(target, key, a, b, base))
        return out

    def segment(self, target: str, key: str, a: Keyframe, b: Keyframe, base: float) -> str:
        start, end = (
            Fraction(a.local_at.numerator, a.local_at.denominator) + self.offset,
            Fraction(b.local_at.numerator, b.local_at.denominator) + self.offset,
        )
        if end <= start:
            return f"tl.set({target}, {{ {key}: {num(b.value + base)} }}, {seconds(end)});"
        frm, to = num(a.value + base), num(b.value + base)
        return (
            f"tl.fromTo({target}, {{ {key}: {frm} }}, {{ {key}: {to},"
            f' duration: {seconds(end - start)}, ease: ease("{b.easing}"),'
            " immediateRender: false },"
            f" {seconds(start)});"
        )

    def counter(self, element_id: str, target: str, frames: tuple[Keyframe, ...]) -> list[str]:
        element = next(e for p in self.parts for e in p.composition.elements if e.id == element_id)
        text = element.text.text if element.text else ""
        match = _NUMBER.match(text)
        decimals = len(match.group("dec") or "") if match else 0
        unit = match.group("unit") if match else ""
        run = (
            element.text.lines[0].runs[0].id if element.text and element.text.lines else element_id
        )
        span = f'el("{self.html_id(run)}")'
        holder = f"c{len(self.counters)}"
        self.counters[element_id] = (decimals, unit)
        out = [
            f"const {holder} = {{ v: {num(frames[0].value)} }};",
            f"const {holder}r = () => {{ {span}.textContent ="
            f' fmt({holder}.v, {decimals}, "{unit}"); }};',
        ]
        first = seconds(self.offset) if self.scene.parts else "0"
        out.append(
            f"tl.set({holder}, {{ v: {num(frames[0].value)}, onUpdate: {holder}r }}, {first});"
        )
        for a, b in pairwise(frames):
            start = Fraction(a.local_at.numerator, a.local_at.denominator) + self.offset
            end = Fraction(b.local_at.numerator, b.local_at.denominator) + self.offset
            if end <= start:
                continue
            out.append(
                f"tl.fromTo({holder}, {{ v: {num(a.value)} }}, {{ v: {num(b.value)},"
                f' duration: {seconds(end - start)}, ease: ease("{b.easing}"),'
                f" immediateRender: false, onUpdate: {holder}r }}, {seconds(start)});"
            )
        return out

    # --- document ---------------------------------------------------------------------------

    def document(self) -> str:
        s = self.scene
        comp = s.composition
        width, height = round(comp.width * s.scale), round(comp.height * s.scale)
        frames = 1 if s.still_frame is not None else s.frames
        duration = _duration_text(frames, s.fps)
        faces = "\n".join(
            f"@font-face {{ font-family: 'mh-{f.key}';"
            f" src: url('fonts/{f.key}.ttf') format('truetype');"
            f" font-weight: {f.weight}; font-style: normal; font-display: block; }}"
            for f in sorted(s.fonts, key=lambda f: f.key)
        )
        if s.parts:
            body = "\n    ".join(
                f'<div id="g{i}" style="display:none">'
                + "".join(self.element(e) for e in p.composition.elements)
                + "</div>"
                for i, p in enumerate(s.parts)
            )
        else:
            body = "\n    ".join(self.element(e) for e in comp.elements)
        easings = ",\n  ".join(
            f'"{e.name}": bez({num(e.x1)}, {num(e.y1)}, {num(e.x2)}, {num(e.y2)})'
            for e in s.easings
        )
        tracks = "\n".join(self.tracks())
        end = seconds(Fraction(s.frames) * Fraction(s.fps.denominator, s.fps.numerator))
        if s.still_frame is not None:
            at = seconds(Fraction(s.still_frame) * Fraction(s.fps.denominator, s.fps.numerator))
            register = (
                "const master = gsap.timeline({ paused: true });\n"
                f"master.add(tl.tweenFromTo({at}, {at},"
                f' {{ duration: {duration}, ease: "none" }}), 0);\n'
                f'window.__timelines["{COMPOSITION_ID}"] = master;'
            )
        else:
            register = f'window.__timelines["{COMPOSITION_ID}"] = tl;'
        return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width={width}, height={height}">
<script src="{GSAP_FILE}"></script>
<style>
{faces}
html, body {{ margin: 0; padding: 0; background: transparent; }}
#root {{ position: relative; width: {width}px; height: {height}px; overflow: hidden;
  background: transparent; }}
#stage {{ position: absolute; left: 0; top: 0; width: {comp.width}px; height: {comp.height}px;
  transform-origin: 0 0; transform: scale({num(s.scale)}); }}
.el {{ position: absolute; box-sizing: border-box; --rv: 1; --bl: 0; }}
.el img {{ display: block; width: 100%; height: 100%; object-fit: contain; }}
.ln {{ white-space: pre; }}
.run {{ display: inline-block; --hl: 0; paint-order: stroke fill; transform-origin: 50% 60%;
  color: color-mix(in srgb, var(--hlc) calc(var(--hl) * 100%), var(--fill)); }}
</style>
</head>
<body>
<div id="root" data-composition-id="{COMPOSITION_ID}" data-start="0" data-duration="{duration}"
  data-width="{width}" data-height="{height}">
  <div id="stage" class="clip" data-start="0" data-duration="{duration}" data-track-index="0">
    {body}
  </div>
</div>
<script>
{_BEZIER_JS}
const E = {{
  {easings}
}};
const ease = (name) => E[name] || E["linear"] || ((t) => t);
const el = (id) => document.getElementById(id);
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
{tracks}
tl.set({{}}, {{}}, {end});
{register}
</script>
</body>
</html>
"""


def _file_id(asset_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", asset_id)


def compose_html(scene: Scene) -> str:
    return _Writer(scene).document()


def image_file(asset_id: str) -> str:
    return f"images/{_file_id(asset_id)}.png"


def scene_digest(document: str, files: list[tuple[str, str]]) -> str:
    h = hashlib.sha256(document.encode("utf-8"))
    for name, checksum in sorted(files):
        h.update(f"\0{name}\0{checksum}".encode())
    return h.hexdigest()
