"""ffprobe's JSON -> the domain's normalised facts.

ffprobe reports nearly everything as strings and uses several spellings for "not stated"
(``N/A``, ``unknown``, ``0/0``, ``0``). This is the only place that knows those conventions.
A value the file does not state becomes ``unknown``; a value derived by a rule (for example a bit
depth from the pixel format) is marked ``inferred`` so nobody mistakes it for a declaration.
"""

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.model import (
    AudioStream,
    ColorInfo,
    ContainerInfo,
    Geometry,
    Integrity,
    ObservedMedia,
    OtherStream,
    TimecodeInfo,
    VideoStream,
)
from media_house.modules.media_inspection.domain.production import production_from_tags
from media_house.modules.media_inspection.domain.timecode import Timecode
from media_house.modules.media_inspection.domain.timing import (
    Packet,
    StreamTiming,
    analyze_audio_packets,
    analyze_video_packets,
)
from media_house.modules.media_inspection.domain.values import (
    ChannelClass,
    JsonValue,
    Rational,
    ScanType,
    Sourced,
)

type Info = Mapping[str, Any]
type PacketsByStream = Mapping[int, Sequence[Packet]]

_NOT_STATED = frozenset({"", "n/a", "unknown", "unspecified", "und", "none"})
_DEFAULT_TICK = 1e-6
_PCM_DEPTH = re.compile(r"pcm_[suf](\d+)")
_SUBSAMPLING = re.compile(r"(?:yuvj?a?|gbrap?|p)(4[1-4][01]|420|422|444)")
_BIT_SUFFIX = re.compile(r"(\d{2})(?:le|be)$")
_EIGHT_BIT = frozenset(
    {
        "nv12",
        "nv21",
        "rgb24",
        "bgr24",
        "rgba",
        "bgra",
        "argb",
        "abgr",
        "gray",
        "gray8",
        "pal8",
        "yuyv422",
        "uyvy422",
    }
)
_ALPHA = re.compile(r"^(?:yuva|gbrap|ya\d|rgba|bgra|argb|abgr)")
_HDR_TRANSFERS = {"smpte2084": "hdr10", "arib-std-b67": "hlg"}
_INTERLACED = frozenset({"tt", "bb", "tb", "bt"})
_LEVEL_DIVISOR = {"h264": 10, "hevc": 30}
_DURATION_TAG = re.compile(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_LAYOUT_COUNT = re.compile(r"\d+ channels?")


# ------------------------------------------------------------------------------------------
# small readers
# ------------------------------------------------------------------------------------------
def _stated(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return None if text.lower() in _NOT_STATED else text


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number == int(number) else None


def _positive(value: object) -> int | None:
    number = _integer(value)
    return number if number is not None and number > 0 else None


def _tags(info: Info) -> dict[str, str]:
    raw = info.get("tags")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _tag(tags: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    return next((_stated(v) for k, v in tags.items() if k.lower() == lowered), None)


def _duration(stream: Info, tags: Mapping[str, str]) -> float | None:
    declared = _number(stream.get("duration"))
    if declared is not None:
        return declared
    text = _tag(tags, "duration")  # Matroska keeps durations as "HH:MM:SS.nnnnnnnnn" tags
    match = _DURATION_TAG.fullmatch(text) if text else None
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _tick(time_base: Rational | None) -> float:
    return time_base.value if time_base else _DEFAULT_TICK


def _level(codec: str, level: object) -> str | None:
    number = _integer(level)
    if number is None or number <= 0:
        return None
    divisor = _LEVEL_DIVISOR.get(codec)
    return f"{number / divisor:.1f}" if divisor else str(number)


# ------------------------------------------------------------------------------------------
# video
# ------------------------------------------------------------------------------------------
def _bit_depth(stream: Info, pix_fmt: str | None) -> Sourced[int]:
    declared = _positive(stream.get("bits_per_raw_sample"))
    if declared is not None:
        return Sourced.declared(declared)
    if pix_fmt is None:
        return Sourced.unknown()
    if (suffix := _BIT_SUFFIX.search(pix_fmt)) is not None:
        return Sourced.inferred(int(suffix.group(1)))
    if pix_fmt in _EIGHT_BIT or re.fullmatch(r"yuvj?a?\d{3}p|gbrap?", pix_fmt):
        return Sourced.inferred(8)
    return Sourced.unknown()


def _subsampling(pix_fmt: str | None) -> Sourced[str]:
    if pix_fmt is None:
        return Sourced.unknown()
    if (match := _SUBSAMPLING.match(pix_fmt)) is not None:
        return Sourced.inferred(match.group(1))
    if pix_fmt in {"nv12", "nv21"} or pix_fmt.startswith("p01"):
        return Sourced.inferred("420")
    if pix_fmt.startswith(("rgb", "bgr", "gbr", "argb", "abgr")):
        return Sourced.inferred("444")
    if pix_fmt.startswith("gray"):
        return Sourced.inferred("400")
    return Sourced.unknown()


def _scan(field_order: str | None) -> Sourced[ScanType]:
    if field_order is None:
        return Sourced.unknown()
    if field_order == "progressive":
        return Sourced.declared(ScanType.PROGRESSIVE)
    if field_order in _INTERLACED:
        return Sourced.declared(ScanType.INTERLACED)
    return Sourced.unknown()


def _rotation(stream: Info, tags: Mapping[str, str]) -> Sourced[int]:
    """Clockwise degrees a player applies (ffprobe reports the matrix counter-clockwise)."""
    for side in stream.get("side_data_list") or ():
        if isinstance(side, dict) and side.get("rotation") is not None:
            degrees = _number(side.get("rotation"))
            if degrees is not None:
                return Sourced.declared(round(-degrees / 90) * 90 % 360)
    legacy = _number(_tag(tags, "rotate"))
    if legacy is not None:
        return Sourced.declared(round(legacy / 90) * 90 % 360)
    return Sourced.inferred(0)  # nothing declared: shown as stored


def _geometry(stream: Info, tags: Mapping[str, str]) -> Geometry:
    width, height = _integer(stream.get("width")) or 0, _integer(stream.get("height")) or 0
    declared_sar = Rational.parse(stream.get("sample_aspect_ratio"))
    sar = Sourced.declared(declared_sar) if declared_sar else Sourced.inferred(Rational(1, 1))
    declared_dar = Rational.parse(stream.get("display_aspect_ratio"))
    if declared_dar is not None:
        dar = Sourced.declared(declared_dar)
    elif width > 0 and height > 0:
        pixel = sar.value or Rational(1, 1)
        dar = Sourced.inferred(Rational(width * pixel.numerator, height * pixel.denominator))
    else:
        dar = Sourced.unknown()
    return Geometry(width, height, sar, dar, _rotation(stream, tags))


def _color(stream: Info, frame_side_data: Sequence[Info]) -> ColorInfo:
    def declared(key: str) -> Sourced[str]:
        value = _stated(stream.get(key))
        return Sourced.declared(value) if value else Sourced.unknown()

    transfer = declared("color_transfer")
    if transfer.value is None:
        dynamic_range: Sourced[str] = Sourced.unknown()
    else:
        dynamic_range = Sourced.inferred(_HDR_TRANSFERS.get(transfer.value, "sdr"))
    kinds = {
        str(side.get("side_data_type", "")).lower()
        for side in (*(stream.get("side_data_list") or ()), *frame_side_data)
        if isinstance(side, dict)
    }
    return ColorInfo(
        space=declared("color_space"),
        primaries=declared("color_primaries"),
        transfer=transfer,
        range=declared("color_range"),
        dynamic_range=dynamic_range,
        mastering_display="mastering display metadata" in kinds,
        content_light_level="content light level metadata" in kinds,
        dolby_vision=any("dovi" in kind or "dolby vision" in kind for kind in kinds),
    )


def _rate(value: object) -> Sourced[Rational]:
    rate = Rational.parse(value)
    return Sourced.declared(rate) if rate else Sourced.unknown()


def _bit_rate(stream: Info, timing: StreamTiming | None) -> Sourced[int]:
    declared = _positive(stream.get("bit_rate"))
    if declared is not None:
        return Sourced.declared(declared)
    if timing is not None and timing.measured_bit_rate:
        return Sourced.detected(timing.measured_bit_rate)
    return Sourced.unknown()


def _video(
    stream: Info,
    packets: Sequence[Packet] | None,
    frame_side_data: Sequence[Info],
    config: InspectionConfig,
) -> VideoStream:
    tags = _tags(stream)
    codec = str(stream.get("codec_name") or "unknown")
    pix_fmt = _stated(stream.get("pix_fmt"))
    time_base = Rational.parse(stream.get("time_base"))
    timing = analyze_video_packets(packets, _tick(time_base), config) if packets else None
    if timing is not None:
        b_frames: Sourced[bool] = Sourced.detected(timing.reordered)
    elif (declared := _integer(stream.get("has_b_frames"))) is not None:
        b_frames = Sourced.declared(declared > 0)
    else:
        b_frames = Sourced.unknown()
    field_order = _stated(stream.get("field_order"))
    return VideoStream(
        index=int(stream.get("index", 0)),
        codec=codec,
        codec_description=_stated(stream.get("codec_long_name")),
        profile=_stated(stream.get("profile")),
        level=_level(codec, stream.get("level")),
        encoder=_tag(tags, "encoder"),
        geometry=_geometry(stream, tags),
        pixel_format=pix_fmt,
        bit_depth=_bit_depth(stream, pix_fmt),
        chroma_subsampling=_subsampling(pix_fmt),
        has_alpha=bool(pix_fmt and _ALPHA.match(pix_fmt)) or _tag(tags, "alpha_mode") == "1",
        scan_type=_scan(field_order),
        field_order=field_order,
        color=_color(stream, frame_side_data),
        frame_rate=_rate(stream.get("r_frame_rate")),
        average_frame_rate=_rate(stream.get("avg_frame_rate")),
        time_base=time_base,
        start_time=_number(stream.get("start_time")),
        duration=_duration(stream, tags),
        declared_frame_count=_positive(stream.get("nb_frames"))
        or _positive(_tag(tags, "number_of_frames")),
        bit_rate=_bit_rate(stream, timing),
        has_b_frames=b_frames,
        language=_stated(tags.get("language")),
        title=_stated(tags.get("title")),
        timing=timing,
        tags=tags,
    )


# ------------------------------------------------------------------------------------------
# audio
# ------------------------------------------------------------------------------------------
def _audio_bit_depth(stream: Info, codec: str) -> Sourced[int]:
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        declared = _positive(stream.get(key))
        if declared is not None:
            return Sourced.declared(declared)
    if (match := _PCM_DEPTH.fullmatch(codec.removesuffix("le").removesuffix("be"))) is not None:
        return Sourced.inferred(int(match.group(1)))
    return Sourced.unknown()  # lossy codecs have no sample depth to report


def _channel_class(channels: int | None) -> ChannelClass:
    if channels is None:
        return ChannelClass.UNKNOWN
    if channels == 1:
        return ChannelClass.MONO
    return ChannelClass.STEREO if channels == 2 else ChannelClass.MULTICHANNEL


def _audio(
    stream: Info,
    packets: Sequence[Packet] | None,
    config: InspectionConfig,
) -> AudioStream:
    tags = _tags(stream)
    codec = str(stream.get("codec_name") or "unknown")
    channels = _positive(stream.get("channels"))
    layout = _stated(stream.get("channel_layout"))
    time_base = Rational.parse(stream.get("time_base"))
    timing = analyze_audio_packets(packets, _tick(time_base), config) if packets else None
    disposition = stream.get("disposition") or {}
    return AudioStream(
        index=int(stream.get("index", 0)),
        codec=codec,
        codec_description=_stated(stream.get("codec_long_name")),
        profile=_stated(stream.get("profile")),
        sample_format=_stated(stream.get("sample_fmt")),
        sample_rate=_positive(stream.get("sample_rate")),
        bit_depth=_audio_bit_depth(stream, codec),
        channels=channels,
        channel_layout=(
            Sourced.declared(layout)
            if layout and not _LAYOUT_COUNT.fullmatch(layout)
            else Sourced.unknown()
        ),
        channel_class=_channel_class(channels),
        time_base=time_base,
        start_time=_number(stream.get("start_time")),
        duration=_duration(stream, tags),
        bit_rate=_bit_rate(stream, timing),
        language=_stated(tags.get("language")),
        title=_stated(tags.get("title")),
        is_default=bool(disposition.get("default")),
        timing=timing,
        tags=tags,
    )


# ------------------------------------------------------------------------------------------
# container, timecode, whole file
# ------------------------------------------------------------------------------------------
def _container(info: Info, stream_count: int) -> ContainerInfo:
    fmt = info.get("format") or {}
    names = tuple(sorted(str(fmt.get("format_name", "")).split(",")))
    return ContainerInfo(
        format_names=tuple(n for n in names if n),
        format_description=_stated(fmt.get("format_long_name")),
        duration=_number(fmt.get("duration")),
        start_time=_number(fmt.get("start_time")),
        bit_rate=_positive(fmt.get("bit_rate")),
        size_bytes=_positive(fmt.get("size")),
        stream_count=stream_count,
        probe_score=_integer(fmt.get("probe_score")),
        tags=_tags(fmt),
    )


def _timecode(
    container_tags: Mapping[str, str],
    streams: Sequence[Info],
    video: VideoStream | None,
) -> TimecodeInfo | None:
    candidates: list[tuple[str, str | None]] = []
    if video is not None:
        candidates.append(("video_stream", _tag(video.tags, "timecode")))
    candidates.append(("container", _tag(container_tags, "timecode")))
    candidates.extend(
        ("timecode_track", _tag(_tags(s), "timecode"))
        for s in streams
        if s.get("codec_tag_string") == "tmcd"
    )
    for source, text in candidates:
        timecode = Timecode.parse(text)
        if timecode is not None:
            rate = video.frame_rate.value if video else None
            return TimecodeInfo(timecode, source, timecode.seconds_at(rate) if rate else None)
    return None


def normalize(
    info: Info,
    packets: PacketsByStream,
    integrity: Integrity,
    config: InspectionConfig,
    frame_side_data: Mapping[int, Sequence[Info]] | None = None,
) -> ObservedMedia:
    """Everything ffprobe knows about the file, in the domain's vocabulary.

    ``frame_side_data`` is what the first frame of a video stream carries (HDR metadata is often
    only there, not in the stream header).
    """
    frame_side_data = frame_side_data or {}
    streams: list[Info] = [s for s in info.get("streams") or () if isinstance(s, dict)]
    videos: list[VideoStream] = []
    audios: list[AudioStream] = []
    others: list[OtherStream] = []
    for stream in streams:
        index = int(stream.get("index", -1))
        kind = str(stream.get("codec_type", "unknown"))
        attached = (stream.get("disposition") or {}).get("attached_pic")
        if kind == "video" and not attached:
            videos.append(
                _video(stream, packets.get(index), frame_side_data.get(index, ()), config)
            )
        elif kind == "audio":
            audios.append(_audio(stream, packets.get(index), config))
        else:
            tags = _tags(stream)
            others.append(
                OtherStream(
                    index=index,
                    kind="attached_picture" if attached else kind,
                    codec=_stated(stream.get("codec_name"))
                    or _stated(stream.get("codec_tag_string")),
                    language=_stated(tags.get("language")),
                    title=_stated(tags.get("title")),
                    tags=tags,
                )
            )
    container = _container(info, len(streams))
    return ObservedMedia(
        container=container,
        video_streams=tuple(videos),
        audio_streams=tuple(audios),
        other_streams=tuple(others),
        timecode=_timecode(container.tags, streams, videos[0] if videos else None),
        production=production_from_tags(container.tags, [v.tags for v in videos[:1]]),
        integrity=integrity,
        raw_metadata=_raw(info, frame_side_data),
    )


def unreadable(reason: str) -> ObservedMedia:
    """What a file that could not be opened looks like: nothing is known except why."""
    return ObservedMedia(
        container=ContainerInfo(),
        video_streams=(),
        audio_streams=(),
        other_streams=(),
        timecode=None,
        production=production_from_tags({}),
        integrity=Integrity(readable=False, decode_checked=False, read_error=reason),
        raw_metadata={},
    )


def _raw(info: Info, frame_side_data: Mapping[int, Sequence[Info]]) -> dict[str, JsonValue]:
    """The tool's own report, untouched (its sections plus the first-frame side data read)."""
    return {
        "format": _json(info.get("format") or {}),
        "streams": _json(list(info.get("streams") or [])),
        "first_frame_side_data": {str(i): _json(list(d)) for i, d in frame_side_data.items()},
    }


def _json(value: object) -> Any:
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
