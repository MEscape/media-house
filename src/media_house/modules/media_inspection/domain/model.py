"""The inspection result: typed, versioned facts about a media file.

Three layers stay distinct (and are all stored):

* ``raw_metadata``: what the probing tool reported, untouched, for debugging and unsupported fields.
* The normalised facts below: stable names and exact units that other modules consume.
* ``findings`` / ``status``: conclusions DERIVED from the facts by ``rules.py``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from media_house.modules.media_inspection.domain.timecode import Timecode
from media_house.modules.media_inspection.domain.timing import StreamTiming
from media_house.modules.media_inspection.domain.values import (
    INSPECTION_VERSION,
    SCHEMA_VERSION,
    Category,
    Certainty,
    ChannelClass,
    Concern,
    Depth,
    JsonValue,
    Rational,
    ScanType,
    Severity,
    Sourced,
    Verdict,
)


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    format_names: tuple[str, ...] = ()
    format_description: str | None = None
    duration: float | None = None
    #: Seconds from the media's time origin to where the container says playback starts.
    start_time: float | None = None
    bit_rate: int | None = None
    size_bytes: int | None = None
    stream_count: int = 0
    #: ffprobe's confidence (0-100) that it identified the format correctly.
    probe_score: int | None = None
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Geometry:
    """Storage size, pixel shape, display shape and rotation: four different things."""

    #: Coded (stored) dimensions, before any aspect-ratio or rotation is applied.
    width: int
    height: int
    sample_aspect_ratio: Sourced[Rational]
    display_aspect_ratio: Sourced[Rational]
    #: Clockwise degrees a player applies on display: 0, 90, 180 or 270.
    rotation: Sourced[int]

    @property
    def square_pixels(self) -> bool:
        sar = self.sample_aspect_ratio.value
        return sar is None or sar == Rational(1, 1)

    @property
    def display_size(self) -> tuple[int, int]:
        """Width and height as shown: pixel shape applied, then rotated."""
        sar = self.sample_aspect_ratio.value or Rational(1, 1)
        width, height = round(self.width * sar.value), self.height
        turned = (self.rotation.value or 0) % 180 == 90
        return (height, width) if turned else (width, height)


@dataclass(frozen=True, slots=True)
class ColorInfo:
    """Colour description as declared. Missing declarations stay ``unknown``, never assumed."""

    space: Sourced[str]  # matrix coefficients, e.g. bt709, bt2020nc
    primaries: Sourced[str]
    transfer: Sourced[str]
    range: Sourced[str]  # tv (limited) or pc (full)
    #: ``hdr10``, ``hlg`` or ``sdr``; INFERRED from the transfer characteristic.
    dynamic_range: Sourced[str]
    mastering_display: bool = False
    content_light_level: bool = False
    dolby_vision: bool = False


@dataclass(frozen=True, slots=True)
class VideoStream:
    index: int
    codec: str
    codec_description: str | None
    profile: str | None
    level: str | None
    encoder: str | None
    geometry: Geometry
    pixel_format: str | None
    bit_depth: Sourced[int]
    chroma_subsampling: Sourced[str]
    has_alpha: bool
    scan_type: Sourced[ScanType]
    field_order: str | None
    color: ColorInfo
    #: The rate the container nominally uses (``r_frame_rate``) and its average (exact fractions).
    frame_rate: Sourced[Rational]
    average_frame_rate: Sourced[Rational]
    time_base: Rational | None
    start_time: float | None
    duration: float | None
    declared_frame_count: int | None
    bit_rate: Sourced[int]
    has_b_frames: Sourced[bool]
    language: str | None = None
    title: str | None = None
    timing: StreamTiming | None = None
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AudioStream:
    index: int
    codec: str
    codec_description: str | None
    profile: str | None
    sample_format: str | None
    sample_rate: int | None
    bit_depth: Sourced[int]
    channels: int | None
    channel_layout: Sourced[str]
    channel_class: ChannelClass
    time_base: Rational | None
    start_time: float | None
    duration: float | None
    bit_rate: Sourced[int]
    language: str | None = None
    title: str | None = None
    is_default: bool = False
    timing: StreamTiming | None = None
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OtherStream:
    """Subtitle, data, attachment and timecode tracks: recorded so the stream map is complete."""

    index: int
    kind: str
    codec: str | None
    language: str | None = None
    title: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TimecodeInfo:
    start: Timecode
    #: Where it was read: ``video_stream``, ``container`` or ``timecode_track``.
    source: str
    #: Position of the timecode on the recording's clock, if the video frame rate is known.
    start_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class StreamOffset:
    """One audio stream's timing relative to the reference video stream."""

    audio_index: int
    start_offset: float | None
    end_offset: float | None
    duration_difference: float | None

    @property
    def drift(self) -> float | None:
        """Change of the offset over the whole stream (seconds); ``None`` if not measurable."""
        if self.start_offset is None or self.end_offset is None:
            return None
        return self.end_offset - self.start_offset


