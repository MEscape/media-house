"""ResolvedSpec: every output-affecting value for ONE source, derived deterministically.

Its canonical JSON is the library derivation config, so the library's processing fingerprint IS
the generation-specification fingerprint. Machine details (encoder build, FFmpeg version) are NOT
in it: they are recorded as provenance and cannot change what the output promises.
"""

import math
from dataclasses import dataclass

from media_house.modules.proxy_manager.domain.profile import ProxyProfile
from media_house.modules.proxy_manager.domain.source import SourceFacts
from media_house.modules.proxy_manager.domain.values import (
    ADAPTER_REVISION,
    SPEC_VERSION,
    AudioPolicy,
    Codec,
    JsonValue,
)

#: The source does not start at zero; the proxy does (default FFmpeg behaviour, measured).
START_REBASED = "start_time_rebased"
#: A stream starting later than this many frames after zero counts as an offset.
_START_EPSILON_FRAMES = 1.0
#: H.264 headroom over the rate ceiling; the measured worst case (white noise) was 1.13x.
_H264_HEADROOM = 1.5
#: ProRes Proxy worst case measured 250.5 bits per macroblock (white noise), plus 50%.
_PRORES_BITS_PER_MB = 375
_OVERHEAD_BYTES = 4 * 1024 * 1024
_PCM_BYTES_PER_SAMPLE = 2


@dataclass(frozen=True, slots=True)
class AudioSpec:
    source_index: int
    codec: str
    bitrate_kbps: int | None
    sample_rate: int
    channels: int


@dataclass(frozen=True, slots=True)
class ResolvedSpec:
    profile_name: str
    profile_version: int
    codec: Codec
    container: str
    width: int
    height: int
    pixel_format: str
    crf: int | None
    maxrate_kbps: int | None
    video_source_index: int
    #: Display rotation is preserved as metadata, never baked into the pixels.
    rotation: int
    audio: tuple[AudioSpec, ...]
    #: Deliberate departures from the source timeline this profile accepts for THIS source.
    transformations: tuple[str, ...]

    def to_config(self) -> dict[str, JsonValue]:
        """Canonical, machine-independent description (the library derivation config)."""
        return {
            "spec_version": SPEC_VERSION,
            "adapter_revision": ADAPTER_REVISION,
            "profile": self.profile_name,
            "profile_version": self.profile_version,
            "codec": self.codec.value,
            "container": self.container,
            "width": self.width,
            "height": self.height,
            "pixel_format": self.pixel_format,
            "crf": self.crf,
            "maxrate_kbps": self.maxrate_kbps,
            "video_source_index": self.video_source_index,
            "rotation": self.rotation,
            "timing": "source",
            "audio": [
                {
                    "source_index": a.source_index,
                    "codec": a.codec,
                    "bitrate_kbps": a.bitrate_kbps,
                    "sample_rate": a.sample_rate,
                    "channels": a.channels,
                }
                for a in self.audio
            ],
            "transformations": list(self.transformations),
        }


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def resolve_spec(profile: ProxyProfile, facts: SourceFacts) -> ResolvedSpec:
    """The concrete output for this source. Anything it cannot reproduce faithfully is refused."""
    video = facts.require_video()
    display_width = video.width * float(video.sample_aspect_ratio)
    scale = min(1.0, profile.max_long_edge / max(display_width, video.height))
    width, height = _even(display_width * scale), _even(video.height * scale)

    chosen = facts.audio[:1] if profile.audio is AudioPolicy.FIRST else facts.audio
    h264 = profile.codec is Codec.H264
    audio = tuple(
        AudioSpec(
            a.index,
            "aac" if h264 else "pcm_s16le",
            profile.audio_bitrate_kbps if h264 else None,
            a.sample_rate,
            a.channels,
        )
        for a in chosen
    )
    limit = video.frame_interval * _START_EPSILON_FRAMES
    rebased = video.first_pts > limit or any(a.first_pts > limit for a in chosen)
    return ResolvedSpec(
        profile_name=profile.name,
        profile_version=profile.version,
        codec=profile.codec,
        container=profile.container,
        width=width,
        height=height,
        pixel_format="yuv420p" if h264 else "yuv422p10le",
        crf=profile.crf if h264 else None,
        maxrate_kbps=profile.video_maxrate_kbps if h264 else None,
        video_source_index=video.index,
        rotation=video.rotation,
        audio=audio,
        transformations=(START_REBASED,) if rebased else (),
    )


def reservation_bytes(spec: ResolvedSpec, facts: SourceFacts) -> int:
    """Conservative output size to reserve AND to cap the encode at (``-fs``).

    Not a proven bound: H.264 rate control and ProRes quantisation depend on content. It is the
    worst case measured (white noise) with headroom, and the encode is hard-capped to it.
    """
    video = facts.require_video()
    seconds = max(video.end_pts - video.first_pts, video.frame_interval)
    if spec.codec is Codec.H264:
        rate_kbps = (spec.maxrate_kbps or 0) + sum(a.bitrate_kbps or 0 for a in spec.audio)
        body = rate_kbps * 1000 / 8 * seconds * _H264_HEADROOM
    else:
        macroblocks = math.ceil(spec.width / 16) * math.ceil(spec.height / 16)
        body = macroblocks * _PRORES_BITS_PER_MB / 8 * video.visible_frames
        body += sum(
            a.sample_rate * a.channels * _PCM_BYTES_PER_SAMPLE * seconds for a in spec.audio
        )
    return int(body) + _OVERHEAD_BYTES
