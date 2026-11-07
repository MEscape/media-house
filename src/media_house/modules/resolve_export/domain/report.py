"""The export report: one record of one export, from the authorization to the validated file."""

from dataclasses import dataclass, field

from media_house.modules.resolve_export.domain.values import (
    MODULE_VERSION,
    SCHEMA_VERSION,
    DeliveryStatus,
    ExportStatus,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExportReport:
    export_id: str
    project_id: str | None
    timeline_id: str
    timeline_version: int
    timeline_content_hash: str
    anchor_asset_id: str
    review_session_id: str
    #: The person's final approval and the authorization this export ran under.
    approval_reference: str
    authorization_id: str
    export_authorized_by: str
    export_profile_id: str
    output_config_hash: str
    #: The project and timeline in Resolve the export was made from (and the version it held).
    resolve_project_reference: str
    resolve_timeline_reference: str
    resolve_bound_version: int | None
    render_id: str
    #: The rendered file as a library asset and its file name (never an absolute path).
    output_asset_id: str
    output_name: str
    codec: str
    container: str
    resolution: str
    frame_rate: str
    audio_configuration: str
    export_status: ExportStatus
    progress: float
    validation_report_reference: str
    validation_status: str
    delivery_status: DeliveryStatus
    #: Where the delivered copy was placed (a name relative to the delivery root), once delivered.
    delivered_as: str
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    created_at: str
    updated_at: str
    module: str = "resolve_export"
    module_version: int = MODULE_VERSION
    content_hash: str = ""
    schema_version: int = field(default=SCHEMA_VERSION)