@dataclass(frozen=True, slots=True)
class Synchronization:
    """Measured placement of the streams on the common clock."""

    reference_video_index: int | None
    video_start: float | None
    offsets: tuple[StreamOffset, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductionMetadata:
    """Camera and clip information carried by the file. Absent fields stay ``None``."""

    creation_time: datetime | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    lens: str | None = None
    focal_length: str | None = None
    aperture: str | None = None
    shutter_speed: str | None = None
    iso: str | None = None
    white_balance: str | None = None
    exposure: str | None = None
    reel_id: str | None = None
    clip_id: str | None = None
    scene: str | None = None
    take: str | None = None
    device: str | None = None
    software: str | None = None
    #: Every container tag as found, so nothing the file said is lost.
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DecodeMessage:
    """One distinct complaint of a tool, and how often it occurred."""

    text: str
    count: int = 1
    #: ``read`` (container and packet scan) or ``decode`` (decoding the streams).
    stage: str = "decode"


@dataclass(frozen=True, slots=True)
class Integrity:
    #: The container could be opened and its streams listed.
    readable: bool
    #: Whether the streams were actually decoded (``Depth.FULL``).
    decode_checked: bool
    #: The distinct messages kept, from both stages.
    decode_messages: tuple[DecodeMessage, ...] = ()
    #: Total decoder messages, which may exceed the number of distinct messages kept.
    decode_message_count: int = 0
    #: Total complaints while reading the container and its packets.
    read_message_count: int = 0
    #: Why the container could not be read (the tool's own words), when ``readable`` is false.
    read_error: str | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    """A technical observation or problem, kept apart from the facts it is based on."""

    code: str
    severity: Severity
    category: Category
    message: str
    certainty: Certainty
    #: Measured numbers the finding rests on.
    evidence: Mapping[str, JsonValue] = field(default_factory=dict)
    stream_index: int | None = None
    concern: Concern | None = None


@dataclass(frozen=True, slots=True)
class InspectionStatus:
    """Can this asset be used, and what should the consumer know first?"""

    verdict: Verdict
    usable: bool
    concerns: tuple[Concern, ...] = ()
    warning_count: int = 0
    error_count: int = 0


@dataclass(frozen=True, slots=True)
class ObservedMedia:
    """Everything read from the file, before any conclusion is drawn."""

    container: ContainerInfo
    video_streams: tuple[VideoStream, ...]
    audio_streams: tuple[AudioStream, ...]
    other_streams: tuple[OtherStream, ...]
    timecode: TimecodeInfo | None
    production: ProductionMetadata
    integrity: Integrity
    raw_metadata: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class MediaInspection:
    """The technical ground truth of one asset. Immutable, versioned, self-describing."""

    asset_id: str
    created_at: datetime
    depth: Depth
    observed: ObservedMedia
    synchronization: Synchronization
    findings: tuple[Finding, ...]
    status: InspectionStatus
    inspection_version: int = INSPECTION_VERSION
    schema_version: int = SCHEMA_VERSION

    # --- convenient views ------------------------------------------------------------------
    @property
    def container(self) -> ContainerInfo:
        return self.observed.container

    @property
    def video_streams(self) -> tuple[VideoStream, ...]:
        return self.observed.video_streams

    @property
    def audio_streams(self) -> tuple[AudioStream, ...]:
        return self.observed.audio_streams

    @property
    def primary_video(self) -> VideoStream | None:
        return self.observed.video_streams[0] if self.observed.video_streams else None

    def audio_stream(self, position: int = 0) -> AudioStream | None:
        """The ``position``-th audio stream (the numbering ``-map 0:a:N`` uses), if present."""
        streams = self.observed.audio_streams
        return streams[position] if 0 <= position < len(streams) else None

    def findings_with(self, code: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.code == code)
