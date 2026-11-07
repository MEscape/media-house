"""Stored export validation reports, read: history, one report, the latest for an export."""

from media_house.modules.export_validation.application.store import ValidationStore
from media_house.modules.export_validation.domain.report import ExportValidationReport


class ValidationHistory:
    """Implements ``ExportValidationCatalog``."""

    def __init__(self, store: ValidationStore) -> None:
        self._store = store

    def history(
        self, anchor_asset_id: str, *, output_asset_id: str | None = None
    ) -> list[ExportValidationReport]:
        """Every validation of the recording, newest first."""
        return [
            r
            for r, _ in self._store.reports(anchor_asset_id)
            if output_asset_id is None or r.output_asset_id == output_asset_id
        ]

    def get(self, anchor_asset_id: str, export_validation_id: str) -> ExportValidationReport | None:
        return next(
            (
                r
                for r in self.history(anchor_asset_id)
                if r.export_validation_id == export_validation_id
            ),
            None,
        )

    def latest_for_export(
        self, anchor_asset_id: str, export_id: str
    ) -> ExportValidationReport | None:
        return next((r for r in self.history(anchor_asset_id) if r.export_id == export_id), None)
