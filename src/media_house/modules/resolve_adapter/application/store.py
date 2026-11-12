"""How the binding and the reports are stored: JSON documents of the anchor asset in the Media
Library (no storage of its own). A binding is a new document per synchronisation, so the history
of what was in sync when is kept; the newest by ``sequence`` is the current one.
"""

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
from media_house.modules.resolve_adapter.domain.binding import (
    SyncState,
    state_from_json,
    state_to_json,
)
from media_house.modules.resolve_adapter.domain.errors import InvalidBindingDocument
from media_house.modules.resolve_adapter.domain.report import (
    ExecutionReport,
    report_from_json,
    report_to_json,
)
from media_house.modules.resolve_adapter.domain.values import (
    ADAPTER_VERSION,
    BINDING_OPERATION,
    REPORT_OPERATION,
)
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


def _spec(operation: str, key: Mapping[str, object]) -> DocumentSpec:
    return DocumentSpec(
        operation,
        ADAPTER_VERSION,
        cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str))),
    )


class ResolveStore:
    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)

    # --- binding ----------------------------------------------------------------------------
    def latest_binding(
        self, anchor_asset_id: str, timeline_id: str, *, project: str | None = None
    ) -> Result[tuple[SyncState, MediaAssetDto] | None, MediaError]:
        """The newest binding of the timeline; with ``project``, else the newest of any timeline
        of this video in that project (a new edit carried into the project Media House made)."""
        bindings = [
            a
            for a in self._library.list_derived(anchor_asset_id)
            if a.metadata.get("processing_type") == BINDING_OPERATION
        ]
        stored = [a for a in bindings if a.metadata.get("timeline_id") == timeline_id]
        if not stored and project is not None:
            stored = [a for a in bindings if a.metadata.get("project") == project]
        for asset in sorted(stored, key=lambda a: (_sequence(a), a.created_at), reverse=True):
            path = self._library.local_path(asset.id)
            if isinstance(path, Err):
                return path
            try:
                return Ok((state_from_json(path.value.read_text(encoding="utf-8")), asset))
            except (InvalidBindingDocument, OSError, UnicodeDecodeError, MediaHouseError) as exc:
                _log.warning("Stored binding unusable, trying an older one", reason=str(exc))
        return Ok(None)

    def save_binding(
        self, anchor: MediaAssetDto, state: SyncState
    ) -> Result[MediaAssetDto, MediaError]:
        key = {
            "anchor": anchor.id,
            "timeline_id": state.project.timeline_id,
            "sequence": state.sequence,
        }
        return self._documents.store(
            anchor,
            state_to_json(state),
            filename="resolve_binding.json",
            spec=_spec(BINDING_OPERATION, key),
            display_name=f"{anchor.display_name} (Resolve link)",
            metadata={
                "timeline_id": state.project.timeline_id,
                "sequence": state.sequence,
                "build_hash": state.build_hash,
                "project": state.project.project_name,
                "open_conflicts": sum(1 for c in state.conflicts if c.resolution.value == "open"),
            },
        )

    # --- reports ----------------------------------------------------------------------------
    def save_report(
        self, anchor: MediaAssetDto, report: ExecutionReport
    ) -> Result[MediaAssetDto, MediaError]:
        return self._documents.store(
            anchor,
            report_to_json(report),
            filename="resolve_execution_report.json",
            spec=_spec(REPORT_OPERATION, {"execution_id": report.execution_id}),
            display_name=f"{anchor.display_name} (Resolve report)",
            metadata={
                "timeline_id": report.timeline_id,
                "execution_id": report.execution_id,
                "outcome": report.outcome.value,
                "state": report.state.value,
                "build_hash": report.build_hash,
                "kind": "render" if report.render is not None else "execution",
            },
        )

    def reports(self, anchor_asset_id: str) -> list[MediaAssetDto]:
        found = [
            a
            for a in self._library.list_derived(anchor_asset_id)
            if a.metadata.get("processing_type") == REPORT_OPERATION
        ]
        return sorted(found, key=lambda a: (a.created_at, a.id), reverse=True)

    def load_report(
        self, report_asset_id: str
    ) -> Result[ExecutionReport, MediaError | InvalidBindingDocument]:
        path = self._library.local_path(report_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(report_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidBindingDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, MediaHouseError) as exc:
            return Err(InvalidBindingDocument(str(exc)))


def _sequence(asset: MediaAssetDto) -> int:
    value = asset.metadata.get("sequence")
    return value if isinstance(value, int) else 0
