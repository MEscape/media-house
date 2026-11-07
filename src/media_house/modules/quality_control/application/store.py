"""How reports are stored: JSON documents of the anchor asset in the Media Library (no storage of
its own). A report is immutable; a new analysis is a new document."""

import json
from typing import TYPE_CHECKING, cast

from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.quality_control.domain.errors import InvalidQualityDocument
from media_house.modules.quality_control.domain.model import QualityReport
from media_house.modules.quality_control.domain.serialization import (
    report_from_json,
    report_to_json,
)
from media_house.modules.quality_control.domain.values import QC_VERSION, RESULT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

_log = get_logger(__name__)


def _spec(report_id: str) -> DocumentSpec:
    key: Mapping[str, object] = {"quality_report_id": report_id}
    return DocumentSpec(
        RESULT_OPERATION, QC_VERSION, cast("Mapping[str, JsonValue]", json.loads(json.dumps(key)))
    )


class QualityStore:
    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)

    def find(
        self, anchor_id: str, report_id: str
    ) -> Result[tuple[QualityReport, MediaAssetDto] | None, MediaError]:
        spec = _spec(report_id)
        fingerprint = self._documents.fingerprint(anchor_id, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        return Ok(self._documents.load(anchor_id, fingerprint.value, report_from_json))

    def save(
        self, anchor: MediaAssetDto, report: QualityReport
    ) -> Result[MediaAssetDto, MediaError]:
        return self._documents.store(
            anchor,
            report_to_json(report),
            filename="quality_report.json",
            spec=_spec(report.quality_report_id),
            display_name=f"{anchor.display_name} (quality report {report.report_version})",
            metadata={
                "timeline_id": report.timeline_id,
                "timeline_version": report.timeline_version,
                "timeline_content_hash": report.timeline_content_hash,
                "quality_report_id": report.quality_report_id,
                "report_version": report.report_version,
                "technical_status": report.technical_status.value,
                "critical": report.severity_summary.critical,
                "high": report.severity_summary.high,
                "content_hash": report.content_hash,
            },
        )

    def history(self, anchor_id: str, timeline_id: str | None = None) -> list[MediaAssetDto]:
        found = [
            a
            for a in self._library.list_derived(anchor_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and (timeline_id is None or a.metadata.get("timeline_id") == timeline_id)
        ]
        return sorted(found, key=lambda a: (a.created_at, a.id), reverse=True)

    def latest(
        self,
        anchor_id: str,
        *,
        timeline_id: str | None = None,
        timeline_content_hash: str | None = None,
    ) -> Result[tuple[QualityReport, MediaAssetDto] | None, MediaError]:
        for asset in self.history(anchor_id, timeline_id):
            if (
                timeline_content_hash is not None
                and asset.metadata.get("timeline_content_hash") != timeline_content_hash
            ):
                continue
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidQualityDocument):
                return Err(loaded.error)
        return Ok(None)

    def load(self, asset_id: str) -> Result[QualityReport, MediaError | InvalidQualityDocument]:
        path = self._library.local_path(asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(report_from_json(path.value.read_text(encoding="utf-8")))
        except (InvalidQualityDocument, OSError, UnicodeDecodeError, MediaHouseError) as exc:
            _log.warning("Stored quality report unusable", asset_id=asset_id, reason=str(exc))
            if isinstance(exc, InvalidQualityDocument):
                return Err(exc)
            return Err(InvalidQualityDocument(str(exc)))
