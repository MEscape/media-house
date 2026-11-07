"""How render reports are stored: JSON documents of the recording's anchor asset in the Media
Library (no storage of its own). Rendered files are derived assets of whoever rendered them."""

import json
from collections.abc import Mapping
from typing import cast

from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.render_manager.domain.errors import InvalidRenderDocument
from media_house.modules.render_manager.domain.report import (
    RenderReport,
    report_from_json,
    report_to_json,
)
from media_house.modules.render_manager.domain.values import MODULE_VERSION, REPORT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


def _spec(key: Mapping[str, object]) -> DocumentSpec:
    return DocumentSpec(
        REPORT_OPERATION,
        MODULE_VERSION,
        cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str))),
    )


class RenderStore:
    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)

    def save(self, report: RenderReport) -> Result[MediaAssetDto, MediaError]:
        anchor = self._library.get(report.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        return self._documents.store(
            anchor.value,
            report_to_json(report),
            filename="render_report.json",
            spec=_spec({"report_id": report.report_id}),
            display_name=f"{anchor.value.display_name} (render {report.render_type.value})",
            metadata={
                "render_id": report.render_id,
                "job_key": report.job_key,
                "report_id": report.report_id,
                "attempt": report.attempt,
                "timeline_id": report.timeline_id,
                "timeline_version": report.timeline_version,
                "content_hash": report.timeline_content_hash,
                "render_type": report.render_type.value,
                "profile": report.render_profile_id,
                "status": report.status.value,
                "verification": report.verification_status.value,
                "output_asset_id": report.output_asset_id,
                "review_session_id": report.review_session_id,
            },
        )

    def assets(self, anchor_asset_id: str) -> list[MediaAssetDto]:
        """Report assets of a recording, newest first (metadata only; nothing is parsed)."""
        found = [
            a
            for a in self._library.list_derived(anchor_asset_id)
            if a.metadata.get("processing_type") == REPORT_OPERATION
        ]
        return sorted(found, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(self, asset: MediaAssetDto) -> RenderReport | None:
        path = self._library.local_path(asset.id)
        if isinstance(path, Err):
            return None
        try:
            return report_from_json(path.value.read_text(encoding="utf-8"))
        except (InvalidRenderDocument, OSError, UnicodeDecodeError, MediaHouseError) as exc:
            _log.warning("Stored render report unusable", asset_id=asset.id, reason=str(exc))
            return None

    def reports(
        self,
        anchor_asset_id: str,
        *,
        render_id: str | None = None,
        job_key: str | None = None,
        timeline_id: str | None = None,
        content_hash: str | None = None,
        render_type: str | None = None,
        output_asset_id: str | None = None,
    ) -> list[tuple[RenderReport, MediaAssetDto]]:
        wanted = {
            "render_id": render_id,
            "job_key": job_key,
            "timeline_id": timeline_id,
            "content_hash": content_hash,
            "render_type": render_type,
            "output_asset_id": output_asset_id,
        }
        found: list[tuple[RenderReport, MediaAssetDto]] = []
        for asset in self.assets(anchor_asset_id):
            if any(v is not None and asset.metadata.get(k) != v for k, v in wanted.items()):
                continue
            report = self.load(asset)
            if report is not None:
                found.append((report, asset))
        return found

    def get(self, anchor_asset_id: str, report_id: str) -> Result[RenderReport | None, MediaError]:
        anchor = self._library.get(anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        for asset in self.assets(anchor_asset_id):
            if asset.metadata.get("report_id") == report_id:
                return Ok(self.load(asset))
        return Ok(None)
