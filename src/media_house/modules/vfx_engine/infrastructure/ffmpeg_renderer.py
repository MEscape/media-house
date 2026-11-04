"""Renders treatments with FFmpeg (through the ``ProcessRunner`` port) and measures the result.

One render = one derived clip: ProRes 422 HQ, ``yuv422p10le``, the edit's frame rate, the
source's size, exactly the planned number of frames, colour tags and (where meaningful) the
source timecode carried over, no audio (the plan states the audio intent). The source file is
hashed before and after: an original is never touched.

Render QC measures the FILE, not the intent: ffprobe facts (size, pix_fmt depth, exact frame
count), FFmpeg's own format negotiation (any automatic conversion below 10 bits), frame-to-frame
change spikes against the source span, clipping against the source, and - for privacy - how
much detail is left inside every tracked box (it must be gone, or the render is refused).
"""

import hashlib
import json
import re
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from media_house.core.application.ports import OutputLine, ProcessRunner, ProcessSpec
from media_house.modules.vfx_engine.application.ports import (
    RenderedClip,
    RenderJob,
    SourceMedia,
)
from media_house.modules.vfx_engine.domain.facts import Box, fr, rat
from media_house.modules.vfx_engine.domain.model import RenderQc
from media_house.modules.vfx_engine.domain.values import (
    RENDER_PIX_FMT,
    RENDER_VERSION,
    EffectType,
)
from media_house.modules.vfx_engine.infrastructure.filter_graph import build_graph
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ProcessFailedError, ToolNotFoundError

_VERSION = re.compile(r"version\s+(\S+)")
_AUTO_SCALE = re.compile(r"auto_scale[^]]*\].*?fmt:(\w+).*?->.*?fmt:(\w+)")
_PROBE_TIMEOUT = 600.0
_ANALYSIS_WIDTH = 480
_SUPPORTED = frozenset(
    {
        EffectType.PUNCH_IN,
        EffectType.SLOW_MOTION,
        EffectType.SPEED_RAMP,
        EffectType.FREEZE_FRAME,
        EffectType.REVERSE,
        EffectType.BLUR,
        EffectType.GLOW,
        EffectType.LIGHT_RAYS,
        EffectType.VIGNETTE,
        EffectType.FILM_GRAIN,
        EffectType.FILM_DAMAGE,
        EffectType.CHROMATIC_ABERRATION,
        EffectType.LENS_DISTORTION,
        EffectType.MOTION_BLUR,
        EffectType.STYLIZE,
        EffectType.PRIVACY_BLUR,
        EffectType.COMPOSITE,
    }
)


def bits_of(pix_fmt: str) -> int:
    """Bits per component of an FFmpeg pixel format name."""
    if "f32" in pix_fmt:
        return 32
    if "f16" in pix_fmt:
        return 16
    match = re.search(r"(\d+)(le|be)$", pix_fmt)
    if match is None:
        return 8
    bits = int(match.group(1))
    return 16 if bits in (48, 64) else bits


