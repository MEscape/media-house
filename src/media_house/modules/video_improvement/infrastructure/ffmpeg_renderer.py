"""Renders the plan with FFmpeg: one streaming pass, no frame ever held in Python.

    decode -> denoise (source YUV) -> [16-bit RGB -> 3D LUT -> YUV] -> sharpen -> encode

The colour stage is a single ``lut3d`` with tetrahedral interpolation in 16-bit RGB, so the
whole grade costs one lookup per pixel regardless of how many corrections it contains. Audio
streams, metadata, chapters and timecode are copied; timestamps are passed through unchanged.

Hardware: a GPU encoder (NVENC) and decoder (CUDA) are used when ``hardware`` is ``auto`` AND a
one-frame trial encode proved them usable AND the pixel format fits the hardware; otherwise, and
whenever a GPU run fails, the same plan runs on the CPU. The CPU path needs nothing special.
"""

from collections.abc import Callable

from media_house.core.application.ports import OutputLine, OutputStream
from media_house.modules.video_improvement.application.ports import RenderRequest
from media_house.modules.video_improvement.domain.color import ColorSpec, Primaries, Transfer
from media_house.modules.video_improvement.domain.settings import ProcessingProfile
from media_house.modules.video_improvement.domain.source import VideoFacts
from media_house.modules.video_improvement.infrastructure.ffmpeg_tool import (
    FfmpegTool,
    matrix_name,
    range_name,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ProcessFailedError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Revision of how commands are built (part of the processing identity).
REVISION = "1"
_RGB_CONVERT = "flags=accurate_rnd+full_chroma_int"
_SOFTWARE = {"h264": "libx264", "h265": "libx265", "prores": "prores_ks"}
_HARDWARE = {"h264": "h264_nvenc", "h265": "hevc_nvenc"}
_TAG_TRANSFER = {Transfer.BT709: "bt709", Transfer.SRGB: "iec61966-2-1"}
_TAG_PRIMARIES = {Primaries.BT709: "bt709", Primaries.BT2020: "bt2020"}


def pixel_format(profile: ProcessingProfile, facts: VideoFacts) -> str:
    """The delivery pixel format: the source's chroma layout and bit depth, unless configured."""
    if profile.output.codec == "prores":
        return "yuv422p10le"
    depth = profile.output.bit_depth or facts.bit_depth or 8
    source = facts.pixel_format or ""
    chroma = "422" if "422" in source else "444" if "444" in source else "420"
    return f"yuv{chroma}p{'10le' if depth >= 10 else ''}"


def _tags(color_applied: bool, facts: VideoFacts) -> str:
    """A ``setparams`` filter tagging the output: Rec.709 when the colour stage ran, else the
    source's own declarations (tags are frame properties, so they are set in the filter chain)."""
    if color_applied:
        return "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
    color: ColorSpec = facts.color
    parts: list[str] = []
    if facts.color_matrix:
        parts.append(f"colorspace={facts.color_matrix}")
    if color.primaries in _TAG_PRIMARIES:
        parts.append(f"color_primaries={_TAG_PRIMARIES[color.primaries]}")
    if color.transfer in _TAG_TRANSFER:
        parts.append(f"color_trc={_TAG_TRANSFER[color.transfer]}")
    if facts.color_range:
        parts.append(f"range={facts.color_range}")
    return "setparams=" + ":".join(parts) if parts else ""


def denoise_filter(strength: float) -> str:
    """hqdn3d with spatial and temporal strengths growing together with ``strength`` (0-1)."""
    luma_spatial = 1.0 + 5.0 * strength
    chroma_spatial = 0.75 * luma_spatial
    luma_temporal = 1.5 * luma_spatial
    chroma_temporal = 0.75 * luma_temporal
    return (
        f"hqdn3d={luma_spatial:.3f}:{chroma_spatial:.3f}:{luma_temporal:.3f}:{chroma_temporal:.3f}"
    )


def sharpen_filter(amount: float) -> str:
    """Luma-only unsharp mask: a 5x5 kernel at ``amount``; chroma untouched."""
    return f"unsharp=lx=5:ly=5:la={amount:.3f}:cx=3:cy=3:ca=0"


def _progress_watcher(
    duration: float, report: Callable[[float], None]
) -> Callable[[OutputLine], None]:
    """Turns FFmpeg's ``-progress`` lines into the finished fraction of the video."""

    def watch(line: OutputLine) -> None:
        text = line.text.strip()
        if line.stream is OutputStream.STDOUT and text.startswith("out_time_us="):
            try:
                seconds = int(text.partition("=")[2]) / 1_000_000
            except ValueError:
                return
            report(min(1.0, max(0.0, seconds / duration)))

    return watch


class FfmpegRenderer:
    """Structurally implements ``application.ports.VideoRenderer``."""

    def __init__(self, tool: FfmpegTool) -> None:
        self._tool = tool
        self._usable: dict[str, bool] = {}

    @property
    def identity(self) -> str:
        return f"ffmpeg-renderer-{REVISION}+{self._tool.version}"

    # --- encoder choice -------------------------------------------------------------------------
    def encoder_for(
        self, profile: ProcessingProfile, facts: VideoFacts, cancellation: CancellationToken
    ) -> str:
        codec = profile.output.codec
        hardware = _HARDWARE.get(codec)
        if (
            profile.execution.hardware == "auto"
            and hardware is not None
            and self._hardware_fits(codec, pixel_format(profile, facts))
            and self._works(hardware, cancellation)
        ):
            return hardware
        return _SOFTWARE[codec]

    @staticmethod
    def _hardware_fits(codec: str, pix_fmt: str) -> bool:
        """NVENC encodes 4:2:0 only; H.264 only at 8 bits (HEVC also at 10)."""
        if "420" not in pix_fmt:
            return False
        return codec == "h265" or pix_fmt == "yuv420p"

    def _works(self, encoder: str, cancellation: CancellationToken) -> bool:
        """Whether a one-frame encode with ``encoder`` succeeds here (asked once, remembered)."""
        if encoder not in self._usable:
            self._usable[encoder] = self._trial(encoder, cancellation)
        return self._usable[encoder]

    def _trial(self, encoder: str, cancellation: CancellationToken) -> bool:
        if encoder not in self._tool.encoders(cancellation):
            _log.info("GPU encoder is not in this FFmpeg build", encoder=encoder)
            return False
        result = self._tool.run(
            [
                "-loglevel", "error", "-f", "lavfi",
                "-i", "color=c=black:s=256x256:r=25:d=0.2",
                "-c:v", encoder, "-pix_fmt", "yuv420p", "-f", "null", "-",
            ],
            cancellation,
            check=False,
        )  # fmt: skip
        if not result.succeeded:
            reason = next(
                (line for line in result.stderr.splitlines() if "nvenc" in line.lower()),
                (result.stderr.strip().splitlines() or ["unknown"])[-1],
            )
            _log.warning(
                "GPU encoder unusable, the CPU encoder is used", encoder=encoder, reason=reason
            )
        return result.succeeded

    # --- rendering -------------------------------------------------------------------------------
    def render(
        self,
        request: RenderRequest,
        cancellation: CancellationToken,
        on_progress: Callable[[float], None] | None = None,
    ) -> None:
        encoder = self.encoder_for(request.profile, request.facts, cancellation)
        hardware = encoder in _HARDWARE.values()
        try:
            self._run(request, encoder, hardware, cancellation, on_progress)
        except ProcessFailedError:
            if not hardware:
                raise
            software = _SOFTWARE[request.profile.output.codec]
            _log.warning("GPU render failed, repeating on the CPU", encoder=encoder)
            self._run(request, software, False, cancellation, on_progress)

    def _run(
        self,
        request: RenderRequest,
        encoder: str,
        hardware: bool,
        cancellation: CancellationToken,
        on_progress: Callable[[float], None] | None = None,
    ) -> None:
        facts, plan, profile = request.facts, request.plan, request.profile
        out_format = pixel_format(profile, facts)
        filters: list[str] = []
        if plan.denoise is not None:
            filters.append(denoise_filter(plan.denoise.strength))
        if plan.color is not None and request.lut_file is not None:
            filters.append(
                f"scale=in_range={range_name(facts)}:in_color_matrix={matrix_name(facts)}:"
                f"out_range=pc:{_RGB_CONVERT},format=gbrp16le,"
                f"lut3d=file={request.lut_file.name}:interp=tetrahedral,"
                f"scale=out_color_matrix=bt709:out_range=tv:{_RGB_CONVERT}"
            )
        filters.append(f"format={out_format}")
        if plan.sharpen is not None:
            filters.append(sharpen_filter(plan.sharpen.amount))
        if tags := _tags(plan.color is not None, facts):
            filters.append(tags)

        args = ["-loglevel", "error"]
        if facts.rotation:
            args.append("-noautorotate")  # the display matrix is kept, not baked into the pixels
        if hardware:
            args += ["-hwaccel", "cuda"]
        args += [
            "-i", str(request.source),
            "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0", "-map_chapters", "0",
            "-vf", ",".join(filters),
            "-fps_mode", "passthrough", "-enc_time_base", "demux",
            *self._codec_args(encoder, profile, out_format),
            "-c:a", "copy",
        ]  # fmt: skip
        if facts.timecode:
            args += ["-timecode", facts.timecode]
        if profile.output.codec != "prores":
            args += ["-movflags", "+faststart"]
        args.append(str(request.destination))
        cwd = request.lut_file.parent if request.lut_file else None
        watcher = None
        if on_progress is not None:
            args = ["-progress", "pipe:1", "-nostats", *args]
            watcher = _progress_watcher(facts.duration, on_progress)
        self._tool.run(args, cancellation, cwd=cwd, on_output=watcher)

    @staticmethod
    def _codec_args(encoder: str, profile: ProcessingProfile, out_format: str) -> list[str]:
        output = profile.output
        if encoder == "prores_ks":
            return ["-c:v", encoder, "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", out_format]
        if encoder.endswith("_nvenc"):
            args = ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", "-cq", str(output.crf)]
            args += ["-b:v", "0", "-pix_fmt", out_format]
        else:
            args = ["-c:v", encoder, "-preset", output.preset, "-crf", str(output.crf)]
            args += ["-pix_fmt", out_format]
        if profile.output.codec == "h265":
            args += ["-tag:v", "hvc1"]
        return args
