"""Render profiles: versioned data that says what a render of a given kind produces.

Nothing about an output (codec, container, frame size, pixel format, colour, audio) is written
anywhere else in the application: a profile names it, and the report records which profile (and
which version of it) made a file. Visual and audio settings are separate. A setting left empty
(``None``) is taken from the timeline (size, rate) and is filled in by ``resolve_settings`` before
anything is hashed, so the ``output_config_hash`` an export is authorized for names the real output.
"""

import re
from dataclasses import dataclass

from media_house.core.domain import Rational, json_codec
from media_house.modules.render_manager.domain.errors import (
    InvalidRenderProfile,
    UnknownRenderProfile,
)
from media_house.modules.render_manager.domain.values import Purpose

_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_QUALITY = re.compile(r"^(auto|best|\d{2,6}k)$")


@dataclass(frozen=True, slots=True)
class VideoSettings:
    #: ``None``: the timeline's size / rate.
    width: int | None
    height: int | None
    frame_rate: Rational | None
    #: The ffprobe name of the codec the file must carry (``h264``, ``prores``).
    codec: str
    #: The file extension without the dot (``mp4``, ``mov``).
    container: str
    pixel_format: str
    quality: str = "best"
    bitrate_kbps: int | None = None
    #: The colour space the picture is delivered in.
    color: str = "rec709"
    #: The aspect the profile is for, reduced; ``None`` = any.
    aspect: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class AudioSettings:
    codec: str
    sample_rate: int
    channels: int
    bitrate_kbps: int | None = None
    bit_depth: int | None = None


@dataclass(frozen=True, slots=True)
class RenderProfile:
    profile_id: str
    version: int
    name: str
    purpose: Purpose
    video: VideoSettings
    audio: AudioSettings
    #: Whether the file must carry sound (a timeline with sound always does).
    audio_required: bool = True
    #: Whether pool items that are proxies may be rendered from (previews only).
    allow_proxy_sources: bool = False
    #: The output name, from the timeline id, version, kind, profile and a sequence number.
    name_pattern: str = "{timeline}_v{version}_{type}_{profile}_{n:03d}"
    metadata: tuple[tuple[str, str], ...] = ()
    #: The editing application's own profile that realises this one ("" = it cannot).
    resolve_profile: str = ""

    def __post_init__(self) -> None:
        if not _ID.match(self.profile_id):
            raise InvalidRenderProfile(f"'{self.profile_id}' is not a profile id (a-z, 0-9, _)")
        if self.version < 1:
            raise InvalidRenderProfile("the version starts at 1")
        v, a = self.video, self.audio
        if (v.width is None) != (v.height is None):
            raise InvalidRenderProfile("width and height are given together or not at all")
        if (
            v.width is not None
            and v.height is not None
            and (v.width < 16 or v.height < 16 or v.width % 2 or v.height % 2)
        ):
            raise InvalidRenderProfile("width and height are even numbers of at least 16")
        if not v.codec or not v.container or not v.pixel_format:
            raise InvalidRenderProfile("codec, container and pixel format are required")
        if not _QUALITY.match(v.quality):
            raise InvalidRenderProfile(
                f"quality '{v.quality}' is not auto, best or a bitrate like 8000k"
            )
        if v.bitrate_kbps is not None and v.bitrate_kbps <= 0:
            raise InvalidRenderProfile("the bitrate must be positive")
        if v.aspect is not None and min(v.aspect) <= 0:
            raise InvalidRenderProfile("the aspect must be positive")
        if a.sample_rate not in (22050, 32000, 44100, 48000, 88200, 96000):
            raise InvalidRenderProfile(f"sample rate {a.sample_rate} is not a delivery rate")
        if a.channels not in (1, 2, 6):
            raise InvalidRenderProfile("audio is mono, stereo or 5.1")
        if "{timeline}" not in self.name_pattern or "{n" not in self.name_pattern:
            raise InvalidRenderProfile("the name pattern needs {timeline} and a sequence {n}")


_AAC = AudioSettings("aac", 48000, 2, bitrate_kbps=320)


def _aspect(w: int, h: int) -> VideoSettings:
    return VideoSettings(None, None, None, "h264", "mp4", "yuv420p", aspect=(w, h))


