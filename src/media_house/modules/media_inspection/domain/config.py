"""What an inspection does and when it calls something a finding.

Every field here changes the RESULT, so all of them are part of the cache fingerprint. Facts
(what the file says and what was measured) never depend on these thresholds; only the findings
derived from them do.
"""

import math
from dataclasses import dataclass, fields

from media_house.modules.media_inspection.domain.errors import InvalidInspectionConfig
from media_house.modules.media_inspection.domain.values import (
    INSPECTION_VERSION,
    Depth,
    JsonValue,
)

_FRACTION_MAX = 0.5


@dataclass(frozen=True, slots=True)
class InspectionConfig:
    depth: Depth = Depth.FULL
    #: A frame interval is regular when within this fraction (or 1.5 time-base ticks) of the median.
    interval_tolerance: float = 0.02
    #: Share of irregular intervals above which the frame rate counts as variable.
    variable_rate_fraction: float = 0.01
    #: A hole in the timestamps at least this long is a discontinuity, not a dropped frame.
    discontinuity_seconds: float = 1.0
    #: Audio/video start offsets and drift beyond this are reported (about one frame at 25 fps).
    sync_tolerance_seconds: float = 0.04
    #: Container, stream and measured durations may differ this much before it is reported.
    duration_tolerance_seconds: float = 0.5
    #: Below this many bits per pixel per frame the encoding is suspiciously starved.
    low_bits_per_pixel: float = 0.02
    #: Frame rate declared by the container and the measured one may differ by this fraction.
    frame_rate_tolerance: float = 0.01
    #: At most this many decoder messages are kept.
    decode_message_limit: int = 20

    def __post_init__(self) -> None:
        for name in (
            "interval_tolerance",
            "variable_rate_fraction",
            "frame_rate_tolerance",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 < value < _FRACTION_MAX:
                raise InvalidInspectionConfig(f"{name} must be between 0 and {_FRACTION_MAX}")
        for name in (
            "discontinuity_seconds",
            "sync_tolerance_seconds",
            "duration_tolerance_seconds",
            "low_bits_per_pixel",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise InvalidInspectionConfig(f"{name} must be positive")
        if self.decode_message_limit < 1:
            raise InvalidInspectionConfig("decode_message_limit must be at least 1")

    def fingerprint_config(self, probe_identity: str) -> dict[str, JsonValue]:
        """Everything that decides the stored result, for the library's cache fingerprint."""
        config: dict[str, JsonValue] = {
            f.name: (v.value if isinstance(v := getattr(self, f.name), Depth) else v)
            for f in fields(self)
        }
        config["probe"] = probe_identity
        config["inspection_version"] = INSPECTION_VERSION
        return config
