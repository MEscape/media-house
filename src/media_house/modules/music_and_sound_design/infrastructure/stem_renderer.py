"""``StemRenderer`` adapter: one FFmpeg filter graph renders a plan's music and effects.

Every music segment and effect is read from its library file, resampled to the stem rate, cut
to its planned source span (sample-exact after resampling), given its planned gain and fades,
and delayed to its timeline position (in samples). Music is summed into a bus that optionally
carries the planned ducking as a gain envelope (linear ramps over the attack before and the
release after each range); effects are summed on top. Sums use ``normalize=0`` and nothing
limits or normalises the result: mastering belongs to audio_improvement.
"""

import re
from fractions import Fraction
from pathlib import Path

from media_house.modules.music_and_sound_design.application.ports import StemOptions, StemReport
from media_house.modules.music_and_sound_design.domain.errors import StemRenderFailed
from media_house.modules.music_and_sound_design.domain.model import DuckingIntent, SoundDesignPlan
from media_house.modules.music_and_sound_design.domain.timeline import fraction
from media_house.modules.music_and_sound_design.domain.values import RENDERER_VERSION
from media_house.modules.music_and_sound_design.infrastructure.ffmpeg_audio import FfmpegAudio
from media_house.shared.concurrency import CancellationToken

#: The codec may carry a tag in brackets: "Audio: pcm_f32le ([3][0][0][0] / 0x0003), 48000 Hz".
_STREAM = re.compile(r"Audio: (\w+)[^,\n]*, (\d+) Hz, (\w+)")
_SAMPLES = re.compile(r"Number of samples: (\d+)")
_PEAK = re.compile(r"Peak level dB: (-?inf|-?\d+(?:\.\d+)?)")
_CHANNELS = {"mono": 1, "stereo": 2}


def _s(value: Fraction | float) -> str:
    return f"{float(value):.6f}"


def _samples(t: Fraction, rate: int) -> int:
    return round(t * rate)


def _chain(
    index: int,
    label: str,
    src_start: Fraction,
    src_end: Fraction,
    timeline_start: Fraction,
    gain_db: float,
    fade_in: Fraction,
    fade_out: Fraction,
    rate: int,
) -> str:
    length = src_end - src_start
    parts = [
        f"[{index}:a]aresample={rate}",
        "aformat=sample_fmts=fltp:channel_layouts=stereo",
        f"atrim=start_sample={_samples(src_start, rate)}:end_sample={_samples(src_end, rate)}",
        "asetpts=PTS-STARTPTS",
        f"volume={gain_db:.3f}dB",
    ]
    if fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={_s(fade_in)}")
    if fade_out > 0:
        parts.append(f"afade=t=out:st={_s(length - fade_out)}:d={_s(fade_out)}")
    delay = _samples(timeline_start, rate)
    if delay:
        parts.append(f"adelay=delays={delay}S:all=1")
    return ",".join(parts) + f"[{label}]"


def ducking_expression(ducks: list[DuckingIntent]) -> str:
    """The music gain (linear) at time ``t``: the deepest active duck wins (never summed)."""
    factors = []
    for d in ducks:
        start, end = float(fraction(d.timeline_start)), float(fraction(d.timeline_end))
        attack, release = max(d.attack_seconds, 1e-3), max(d.release_seconds, 1e-3)
        floor = 10 ** (-d.depth_db / 20)
        envelope = (
            f"clip(min((t-{start - attack:.6f})/{attack:.6f}"
            f"\\,({end + release:.6f}-t)/{release:.6f})"
            "\\,0\\,1)"
        )
        factors.append(f"(1-{1 - floor:.6f}*{envelope})")
    if not factors:
        return "1"
    # A balanced tree of min(): FFmpeg's expression parser refuses nesting deeper than ~100,
    # which a chain of one min() per duck reached in a 2-minute talking video.
    while len(factors) > 1:
        pairs = zip(factors[::2], factors[1::2], strict=False)
        merged = [f"min({a}\\,{b})" for a, b in pairs]
        factors = merged + ([factors[-1]] if len(factors) % 2 else [])
    return factors[0]


