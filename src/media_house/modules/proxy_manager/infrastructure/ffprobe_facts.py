"""Reads source and output facts with ffprobe: the same measurements for both, so they compare.

Frames are counted from packets with a presentation time from zero on, the way a player shows
them: a clip cut without re-encoding carries hidden pre-roll packets that no decoder displays.
Nothing is decoded unless ``decode`` is asked for.
"""

import statistics
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Any

from media_house.modules.proxy_manager.domain.errors import UnsupportedSource, ValidationFailed
from media_house.modules.proxy_manager.domain.source import (
    ORIGIN_PROBE,
    AudioFacts,
    SourceFacts,
    VideoFacts,
)
from media_house.modules.proxy_manager.domain.validation import (
    OutputAudio,
    OutputFacts,
    OutputVideo,
)
from media_house.modules.proxy_manager.infrastructure.ffmpeg_tool import PacketTime, ProxyFfmpeg
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ProcessFailedError

_PRE_ROLL_SLACK = 1e-6
_VFR_TOLERANCE = 0.01
_DEFAULT_INTERVAL = 1 / 25


def _fraction(text: object) -> Fraction | None:
    if not isinstance(text, str):
        return None
    parts = text.replace(":", "/").split("/")
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None
    if len(numbers) == 1:
        numbers.append(1)
    if len(numbers) != 2 or numbers[0] <= 0 or numbers[1] <= 0:
        return None
    return Fraction(numbers[0], numbers[1])


def _rotation(stream: dict[str, Any]) -> int:
    """Clockwise degrees a player turns the picture (ffprobe reports it counter-clockwise)."""
    for side in stream.get("side_data_list") or ():
        if isinstance(side, dict) and isinstance(side.get("rotation"), int | float):
            return round(-float(side["rotation"]) / 90) * 90 % 360
    return 0


def _timeline(packets: list[PacketTime]) -> tuple[int, float, float, float] | None:
    """(visible packets, first pts, end of last packet, typical interval); ``None`` if empty."""
    stamped = [(p.pts, p.duration or 0.0) for p in packets if p.pts is not None]
    visible = sorted((t, d) for t, d in stamped if t >= -_PRE_ROLL_SLACK)
    if not visible:
        return None
    times = [t for t, _ in visible]
    first, end = times[0], max(t + d for t, d in visible)
    gaps = [b - a for a, b in pairwise(times) if b > a]
    interval = statistics.median(gaps) if gaps else (visible[0][1] or _DEFAULT_INTERVAL)
    return len(visible), first, end, interval


