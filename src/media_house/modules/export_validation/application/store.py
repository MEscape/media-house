"""How validation reports are stored: JSON documents of the recording's anchor asset in the Media
Library (no storage of its own). A report is found by the cache key of the validation it answers."""

import json
from typing import TYPE_CHECKING, cast

from media_house.modules.export_validation.domain.report import (
    ExportValidationReport,
    InvalidValidationDocument,
    report_from_json,
    report_to_json,
)
from media_house.modules.export_validation.domain.values import (
    REPORT_OPERATION,
    VALIDATOR_VERSION,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

_log = get_logger(__name__)


def _spec(cache_key: str) -> DocumentSpec:
    return DocumentSpec(
        REPORT_OPERATION,
        VALIDATOR_VERSION,
        cast("Mapping[str, JsonValue]", json.loads(json.dumps({"cache_key": cache_key}))),
    )


class ValidationStore:
    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)

    def find(
        self, anchor_id: str, cache_key: str
    ) -> Result[tuple[ExportValidationReport, MediaAssetDto] | None, MediaError]:
        fingerprint = self._documents.fingerprint(anchor_id, _spec(cache_key))
        if isinstance(fingerprint, Err):
            return fingerprint
        return Ok(self._documents.load(anchor_id, fingerprint.value, report_from_json))

    def save(self, report: ExportValidationReport) -> Result[MediaAssetDto, MediaError]:
        anchor = self._library.get(report.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        return self._documents.store(
            anchor.value,
            report_to_json(report),
            filename="export_validation_report.json",
            spec=_spec(report.cache_key),
            display_name=f"{anchor.value.display_name} (export validation)",
            metadata={
                "export_validation_id": report.export_validation_id,
                "export_id": report.export_id,
                "output_asset_id": report.output_asset_id,
                "timeline_id": report.timeline_id,
                "timeline_version": report.timeline_version,
                "content_hash": report.approved_version_reference.content_hash,
                "delivery_profile": report.delivery_profile_id,
                "status": report.validation_status.value,
                "file_checksum": report.file_checksum,
            },
        )

    def reports(self, anchor_id: str) -> list[tuple[ExportValidationReport, MediaAssetDto]]:
        found: list[tuple[ExportValidationReport, MediaAssetDto]] = []
        assets = [
            a
            for a in self._library.list_derived(anchor_id)
            if a.metadata.get("processing_type") == REPORT_OPERATION
        ]
        for asset in sorted(assets, key=lambda a: (a.created_at, a.id), reverse=True):
            path = self._library.local_path(asset.id)
            if isinstance(path, Err):
                continue
            try:
                found.append((report_from_json(path.value.read_text(encoding="utf-8")), asset))
            except (InvalidValidationDocument, OSError, UnicodeDecodeError, MediaHouseError) as exc:
                _log.warning(
                    "Stored validation report unusable", asset_id=asset.id, reason=str(exc)
                )
        return found
