"""From what was read to what is concluded: the one place a ``MediaInspection`` is assembled."""

from datetime import datetime

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.model import MediaInspection, ObservedMedia
from media_house.modules.media_inspection.domain.rules import derive_status, evaluate
from media_house.modules.media_inspection.domain.synchronization import synchronize


def assess(
    asset_id: str,
    observed: ObservedMedia,
    config: InspectionConfig,
    *,
    now: datetime,
) -> MediaInspection:
    """Measured synchronisation, findings and status for ``observed``; the facts stay as read."""
    sync = synchronize(observed.video_streams, observed.audio_streams)
    findings = evaluate(observed, sync, config)
    return MediaInspection(
        asset_id=asset_id,
        created_at=now,
        depth=config.depth,
        observed=observed,
        synchronization=sync,
        findings=findings,
        status=derive_status(findings),
    )
