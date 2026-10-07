"""Reuse of technical facts that Media Inspection has already established.

Video Improvement works on any video, so it never ASSUMES an inspection exists. When a stored
inspection of the source is found, the facts below come from it and the source is not probed;
otherwise the minimal probe (``VideoProbe``) reads them. Camera and colour metadata arrive the
same way, so source-profile detection is identical in both cases.
"""

from media_house.modules.media_inspection.application.contracts import (
    FrameRateMode,
    InspectionCatalog,
    ScanType,
    StreamTiming,
)
from media_house.modules.video_improvement.domain.color import color_from_tags
from media_house.modules.video_improvement.domain.source import VideoFacts
from media_house.modules.video_improvement.domain.values import FactsSource, FrameRate
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


def _visible_frames(timing: StreamTiming | None) -> int | None:
    """Frames a player shows: the packets, minus hidden pre-roll before time zero (a clip cut
    without re-encoding starts at an earlier key frame whose leading frames are never shown)."""
    if timing is None:
        return None
    hidden = 0
    if timing.first_pts is not None and timing.first_pts < 0 and timing.median_interval:
        hidden = round(-timing.first_pts / timing.median_interval)
    return timing.packet_count - hidden


def known_video_facts(catalog: InspectionCatalog | None, asset_id: str) -> VideoFacts | None:
    """The source's facts from a stored inspection, or ``None`` (probe instead)."""
    if catalog is None:
        return None
    found = catalog.find(asset_id)
    if found is None or not found.inspection.observed.integrity.readable:
        return None
    inspection = found.inspection
    video = inspection.primary_video
    if video is None:
        return None
    nominal = video.frame_rate.value or video.average_frame_rate.value
    # the video stream's own length: the container's can include longer audio or data tracks
    duration = video.duration or inspection.container.duration
    if nominal is None or not duration or duration <= 0 or video.geometry.width <= 0:
        return None
    timing = video.timing
    tags: dict[str, str] = {}
    for source in (inspection.container.tags, video.tags):
        tags.update({key.lower(): value for key, value in source.items()})
    production = inspection.observed.production
    timecode = inspection.observed.timecode
    color = video.color
    _log.info("Video facts taken from the stored inspection", asset_id=asset_id)
    return VideoFacts(
        width=video.geometry.width,
        height=video.geometry.height,
        frame_rate=FrameRate(nominal.numerator, nominal.denominator),
        duration=duration,
        frame_count=_visible_frames(timing),
        pixel_format=video.pixel_format,
        bit_depth=video.bit_depth.value,
        color=color_from_tags(color.transfer.value, color.primaries.value),
        color_range=color.range.value,
        color_matrix=color.space.value,
        interlaced=video.scan_type.value is ScanType.INTERLACED,
        rotation=video.geometry.rotation.value or 0,
        variable_frame_rate=timing is not None and timing.mode is FrameRateMode.VARIABLE,
        audio_stream_count=len(inspection.audio_streams),
        timecode=str(timecode.start) if timecode else None,
        camera_make=production.camera_make,
        camera_model=production.camera_model,
        tags=tags,
        source=FactsSource.INSPECTION,
    )
