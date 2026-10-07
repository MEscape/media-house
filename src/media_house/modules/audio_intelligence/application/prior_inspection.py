"""Reuse of technical facts that Media Inspection has already established.

Audio Intelligence works on any media, so it never ASSUMES an inspection exists. When a stored
inspection of the source is found it supplies the timing facts that would otherwise cost a probe
(same meaning as the probe: container duration, offset of the chosen audio stream from the
container origin); otherwise the preparer probes the file itself.
"""

from media_house.modules.audio_intelligence.application.ports import KnownSourceTiming
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


def known_source_timing(
    catalog: InspectionCatalog | None,
    asset_id: str,
    audio_stream: int,
) -> KnownSourceTiming | None:
    """Timing of audio stream ``audio_stream`` from a stored inspection, or ``None`` (probe)."""
    if catalog is None:
        return None
    found = catalog.find(asset_id)
    if found is None or not found.inspection.observed.integrity.readable:
        return None
    inspection = found.inspection
    audio = inspection.audio_stream(audio_stream)
    if audio is None:
        return None
    duration = inspection.container.duration or audio.duration
    if not duration or duration <= 0:
        return None
    origin = inspection.container.start_time or 0.0
    start = audio.start_time or 0.0
    _log.info("Source timing taken from the stored inspection", asset_id=asset_id)
    return KnownSourceTiming(duration, max(0.0, start - origin))
