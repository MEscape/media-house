"""Proxy profiles: the stable, versioned REQUEST (``ResolvedSpec`` is what gets built)."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from media_house.modules.proxy_manager.domain.errors import InvalidProfile
from media_house.modules.proxy_manager.domain.values import AudioPolicy, Codec, JsonValue

_MIN_LONG_EDGE = 16
_MAX_LONG_EDGE = 8192
_CONTAINERS = {Codec.H264: "mp4", Codec.PRORES: "mov"}


@dataclass(frozen=True, slots=True)
class ProxyProfile:
    """Workload-shaped output: how small, which codec, which audio. Timing stays the source's."""

    name: str
    #: Bump when the same name should produce a different result (part of the spec fingerprint).
    version: int
    codec: Codec
    #: Longest side of the picture as displayed; sources already smaller are never upscaled.
    max_long_edge: int
    audio: AudioPolicy
    #: H.264 only: constant-quality target and the rate ceiling that bounds file size.
    crf: int = 28
    video_maxrate_kbps: int = 1500
    audio_bitrate_kbps: int = 96
    #: Decode the finished file to the end. Costly, so off until measured (see README).
    full_decode_validation: bool = False

    def __post_init__(self) -> None:
        if not self.name or self.version < 1:
            raise InvalidProfile("a profile needs a name and a version of at least 1")
        if not _MIN_LONG_EDGE <= self.max_long_edge <= _MAX_LONG_EDGE:
            raise InvalidProfile(
                f"max_long_edge must be {_MIN_LONG_EDGE}-{_MAX_LONG_EDGE}, not {self.max_long_edge}"
            )
        if self.codec is Codec.H264 and not (
            0 <= self.crf <= 51 and self.video_maxrate_kbps >= 100 and self.audio_bitrate_kbps >= 16
        ):
            raise InvalidProfile("H.264 needs crf 0-51, maxrate >= 100 kbps and audio >= 16 kbps")

    @property
    def container(self) -> str:
        return _CONTAINERS[self.codec]


_BUILT_IN = (
    ProxyProfile("analysis", 1, Codec.H264, 640, AudioPolicy.FIRST),
    ProxyProfile("edit", 1, Codec.PRORES, 960, AudioPolicy.ALL),
)
PROFILES: Mapping[str, ProxyProfile] = MappingProxyType({p.name: p for p in _BUILT_IN})
DEFAULT_PROFILE = "edit"
_INTEGER_FIELDS = frozenset({"max_long_edge", "crf", "video_maxrate_kbps", "audio_bitrate_kbps"})
#: Fields an override may change. ``name``, ``version`` and ``codec`` define the profile itself.
_OVERRIDABLE = _INTEGER_FIELDS | {"audio", "full_decode_validation"}


def resolve_profile(name: str, overrides: Mapping[str, JsonValue] | None = None) -> ProxyProfile:
    """The named profile with ``overrides`` applied; any unknown or mistyped value is refused."""
    base = PROFILES.get(name)
    if base is None:
        raise InvalidProfile(f"unknown profile '{name}' (known: {', '.join(sorted(PROFILES))})")
    changes: dict[str, object] = {}
    for key, value in (overrides or {}).items():
        if key not in _OVERRIDABLE:
            raise InvalidProfile(f"'{key}' cannot be overridden")
        if key == "audio":
            try:
                changes[key] = AudioPolicy(str(value))
            except ValueError as exc:
                raise InvalidProfile(
                    f"audio must be one of {[p.value for p in AudioPolicy]}"
                ) from exc
        elif key == "full_decode_validation":
            if not isinstance(value, bool):
                raise InvalidProfile(f"'{key}' must be true or false")
            changes[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidProfile(f"'{key}' must be a whole number")
            changes[key] = value
    return replace(base, **changes) if changes else base  # type: ignore[arg-type]
