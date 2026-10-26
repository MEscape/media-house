"""Builds the FFmpeg command for a ResolvedSpec and runs it: one streaming pass, software encoders.

What the command promises (each point is checked by validation, not trusted):

* The picture is scaled to the spec's size with the pixel shape baked in (square pixels out).
* Display rotation stays metadata: ``-noautorotate`` is an INPUT option, so it precedes ``-i``.
* Timing is the source's: ``-fps_mode passthrough`` keeps every frame and its timestamp.
* Exactly the mapped video and audio streams; chapters, data and subtitles are omitted by design.
* ``-fs`` caps the file at the reservation. It is approximate (measured overshoot up to ~8%), so
  the finished size is checked again by validation.
"""

from collections.abc import Callable

from media_house.core.application.ports import OutputLine, OutputStream
from media_house.modules.proxy_manager.application.ports import EncodeOutcome, EncodeRequest
from media_house.modules.proxy_manager.domain.spec import ResolvedSpec
from media_house.modules.proxy_manager.domain.values import Codec
from media_house.modules.proxy_manager.infrastructure.ffmpeg_tool import ProxyFfmpeg
from media_house.shared.concurrency import CancellationToken

_ENCODERS = {Codec.H264: "libx264", Codec.PRORES: "prores_ks"}
_MICROSECONDS = 1_000_000


def _video_args(spec: ResolvedSpec) -> list[str]:
    if spec.codec is Codec.PRORES:
        # profile 0 = ProRes Proxy; vendor apl0 makes players treat it as Apple's own
        return [
            *("-c:v", "prores_ks", "-profile:v", "0", "-vendor", "apl0"),
            *("-pix_fmt", spec.pixel_format),
        ]
    rate = spec.maxrate_kbps or 0
    return [
        *("-c:v", "libx264", "-preset", "veryfast", "-crf", str(spec.crf)),
        *("-maxrate", f"{rate}k", "-bufsize", f"{2 * rate}k"),
        *("-pix_fmt", spec.pixel_format),
    ]


def _audio_args(spec: ResolvedSpec) -> list[str]:
    if not spec.audio:
        return ["-an"]
    first = spec.audio[0]
    args = ["-c:a", first.codec]
    if first.bitrate_kbps is not None:
        args += ["-b:a", f"{first.bitrate_kbps}k"]
    return args


def build_encode_args(request: EncodeRequest) -> list[str]:
    """The complete FFmpeg argument list (pure, so tests can assert it without a process)."""
    spec = request.spec
    args = ["-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    if spec.rotation:
        args.append("-noautorotate")  # input option: must come before -i
    args += ["-i", str(request.source), "-map", f"0:{spec.video_source_index}"]
    for audio in spec.audio:
        args += ["-map", f"0:{audio.source_index}"]
    # Global tags, then each mapped stream's own tags (language, title). FFmpeg does not carry
    # them for every stream by itself: measured, the second audio track lost its language.
    args += ["-map_metadata", "0", "-map_chapters", "-1"]
    args += ["-map_metadata:s:0", f"0:s:{spec.video_source_index}"]
    for position, audio in enumerate(spec.audio, start=1):
        args += [f"-map_metadata:s:{position}", f"0:s:{audio.source_index}"]
    args += [
        "-vf", f"scale={spec.width}:{spec.height}:flags=bicubic,setsar=1",
        "-fps_mode", "passthrough", "-enc_time_base", "demux",
        *_video_args(spec), *_audio_args(spec),
        "-fs", str(request.max_bytes),
    ]  # fmt: skip
    if spec.container == "mp4":
        args += ["-movflags", "+faststart"]
    args.append(str(request.destination))
    return args


def _progress_watcher(
    seconds: float, report: Callable[[float], None]
) -> Callable[[OutputLine], None]:
    """Turns FFmpeg's ``-progress`` lines into the finished fraction of the video."""

    def watch(line: OutputLine) -> None:
        text = line.text.strip()
        if line.stream is OutputStream.STDOUT and text.startswith("out_time_us="):
            try:
                done = int(text.partition("=")[2]) / _MICROSECONDS
            except ValueError:
                return
            report(min(1.0, max(0.0, done / seconds)))

    return watch


class FfmpegEncoder:
    """Structurally implements ``application.ports.ProxyEncoder``."""

    def __init__(self, tool: ProxyFfmpeg) -> None:
        self._tool = tool

    @property
    def identity(self) -> str:
        return f"ffmpeg {self._tool.version}"

    def encode(
        self,
        request: EncodeRequest,
        cancellation: CancellationToken,
        on_progress: Callable[[float], None],
    ) -> EncodeOutcome:
        video = request.facts.require_video()
        seconds = max(video.end_pts - video.first_pts, video.frame_interval)
        self._tool.run(
            build_encode_args(request),
            cancellation,
            on_output=_progress_watcher(seconds, on_progress),
        )
        return EncodeOutcome(encoder=_ENCODERS[request.spec.codec])
