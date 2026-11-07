"""The render report: one stored document per render attempt, read strictly.

It says what was asked (request), what made the file (profile and version, backend and version),
what could not be applied, what the file turned out to be (checks), and what was not measured.
A report is never edited; a retry is a new report of the same ``render_id``.
"""

import json
from dataclasses import dataclass, field, replace
from datetime import datetime

from media_house.core.domain import Rational, json_codec
from media_house.modules.render_manager.domain.errors import InvalidRenderDocument
from media_house.modules.render_manager.domain.values import (
    MODULE_VERSION,
    SCHEMA_VERSION,
    UNMEASURED,
    JobState,
    RenderType,
    Verification,
)
from media_house.modules.render_manager.domain.verify import Check

DOCUMENT_TYPE = "render_report"


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """Enough of the request to repeat it (a retry) and to read the report without the caller."""

    render_type: RenderType
    profile_id: str
    anchor_asset_id: str
    review_session_id: str = ""
    section_id: str = ""
    start_frame: int | None = None
    end_frame: int | None = None
    padding_seconds: float = 0.0
    reference_render_id: str = ""
    priority: int = 0


@dataclass(frozen=True, slots=True)
class Limitation:
    """A setting the profile asks for that the backend did not apply. Never silently dropped."""

    setting: str
    wanted: str
    reason: str


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    wall_seconds: float
    disk_free_before_bytes: int | None = None
    disk_free_after_bytes: int | None = None
    cpu: str = UNMEASURED
    gpu: str = UNMEASURED
    ram: str = UNMEASURED
    vram: str = UNMEASURED


@dataclass(frozen=True, slots=True)
class AudioConfiguration:
    codec: str
    sample_rate: int
    channels: int


@dataclass(frozen=True, slots=True)
class RenderReport:
    report_id: str
    #: Stable for a request: the same request always has this id; attempts are numbered.
    render_id: str
    job_key: str
    attempt: int
    anchor_asset_id: str
    project_id: str | None
    timeline_id: str
    timeline_version: int
    #: The content hash of the timeline version the render is of.
    timeline_content_hash: str
    review_session_id: str
    request: RequestRecord
    render_profile_id: str
    render_profile_version: int
    output_config_hash: str
    render_type: RenderType
    render_backend: str
    render_backend_version: str
    status: JobState
    progress: float
    created_at: datetime
    resource_usage: ResourceUsage
    verification_status: Verification
    output_asset_id: str = ""
    output_name: str = ""
    codec: str = ""
    container: str = ""
    width: int | None = None
    height: int | None = None
    frame_rate: Rational | None = None
    audio: AudioConfiguration | None = None
    file_size: int = 0
    start_frame: int | None = None
    end_frame: int | None = None
    #: A stored identical render served instead of rendering again.
    reused: bool = False
    #: The FINAL render carried this export authorization.
    authorization_id: str = ""
    checks: tuple[Check, ...] = ()
    limitations: tuple[Limitation, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    #: Every report names the module that made it and what it read (14.2).
    module: str = "render_manager"
    module_version: int = MODULE_VERSION
    inputs: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    content_hash: str = ""
    schema_version: int = SCHEMA_VERSION

    @property
    def succeeded(self) -> bool:
        """The job completed AND the file was looked at and did not fail its checks."""
        return (
            self.status is JobState.COMPLETED
            and bool(self.output_asset_id)
            and self.verification_status in (Verification.VERIFIED, Verification.WITH_WARNINGS)
        )


def with_content_hash(report: RenderReport) -> RenderReport:
    blank = replace(report, content_hash="", report_id="", created_at=_EPOCH)
    return replace(report, content_hash=json_codec.digest(blank))


_EPOCH = datetime(1970, 1, 1)  # noqa: DTZ001 - a fixed placeholder inside a digest, never shown


def report_to_json(report: RenderReport) -> str:
    return json_codec.dumps(DOCUMENT_TYPE, report)


def report_from_json(text: str) -> RenderReport:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidRenderDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != DOCUMENT_TYPE:
        raise InvalidRenderDocument("not a render report")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidRenderDocument(f"schema version {raw.get('schema_version')!r} is not readable")
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(RenderReport, body, "report")
    except (TypeError, ValueError) as exc:
        raise InvalidRenderDocument(str(exc)) from exc
    if not isinstance(decoded, RenderReport):
        raise InvalidRenderDocument("not a render report")
    return decoded
