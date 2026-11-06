"""The FFmpeg filter graphs of a stem and of the final mix, as text.

Pure string building (no process), so it is testable on its own. A stem is one bus: every clip is
cut to its part of its file (sample-exact when the file's rate is known), resampled to the working
rate, given its gain, fades, ducking and delay, summed, then the bus's own gain, EQ, automation and
dynamics. The final mix sums the stems and applies the master chain. Sums never normalise
(``normalize=0``); a level problem is fixed with gain, not hidden by the mixer.
"""

from fractions import Fraction
from pathlib import Path

from media_house.modules.audio_mix_engine.application.ports import MixRequest, StemRequest
from media_house.modules.audio_mix_engine.domain.profile import Compressor, EqBand, Limiter
from media_house.modules.audio_mix_engine.domain.spec import (
    Automation,
    Duck,
    GainPoint,
    MixBus,
    MixClip,
)
from media_house.modules.audio_mix_engine.domain.values import Interpolation

#: The working layout of every stem: stereo, 32-bit float.
LAYOUT = "stereo"

_ESC = "\\,"  # a comma inside an FFmpeg expression


def seconds(value: Fraction | float) -> str:
    return f"{float(value):.6f}"


def _lin(db: float, low: float, high: float) -> float:
    return max(low, min(high, 10 ** (db / 20)))


def eq_filter(band: EqBand) -> str:
    f = f"{band.frequency_hz:g}"
    if band.kind == "highpass":
        return f"highpass=f={f}"
    if band.kind == "lowpass":
        return f"lowpass=f={f}"
    if band.kind == "peaking":
        return f"equalizer=f={f}:t=q:w={band.q:g}:g={band.gain_db:g}"
    if band.kind == "lowshelf":
        return f"lowshelf=f={f}:g={band.gain_db:g}"
    if band.kind == "highshelf":
        return f"highshelf=f={f}:g={band.gain_db:g}"
    if band.kind == "notch":
        return f"bandreject=f={f}:t=q:w={band.q:g}"
    raise ValueError(f"unknown EQ band {band.kind!r}")


def compressor_filter(c: Compressor) -> str:
    return (
        f"acompressor=threshold={_lin(c.threshold_db, 0.000977, 1.0):.6f}:ratio={c.ratio:g}"
        f":attack={c.attack_ms:g}:release={c.release_ms:g}"
        f":makeup={_lin(c.makeup_db, 1.0, 64.0):.4f}"
    )


def limiter_filter(limiter: Limiter) -> str:
    return (
        f"alimiter=limit={_lin(limiter.ceiling_db, 0.0625, 1.0):.5f}"
        f":attack={limiter.attack_ms:g}:release={limiter.release_ms:g}:level=disabled"
    )


def duck_expression(ducks: list[Duck], rate: int) -> str:
    """The gain (linear) at time ``t`` seconds: the deepest active duck wins, never summed."""
    factors = []
    for d in ducks:
        start, end = d.start_sample / rate, d.end_sample / rate
        attack, release = max(d.attack_seconds, 1e-3), max(d.release_seconds, 1e-3)
        floor = 10 ** (-d.depth_db / 20)
        envelope = (
            f"clip(min((t-{start - attack:.6f})/{attack:.6f}{_ESC}"
            f"({end + release:.6f}-t)/{release:.6f})"
            f"{_ESC}0{_ESC}1)"
        )
        factors.append(f"(1-{1 - floor:.6f}*{envelope})")
    if not factors:
        return "1"
    # a balanced tree: FFmpeg's expression parser refuses a chain nested ~100 deep
    while len(factors) > 1:
        pairs = zip(factors[::2], factors[1::2], strict=False)
        merged = [f"min({a}{_ESC}{b})" for a, b in pairs]
        factors = merged + ([factors[-1]] if len(factors) % 2 else [])
    return factors[0]


def automation_expression(
    points: tuple[GainPoint, ...], interpolation: Interpolation, rate: int, offset_seconds: float
) -> str:
    """The automation as a linear gain at time ``t``, held outside the points."""
    pts = sorted(points, key=lambda p: p.sample)
    if not pts:
        return "1"
    expr = f"{pts[-1].db:.4f}"
    for a, b in zip(reversed(pts[:-1]), reversed(pts[1:]), strict=True):
        t0, t1 = a.sample / rate + offset_seconds, b.sample / rate + offset_seconds
        u = f"clip((t-{t0:.6f})/{max(t1 - t0, 1e-6):.6f}{_ESC}0{_ESC}1)"
        shape = f"(1-cos(PI*{u}))/2" if interpolation is Interpolation.SMOOTH else u
        segment = f"{a.db:.4f}+({b.db - a.db:.4f})*{shape}"
        expr = f"if(lt(t{_ESC}{t1:.6f}){_ESC}{segment}{_ESC}{expr})"
    first = pts[0]
    expr = (
        f"if(lt(t{_ESC}{first.sample / rate + offset_seconds:.6f}){_ESC}{first.db:.4f}{_ESC}{expr})"
    )
    return f"pow(10{_ESC}({expr})/20)"


def _atempo(speed: Fraction) -> list[str]:
    """``atempo`` takes 0.5-2: a bigger change is a chain."""
    factor = float(speed)
    chain: list[str] = []
    while factor > 2.0:
        chain.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        chain.append("atempo=0.5")
        factor /= 0.5
    chain.append(f"atempo={factor:.8f}")
    return chain