class FfmpegEffectRenderer:
    def __init__(
        self, runner: ProcessRunner, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"
    ) -> None:
        self._runner = runner
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._version: str | None = None

    # --- identity ------------------------------------------------------------------------------
    def _ffmpeg_version(self) -> str:
        if self._version is None:
            result = self._runner.run(
                ProcessSpec(self._ffmpeg, ["-version"], timeout_seconds=_PROBE_TIMEOUT, check=True)
            )
            match = _VERSION.search(result.stdout.splitlines()[0] if result.stdout else "")
            self._version = match.group(1) if match else "unknown"
        return self._version

    def identity(self) -> str:
        try:
            return f"ffmpeg {self._ffmpeg_version()} / vfx-render {RENDER_VERSION}"
        except (ToolNotFoundError, ProcessFailedError):
            return f"ffmpeg unavailable / vfx-render {RENDER_VERSION}"

    def unavailable(self) -> str | None:
        try:
            self._ffmpeg_version()
        except ToolNotFoundError:
            return "FFmpeg is not on PATH"
        except ProcessFailedError as exc:
            return f"FFmpeg does not run: {exc}"
        return None

    def supports(self) -> frozenset[EffectType]:
        return _SUPPORTED if self.unavailable() is None else frozenset()

    # --- probing (only without a stored inspection) ---------------------------------------------
    def probe(self, path: Path, cancellation: CancellationToken) -> SourceMedia:
        stream = self._stream(path, cancellation, count=False)
        rate = Fraction(str(stream.get("r_frame_rate") or stream.get("avg_frame_rate") or "0/1"))
        tags = stream.get("tags") or {}
        return SourceMedia(
            width=int(stream["width"]),
            height=int(stream["height"]),
            frame_rate=rate,
            pixel_format=str(stream.get("pix_fmt") or "unknown"),
            color_space=stream.get("color_space"),
            color_primaries=stream.get("color_primaries"),
            color_transfer=stream.get("color_transfer"),
            color_range=stream.get("color_range"),
            timecode=tags.get("timecode") if isinstance(tags, dict) else None,
        )

    def _stream(
        self, path: Path, cancellation: CancellationToken, *, count: bool
    ) -> dict[str, Any]:
        args = ["-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json"]
        if count:
            args.insert(0, "-count_frames")
        result = self._runner.run(
            ProcessSpec(
                self._ffprobe, [*args, str(path)], timeout_seconds=_PROBE_TIMEOUT, check=True
            ),
            cancellation=cancellation,
        )
        streams = json.loads(result.stdout or "{}").get("streams") or []
        if not streams:
            raise ProcessFailedError(self._ffprobe, 0, f"no video stream in {path.name}")
        stream: dict[str, Any] = streams[0]
        return stream

    # --- rendering -----------------------------------------------------------------------------
    def render(self, job: RenderJob, cancellation: CancellationToken) -> RenderedClip:
        before = _sha256(job.source)
        workdir = job.output.parent
        graph = build_graph(job, workdir)
        for name, text in graph.commands.items():
            (workdir / name).write_text(text, encoding="utf-8")
        script = workdir / "graph.txt"
        script.write_text(graph.script(), encoding="utf-8")
        negotiated: list[str] = []

        def watch(line: OutputLine) -> None:
            match = _AUTO_SCALE.search(line.text)
            # only a loss counts: >= 10-bit picture data squeezed below 10 bits (8-bit sources
            # and binary masks entering the float chain are not losses)
            if match and bits_of(match.group(1)) >= 10 and bits_of(match.group(2)) < 10:
                negotiated.append(f"{match.group(1)}->{match.group(2)}")

        media = job.media
        args: list[str] = ["-hide_banner", "-nostdin", "-y", "-v", "verbose"]
        for spec in graph.inputs:
            args += spec
        args += ["-/filter_complex", script.name, "-map", "[out]", "-an", "-sn", "-dn"]
        args += ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0"]
        args += ["-pix_fmt", RENDER_PIX_FMT, "-r", _rate(job.frame_rate)]
        args += ["-frames:v", str(job.treatment.frames), "-map_metadata", "-1"]
        for flag, value in (
            ("-color_primaries", media.color_primaries),
            ("-color_trc", media.color_transfer),
            ("-colorspace", media.color_space),
            ("-color_range", media.color_range),
        ):
            if value and value not in {"unknown", "reserved"}:
                args += [flag, value]
        timecode = _timecode(media.timecode, fr(job.treatment.source_start), media.frame_rate)
        if timecode is not None:
            args += ["-timecode", timecode]
        args += ["-metadata", f"comment=media-house vfx {job.treatment.treatment_id}"]
        args += [job.output.name]
        started = time.monotonic()
        result = self._runner.run(
            ProcessSpec(
                self._ffmpeg,
                args,
                cwd=workdir,
                timeout_seconds=job.timeout_seconds,
                check=False,
                max_captured_lines=400,
            ),
            cancellation=cancellation,
            on_output=watch,
        )
        if result.exit_code != 0 or not job.output.is_file():
            tail = [x for x in result.stderr.splitlines() if "rror" in x][-5:]
            raise ProcessFailedError(self._ffmpeg, result.exit_code, " | ".join(tail))
        seconds = time.monotonic() - started
        qc = self._measure(
            job, cancellation, before, sorted(set(negotiated + graph.low_precision)), seconds
        )
        return RenderedClip(path=job.output, qc=qc, engine=self.identity())

    # --- render QC -------------------------------------------------------------------------------
    def _measure(
        self,
        job: RenderJob,
        cancellation: CancellationToken,
        before: str,
        low: list[str],
        seconds: float,
    ) -> RenderQc:
        stream = self._stream(job.output, cancellation, count=True)
        frames = int(str(stream.get("nb_read_frames") or 0))
        pix = str(stream.get("pix_fmt") or "")
        depth = int(str(stream.get("bits_per_raw_sample") or 0)) or bits_of(pix)
        rate = Fraction(str(stream.get("r_frame_rate") or "0/1"))
        w, h = int(str(stream.get("width"))), int(str(stream.get("height")))
        aw = min(_ANALYSIS_WIDTH, w)
        ah = max(2, round(aw * h / w / 2) * 2)
        out = self._gray(
            job.output,
            [],
            f"scale={aw}:{ah}:flags=area",
            aw,
            ah,
            job.output.parent / "out.gray",
            cancellation,
        )
        t = job.treatment
        used_end = fr(t.time_map.source_used_end) if t.time_map else fr(t.source_end)
        start = fr(t.source_start)
        src = self._gray(
            job.source,
            ["-copyts", "-ss", f"{float(max(Fraction(0), start - 2)):.6f}"],
            f"trim=start={float(start):.6f}:end={float(used_end + 2 / job.media.frame_rate):.6f},"
            f"setpts=PTS-STARTPTS,fps={_rate(job.frame_rate)},scale={aw}:{ah}:flags=area",
            aw,
            ah,
            job.output.parent / "src.gray",
            cancellation,
        )
        boundaries = set()
        if t.time_map is not None:
            boundaries = {s.start_frame - t.start_frame for s in t.time_map.segments}
        new_spikes = max(
            0,
            _spikes(out, job.limits.spike_ratio, boundaries)
            - _spikes(src, job.limits.spike_ratio, set()),
        )
        concealment = None
        if job.protected and any(job.protected):
            concealment = _concealment(job, out, src, aw, ah)
        after = _sha256(job.source)
        return RenderQc(
            width=w,
            height=h,
            pix_fmt=pix,
            bit_depth=depth,
            frame_rate=rat(rate),
            frames_expected=t.frames,
            frames_actual=frames,
            low_precision_steps=tuple(low),
            new_spikes=new_spikes,
            clipped_source=_clipped(src),
            clipped_output=_clipped(out),
            concealment=concealment,
            source_unchanged=before == after,
            render_seconds=round(seconds, 3),
        )

    def _gray(
        self,
        path: Path,
        pre: list[str],
        chain: str,
        w: int,
        h: int,
        target: Path,
        cancellation: CancellationToken,
    ) -> NDArray[np.float32]:
        args = ["-hide_banner", "-nostdin", "-y", "-v", "error", *pre, "-i", str(path), "-an"]
        args += ["-vf", f"{chain},format=gray16le", "-f", "rawvideo", str(target)]
        self._runner.run(
            ProcessSpec(self._ffmpeg, args, timeout_seconds=_PROBE_TIMEOUT, check=True),
            cancellation=cancellation,
        )
        raw = np.fromfile(target, dtype="<u2")
        target.unlink(missing_ok=True)
        count = raw.size // (w * h)
        return (raw[: count * w * h].reshape(count, h, w).astype(np.float32)) / 65535.0


