"""A treatment -> one FFmpeg filter graph (text) plus its per-frame command files.

Precision path (verified per render from FFmpeg's own negotiation log): decode -> 32-bit float
RGB (``gbrpf32le``) -> every effect -> one conversion to ``yuv422p10le`` for ProRes 422 HQ.
Filters that cannot work in float are used only where they stay at 12-16 bit (``framerate``
12-bit, ``lenscorrection``/``rgbashift``/``pixelize`` 16-bit, grain in ``yuv444p16le``: FFmpeg's
float noise is not calibrated). 8-bit-only filters (``vignette``, ``drawbox``, ``perspective``,
``zoompan``, ``minterpolate``) never touch the picture; ``drawbox`` draws only binary masks,
which are exact in 8 bits and are feathered in float. ``minterpolate`` runs only with explicit
consent and is then reported as a low-precision step.
"""

import hashlib
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from statistics import median

from media_house.modules.vfx_engine.application.ports import RenderJob
from media_house.modules.vfx_engine.domain.facts import Box, fr
from media_house.modules.vfx_engine.domain.model import Effect
from media_house.modules.vfx_engine.domain.values import (
    RENDER_PIX_FMT,
    WORKING_PIX_FMT,
    EffectType,
    Interpolation,
    SegmentKind,
)

_F = WORKING_PIX_FMT
#: Highlights isolated by ``colorlevels`` go negative in float; a 16-bit hop clamps them to 0-1
#: (a negative value would DARKEN a screen blend).
_CLAMP = f"format=gbrp16le,format={WORKING_PIX_FMT}"
#: noise filter strength per unit of grain standard deviation (measured on yuv444p16le).
_NOISE_PER_STD = 1 / 0.0024
_MATRIX = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "smpte170m", "bt2020nc": "bt2020"}


def _q(rate: Fraction) -> str:
    """An exact rate for FFmpeg (``30000/1001``; a decimal would drift and break ``framerate``)."""
    return f"{rate.numerator}/{rate.denominator}"


