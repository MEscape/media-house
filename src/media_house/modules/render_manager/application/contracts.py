"""The Render Manager PUBLIC API for other modules (import this file only).

``render_manager`` renders a timeline for review or for delivery through one backend port (Resolve
today, via ``resolve_adapter``), keeps versioned render profiles, queues and controls render jobs,
checks every finished file and keeps a report of every attempt. It decides nothing creative,
approves nothing and exports nothing without a person's authorization.

    outcome = renders.render(RenderRequest(anchor_asset_id=..., render_type=RenderType.REVIEW,
                                           profile_id="review_fast"), ctx)
    outcome.value.succeeded            # the job completed AND the file passed its checks
    outcome.value.report.verification_status, .limitations, .errors

    profiles.config_hash("youtube_16_9", anchor_asset_id=...)   # what an export is authorized for
    renders.render(RenderRequest(..., render_type=RenderType.FINAL,
                                 authorization=RenderAuthorization(...)), ctx)

Blocking: run as jobs (progress and cancellation through ``JobContext``).
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaError
from media_house.modules.render_manager.application.catalog import RenderHistory
from media_house.modules.render_manager.application.events import RenderFinished
from media_house.modules.render_manager.application.ports import (
    BackendCapabilities,
    BackendIdentity,
    BackendRequest,
    BackendResult,
    RenderBackend,
)
from media_house.modules.render_manager.application.profiles import (
    ProfileFailure,
    ProfileResolution,
)
from media_house.modules.render_manager.application.render import (
    ALLOWED_PURPOSES,
    PlannedRender,
    RenderAuthorization,
    RenderFailure,
    RenderOutcome,
    RenderRequest,
    RenderStatus,
)
from media_house.modules.render_manager.domain.errors import (
    InvalidRenderDocument,
    InvalidRenderProfile,
    NotControllable,
    RenderManagerError,
    RenderRefused,
    UnknownRender,
    UnknownRenderProfile,
)
from media_house.modules.render_manager.domain.keys import FrameRange
from media_house.modules.render_manager.domain.profile import (
    PROFILES,
    AudioSettings,
    RenderProfile,
    ResolvedSettings,
    VideoSettings,
    get_profile,
    output_config_hash,
)
from media_house.modules.render_manager.domain.report import (
    AudioConfiguration,
    Limitation,
    RenderReport,
    RequestRecord,
    ResourceUsage,
    report_from_json,
    report_to_json,
)
from media_house.modules.render_manager.domain.values import (
    MODULE_VERSION,
    REPORT_OPERATION,
    UNMEASURED,
    CheckState,
    JobState,
    Purpose,
    RenderType,
    Verification,
)
from media_house.modules.render_manager.domain.verify import Check
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result


class RenderManager(Protocol):
    def plan(self, request: RenderRequest) -> Result[PlannedRender, RenderFailure]:
        """Decide everything that needs no rendering: allowed, output, stored answer."""
        ...

    def render(
        self, request: RenderRequest, ctx: JobContext
    ) -> Result[RenderOutcome, RenderFailure]:
        """Plan, render, check the file, report. ``outcome.succeeded`` is the verdict."""
        ...

    def execute(
        self, planned: PlannedRender, ctx: JobContext
    ) -> Result[RenderOutcome, RenderFailure]: ...

    def retry(
        self,
        anchor_asset_id: str,
        render_id: str,
        ctx: JobContext,
        *,
        authorization: RenderAuthorization | None = None,
    ) -> Result[RenderOutcome, RenderFailure]: ...

    def status(self, render_id: str) -> Result[RenderStatus, RenderManagerError]: ...

    def cancel(self, render_id: str) -> Result[RenderStatus, RenderManagerError]: ...

    def pause(self, render_id: str) -> Result[RenderStatus, RenderManagerError]: ...

    def resume(self, render_id: str) -> Result[RenderStatus, RenderManagerError]: ...


class RenderQueueing(Protocol):
    def enqueue(self, request: RenderRequest) -> Result[RenderStatus, RenderFailure]: ...

    def pending(self) -> list[RenderStatus]: ...

    def run_next(self, ctx: JobContext) -> Result[RenderOutcome, RenderFailure] | None: ...

    def hold(self, render_id: str) -> Result[RenderStatus, RenderManagerError]: ...

    def release(self, render_id: str) -> Result[RenderStatus, RenderManagerError]: ...

    def withdraw(self, render_id: str) -> Result[RenderStatus, RenderManagerError]: ...


class RenderProfiles(Protocol):
    def profiles(self) -> tuple[RenderProfile, ...]: ...

    def profile(self, profile_id: str) -> Result[RenderProfile, RenderManagerError]: ...

    def resolve(
        self,
        profile_id: str,
        *,
        anchor_asset_id: str,
        timeline_id: str | None = None,
        custom: RenderProfile | None = None,
    ) -> Result[ProfileResolution, ProfileFailure]: ...

    def config_hash(
        self, profile_id: str, *, anchor_asset_id: str, timeline_id: str | None = None
    ) -> Result[str, ProfileFailure]:
        """The ``output_config_hash`` an export authorization for this output is bound to."""
        ...


class RenderCatalog(Protocol):
    def history(
        self,
        anchor_asset_id: str,
        *,
        timeline_id: str | None = None,
        render_type: RenderType | None = None,
    ) -> list[RenderReport]: ...

    def get(
        self, anchor_asset_id: str, report_id: str
    ) -> Result[RenderReport | None, MediaError]: ...

    def latest(
        self,
        anchor_asset_id: str,
        *,
        timeline_id: str | None = None,
        content_hash: str | None = None,
        render_type: RenderType | None = None,
        succeeded_only: bool = True,
    ) -> RenderReport | None: ...

    def for_output(self, anchor_asset_id: str, output_asset_id: str) -> RenderReport | None: ...


__all__ = [
    "ALLOWED_PURPOSES",
    "MODULE_VERSION",
    "PROFILES",
    "REPORT_OPERATION",
    "UNMEASURED",
    "AudioConfiguration",
    "AudioSettings",
    "BackendCapabilities",
    "BackendIdentity",
    "BackendRequest",
    "BackendResult",
    "Check",
    "CheckState",
    "FrameRange",
    "InvalidRenderDocument",
    "InvalidRenderProfile",
    "JobState",
    "Limitation",
    "NotControllable",
    "PlannedRender",
    "ProfileFailure",
    "ProfileResolution",
    "Purpose",
    "RenderAuthorization",
    "RenderBackend",
    "RenderCatalog",
    "RenderFailure",
    "RenderFinished",
    "RenderHistory",
    "RenderManager",
    "RenderManagerError",
    "RenderOutcome",
    "RenderProfile",
    "RenderProfiles",
    "RenderQueueing",
    "RenderRefused",
    "RenderReport",
    "RenderRequest",
    "RenderStatus",
    "RenderType",
    "RequestRecord",
    "ResolvedSettings",
    "ResourceUsage",
    "UnknownRender",
    "UnknownRenderProfile",
    "Verification",
    "VideoSettings",
    "get_profile",
    "output_config_hash",
    "report_from_json",
    "report_to_json",
]
