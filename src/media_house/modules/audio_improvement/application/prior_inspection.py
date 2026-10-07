"""Reuse of technical facts that Media Inspection has already established.

Audio Improvement works on any media, so it never ASSUMES an inspection exists. When a stored
inspection of the source is found it supplies the stream facts that would otherwise cost a probe
(same meaning as the probe: container duration, offset of the audio from the container origin);
otherwise the transcoder probes the file itself.
"""

from media_house.modules.audio_improvement.application.ports import SourceAudioFacts
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


def known_audio_facts(catalog: InspectionCatalog | None, asset_id: str) -> SourceAudioFacts | None:
    """The first audio stream's facts from a stored inspection, or ``None`` (probe instead)."""
    if catalog is None:
        return None
    found = catalog.find(asset_id)
    if found is None or not found.inspection.observed.integrity.readable:
        return None
    inspection = found.inspection
    audio = inspection.audio_stream(0)
    if audio is None or not audio.sample_rate or not audio.channels:
        return None
    duration = inspection.container.duration or audio.duration
    if not duration or duration <= 0:
        return None
    origin = inspection.container.start_time or 0.0
    start = audio.start_time or 0.0
    _log.info("Stream facts taken from the stored inspection", asset_id=asset_id)
    return SourceAudioFacts(audio.sample_rate, audio.channels, duration, max(0.0, start - origin))