def build_graph(plan: SoundDesignPlan, options: StemOptions) -> tuple[list[str], str]:
    """The asset id of every FFmpeg input (in order) and the filter graph text."""
    rate = options.sample_rate
    total = _samples(fraction(plan.timeline_duration), rate)
    inputs: list[str] = []
    music_labels: list[str] = []
    sfx_labels: list[str] = []
    lines: list[str] = []
    for cue in plan.music:
        if cue.asset is None:
            continue
        for seg in cue.segments:
            label = f"m{len(music_labels)}"
            lines.append(
                _chain(
                    len(inputs),
                    label,
                    fraction(seg.source_start),
                    fraction(seg.source_end),
                    fraction(seg.timeline_start),
                    seg.gain_db,
                    fraction(seg.fade_in),
                    fraction(seg.fade_out),
                    rate,
                )
            )
            inputs.append(cue.asset.asset_id)
            music_labels.append(label)
    for event in plan.sfx:
        label = f"s{len(sfx_labels)}"
        lines.append(
            _chain(
                len(inputs),
                label,
                fraction(event.source_start),
                fraction(event.source_end),
                fraction(event.timeline_start),
                event.gain_db,
                Fraction(0),
                Fraction(0),
                rate,
            )
        )
        inputs.append(event.asset.asset_id)
        sfx_labels.append(label)
    buses: list[str] = []
    if music_labels:
        mixed = (
            "".join(f"[{m}]" for m in music_labels)
            + f"amix=inputs={len(music_labels)}:normalize=0:duration=longest:dropout_transition=0"
            if len(music_labels) > 1
            else f"[{music_labels[0]}]anull"
        )
        ducks = list(plan.ducking) if options.include_ducking else []
        lines.append(f"{mixed},volume=volume='{ducking_expression(ducks)}':eval=frame[music]")
        buses.append("music")
    buses += sfx_labels
    tail = f"apad=whole_len={total},atrim=end_sample={total}"
    if not buses:
        lines.append(f"anullsrc=r={rate}:cl=stereo,atrim=end_sample={total}[out]")
    elif len(buses) == 1:
        lines.append(f"[{buses[0]}]{tail}[out]")
    else:
        lines.append(
            "".join(f"[{b}]" for b in buses)
            + f"amix=inputs={len(buses)}:normalize=0:duration=longest:dropout_transition=0,"
            + f"{tail}[out]"
        )
    return inputs, ";\n".join(lines)


class FfmpegStemRenderer:
    def __init__(self, ffmpeg: FfmpegAudio) -> None:
        self._ffmpeg = ffmpeg

    @property
    def identity(self) -> str:
        return f"stem renderer {RENDERER_VERSION}; ffmpeg {self._ffmpeg.version}"

    def render(
        self,
        plan: SoundDesignPlan,
        sources: dict[str, Path],
        destination: Path,
        options: StemOptions,
        cancellation: CancellationToken,
    ) -> StemReport:
        inputs, graph = build_graph(plan, options)
        missing = sorted({a for a in inputs if a not in sources})
        if missing:
            raise StemRenderFailed(f"no local file for {', '.join(missing)}")
        script = destination.with_suffix(".filtergraph.txt")
        script.write_text(graph, encoding="utf-8")
        args = ["-loglevel", "error"]
        for asset_id in inputs:
            args += ["-i", str(sources[asset_id])]
        args += [
            "-/filter_complex", str(script), "-map", "[out]",
            "-c:a", "pcm_f32le", "-ar", str(options.sample_rate), "-ac", "2", str(destination),
        ]  # fmt: skip
        self._ffmpeg.run(args, cancellation)
        log = self._ffmpeg.run(
            [
                "-loglevel", "info", "-i", str(destination),
                "-af", "astats=measure_perchannel=none", "-f", "null", "-",
            ],
            cancellation,
        )  # fmt: skip
        stream = _STREAM.search(log)
        samples = _SAMPLES.findall(log)
        peak = _PEAK.findall(log)
        if stream is None or not samples:
            raise StemRenderFailed("the rendered stem could not be read back")
        rate = int(stream.group(2))
        return StemReport(
            duration_seconds=int(samples[-1]) / rate,
            peak_dbfs=None if not peak or "inf" in peak[-1] else float(peak[-1]),
            sample_rate=rate,
            channels=_CHANNELS.get(stream.group(3), 0),
            codec=stream.group(1),
        )
