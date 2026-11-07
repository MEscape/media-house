"""How export reports are stored: JSON documents of the anchor asset in the Media Library (no
storage of its own). An export moves (authorized, rendering, validated, delivered); every state
is a new document and the earlier ones stay, so the history of an export can be read back."""

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
from media_house.modules.resolve_export.domain.errors import InvalidExportDocument
from media_house.modules.resolve_export.domain.report import ExportReport
from media_house.modules.resolve_export.domain.serialization import report_from_json, report_to_json
from media_house.modules.resolve_export.domain.values import MODULE_VERSION, REPORT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

_log = get_logger(__name__)


def _spec(report: ExportReport) -> DocumentSpec:
    key: Mapping[str, object] = {"export_id": report.export_id, "content_hash": report.content_hash}
    return DocumentSpec(
        REPORT_OPERATION,
        MODULE_VERSION,
        cast("Mapping[str, JsonValue]", json.loads(json.dumps(key))),
    )


class ExportStore:
    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)

    def save(
        self, anchor: MediaAssetDto, report: ExportReport
    ) -> Result[MediaAssetDto, MediaError]:
        return self._documents.store(
            anchor,
            report_to_json(report),
            filename="export_report.json",
            spec=_spec(report),
            display_name=f"{anchor.display_name} (export {report.export_id[:8]})",
            metadata={
                "export_id": report.export_id,
                "review_session_id": report.review_session_id,
                "timeline_id": report.timeline_id,
                "timeline_version": report.timeline_version,
                "export_status": report.export_status.value,
                "delivery_status": report.delivery_status.value,
            },
        )

    def history(self, anchor_id: str) -> list[MediaAssetDto]:
        found = [
            a
            for a in self._library.list_derived(anchor_id)
            if a.metadata.get("processing_type") == REPORT_OPERATION
        ]
        return sorted(found, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(self, asset_id: str) -> Result[ExportReport, MediaError | InvalidExportDocument]:
        path = self._library.local_path(asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(report_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidExportDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, MediaHouseError) as exc:
            _log.warning("Stored export report unusable", asset_id=asset_id, reason=str(exc))
            return Err(InvalidExportDocument(str(exc)))

    def exports(
        self, anchor_id: str, *, session_id: str | None = None
    ) -> Result[list[tuple[ExportReport, MediaAssetDto]], MediaError]:
        """The newest document of every export, newest first."""
        newest: dict[str, tuple[ExportReport, MediaAssetDto]] = {}
        for asset in self.history(anchor_id):
            loaded = self.load(asset.id)
            if isinstance(loaded, Err):
                if isinstance(loaded.error, InvalidExportDocument):
                    continue
                return Err(loaded.error)
            if session_id is None or loaded.value.review_session_id == session_id:
                newest.setdefault(loaded.value.export_id, (loaded.value, asset))
        return Ok(list(newest.values()))

    def find(
        self, anchor_id: str, export_id: str
    ) -> Result[tuple[ExportReport, MediaAssetDto] | None, MediaError]:
        listed = self.exports(anchor_id)
        if isinstance(listed, Err):
            return listed
        return Ok(next((p for p in listed.value if p[0].export_id == export_id), None))
