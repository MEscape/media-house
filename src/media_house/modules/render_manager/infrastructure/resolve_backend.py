"""The default render backend: Resolve, through ``resolve_adapter``'s public contract.

This adapter holds no Resolve connection of its own: it asks the adapter's ``ResolveRenderer`` to
render (which guards the project, registers the file in the Media Library and compares it with the
timeline) and turns its report into a ``BackendResult``. What Resolve really supports is stated
here, not assumed: the adapter renders the whole timeline with its own two profiles (``youtube``
and ``master``) and cannot pause or resume a render.
"""

from media_house.modules.render_manager.application.ports import (
    BackendCapabilities,
    BackendIdentity,
    BackendRequest,
    BackendResult,
)
from media_house.modules.resolve_adapter.application.contracts import (
    RENDER_PROFILES,
    Outcome,
    RenderCommand,
    RenderConfig,
    ResolveInspector,
    ResolveRenderer,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err

NAME = "resolve"
_CAPABILITIES = BackendCapabilities(
    range_render=False,
    pause_resume=False,
    profiles=frozenset(p.key for p in RENDER_PROFILES),
    #: Resolve's adapter sets the container, the video codec and the audio codec per profile.
    applies=frozenset({"container", "codec", "audio_codec"}),
)


class ResolveRenderBackend:
    """Structurally implements ``RenderBackend``."""

    def __init__(self, renderer: ResolveRenderer, inspector: ResolveInspector) -> None:
        self._renderer = renderer
        self._inspector = inspector
        self._version = ""

    def identity(self) -> BackendIdentity:
        if not self._version:
            status = self._inspector.execute().status
            environment = status.environment
            if status.connected and environment is not None and environment.version is not None:
                self._version = environment.version.text
        return BackendIdentity(NAME, self._version or "not connected", _CAPABILITIES)

    def render(self, request: BackendRequest, ctx: JobContext) -> BackendResult:
        done = self._renderer.execute(
            RenderCommand(
                anchor_asset_id=request.anchor_asset_id,
                timeline_id=request.timeline_id,
                render=RenderConfig(
                    profile=request.native_profile,
                    file_name=request.file_name,
                    require_final_quality=request.require_final_quality,
                ),
                reuse=request.reuse,
            ),
            ctx,
        )
        if isinstance(done, Err):
            return BackendResult("failed", None, errors=(done.error.user_message,))
        result = done.value
        report = result.report
        warnings = tuple(report.warnings)
        if report.outcome is Outcome.CANCELLED:
            return BackendResult("cancelled", None, errors=("the render was cancelled",))
        if report.outcome in (Outcome.COMPLETED, Outcome.COMPLETED_WITH_WARNINGS):
            if result.output is None:
                return BackendResult(
                    "failed", None, errors=("Resolve reported success but no file was registered",)
                )
            return BackendResult("completed", result.output, result.reused, warnings=warnings)
        return BackendResult(
            "failed",
            None,
            errors=tuple(report.errors) or (f"the render ended as {report.outcome.value}",),
            warnings=warnings,
        )

    def pause(self) -> bool:
        return False

    def resume(self) -> bool:
        return False