def _num(value: Fraction | float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


@dataclass(slots=True)
class Graph:
    """The filter graph script and the command files it reads (written next to it)."""

    lines: list[str] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)
    inputs: list[list[str]] = field(default_factory=list)
    low_precision: list[str] = field(default_factory=list)
    counter: int = 0

    def label(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"

    def add(self, text: str) -> None:
        self.lines.append(text)

    def script(self) -> str:
        return ";\n".join(self.lines)


def build_graph(job: RenderJob, workdir: Path) -> Graph:
    t = job.treatment
    media = job.media
    fps = job.frame_rate
    w, h = media.width, media.height
    g = Graph()
    matrix = _MATRIX.get((media.color_space or "").lower(), "bt709" if h >= 720 else "smpte170m")
    rng = "full" if (media.color_range or "") == "pc" else "limited"
    # input 0: the source, seeked near the first needed frame; -copyts keeps container times
    times = [fr(t.source_start)]
    if t.time_map is not None:
        times += [fr(s.source_start) for s in t.time_map.segments]
    seek = max(Fraction(0), min(times) - 2)
    g.inputs.append(["-copyts", "-ss", _num(seek), "-i", str(job.source)])
    main = _time_mapped(g, job, matrix, rng)
    for effect in t.active():
        main = _effect(g, job, effect, main, w, h, fps, workdir)
    out_matrix = matrix if matrix != "smpte170m" else "bt601"
    g.add(
        f"[{main}]scale=out_color_matrix={out_matrix}:out_range={rng}:"
        f"flags=lanczos+accurate_rnd+full_chroma_int,format={RENDER_PIX_FMT}[out]"
    )
    return g


# --- time ------------------------------------------------------------------------------------
def _time_mapped(g: Graph, job: RenderJob, matrix: str, rng: str) -> str:
    t = job.treatment
    fps = job.frame_rate
    src_fps = job.media.frame_rate
    decode = (
        f"[0:v]scale=in_color_matrix={matrix}:in_range={rng}:flags=accurate_rnd+full_chroma_int,"
        f"format={_F},setsar=1"
    )
    if t.time_map is None:
        pieces = [(SegmentKind.NORMAL, fr(t.source_start), Fraction(1), t.frames)]
        interpolation = Interpolation.DUPLICATE
    else:
        pieces = [
            (s.kind, fr(s.source_start), fr(s.rate), s.end_frame - s.start_frame)
            for s in t.time_map.segments
        ]
        interpolation = t.time_map.interpolation
    pieces = [p for p in pieces if p[3] > 0]
    labels = [g.label("seg") for _ in pieces]
    if len(pieces) == 1:
        g.add(f"{decode}[{labels[0]}in]")
    else:
        g.add(f"{decode},split={len(pieces)}" + "".join(f"[{x}in]" for x in labels))
    for (kind, s0, rate, frames), lab in zip(pieces, labels, strict=True):
        exact = (
            f"tpad=stop_mode=clone:stop={frames},trim=end_frame={frames},setpts=N/(({_q(fps)})*TB)"
        )
        if kind is SegmentKind.FREEZE:
            g.add(
                f"[{lab}in]trim=start={_num(s0)},trim=end_frame=1,setpts=PTS-STARTPTS,"
                f"loop=loop={frames - 1}:size=1:start=0,{exact}[{lab}]"
            )
            continue
        span = rate * Fraction(frames) / fps
        if kind is SegmentKind.REVERSE:
            g.add(
                f"[{lab}in]trim=start={_num(s0)}:end={_num(s0 + span + 1 / src_fps)},"
                f"setpts=PTS-STARTPTS,reverse,setpts=N/(({_q(src_fps)})*TB),fps={_q(fps)},"
                f"{exact}[{lab}]"
            )
            continue
        margin = 2 / src_fps
        retime = "" if rate == 1 else f",setpts=(PTS-STARTPTS)/{_num(rate)}"
        if rate < 1 and interpolation is Interpolation.BLEND:
            resample = f"framerate=fps={_q(fps)},format={_F}"
        elif rate < 1 and interpolation is Interpolation.MOTION:
            resample = f"minterpolate=fps={_q(fps)}:mi_mode=mci:mc_mode=aobmc:vsbmc=1,format={_F}"
            g.low_precision.append("minterpolate: 8-bit yuv (explicitly allowed)")
        else:
            resample = f"fps={_q(fps)}"
        g.add(
            f"[{lab}in]trim=start={_num(s0)}:end={_num(s0 + span + margin)},setpts=PTS-STARTPTS"
            f"{retime},{resample},{exact}[{lab}]"
        )
    if len(pieces) == 1:
        return labels[0]
    joined = g.label("tm")
    g.add(
        "".join(f"[{x}]" for x in labels)
        + f"concat=n={len(pieces)}:v=1:a=0,setpts=N/(({_q(fps)})*TB)[{joined}]"
    )
    return joined


# --- effects -----------------------------------------------------------------------------------
def _effect(
    g: Graph,
    job: RenderJob,
    e: Effect,
    main: str,
    w: int,
    h: int,
    fps: Fraction,
    workdir: Path,
) -> str:
    short = min(w, h)
    out = g.label("fx")
    match e.effect_type:
        case EffectType.PUNCH_IN:
            if "crop@pi" in g.script():
                return main  # every punch-in of the clip is one per-frame crop track
            g.commands["punch.cmd"] = _punch_commands(job, w, h, fps)
            g.add(
                f"[{main}]sendcmd=f=punch.cmd,crop@pi=w={w}:h={h}:x=0:y=0,"
                f"scale={w}:{h}:flags=lanczos+accurate_rnd,setsar=1[{out}]"
            )
        case EffectType.PRIVACY_BLUR:
            return _privacy(g, job, e, main, w, h, fps)
        case EffectType.BLUR:
            sigma = max(0.5, e.param("sigma") * short)
            kind = {
                "box": f"avgblur=sizeX={max(1, round(sigma))}",
                "directional": f"dblur=angle={_num(e.param('angle'))}:radius={_num(sigma * 2)}",
            }.get(e.mode, f"gblur=sigma={_num(sigma)}:steps=3")
            g.add(f"[{main}]{kind}[{out}]")
        case EffectType.LENS_DISTORTION:
            g.add(
                f"[{main}]lenscorrection=k1={_num(e.param('k1'))}:k2={_num(e.param('k2'))}:"
                f"i=bilinear,format={_F}[{out}]"
            )
        case EffectType.GLOW:
            a, b, glow = g.label("ga"), g.label("gb"), g.label("gl")
            th = e.param("threshold", 0.85)
            tint = ""
            if job.glow_tint:
                r, gg, bb = (int(job.glow_tint[i : i + 2], 16) / 255 for i in (1, 3, 5))
                tint = f",colorchannelmixer=rr={_num(r)}:gg={_num(gg)}:bb={_num(bb)}"
            g.add(f"[{main}]split[{a}][{b}]")
            g.add(
                f"[{b}]colorlevels=rimin={_num(th)}:gimin={_num(th)}:bimin={_num(th)},{_CLAMP},"
                f"gblur=sigma={_num(max(1.0, e.param('radius') * short))}:steps=3{tint}[{glow}]"
            )
            g.add(
                f"[{a}][{glow}]blend=all_mode=screen:all_opacity={_num(e.param('strength'))}[{out}]"
            )
        case EffectType.LIGHT_RAYS:
            return _light_rays(g, e, main, w, h)
        case EffectType.MOTION_BLUR:
            frames = max(2, round(e.param("frames", 3)))
            g.add(f"[{main}]tmix=frames={frames}:weights='{' '.join(['1'] * frames)}'[{out}]")
        case EffectType.STYLIZE:
            if e.mode == "posterize":
                step = 65536 / max(2, round(e.param("levels", 6)))
                expr = f"floor(val/{step:.3f})*{step:.3f}+{step / 2:.3f}"
                g.add(
                    f"[{main}]format=rgb48le,lutrgb=r='{expr}':g='{expr}':b='{expr}',format={_F}[{out}]"
                )
            else:
                m = "0.2126:0.7152:0.0722"
                rr, rg, rb = m.split(":")
                g.add(
                    f"[{main}]colorchannelmixer=rr={rr}:rg={rg}:rb={rb}:gr={rr}:gg={rg}:gb={rb}:"
                    f"br={rr}:bg={rg}:bb={rb}[{out}]"
                )
        case EffectType.CHROMATIC_ABERRATION:
            px = max(1, round(e.param("shift") * w))
            g.add(f"[{main}]rgbashift=rh={px}:bh={-px}:edge=smear,format={_F}[{out}]")
        case EffectType.VIGNETTE:
            return _vignette(g, job, e, main, w, h, fps)
        case EffectType.FILM_GRAIN:
            strength = max(1, min(100, round(e.param("amount") * _NOISE_PER_STD)))
            g.add(
                f"[{main}]format=yuv444p16le,noise=c0s={strength}:c0f=t:all_seed={job.seed},"
                f"format={_F}[{out}]"
            )
        case EffectType.FILM_DAMAGE:
            return _film_damage(g, job, e, main, w, h, fps)
        case EffectType.COMPOSITE:
            return _composite(g, job, e, main, w, h, fps)
        case _:
            return main
    return out


def _times(job: RenderJob, fps: Fraction) -> list[str]:
    # a command applies from half a frame before its frame (no float edge ambiguity)
    return [
        _num(max(Fraction(0), (Fraction(n) - Fraction(1, 2)) / fps)) for n in range(len(job.frames))
    ]


def _punch_commands(job: RenderJob, w: int, h: int, fps: Fraction) -> str:
    lines, last = [], None
    for at, view in zip(_times(job, fps), job.frames, strict=True):
        c = view.crop
        cw, ch = max(2, round(c.width * w)), max(2, round(c.height * h))
        x = min(max(0, round(c.x0 * w)), w - cw)
        y = min(max(0, round(c.y0 * h)), h - ch)
        value = (cw, ch, x, y)
        if value != last:
            lines.append(f"{at} crop@pi w {cw}, crop@pi h {ch}, crop@pi x {x}, crop@pi y {y};")
            last = value
    return "\n".join(lines) + "\n"


def _pixel_rect(box: Box, w: int, h: int, grow: int) -> tuple[int, int, int, int]:
    """A box as whole pixels, rounded OUTWARD (a mask never shrinks), grown by ``grow``."""
    x0 = max(0, int(box.x0 * w) - grow)
    y0 = max(0, int(box.y0 * h) - grow)
    x1 = min(w, -int(-box.x1 * w // 1) + grow)
    y1 = min(h, -int(-box.y1 * h // 1) + grow)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _mask_stream(
    g: Graph,
    job: RenderJob,
    w: int,
    h: int,
    fps: Fraction,
    indices: list[int],
    name: str,
    grow: int,
    feather: float,
) -> str:
    """A float mask (1 = effect) drawn per frame from the domain's boxes, feathered in float."""
    duration = Fraction(len(job.frames) + 2) / fps
    boxes = [f"drawbox@{name}{i}=x={w + 10}:y=0:w=1:h=1:c=white:t=fill" for i in indices]
    lines: list[str] = []
    last: dict[int, tuple[int, int, int, int]] = {}
    for at, view in zip(_times(job, fps), job.frames, strict=True):
        cmds = []
        for i in indices:
            box = view.masks[i]
            rect = (w + 10, 0, 1, 1) if box is None else _pixel_rect(box, w, h, grow)
            if last.get(i) != rect:
                x, y, bw, bh = rect
                cmds.append(
                    f"drawbox@{name}{i} x {x}, drawbox@{name}{i} y {y}, "
                    f"drawbox@{name}{i} w {bw}, drawbox@{name}{i} h {bh}"
                )
                last[i] = rect
        if cmds:
            lines.append(f"{at} " + ", ".join(cmds) + ";")
    g.commands[f"{name}.cmd"] = "\n".join(lines) + "\n"
    label = g.label("mask")
    blur = f",gblur=sigma={_num(feather)}:steps=3" if feather > 0.3 else ""
    g.add(
        f"color=c=black:s={w}x{h}:r={_q(fps)}:d={_num(duration)},format=gray,"
        f"sendcmd=f={name}.cmd,{','.join(boxes)},format={_F}{blur},"
        f"trim=end_frame={len(job.frames)},setpts=N/(({_q(fps)})*TB)[{label}]"
    )
    return label


def _privacy(g: Graph, job: RenderJob, e: Effect, main: str, w: int, h: int, fps: Fraction) -> str:
    first = sum(len(x.masks) for x in job.treatment.active()[: job.treatment.active().index(e)])
    indices = list(range(first, first + len(e.masks)))
    sizes = [
        (view.masks[i].y1 - view.masks[i].y0) * h  # type: ignore[union-attr]
        for view in job.frames
        for i in indices
        if view.masks[i] is not None and view.masks[i] != Box(x0=0, y0=0, x1=1, y1=1)
    ]
    box_h = median(sizes) if sizes else h / 4
    feather = e.param("feather") * min(w, h)
    mask = _mask_stream(g, job, w, h, fps, indices, "pm", round(2 * feather), feather)
    a, b, hidden, out = g.label("pa"), g.label("pb"), g.label("ph"), g.label("fx")
    g.add(f"[{main}]split[{a}][{b}]")
    strength = e.param("strength", 0.35)
    if e.mode == "pixelate":
        cell = max(6, round(strength * box_h * 0.5))
        g.add(f"[{b}]pixelize=w={cell}:h={cell}:m=avg,format={_F}[{hidden}]")
    else:
        sigma = max(8.0, strength * box_h)
        g.add(f"[{b}]gblur=sigma={_num(sigma)}:steps=6,gblur=sigma={_num(sigma)}:steps=6[{hidden}]")
    g.add(f"[{a}][{hidden}][{mask}]maskedmerge[{out}]")
    return out


def _vignette(g: Graph, job: RenderJob, e: Effect, main: str, w: int, h: int, fps: Fraction) -> str:
    s, r0 = e.param("strength", 0.25), min(0.95, e.param("radius", 0.55))
    rr = "hypot((X/W-0.5)*2,(Y/H-0.5)*2)/sqrt(2)"
    tt = f"clip(({rr}-{_num(r0)})/{_num(1 - r0)},0,1)"
    expr = f"1-{_num(s)}*({tt})*({tt})*(3-2*({tt}))"
    mask, out = g.label("vm"), g.label("fx")
    g.add(
        f"color=c=white:s={w}x{h}:r={_q(fps)}:d={_num(1 / fps)},format={_F},"
        f"geq=r='{expr}':g='{expr}':b='{expr}',loop=loop={len(job.frames)}:size=1:start=0,"
        f"trim=end_frame={len(job.frames)},setpts=N/(({_q(fps)})*TB)[{mask}]"
    )
    g.add(f"[{main}][{mask}]blend=all_mode=multiply:shortest=1[{out}]")
    return out


def _light_rays(g: Graph, e: Effect, main: str, w: int, h: int) -> str:
    taps = 8
    th, length, strength = (
        e.param("threshold", 0.85),
        e.param("length", 0.15),
        e.param("strength", 0.35),
    )
    a, b = g.label("la"), g.label("lb")
    g.add(f"[{main}]split[{a}][{b}]")
    tap_labels = [g.label("lt") for _ in range(taps)]
    g.add(
        f"[{b}]colorlevels=rimin={_num(th)}:gimin={_num(th)}:bimin={_num(th)},{_CLAMP},split={taps}"
        + "".join(f"[{x}i]" for x in tap_labels)
    )
    for i, lab in enumerate(tap_labels):
        f = 1 + length * i / (taps - 1)
        sw, sh = round(w * f / 2) * 2, round(h * f / 2) * 2
        g.add(
            f"[{lab}i]scale={sw}:{sh}:flags=bilinear,"
            f"crop={w}:{h}:{(sw - w) // 2}:{(sh - h) // 2}[{lab}]"
        )
    rays, out = g.label("lr"), g.label("fx")
    g.add("".join(f"[{x}]" for x in tap_labels) + f"mix=inputs={taps}[{rays}]")
    g.add(f"[{a}][{rays}]blend=all_mode=screen:all_opacity={_num(strength)}[{out}]")
    return out


def _film_damage(
    g: Graph, job: RenderJob, e: Effect, main: str, w: int, h: int, fps: Fraction
) -> str:
    """Exposure flicker and thin vertical scratches, both from a seeded hash (deterministic)."""
    flicker, per_second = e.param("flicker", 0.06), e.param("scratches", 1.5)
    lines, scratch = [], []
    for n, at in enumerate(_times(job, fps)):
        rnd = [
            int.from_bytes(hashlib.sha256(f"{job.seed}:{n}:{k}".encode()).digest()[:4]) / 2**32
            for k in range(3)
        ]
        lines.append(f"{at} exposure@fd exposure {_num((rnd[0] * 2 - 1) * flicker)};")
        on = rnd[1] < per_second / float(fps)
        x = round(rnd[2] * (w - 4)) if on else w + 10
        scratch.append(f"{at} drawbox@fs0 x {x};")
    g.commands["flicker.cmd"] = "\n".join(lines) + "\n"
    g.commands["scratch.cmd"] = "\n".join(scratch) + "\n"
    lines_label, flick, out = g.label("sc"), g.label("fl"), g.label("fx")
    duration = Fraction(len(job.frames) + 2) / fps
    g.add(
        f"color=c=black:s={w}x{h}:r={_q(fps)}:d={_num(duration)},format=gray,"
        f"sendcmd=f=scratch.cmd,drawbox@fs0=x={w + 10}:y=0:w={max(1, w // 640)}:h={h}:c=white:t=fill,"  # noqa: E501
        f"format={_F},gblur=sigma=0.7,trim=end_frame={len(job.frames)},"
        f"setpts=N/(({_q(fps)})*TB)[{lines_label}]"
    )
    g.add(f"[{main}]sendcmd=f=flicker.cmd,exposure@fd=exposure=0[{flick}]")
    g.add(f"[{flick}][{lines_label}]blend=all_mode=screen:all_opacity=0.35:shortest=1[{out}]")
    return out


def _composite(
    g: Graph, job: RenderJob, e: Effect, main: str, w: int, h: int, fps: Fraction
) -> str:
    if not job.overlays:
        return main
    o = job.overlays[0]
    index = len(g.inputs)
    g.inputs.append(
        ["-copyts", "-ss", _num(max(Fraction(0), o.source_offset - 2)), "-i", str(o.path)]
    )
    scale, margin = e.param("scale", 0.3), e.param("margin", 0.04)
    pw, ph = round(w * scale / 2) * 2, round(h * scale / 2) * 2
    corner = round(e.param("corner", 1))
    mx, my = round(margin * w), round(margin * h)
    x = mx if corner in (0, 2) else w - pw - mx
    y = my if corner in (0, 1) else h - ph - my
    frames = len(job.frames)
    first = job.treatment.start_frame
    start = (e.start_frame - first, e.end_frame - first)
    ov, mask, out = g.label("ov"), g.label("om"), g.label("fx")
    g.add(
        f"[{index}:v]format={_F},trim=start={_num(o.source_offset)},setpts=PTS-STARTPTS,"
        f"fps={_q(fps)},scale={pw}:{ph}:flags=lanczos,pad={w}:{h}:{x}:{y},"
        f"tpad=start={start[0]}:stop={frames}:stop_mode=clone,trim=end_frame={frames},"
        f"setpts=N/(({_q(fps)})*TB)[{ov}]"
    )
    level = max(0, min(255, round(e.param("opacity", 1.0) * 255)))
    g.add(
        f"color=c=black:s={w}x{h}:r={_q(fps)}:d={_num(Fraction(frames + 2) / fps)},format=gray,"
        f"drawbox=x={x}:y={y}:w={pw}:h={ph}:c=0x{level:02x}{level:02x}{level:02x}:t=fill:"
        f"enable='between(n,{start[0]},{start[1] - 1})',format={_F},"
        f"trim=end_frame={frames},setpts=N/(({_q(fps)})*TB)[{mask}]"
    )
    top = ov
    if e.mode and e.mode != "normal":
        mode = {"addition": "addition"}.get(e.mode, e.mode)
        a, b, blended = g.label("ca"), g.label("cb"), g.label("cbl")
        g.add(f"[{main}]split[{a}][{b}]")
        g.add(f"[{b}][{ov}]blend=all_mode={mode}[{blended}]")
        main, top = a, blended
    g.add(f"[{main}][{top}][{mask}]maskedmerge[{out}]")
    return out
