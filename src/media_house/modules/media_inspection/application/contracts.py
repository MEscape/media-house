"""The Media Inspection PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``media_inspection``.

An inspection is the technical ground truth of an audio or video asset: container, streams,
exact timing, colour, timecode, synchronisation, integrity, plus findings and an overall status.
It never changes the media and never interprets it.

Two ways to use it, both optional for the consumer:

* ``MediaInspector.execute`` inspects (or returns the stored inspection). Blocking: run it as a job.
* ``InspectionCatalog.find`` only LOOKS UP a stored inspection and returns ``None`` otherwise.
  Consumers that merely benefit from known facts depend on this and fall back to their own
  probing, so they keep working when nothing was ever inspected.
"""

from typing import Protocol

from media_house.modules.media_inspection.application.inspect_media import (
    InspectError,
    InspectionResult,
    InspectMediaCommand,
)
from media_house.modules.media_inspection.application.related_media import RelatedMedia
from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.errors import (
    InvalidInspectionConfig,
    InvalidInspectionDocument,
    MediaInspectionError,
)
from media_house.modules.media_inspection.domain.model import (
    AudioStream,
    ContainerInfo,
    Finding,
    InspectionStatus,
    MediaInspection,
    VideoStream,
)
from media_house.modules.media_inspection.domain.relations import (
    RelationCandidate,
    RelationKind,
)
from media_house.modules.media_inspection.domain.timecode import Timecode
from media_house.modules.media_inspection.domain.values import (
    Certainty,
    Concern,
    Depth,
    Provenance,
    Rational,
    Severity,
    Sourced,
    Verdict,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import MediaHouseError, Result


class InspectionCatalog(Protocol):
    """Read-only access to inspections that already exist."""

    def find(
        self, asset_id: str, config: InspectionConfig | None = None
    ) -> InspectionResult | None:
        """The stored inspection of the asset, or ``None``. Never probes, never fails."""
        ...


class MediaInspector(InspectionCatalog, Protocol):
    """Inspect one audio/video asset (or return the identical inspection stored earlier)."""

    def execute(
        self,
        command: InspectMediaCommand,
        ctx: JobContext,
    ) -> Result[InspectionResult, InspectError]: ...


class MediaRelations(Protocol):
    """Find assets related to a given one, as recorded facts or candidates with confidence."""

    def execute(self, asset_id: str) -> Result[RelatedMedia, MediaHouseError]: ...


__all__ = [
    "AudioStream",
    "Certainty",
    "Concern",
    "ContainerInfo",
    "Depth",
    "Finding",
    "InspectError",
    "InspectMediaCommand",
    "InspectionCatalog",
    "InspectionConfig",
    "InspectionResult",
    "InspectionStatus",
    "InvalidInspectionConfig",
    "InvalidInspectionDocument",
    "MediaInspection",
    "MediaInspectionError",
    "MediaInspector",
    "MediaRelations",
    "Provenance",
    "Rational",
    "RelatedMedia",
    "RelationCandidate",
    "RelationKind",
    "Severity",
    "Sourced",
    "Timecode",
    "Verdict",
    "VideoStream",
]