class FfprobeFacts:
    """Structurally implements ``application.ports.MediaProbe``."""

    def __init__(self, tool: ProxyFfmpeg) -> None:
        self._tool = tool

    # --------------------------------------------------------------------- source
    def probe_source(self, path: Path, cancellation: CancellationToken) -> SourceFacts:
        try:
            info = self._tool.streams_json(path, cancellation)
            packets = self._tool.packets(path, cancellation)
        except ProcessFailedError as exc:
            raise UnsupportedSource("the file could not be read by ffprobe") from exc
        streams: list[dict[str, Any]] = info.get("streams", [])
        video: VideoFacts | None = None
        for stream in streams:
            if stream.get("codec_type") == "video" and not _attached_picture(stream):
                video = self._source_video(stream, packets.get(int(stream["index"]), []))
                break
        audio = tuple(
            self._source_audio(s, packets.get(int(s["index"]), []))
            for s in streams
            if s.get("codec_type") == "audio"
        )
        return SourceFacts(video, audio, _timecode(info, streams), ORIGIN_PROBE)

    @staticmethod
    def _source_video(stream: dict[str, Any], packets: list[PacketTime]) -> VideoFacts:
        timeline = _timeline(packets)
        width, height = stream.get("width"), stream.get("height")
        if timeline is None or not isinstance(width, int) or not isinstance(height, int):
            raise UnsupportedSource("the video stream has no usable size or frames")
        frames, first, end, interval = timeline
        nominal, average = (
            _fraction(stream.get("r_frame_rate")),
            _fraction(stream.get("avg_frame_rate")),
        )
        variable = (
            nominal is not None
            and average is not None
            and abs(float(average) - float(nominal)) / float(nominal) > _VFR_TOLERANCE
        )
        return VideoFacts(
            index=int(stream["index"]),
            width=width,
            height=height,
            sample_aspect_ratio=_fraction(stream.get("sample_aspect_ratio")) or Fraction(1),
            rotation=_rotation(stream),
            visible_frames=frames,
            first_pts=first,
            end_pts=end,
            frame_interval=interval,
            variable_frame_rate=variable,
        )

    @staticmethod
    def _source_audio(stream: dict[str, Any], packets: list[PacketTime]) -> AudioFacts:
        timeline = _timeline(packets)
        rate, channels = stream.get("sample_rate"), stream.get("channels")
        if timeline is None or not isinstance(channels, int) or not str(rate).isdigit():
            raise UnsupportedSource(
                f"audio stream {stream.get('index')} has no usable channels, rate or packets"
            )
        _, first, end, _ = timeline
        return AudioFacts(
            index=int(stream["index"]),
            codec=str(stream.get("codec_name")),
            channels=channels,
            sample_rate=int(str(rate)),
            first_pts=first,
            end_pts=end,
            language=(stream.get("tags") or {}).get("language"),
        )

    # --------------------------------------------------------------------- output
    def probe_output(
        self, path: Path, cancellation: CancellationToken, *, decode: bool
    ) -> OutputFacts:
        try:
            info = self._tool.streams_json(path, cancellation)
            packets = self._tool.packets(path, cancellation)
        except ProcessFailedError as exc:
            raise ValidationFailed("the finished file cannot be read by ffprobe") from exc
        streams: list[dict[str, Any]] = info.get("streams", [])
        video: OutputVideo | None = None
        audio: list[OutputAudio] = []
        for stream in streams:
            kind = stream.get("codec_type")
            index = int(stream["index"])
            if kind == "video" and video is None and not _attached_picture(stream):
                video = _output_video(stream, packets.get(index, []))
            elif kind == "audio":
                found = _output_audio(stream, packets.get(index, []))
                if found is not None:
                    audio.append(found)
        # a timecode track is how the container carries the source timecode (checked by tag)
        timecode_tracks = sum(1 for s in streams if _is_timecode_track(s))
        counted = (1 if video else 0) + len(audio) + timecode_tracks
        return OutputFacts(
            size_bytes=path.stat().st_size,
            video=video,
            audio=tuple(audio),
            other_streams=len(streams) - counted,
            timecode=_timecode(info, streams),
            decode_errors=self._decode_errors(path, cancellation) if decode else None,
        )

    def _decode_errors(self, path: Path, cancellation: CancellationToken) -> int:
        """Decoder complaints decoding every stream to the end (0 means a clean decode)."""
        result = self._tool.run(
            ["-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
            cancellation,
            check=False,
        )
        complaints = [line for line in result.stderr.splitlines() if line.strip()]
        if result.exit_code != 0 and not complaints:
            return 1
        return len(complaints)


def _is_timecode_track(stream: dict[str, Any]) -> bool:
    return stream.get("codec_type") == "data" and stream.get("codec_tag_string") == "tmcd"


def _attached_picture(stream: dict[str, Any]) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def _timecode(info: dict[str, Any], streams: list[dict[str, Any]]) -> str | None:
    for tags in [(info.get("format") or {}).get("tags"), *(s.get("tags") for s in streams)]:
        if isinstance(tags, dict):
            for key, value in tags.items():
                if str(key).lower() == "timecode" and value:
                    return str(value)
    return None


def _output_video(stream: dict[str, Any], packets: list[PacketTime]) -> OutputVideo | None:
    timeline = _timeline(packets)
    width, height = stream.get("width"), stream.get("height")
    if timeline is None or not isinstance(width, int) or not isinstance(height, int):
        return None
    frames, first, end, interval = timeline
    return OutputVideo(
        codec=str(stream.get("codec_name")),
        width=width,
        height=height,
        sample_aspect_ratio=_fraction(stream.get("sample_aspect_ratio")) or Fraction(1),
        rotation=_rotation(stream),
        pixel_format=stream.get("pix_fmt"),
        frames=frames,
        first_pts=first,
        end_pts=end,
        frame_interval=interval,
    )


def _output_audio(stream: dict[str, Any], packets: list[PacketTime]) -> OutputAudio | None:
    timeline = _timeline(packets)
    rate, channels = stream.get("sample_rate"), stream.get("channels")
    if timeline is None or not isinstance(channels, int) or not str(rate).isdigit():
        return None
    _, first, end, _ = timeline
    language = (stream.get("tags") or {}).get("language")
    return OutputAudio(
        str(stream.get("codec_name")), channels, int(str(rate)), first, end, language
    )
