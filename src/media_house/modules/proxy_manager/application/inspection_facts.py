"""Reuse of the facts Media Inspection already established, so the source is not probed twice.

Proxy generation works on any video, so it never ASSUMES an inspection exists. When a stored
inspection of the source has everything needed, the facts come from it; anything missing or
ambiguous (a clip cut without re-encoding has hidden pre-roll before time zero, so its first
visible frame time is unknown) returns ``None`` and the probe reads the file instead.
"""

from fractions import Fraction

from media_house.modules.media_inspection.application.contracts import (
    AudioStream,
    InspectionCatalog,
    StreamTiming,
    VideoStream,
)
from media_house.modules.proxy_manager.domain.source import (
    ORIGIN_INSPECTION,
    AudioFacts,
    SourceFacts,
    VideoFacts,
)
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_PRE_ROLL_SLACK = 1e-6
#: Audio encoder priming (two AAC frames at 44.1 kHz is 0.046 s) stays well below this.
_MAX_PRIMING_SECONDS = 0.1
_VFR_MODE = "variable"


def _fraction(value: object) -> Fraction | None:
    numerator, denominator = getattr(value, "numerator", None), getattr(value, "denominator", None)
    if isinstance(numerator, int) and isinstance(denominator, int) and numerator > 0:
        return Fraction(numerator, denominator)
    return None


def _timeline(timing: StreamTiming | None, *, priming: bool = False) -> tuple[float, float] | None:
    """(first pts, end pts) when they are exact visible times; ``None`` when unknown.

    Video before time zero is hidden pre-roll whose frame count is ambiguous, so it is refused.
    Audio may start slightly before zero: that is encoder priming, which decoders discard.
    """
    if timing is None or timing.first_pts is None or timing.end_pts is None:
        return None
    first = timing.first_pts
    if first < -_PRE_ROLL_SLACK:
        if not priming or first < -_MAX_PRIMING_SECONDS:
            return None
        first = 0.0
    return first, timing.end_pts


def _video(stream: VideoStream) -> VideoFacts | None:
    timing = stream.timing
    span = _timeline(timing)
    if timing is None or span is None or not timing.median_interval or timing.packet_count <= 0:
        return None
    return VideoFacts(
        index=stream.index,
        width=stream.geometry.width,
        height=stream.geometry.height,
        sample_aspect_ratio=_fraction(stream.geometry.sample_aspect_ratio.value) or Fraction(1),
        rotation=stream.geometry.rotation.value or 0,
        visible_frames=timing.packet_count,
        first_pts=span[0],
        end_pts=span[1],
        frame_interval=timing.median_interval,
        variable_frame_rate=timing.mode.value == _VFR_MODE,
    )


def _audio(stream: AudioStream) -> AudioFacts | None:
    span = _timeline(stream.timing, priming=True)
    if span is None or not stream.channels or not stream.sample_rate:
        return None
    return AudioFacts(
        index=stream.index,
        codec=stream.codec,
        channels=stream.channels,
        sample_rate=stream.sample_rate,
        first_pts=span[0],
        end_pts=span[1],
        language=stream.language,
    )


def _timecode(tags: list[dict[str, str]]) -> str | None:
    for source in tags:
        for key, value in source.items():
            if key.lower() == "timecode" and value:
                return value
    return None


def known_source_facts(catalog: InspectionCatalog | None, asset_id: str) -> SourceFacts | None:
    """The source's facts from a stored inspection, or ``None`` (probe instead)."""
    if catalog is None:
        return None
    found = catalog.find(asset_id)
    if found is None or not found.inspection.observed.integrity.readable:
        return None
    inspection = found.inspection
    primary = inspection.primary_video
    if primary is None:
        return None
    video = _video(primary)
    audio = [_audio(s) for s in inspection.audio_streams]
    if video is None or any(a is None for a in audio):
        return None
    _log.info("Source facts taken from the stored inspection", asset_id=asset_id)
    return SourceFacts(
        video=video,
        audio=tuple(a for a in audio if a is not None),
        timecode=_timecode([dict(inspection.container.tags), dict(primary.tags)]),
        origin=ORIGIN_INSPECTION,
    )
