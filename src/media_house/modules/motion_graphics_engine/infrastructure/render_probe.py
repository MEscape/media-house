"""Render QC facts from FFprobe and decoded frames (FFmpeg -> RGBA -> alpha statistics)."""

import json
from fractions import Fraction
from pathlib import Path

import numpy as np

from media_house.core.application.ports import ProcessRunner, ProcessSpec
from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.errors import RenderFailed
from media_house.modules.motion_graphics_engine.domain.qc import AlphaSample, RenderFacts
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import Err, Ok, ProcessTimeoutError, Result, ToolNotFoundError


class FfmpegRenderProbe:
    def __init__(
        self, runner: ProcessRunner, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg"
    ) -> None:
        self._runner = runner
        self._ffprobe = ffprobe
        self._ffmpeg = ffmpeg

    def probe(
        self, path: Path, sample_frames: tuple[int, ...], cancellation: CancellationToken
    ) -> Result[RenderFacts, RenderFailed]:
        try:
            return self._probe(path, sample_frames, cancellation)
        except (ToolNotFoundError, ProcessTimeoutError) as exc:
            return Err(RenderFailed(str(exc)))

    def _probe(
        self, path: Path, sample_frames: tuple[int, ...], cancellation: CancellationToken
    ) -> Result[RenderFacts, RenderFailed]:
        args = (
            "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries",
            "stream=codec_name,profile,pix_fmt,width,height,r_frame_rate,nb_read_frames",
            "-of", "json", str(path),
        )  # fmt: skip
        result = self._runner.run(
            ProcessSpec(self._ffprobe, args, timeout_seconds=600), cancellation=cancellation
        )
        if not result.succeeded:
            return Err(RenderFailed(f"ffprobe: {result.stderr[-500:]}"))
        try:
            stream = json.loads(result.stdout)["streams"][0]
            rate = Fraction(stream.get("r_frame_rate", "0/1"))
            width, height = int(stream["width"]), int(stream["height"])
            frames = int(stream.get("nb_read_frames", 1))
        except (KeyError, IndexError, ValueError, ZeroDivisionError) as exc:
            return Err(RenderFailed(f"unreadable ffprobe output ({exc})"))
        samples = tuple(sorted({min(max(f, 0), max(frames - 1, 0)) for f in sample_frames}))
        alpha = self._alpha(path, samples, width, height, cancellation)
        if isinstance(alpha, Err):
            return alpha
        return Ok(
            RenderFacts(
                codec=str(stream.get("codec_name", "")),
                profile=str(stream.get("profile", "")),
                pix_fmt=str(stream.get("pix_fmt", "")),
                width=width,
                height=height,
                fps=Rational(rate.numerator, rate.denominator) if rate > 0 else Rational(1, 1),
                frames=frames,
                alpha=alpha.value,
            )
        )

    def _alpha(
        self,
        path: Path,
        frames: tuple[int, ...],
        width: int,
        height: int,
        cancellation: CancellationToken,
    ) -> Result[tuple[AlphaSample, ...], RenderFailed]:
        raw = path.with_name(f"{path.stem}-alpha.rgba")
        select = "+".join(f"eq(n\\,{f})" for f in frames)
        args = (
            "-v", "error", "-y", "-i", str(path), "-vf", f"select={select}",
            "-fps_mode", "passthrough",
            "-f", "rawvideo", "-pix_fmt", "rgba", str(raw),
        )  # fmt: skip
        result = self._runner.run(
            ProcessSpec(self._ffmpeg, args, timeout_seconds=600), cancellation=cancellation
        )
        if not result.succeeded or not raw.is_file():
            return Err(RenderFailed(f"ffmpeg alpha decode: {result.stderr[-500:]}"))
        data = np.fromfile(raw, dtype=np.uint8)
        raw.unlink(missing_ok=True)
        size = width * height * 4
        if data.size != size * len(frames):
            return Err(RenderFailed(f"decoded {data.size} bytes, expected {size * len(frames)}"))
        samples = []
        for i, frame in enumerate(frames):
            a = data[i * size : (i + 1) * size].reshape(height, width, 4)[:, :, 3]
            samples.append(
                AlphaSample(
                    frame=frame,
                    alpha_min=int(a.min()),
                    alpha_max=int(a.max()),
                    covered=round(float((a > 0).mean()), 4),
                    opaque=round(float((a == 255).mean()), 4),
                )
            )
        return Ok(tuple(samples))