def _pan(position: float) -> str:
    """Equal-power pan of a stereo signal; -1 left, +1 right."""
    import math

    angle = (position + 1) * math.pi / 4
    left, right = math.cos(angle) * math.sqrt(2), math.sin(angle) * math.sqrt(2)
    return f"pan=stereo|c0={left:.5f}*c0|c1={right:.5f}*c1"


def clip_chain(
    index: int,
    label: str,
    clip: MixClip,
    request: StemRequest,
    ducks: list[Duck],
    automation: list[Automation],
) -> str:
    rate, tb = request.sample_rate, request.timebase_rate
    start = round(clip.start_sample * rate / tb)
    length = max(1, round((clip.end_sample - clip.start_sample) * rate / tb))
    src_rate = request.source_rates.get(clip.source_id)
    s_in = Fraction(clip.source_in.numerator, clip.source_in.denominator)
    s_out = Fraction(clip.source_out.numerator, clip.source_out.denominator)
    if src_rate:
        trim = f"atrim=start_sample={round(s_in * src_rate)}:end_sample={round(s_out * src_rate)}"
    else:
        trim = f"atrim=start={seconds(s_in)}:end={seconds(s_out)}"
    parts = [f"[{index}:a]{trim}", "asetpts=PTS-STARTPTS", f"aresample={rate}"]
    parts.append(f"aformat=sample_fmts=fltp:channel_layouts={LAYOUT}")
    if clip.speed.numerator != clip.speed.denominator:
        parts += _atempo(Fraction(clip.speed.numerator, clip.speed.denominator))
    parts.append(f"apad=whole_len={length},atrim=end_sample={length}")
    gain = clip.gain_db + request.trims.get(clip.clip_id, 0.0)
    if gain:
        parts.append(f"volume={gain:.3f}dB")
    curve = "qsin" if clip.fade_curve == "equal_power" else "tri"
    length_s = length / rate
    fade_in, fade_out = clip.fade_in.value, clip.fade_out.value
    if fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={min(fade_in, length_s):.6f}:curve={curve}")
    if fade_out > 0:
        d = min(fade_out, length_s)
        parts.append(f"afade=t=out:st={length_s - d:.6f}:d={d:.6f}:curve={curve}")
    if clip.pan:
        parts.append(_pan(clip.pan))
    if start:
        parts.append(f"adelay=delays={start}S:all=1")
    mine = [d for d in ducks if clip.clip_id in d.clip_ids]
    if mine:
        parts.append(f"volume=volume='{duck_expression(mine, tb)}':eval=frame")
    for a in automation:
        if a.target_kind == "clip" and a.target_id == clip.clip_id:
            expr = automation_expression(a.points, a.interpolation, tb, 0.0)
            parts.append(f"volume=volume='{expr}':eval=frame")
    return ",".join(parts) + f"[{label}]"


def bus_chain(bus: MixBus, automation: list[Automation], request: StemRequest) -> list[str]:
    parts: list[str] = []
    if bus.gain_db:
        parts.append(f"volume={bus.gain_db:.3f}dB")
    parts += [eq_filter(b) for b in bus.eq]
    for a in automation:
        if a.target_kind == "bus" and a.target_id == bus.bus_id:
            expr = automation_expression(a.points, a.interpolation, request.timebase_rate, 0.0)
            parts.append(f"volume=volume='{expr}':eval=frame")
    if bus.compressor is not None:
        parts.append(compressor_filter(bus.compressor))
    if bus.pan:
        parts.append(_pan(bus.pan))
    return parts


def stem_graph(request: StemRequest) -> tuple[list[str], str]:
    """The source ids of the FFmpeg inputs (in order) and the filter graph text."""
    order = sorted({c.source_id for c in request.clips})
    index = {s: i for i, s in enumerate(order)}
    rate = request.sample_rate
    total = round(request.duration_samples * rate / request.timebase_rate)
    lines: list[str] = []
    labels: list[str] = []
    for n, clip in enumerate(sorted(request.clips, key=lambda c: (c.start_sample, c.clip_id))):
        label = f"c{n}"
        lines.append(
            clip_chain(
                index[clip.source_id],
                label,
                clip,
                request,
                list(request.ducks),
                list(request.automation),
            )
        )
        labels.append(label)
    summed = (
        "".join(f"[{label}]" for label in labels)
        + f"amix=inputs={len(labels)}:normalize=0:duration=longest:dropout_transition=0"
        if len(labels) > 1
        else f"[{labels[0]}]anull"
    )
    chain = bus_chain(request.bus, list(request.automation), request)
    chain.append(f"apad=whole_len={total},atrim=end_sample={total}")
    lines.append(summed + "," + ",".join(chain) + "[out]")
    return order, ";\n".join(lines)


def mix_graph(request: MixRequest) -> str:
    master = request.master
    count = len(request.stems)
    summed = (
        "".join(f"[{i}:a]" for i in range(count))
        + f"amix=inputs={count}:normalize=0:duration=longest:dropout_transition=0"
        if count > 1
        else "[0:a]anull"
    )
    parts: list[str] = []
    gain = master.gain_db + request.trim_db
    if gain:
        parts.append(f"volume={gain:.3f}dB")
    parts += [eq_filter(b) for b in master.eq]
    if master.compressor is not None:
        parts.append(compressor_filter(master.compressor))
    if master.limiter is not None:
        parts.append(limiter_filter(master.limiter))
    if request.render.channels == 1:
        parts.append("pan=mono|c0=0.5*c0+0.5*c1")
    total = request.duration_samples
    parts.append(f"apad=whole_len={total},atrim=end_sample={total}")
    return summed + "," + ",".join(parts) + "[out]"


def source_paths(order: list[str], sources: dict[str, Path]) -> list[Path]:
    return [sources[s] for s in order]
