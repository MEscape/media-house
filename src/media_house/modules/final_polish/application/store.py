"""How polish reports are stored: JSON documents of the anchor asset in the Media Library (no
storage of its own). A report is immutable; a new analysis is a new document."""

import json
from typing import TYPE_CHECKING, cast

from media_house.modules.final_polish.domain.errors import InvalidPolishDocument
from media_house.modules.final_polish.domain.model import FinalPolishReport
from media_house.modules.final_polish.domain.serialization import report_from_json, report_to_json
from media_house.modules.final_polish.domain.values import POLISH_VERSION, RESULT_OPERATION
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


def _spec(report_id: str) -> DocumentSpec:
    key: Mapping[str, object] = {"final_polish_report_id": report_id}
    return DocumentSpec(
        RESULT_OPERATION,
        POLISH_VERSION,
        cast("Mapping[str, JsonValue]", json.loads(json.dumps(key))),
    )


class PolishStore:
    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)

    def find(
        self, anchor_id: str, report_id: str
    ) -> Result[tuple[FinalPolishReport, MediaAssetDto] | None, MediaError]:
        fingerprint = self._documents.fingerprint(anchor_id, _spec(report_id))
        if isinstance(fingerprint, Err):
            return fingerprint
        return Ok(self._documents.load(anchor_id, fingerprint.value, report_from_json))

    def save(
        self, anchor: MediaAssetDto, report: FinalPolishReport
    ) -> Result[MediaAssetDto, MediaError]:
        return self._documents.store(
            anchor,
            report_to_json(report),
            filename="final_polish_report.json",
            spec=_spec(report.final_polish_report_id),
            display_name=f"{anchor.display_name} (final polish)",
            metadata={
                "timeline_id": report.timeline_id,
                "timeline_version": report.timeline_version,
                "timeline_content_hash": report.timeline_content_hash,
                "final_polish_report_id": report.final_polish_report_id,
                "readiness": report.readiness_status.value,
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
    ) -> Result[tuple[FinalPolishReport, MediaAssetDto] | None, MediaError]:
        for asset in self.history(anchor_id, timeline_id):
            if (
                timeline_content_hash is not None
                and asset.metadata.get("timeline_content_hash") != timeline_content_hash
            ):
                continue
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidPolishDocument):
                return Err(loaded.error)
        return Ok(None)

    def load(self, asset_id: str) -> Result[FinalPolishReport, MediaError | InvalidPolishDocument]:
        path = self._library.local_path(asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(report_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidPolishDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, MediaHouseError) as exc:
            _log.warning("Stored polish report unusable", asset_id=asset_id, reason=str(exc))
            return Err(InvalidPolishDocument(str(exc)))
