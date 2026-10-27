"""Copies the few inspection values the detectors read into this module's own value object.

The detection rules never see a media-inspection type: this is the only place that knows where the
values live in an inspection, and it copies, it does not interpret.
"""

from media_house.modules.asset_relationships.domain.detection import InspectionFacts
from media_house.modules.media_inspection.application.contracts import InspectionResult


def inspection_facts(asset_id: str, found: InspectionResult) -> InspectionFacts:
    observed = found.inspection.observed
    production = observed.production
    derivation = found.asset.derivation
    return InspectionFacts(
        asset_id=asset_id,
        inspection_id=found.asset.id,
        inspection_fingerprint="" if derivation is None else derivation.fingerprint,
        duration_seconds=observed.container.duration,
        creation_time=production.creation_time,
        device=production.device,
        reel_id=production.reel_id,
        scene=production.scene,
        take=production.take,
    )
