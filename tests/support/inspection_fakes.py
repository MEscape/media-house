"""Builders for inspection domain objects: sensible defaults, override what a test is about."""

from dataclasses import replace
from datetime import UTC, datetime

from media_house.modules.media_inspection.domain.assessment import assess
from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.model import (
    AudioStream,
    ColorInfo,
    ContainerInfo,
    Geometry,
    Integrity,
    MediaInspection,
    ObservedMedia,
    ProductionMetadata,
    VideoStream,
)
from media_house.modules.media_inspection.domain.timing import Packet, StreamTiming
from media_house.modules.media_inspection.domain.values import (
    ChannelClass,
    FrameRateMode,
    Rational,
    ScanType,
    Sourced,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
CONFIG = InspectionConfig()


def timing(**changes: object) -> StreamTiming:
    """A clean 30 fps stream of 3 s (90 frames)."""
    base = StreamTiming(
        packet_count=90,
        first_pts=0.0,
        end_pts=3.0,
        missing_timestamps=0,
        duplicate_timestamps=0,
        discontinuity_count=0,
        discontinuity_positions=(),
        measured_bit_rate=4_000_000,
        mode=FrameRateMode.CONSTANT,
        median_interval=1 / 30,
        min_interval=1 / 30,
        max_interval=1 / 30,
        keyframe_count=3,
        max_keyframe_interval=30,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def video(**changes: object) -> VideoStream:
    """A well-formed 1080p 30 fps BT.709 H.264 stream."""
    base = VideoStream(
        index=0,
        codec="h264",
        codec_description="H.264",
        profile="High",
        level="4.0",
        encoder=None,
        geometry=Geometry(
            1920,
            1080,
            Sourced.declared(Rational(1, 1)),
            Sourced.declared(Rational(16, 9)),
            Sourced.inferred(0),
        ),
        pixel_format="yuv420p",
        bit_depth=Sourced.inferred(8),
        chroma_subsampling=Sourced.inferred("420"),
        has_alpha=False,
        scan_type=Sourced.declared(ScanType.PROGRESSIVE),
        field_order="progressive",
        color=ColorInfo(
            space=Sourced.declared("bt709"),
            primaries=Sourced.declared("bt709"),
            transfer=Sourced.declared("bt709"),
            range=Sourced.declared("tv"),
            dynamic_range=Sourced.inferred("sdr"),
        ),
        frame_rate=Sourced.declared(Rational(30, 1)),
        average_frame_rate=Sourced.declared(Rational(30, 1)),
        time_base=Rational(1, 15360),
        start_time=0.0,
        duration=3.0,
        declared_frame_count=90,
        bit_rate=Sourced.declared(8_000_000),
        has_b_frames=Sourced.detected(True),
        timing=timing(),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def audio(**changes: object) -> AudioStream:
    """A well-formed 48 kHz stereo AAC stream."""
    base = AudioStream(
        index=1,
        codec="aac",
        codec_description="AAC",
        profile="LC",
        sample_format="fltp",
        sample_rate=48_000,
        bit_depth=Sourced.unknown(),
        channels=2,
        channel_layout=Sourced.declared("stereo"),
        channel_class=ChannelClass.STEREO,
        time_base=Rational(1, 48_000),
        start_time=0.0,
        duration=3.0,
        bit_rate=Sourced.declared(128_000),
        timing=StreamTiming(
            packet_count=141,
            first_pts=-0.021,
            end_pts=3.0,
            missing_timestamps=0,
            duplicate_timestamps=0,
            discontinuity_count=0,
            discontinuity_positions=(),
            measured_bit_rate=128_000,
        ),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def observed(
    videos: tuple[VideoStream, ...] = (),
    audios: tuple[AudioStream, ...] = (),
    **changes: object,
) -> ObservedMedia:
    base = ObservedMedia(
        container=ContainerInfo(
            format_names=("mp4",), duration=3.0, start_time=0.0, stream_count=len(videos + audios)
        ),
        video_streams=videos,
        audio_streams=audios,
        other_streams=(),
        timecode=None,
        production=ProductionMetadata(creation_time=NOW),
        integrity=Integrity(readable=True, decode_checked=True),
        raw_metadata={},
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def inspect(media: ObservedMedia, config: InspectionConfig = CONFIG) -> MediaInspection:
    return assess("asset-1", media, config, now=NOW)


def codes(inspection: MediaInspection) -> set[str]:
    return {f.code for f in inspection.findings}


def packets(
    times: list[float | None], duration: float = 1 / 30, keyframe_every: int = 30
) -> list[Packet]:
    """Packets at ``times`` (None = no timestamp), in the order given (decode order)."""
    return [
        Packet(t, t, duration, 1000, keyframe=i % keyframe_every == 0) for i, t in enumerate(times)
    ]