PROFILES: tuple[RenderProfile, ...] = (
    RenderProfile(
        "youtube_16_9", 1, "YouTube 16:9", Purpose.DELIVERY, _aspect(16, 9), _AAC,
        resolve_profile="youtube",
    ),
    RenderProfile(
        "shorts_9_16", 1, "YouTube Shorts 9:16", Purpose.DELIVERY, _aspect(9, 16), _AAC,
        resolve_profile="youtube",
    ),
    RenderProfile(
        "reels_9_16", 1, "Instagram Reels 9:16", Purpose.DELIVERY, _aspect(9, 16),
        AudioSettings("aac", 48000, 2, bitrate_kbps=256), resolve_profile="youtube",
    ),
    RenderProfile(
        "square_1_1", 1, "Square 1:1", Purpose.DELIVERY, _aspect(1, 1), _AAC,
        resolve_profile="youtube",
    ),
    RenderProfile(
        "review_fast", 1, "Review (fast)", Purpose.PREVIEW,
        VideoSettings(None, None, None, "h264", "mp4", "yuv420p", quality="auto"),
        AudioSettings("aac", 48000, 2, bitrate_kbps=128),
        allow_proxy_sources=True, resolve_profile="youtube",
    ),
    RenderProfile(
        "review_proxy", 1, "Review (small, proxies allowed)", Purpose.PREVIEW,
        VideoSettings(960, 540, None, "h264", "mp4", "yuv420p", quality="auto", aspect=(16, 9)),
        AudioSettings("aac", 48000, 2, bitrate_kbps=96),
        allow_proxy_sources=True, resolve_profile="youtube",
    ),
    RenderProfile(
        "intermediate", 1, "Intermediate (ProRes 422)", Purpose.INTERMEDIATE,
        VideoSettings(None, None, None, "prores", "mov", "yuv422p10le"),
        AudioSettings("pcm_s24le", 48000, 2, bit_depth=24), resolve_profile="master",
    ),
    RenderProfile(
        "master_hq", 1, "Master (ProRes 422 HQ)", Purpose.MASTER,
        VideoSettings(None, None, None, "prores", "mov", "yuv422p10le"),
        AudioSettings("pcm_s24le", 48000, 2, bit_depth=24), resolve_profile="master",
    ),
)  # fmt: skip


def get_profile(profile_id: str, extra: tuple[RenderProfile, ...] = ()) -> RenderProfile:
    found = next((p for p in (*PROFILES, *extra) if p.profile_id == profile_id), None)
    if found is None:
        raise UnknownRenderProfile(profile_id, [p.profile_id for p in (*PROFILES, *extra)])
    return found


@dataclass(frozen=True, slots=True)
class ResolvedSettings:
    """A profile with the timeline's size and rate filled in: exactly what the output is."""

    profile_id: str
    profile_version: int
    width: int
    height: int
    frame_rate: Rational
    video: VideoSettings
    audio: AudioSettings
    audio_required: bool
    metadata: tuple[tuple[str, str], ...]


def resolve_settings(
    profile: RenderProfile, *, width: int, height: int, frame_rate: Rational
) -> ResolvedSettings:
    v = profile.video
    w = v.width if v.width is not None else width
    h = v.height if v.height is not None else height
    rate = v.frame_rate if v.frame_rate is not None else frame_rate
    return ResolvedSettings(
        profile_id=profile.profile_id,
        profile_version=profile.version,
        width=w,
        height=h,
        frame_rate=rate,
        video=VideoSettings(
            w,
            h,
            rate,
            v.codec,
            v.container,
            v.pixel_format,
            v.quality,
            v.bitrate_kbps,
            v.color,
            v.aspect,
        ),
        audio=profile.audio,
        audio_required=profile.audio_required,
        metadata=profile.metadata,
    )


def output_config_hash(settings: ResolvedSettings) -> str:
    """The identity of an output: what an export authorization is given for and bound to."""
    return json_codec.digest(settings)


def profile_problems(profile: RenderProfile, *, width: int, height: int) -> tuple[str, ...]:
    """Why this profile does not fit this timeline (e.g. a 9:16 profile for a 16:9 timeline)."""
    found: list[str] = []
    resolved_w = profile.video.width or width
    resolved_h = profile.video.height or height
    aspect = profile.video.aspect
    if aspect is not None and resolved_w * aspect[1] != resolved_h * aspect[0]:
        found.append(
            f"profile {profile.profile_id} is {aspect[0]}:{aspect[1]} but the output would be "
            f"{resolved_w}x{resolved_h}"
        )
    return tuple(found)