# --- measurements --------------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _rate(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _spikes(frames: NDArray[np.float32], ratio: float, skip: set[int]) -> int:
    """Frames changing ``ratio`` x more than the median change from their predecessor."""
    if len(frames) < 3:
        return 0
    diffs = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    floor = max(float(np.median(diffs)) * ratio, 0.02)
    return sum(1 for i, d in enumerate(diffs, start=1) if d > floor and i not in skip)


def _clipped(frames: NDArray[np.float32]) -> float | None:
    if len(frames) == 0:
        return None
    return round(float((frames >= 0.98).mean()), 5)


def _detail(region: NDArray[np.float32]) -> float:
    if region.shape[0] < 3 or region.shape[1] < 3:
        return 0.0
    lap = (
        4 * region[1:-1, 1:-1]
        - region[:-2, 1:-1]
        - region[2:, 1:-1]
        - region[1:-1, :-2]
        - region[1:-1, 2:]
    )
    return float(np.abs(lap).mean())


def _concealment(
    job: RenderJob, out: NDArray[np.float32], src: NDArray[np.float32], w: int, h: int
) -> float | None:
    """Smallest share of detail removed inside the tracked boxes (1 = nothing left)."""
    start = fr(job.treatment.source_start)
    fps = job.frame_rate
    removed: list[float] = []
    for view, boxes in zip(job.frames, job.protected, strict=False):
        if view.index >= len(out):
            break
        source_index = round((view.source_time - start) * fps)
        if not 0 <= source_index < len(src):
            continue
        for box in boxes:
            x0, y0, x1, y1 = _rect(box, w, h)
            before = _detail(src[source_index, y0:y1, x0:x1])
            if before < 0.004:  # nothing recognisable there to begin with (flat / black)
                continue
            after = _detail(out[view.index, y0:y1, x0:x1])
            removed.append(max(0.0, 1 - after / before))
    return round(min(removed), 4) if removed else None


def _rect(box: Box, w: int, h: int) -> tuple[int, int, int, int]:
    return (
        max(0, int(box.x0 * w)),
        max(0, int(box.y0 * h)),
        min(w, int(box.x1 * w + 0.999)),
        min(h, int(box.y1 * h + 0.999)),
    )


def _timecode(text: str | None, offset: Fraction, rate: Fraction) -> str | None:
    """The source timecode advanced to the clip's first frame (integer rates only)."""
    if text is None or rate.denominator != 1:
        return None
    match = re.fullmatch(r"(\d\d):(\d\d):(\d\d)[:;](\d\d)", text.strip())
    if match is None:
        return None
    fps = rate.numerator
    hh, mm, ss, ff = (int(x) for x in match.groups())
    total = ((hh * 60 + mm) * 60 + ss) * fps + ff + round(offset * fps)
    ff, total = total % fps, total // fps
    ss, total = total % 60, total // 60
    mm, hh = total % 60, (total // 60) % 24
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"
