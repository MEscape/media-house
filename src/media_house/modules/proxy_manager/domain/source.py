"""What the proxy needs to know about the source, independent of where the facts came from."""

from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.proxy_manager.domain.errors import UnsupportedSource

ORIGIN_INSPECTION = "inspection"
ORIGIN_PROBE = "probe"


@dataclass(frozen=True, slots=True)
class VideoFacts:
    index: int
    width: int
    height: int
    #: Pixel shape: display width is ``width * sample_aspect_ratio``.
    sample_aspect_ratio: Fraction
    #: Clockwise degrees a player turns the picture: 0, 90, 180 or 270.
    rotation: int
    #: Frames a player shows (packets from time zero on; hidden pre-roll excluded).
    visible_frames: int
    #: Presentation time of the first visible frame and the end of the last one, in seconds.
    first_pts: float
    end_pts: float
    #: Typical spacing of frames in seconds (the tolerance unit for this stream).
    frame_interval: float
    variable_frame_rate: bool


@dataclass(frozen=True, slots=True)
class AudioFacts:
    index: int
    codec: str
    channels: int
    sample_rate: int
    first_pts: float
    end_pts: float
    language: str | None = None


@dataclass(frozen=True, slots=True)
class SourceFacts:
    video: VideoFacts | None
    audio: tuple[AudioFacts, ...]
    timecode: str | None
    #: ``inspection`` when taken from a stored inspection, ``probe`` when read from the file.
    origin: str

    def require_video(self) -> VideoFacts:
        if self.video is None:
            raise UnsupportedSource("the media has no video stream (audio-only media)")
        return self.video
