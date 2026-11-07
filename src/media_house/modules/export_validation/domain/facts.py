"""What the validation knows about the delivered file and about what it should be.

These are the validator's own facts. The application fills them from the inspection of the file,
the analyzer's measurements, the approved timeline and the render report; the rules below never see
another module's types. A measurement that was not made is ``None`` (never zero, never "fine").
"""

from dataclasses import dataclass, field

from media_house.core.domain import Rational


@dataclass(frozen=True, slots=True)
class Span:
    """A stretch of the file in seconds; ``end`` is ``None`` when it runs to the end."""

    start: float
    end: float | None


@dataclass(frozen=True, slots=True)
class VideoFacts:
    codec: str
    width: int
    height: int
    frame_rate: Rational | None
    average_frame_rate: Rational | None
    pixel_format: str | None
    display_aspect: Rational | None
    duration: float | None
    declared_frames: int | None
    dropped_frames: int | None
    duplicate_timestamps: int | None
    discontinuities: int | None
    variable_rate: bool | None
    color_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AudioFacts:
    codec: str
    sample_rate: int | None
    channels: int | None
    duration: float | None
    #: Start offset against the picture (seconds), from the inspection's synchronisation.
    start_offset: float | None
    duration_difference: float | None


@dataclass(frozen=True, slots=True)
class LoudnessFacts:
    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbtp: float | None
    sample_peak_dbfs: float | None
    has_audio: bool


@dataclass(frozen=True, slots=True)
class DecodeFacts:
    complete: bool
    frames_decoded: int | None
    decoded_until: float | None
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FrameSample:
    """One picture of the delivered file, compared with the same moment of the approved review
    render. ``similarity`` is ``None`` when there is nothing to compare with."""

    label: str
    #: ``opening`` | ``ending`` | ``section``
    kind: str
    at_seconds: float
    section_id: str
    similarity: float | None
    black_fraction: float


@dataclass(frozen=True, slots=True)
class FileFacts:
    exists: bool
    readable: bool
    size_bytes: int
    extension: str
    file_name: str
    duration: float | None
    inspection_error: bool
    video: VideoFacts | None
    audio: AudioFacts | None
    tags: tuple[tuple[str, str], ...] = ()
    #: ``None`` = the measurement was not made (not "nothing found").
    decode: DecodeFacts | None = None
    black: tuple[Span, ...] | None = None
    frozen: tuple[Span, ...] | None = None
    silent: tuple[Span, ...] | None = None
    loudness: LoudnessFacts | None = None
    frames: tuple[FrameSample, ...] | None = None
    #: A reference render of the approved timeline was available to compare pictures with.
    reference_used: bool = False
    #: Which sampled moments could not be decoded.
    unreadable_frames: int = 0
    #: The file's streams could be inspected (``False``: only the measurements are known).
    inspected: bool = True


@dataclass(frozen=True, slots=True)
class Expected:
    """What the approved timeline and the delivery's render profile say the file must be."""

    width: int
    height: int
    frame_rate: Rational
    duration_seconds: float
    has_audio: bool
    container: str
    video_codec: str
    pixel_format: str
    audio_codec: str
    sample_rate: int
    channels: int
    aspect: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class Lineage:
    """Which render made the file, and which timeline version it is of, against the approved one."""

    approved_timeline_id: str
    approved_version: int
    approved_hash: str
    approved_session_id: str
    #: ``False``: no render report is known for this file (it came from somewhere else).
    render_found: bool
    render_id: str = ""
    render_timeline_id: str = ""
    render_version: int = 0
    render_hash: str = ""
    render_session_id: str = ""
    render_succeeded: bool = True
    render_config_hash: str = ""
    #: The output configuration the export was authorized for (empty: not given).
    authorized_config_hash: str = ""


@dataclass(frozen=True, slots=True)
class Intentional:
    """A stretch the plan declared on purpose (a fade through black, a held title, a silence)."""

    kind: str  # black | silence | freeze
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class Measurements:
    """What was read from the file, kept in the report as the evidence of the verdict."""

    values: tuple[tuple[str, str], ...] = field(default_factory=tuple)
