"""Rendering: plan a request, carry it out, check the file, report what happened.

``plan`` decides everything that can be decided without rendering (is it allowed, what is the
output, which stored render already answers it); ``execute`` renders, checks the finished file and
stores a report. A render that completed is NOT a render that succeeded: the file is looked at, and
``RenderOutcome.succeeded`` says whether it passed. A FINAL render is refused unless the review
orchestrator confirms an export authorization for exactly this timeline version and output, and
that is confirmed again at the moment the render starts, not only when it was requested.
"""

import time
from dataclasses import dataclass, replace

from media_house.core.application.ports import Clock
from media_house.modules.media_inspection.application.contracts import (
    Depth,
    InspectionConfig,
    InspectionResult,
    InspectMediaCommand,
    MediaInspector,
)
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.render_manager.application.events import RenderFinished
from media_house.modules.render_manager.application.ports import (
    BackendIdentity,
    BackendRequest,
    BackendResult,
    DiskProbe,
    RenderBackend,
    SectionCutter,
)
from media_house.modules.render_manager.application.store import RenderStore
from media_house.modules.render_manager.application.views import (
    TimelineView,
    output_facts,
    seconds_of,
    timeline_view,
)
from media_house.modules.render_manager.domain.errors import (
    NotControllable,
    RenderManagerError,
    RenderRefused,
    UnknownRender,
)
from media_house.modules.render_manager.domain.keys import (
    FrameRange,
    job_key,
    output_name,
    render_id_of,
)
from media_house.modules.render_manager.domain.profile import (
    RenderProfile,
    ResolvedSettings,
    get_profile,
    output_config_hash,
    profile_problems,
    resolve_settings,
)
from media_house.modules.render_manager.domain.report import (
    AudioConfiguration,
    Limitation,
    RenderReport,
    RequestRecord,
    ResourceUsage,
    with_content_hash,
)
from media_house.modules.render_manager.domain.values import (
    JobState,
    Purpose,
    RenderType,
    Verification,
)
from media_house.modules.render_manager.domain.verify import (
    Check,
    Expectation,
    OutputFacts,
    overall,
    verify_output,
)
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ArtifactKind,
    ReviewFailure,
    ReviewSessions,
    TimelineRef,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import (
    Err,
    MediaHouseError,
    NotFoundError,
    Ok,
    OperationCancelledError,
    Result,
)
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger
from media_house.shared.types import new_id

_log = get_logger(__name__)
SECTION_OPERATION = "render_section_preview"
#: Which profile purposes a kind of render may use: a preview never spends a master's time.
ALLOWED_PURPOSES: dict[RenderType, tuple[Purpose, ...]] = {
    RenderType.REVIEW: (Purpose.PREVIEW,),
    RenderType.SECTION_PREVIEW: (Purpose.PREVIEW,),
    RenderType.COMPARISON: (Purpose.PREVIEW,),
    RenderType.INTERMEDIATE: (Purpose.INTERMEDIATE,),
    RenderType.FINAL: (Purpose.DELIVERY, Purpose.MASTER),
}

type RenderFailure = RenderManagerError | MediaError | NotFoundError | ReviewFailure


@dataclass(frozen=True, slots=True)
class RenderAuthorization:
    """The export authorization a FINAL render runs under (from ``resolve_export``)."""

    session_id: str
    authorization_id: str
    #: The version of the timeline that was authorized.
    timeline: TimelineRef


@dataclass(frozen=True, slots=True)
class RenderRequest:
    anchor_asset_id: str
    render_type: RenderType
    profile_id: str
    timeline_id: str | None = None
    review_session_id: str = ""
    #: A section preview names a section, or gives frames of the timeline.
    section_id: str = ""
    start_frame: int | None = None
    end_frame: int | None = None
    #: Context kept around a section so transitions and continuity can be judged.
    padding_seconds: float = 2.0
    authorization: RenderAuthorization | None = None
    #: A validated custom profile (its id must not be a built-in's).
    profile: RenderProfile | None = None
    #: A COMPARISON render sits next to this earlier render.
    reference_render_id: str = ""
    priority: int = 0
    #: Render again although an identical stored render exists (never for FINAL).
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
class PlannedRender:
    request: RenderRequest
    profile: RenderProfile
    render_id: str
    job_key: str
    ref: TimelineRef
    view: TimelineView
    settings: ResolvedSettings
    config_hash: str
    frames: FrameRange | None
    identity: BackendIdentity
    limitations: tuple[Limitation, ...]
    warnings: tuple[str, ...]
    #: The stored render that already answers this request (``None``: it has to be rendered).
    existing: tuple[RenderReport, MediaAssetDto] | None = None


@dataclass(frozen=True, slots=True)
class RenderOutcome:
    report: RenderReport
    report_asset: MediaAssetDto | None
    output: MediaAssetDto | None
    #: A stored identical render was served; nothing was rendered.
    reused: bool

    @property
    def succeeded(self) -> bool:
        return self.report.succeeded


@dataclass(frozen=True, slots=True)
class RenderStatus:
    render_id: str
    state: JobState
    progress: float
    message: str = ""


class _Linked(CancellationToken):
    """The job's own cancellation OR a ``cancel(render_id)`` request."""

    def __init__(self, parent: CancellationToken) -> None:
        super().__init__()
        self._parent = parent

    @property
    def is_cancelled(self) -> bool:
        return self._parent.is_cancelled or super().is_cancelled

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise OperationCancelledError("Operation was cancelled")

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.is_cancelled:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            super().wait(min(0.05, remaining))
        return True


class _Tracking:
    """Progress passed on to the caller and remembered for ``status``."""

    def __init__(self, inner: JobContext, status: dict[str, RenderStatus], render_id: str) -> None:
        self._inner, self._status, self._id = inner, status, render_id

    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        percent = 100.0 * current / total if total else float(current)
        previous = self._status.get(self._id)
        state = previous.state if previous else JobState.RUNNING
        self._status[self._id] = RenderStatus(self._id, state, percent, message)
        self._inner.progress.report(current, total, message)


class RenderService:
    """Implements ``RenderManager``."""

    def __init__(
        self,
        library: MediaLibrary,
        catalog: BuiltTimelineCatalog,
        backend: RenderBackend,
        cutter: SectionCutter,
        disk: DiskProbe,
        inspector: MediaInspector,
        sessions: ReviewSessions,
        store: RenderStore,
        paths: AppPaths,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._library = library
        self._catalog = catalog
        self._backend = backend
        self._cutter = cutter
        self._disk = disk
        self._inspector = inspector
        self._sessions = sessions
        self._store = store
        self._paths = paths
        self._clock = clock
        self._events = events
        self._tokens: dict[str, _Linked] = {}
        self._status: dict[str, RenderStatus] = {}

    # --- plan -----------------------------------------------------------------------------------
    def plan(self, request: RenderRequest) -> Result[PlannedRender, RenderFailure]:
        try:
            profile = request.profile or get_profile(request.profile_id)
        except RenderManagerError as exc:
            return Err(exc)
        anchor = self._library.get(request.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        latest = self._catalog.latest(request.anchor_asset_id, timeline_id=request.timeline_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(
                NotFoundError(
                    "No built timeline", user_message="There is no built timeline to render."
                )
            )
        timeline, build_asset = latest.value
        view = timeline_view(timeline)
        ref = TimelineRef(
            view.timeline_id,
            view.version,
            view.content_hash,
            request.anchor_asset_id,
            build_asset.id,
        )
        identity = self._backend.identity()
        frames, range_reasons = _frames(request, view, identity)
        reasons = [*range_reasons, *self._refusals(request, profile, view, ref, identity)]
        if reasons:
            return Err(RenderRefused(reasons))
        settings = resolve_settings(
            profile, width=view.width, height=view.height, frame_rate=view.frame_rate
        )
        config_hash = output_config_hash(settings)
        if request.render_type is RenderType.FINAL and request.authorization is not None:
            checked = self._authorize(request.authorization, profile, config_hash)
            if isinstance(checked, Err):
                return checked
        key = job_key(
            timeline_id=view.timeline_id,
            content_hash=view.content_hash,
            render_type=request.render_type,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            config_hash=config_hash,
            frames=frames,
            backend=identity.name,
        )
        existing = None if request.overwrite else self._reusable(request.anchor_asset_id, key)
        problems = profile_problems(profile, width=view.width, height=view.height)
        return Ok(
            PlannedRender(
                request=request,
                profile=profile,
                render_id=render_id_of(key),
                job_key=key,
                ref=ref,
                view=view,
                settings=settings,
                config_hash=config_hash,
                frames=frames,
                identity=identity,
                limitations=_limitations(profile, identity),
                warnings=problems,
                existing=existing,
            )
        )

    def _refusals(
        self,
        request: RenderRequest,
        profile: RenderProfile,
        view: TimelineView,
        ref: TimelineRef,
        identity: BackendIdentity,
    ) -> list[str]:
        reasons: list[str] = []
        kind = request.render_type
        if profile.purpose not in ALLOWED_PURPOSES[kind]:
            wanted = " or ".join(p.value for p in ALLOWED_PURPOSES[kind])
            reasons.append(
                f"a {kind.value} render uses a {wanted} profile; '{profile.profile_id}' is "
                f"{profile.purpose.value}"
            )
        if profile.resolve_profile not in identity.capabilities.profiles:
            reasons.append(
                f"the {identity.name} backend cannot realise profile '{profile.profile_id}'"
            )
        if kind is RenderType.FINAL:
            if request.authorization is None:
                reasons.append("a final render needs the authorization of a person for this export")
            elif not request.authorization.timeline.same_content(ref):
                reasons.append(
                    f"the timeline changed since the export was authorized (authorized "
                    f"version {request.authorization.timeline.version}, now {ref.version})"
                )
            if request.overwrite:
                reasons.append("a final render never overwrites an earlier output")
            if profile.allow_proxy_sources:
                reasons.append(f"profile '{profile.profile_id}' may render from proxies")
            reasons += list(profile_problems(profile, width=view.width, height=view.height))
        if kind is RenderType.SECTION_PREVIEW and not (
            request.section_id or request.start_frame is not None
        ):
            reasons.append("a section preview names a section or a frame range")
        return reasons

    def _authorize(
        self, authorization: RenderAuthorization, profile: RenderProfile, config_hash: str
    ) -> Result[object, RenderFailure]:
        checked = self._sessions.verify_export(
            authorization.session_id,
            authorization.authorization_id,
            timeline=authorization.timeline,
            export_profile_id=profile.profile_id,
            output_config_hash=config_hash,
        )
        if isinstance(checked, Err):
            return checked
        return Ok(checked.value)

    def _reusable(
        self, anchor_asset_id: str, key: str
    ) -> tuple[RenderReport, MediaAssetDto] | None:
        for report, asset in self._store.reports(anchor_asset_id, job_key=key):
            if not (report.succeeded and not report.reused):
                continue
            output = self._library.get(report.output_asset_id)
            if isinstance(output, Ok):
                return report, asset
        return None

    # --- render ---------------------------------------------------------------------------------
    def render(
        self, request: RenderRequest, ctx: JobContext
    ) -> Result[RenderOutcome, RenderFailure]:
        planned = self.plan(request)
        if isinstance(planned, Err):
            return planned
        return self.execute(planned.value, ctx)

    def execute(
        self, planned: PlannedRender, ctx: JobContext
    ) -> Result[RenderOutcome, RenderFailure]:
        request = planned.request
        if planned.existing is not None:
            report, asset = planned.existing
            output = self._library.get(report.output_asset_id)
            _log.info("Stored render served", render_id=planned.render_id)
            return Ok(
                RenderOutcome(
                    report, asset, output.value if isinstance(output, Ok) else None, reused=True
                )
            )
        if request.render_type is RenderType.FINAL:
            # The authorization is asked again now: it may have been withdrawn while queued.
            if request.authorization is None:
                return Err(RenderRefused(["a final render needs an export authorization"]))
            checked = self._authorize(request.authorization, planned.profile, planned.config_hash)
            if isinstance(checked, Err):
                return checked
        attempt = len(self._store.reports(request.anchor_asset_id, render_id=planned.render_id)) + 1
        token = _Linked(ctx.cancellation)
        self._tokens[planned.render_id] = token
        self._status[planned.render_id] = RenderStatus(planned.render_id, JobState.RUNNING, 0.0)
        run_ctx = JobContext(ctx.job_id, token, _Tracking(ctx, self._status, planned.render_id))
        started = self._clock.now()
        before = self._disk.free_bytes(self._paths.data_dir)
        errors: list[str] = []
        state = JobState.FAILED
        result = BackendResult("failed", None)
        try:
            run_ctx.raise_if_cancelled()
            result = self._carry_out(planned, attempt, run_ctx)
            state = {
                "completed": JobState.COMPLETED,
                "cancelled": JobState.CANCELLED,
            }.get(result.state, JobState.FAILED)
        except OperationCancelledError:
            state = JobState.CANCELLED
            errors.append("the render was cancelled")
        except RenderManagerError as exc:
            errors.append(exc.user_message)
        finally:
            self._tokens.pop(planned.render_id, None)
        seconds = (self._clock.now() - started).total_seconds()
        usage = ResourceUsage(seconds, before, self._disk.free_bytes(self._paths.data_dir))
        report = self._report(planned, attempt, state, result, errors, usage, ctx)
        stored = self._store.save(report)
        if isinstance(stored, Err):
            return stored
        self._status[planned.render_id] = RenderStatus(
            planned.render_id, report.status, 100.0 if report.succeeded else 0.0
        )
        self._tell_session(planned, report)
        self._events.publish(
            RenderFinished(
                occurred_at=self._clock.now(),
                render_id=report.render_id,
                report_id=report.report_id,
                render_type=report.render_type.value,
                timeline_version=report.timeline_version,
                status=report.status.value,
                succeeded=report.succeeded,
            )
        )
        _log.info(
            "Render finished",
            render_id=report.render_id,
            status=report.status.value,
            verification=report.verification_status.value,
        )
        return Ok(RenderOutcome(report, stored.value, result.output, reused=result.reused))

    def _carry_out(self, planned: PlannedRender, attempt: int, ctx: JobContext) -> BackendResult:
        request, view = planned.request, planned.view
        name = output_name(
            planned.profile.name_pattern,
            timeline=view.timeline_id,
            version=view.version,
            render_type=request.render_type.value,
            profile=planned.profile.profile_id,
            n=attempt,
        )
        caps = planned.identity.capabilities
        if request.render_type is RenderType.SECTION_PREVIEW and not caps.range_render:
            return self._cut_section(planned, ctx)
        return self._backend.render(
            BackendRequest(
                anchor_asset_id=request.anchor_asset_id,
                timeline_id=view.timeline_id,
                native_profile=planned.profile.resolve_profile,
                file_name=name,
                require_final_quality=request.render_type is RenderType.FINAL
                or not planned.profile.allow_proxy_sources,
                reuse=not request.overwrite,
                start_frame=planned.frames.start_frame if planned.frames else None,
                end_frame=planned.frames.end_frame if planned.frames else None,
            ),
            ctx,
        )

    def _cut_section(self, planned: PlannedRender, ctx: JobContext) -> BackendResult:
        """A section preview from a whole review render (rendered first if there is none)."""
        request, view, frames = planned.request, planned.view, planned.frames
        if frames is None:
            return BackendResult("failed", None, errors=("a section preview needs a frame range",))
        source = self._full_review(planned, ctx)
        if isinstance(source, str):
            return BackendResult("failed", None, errors=(source,))
        path = self._library.local_path(source.id)
        if isinstance(path, Err):
            return BackendResult("failed", None, errors=(path.error.user_message,))
        start = seconds_of(frames.start_frame, view.frame_rate)
        end = seconds_of(frames.end_frame, view.frame_rate)
        with self._paths.temporary_directory(prefix="section-preview-") as tmp:
            target = (
                tmp
                / f"{view.timeline_id}_v{view.version}_{frames.start_frame}-{frames.end_frame}.mp4"
            )
            self._cutter.cut(
                path.value,
                start_seconds=float(start),
                end_seconds=float(end),
                destination=target,
                ctx=ctx,
            )
            registered = self._library.register_derived(
                request.anchor_asset_id,
                target,
                operation=SECTION_OPERATION,
                config={
                    "source_render": source.id,
                    "start_frame": frames.start_frame,
                    "end_frame": frames.end_frame,
                    "profile": f"{planned.profile.profile_id}@{planned.profile.version}",
                    "job_key": planned.job_key,
                },
                display_name=(
                    f"{view.timeline_id} v{view.version} "
                    f"frames {frames.start_frame}-{frames.end_frame}"
                ),
                metadata={
                    "render_id": planned.render_id,
                    "timeline_version": view.version,
                },
            )
        if isinstance(registered, Err):
            return BackendResult("failed", None, errors=(registered.error.user_message,))
        return BackendResult("completed", registered.value.asset)

    def _full_review(self, planned: PlannedRender, ctx: JobContext) -> MediaAssetDto | str:
        request = planned.request
        stored = self._store.reports(
            request.anchor_asset_id,
            timeline_id=planned.view.timeline_id,
            content_hash=planned.view.content_hash,
            render_type=RenderType.REVIEW.value,
        )
        for report, _ in stored:
            if report.succeeded and report.render_profile_id == planned.profile.profile_id:
                found = self._library.get(report.output_asset_id)
                if isinstance(found, Ok):
                    return found.value
        made = self.render(
            replace(
                request,
                render_type=RenderType.REVIEW,
                section_id="",
                start_frame=None,
                end_frame=None,
                padding_seconds=0.0,
            ),
            ctx,
        )
        if isinstance(made, Err):
            return made.error.user_message
        if not made.value.succeeded or made.value.output is None:
            return "the whole-timeline review render this preview is cut from did not succeed"
        return made.value.output

    # --- the report -----------------------------------------------------------------------------
    def _report(
        self,
        planned: PlannedRender,
        attempt: int,
        state: JobState,
        result: BackendResult,
        errors: list[str],
        usage: ResourceUsage,
        ctx: JobContext,
    ) -> RenderReport:
        request, view, settings = planned.request, planned.view, planned.settings
        output = result.output
        checks: tuple[Check, ...] = ()
        facts: OutputFacts | None = None
        verification = Verification.NOT_VERIFIED
        if state is JobState.COMPLETED and output is None:
            state = JobState.FAILED
            errors.append("the backend reported success but produced no file")
        if state is JobState.COMPLETED and output is not None:
            checks, facts = self._check_file(planned, output, ctx)
            verification = overall(checks)
        all_errors = (*result.errors, *errors)
        if state is JobState.FAILED and not all_errors:
            all_errors = ("the render did not complete",)
        report = RenderReport(
            report_id=new_id(),
            render_id=planned.render_id,
            job_key=planned.job_key,
            attempt=attempt,
            anchor_asset_id=request.anchor_asset_id,
            project_id=view.project_id,
            timeline_id=view.timeline_id,
            timeline_version=view.version,
            timeline_content_hash=view.content_hash,
            review_session_id=request.review_session_id,
            request=RequestRecord(
                request.render_type,
                planned.profile.profile_id,
                request.anchor_asset_id,
                request.review_session_id,
                request.section_id,
                request.start_frame,
                request.end_frame,
                request.padding_seconds,
                request.reference_render_id,
                request.priority,
            ),
            render_profile_id=planned.profile.profile_id,
            render_profile_version=planned.profile.version,
            output_config_hash=planned.config_hash,
            render_type=request.render_type,
            render_backend=planned.identity.name,
            render_backend_version=planned.identity.version,
            status=state,
            progress=100.0 if state is JobState.COMPLETED else 0.0,
            created_at=self._clock.now(),
            resource_usage=usage,
            verification_status=verification,
            output_asset_id=output.id if output is not None and state is JobState.COMPLETED else "",
            output_name=output.display_name if output is not None else "",
            codec=facts.video_codec or "" if facts else "",
            container=output.extension.lstrip(".") if output is not None else "",
            width=facts.width if facts else None,
            height=facts.height if facts else None,
            frame_rate=facts.frame_rate if facts else None,
            audio=(
                AudioConfiguration(
                    facts.audio_codec or "", facts.sample_rate or 0, facts.channels or 0
                )
                if facts and facts.has_audio
                else None
            ),
            file_size=output.file_size if output is not None else 0,
            start_frame=planned.frames.start_frame if planned.frames else None,
            end_frame=planned.frames.end_frame if planned.frames else None,
            reused=result.reused,
            authorization_id=request.authorization.authorization_id
            if request.authorization
            else "",
            checks=checks,
            limitations=planned.limitations,
            errors=all_errors,
            warnings=(*planned.warnings, *result.warnings),
            inputs=(
                ("timeline", f"{view.timeline_id}@{view.version}"),
                ("profile", f"{planned.profile.profile_id}@{planned.profile.version}"),
                ("backend", f"{planned.identity.name} {planned.identity.version}"),
                ("settings", settings.profile_id),
            ),
        )
        return with_content_hash(report)

    def _inspect(self, asset: MediaAssetDto, ctx: JobContext) -> InspectionResult | None:
        """The inspection of a rendered file, or ``None`` if it cannot be had (never a crash:
        the file is then reported as not verified)."""
        try:
            inspected = self._inspector.execute(
                InspectMediaCommand(asset.id, InspectionConfig(depth=Depth.PROBE)), ctx
            )
        except MediaHouseError as exc:
            _log.warning("A render could not be inspected", asset_id=asset.id, reason=str(exc))
            return None
        return inspected.value if isinstance(inspected, Ok) else None

    def _check_file(
        self, planned: PlannedRender, output: MediaAssetDto, ctx: JobContext
    ) -> tuple[tuple[Check, ...], OutputFacts]:
        view, settings, frames = planned.view, planned.settings, planned.frames
        duration_frames = frames.frames if frames else view.duration_frames
        applies = planned.identity.capabilities.applies
        expectation = Expectation(
            width=settings.width,
            height=settings.height,
            frame_rate=settings.frame_rate,
            duration=float(seconds_of(duration_frames, settings.frame_rate)),
            video_codec=settings.video.codec,
            container=settings.video.container,
            pixel_format=settings.video.pixel_format,
            audio_required=settings.audio_required and view.has_sound,
            audio_codec=settings.audio.codec,
            sample_rate=settings.audio.sample_rate,
            channels=settings.audio.channels,
            not_applied=tuple(
                name
                for name, key in (
                    ("codec", "codec"),
                    ("pixel_format", "pixel_format"),
                    ("audio_codec", "audio_codec"),
                )
                if key not in applies
            ),
        )
        facts = output_facts(output, self._inspect(output, ctx))
        return verify_output(facts, expectation), facts

    def _tell_session(self, planned: PlannedRender, report: RenderReport) -> None:
        session_id = planned.request.review_session_id
        if not session_id:
            return
        attached = self._sessions.attach(
            session_id,
            ArtifactKind.RENDER_REPORT,
            report.report_id,
            timeline=planned.ref,
            actor=Actor.system("render_manager"),
        )
        if isinstance(attached, Err):
            _log.warning("Render report not recorded in the review", reason=str(attached.error))

    # --- control --------------------------------------------------------------------------------
    def status(self, render_id: str) -> Result[RenderStatus, RenderManagerError]:
        known = self._status.get(render_id)
        if known is None:
            return Err(UnknownRender(render_id))
        return Ok(known)

    def cancel(self, render_id: str) -> Result[RenderStatus, RenderManagerError]:
        token = self._tokens.get(render_id)
        if token is None:
            return Err(UnknownRender(render_id))
        token.cancel()
        self._status[render_id] = RenderStatus(render_id, JobState.CANCELLED, 0.0, "cancelling")
        return Ok(self._status[render_id])

    def pause(self, render_id: str) -> Result[RenderStatus, RenderManagerError]:
        return self._pause_or_resume(render_id, pause=True)

    def resume(self, render_id: str) -> Result[RenderStatus, RenderManagerError]:
        return self._pause_or_resume(render_id, pause=False)

    def _pause_or_resume(
        self, render_id: str, *, pause: bool
    ) -> Result[RenderStatus, RenderManagerError]:
        verb = "pause" if pause else "resume"
        if render_id not in self._tokens:
            return Err(UnknownRender(render_id))
        identity = self._backend.identity()
        if not identity.capabilities.pause_resume:
            return Err(NotControllable(verb, f"the {identity.name} backend cannot {verb} a render"))
        done = self._backend.pause() if pause else self._backend.resume()
        if not done:
            return Err(NotControllable(verb, f"the {identity.name} backend refused"))
        state = JobState.PAUSED if pause else JobState.RUNNING
        previous = self._status.get(render_id)
        self._status[render_id] = RenderStatus(
            render_id, state, previous.progress if previous else 0.0
        )
        return Ok(self._status[render_id])

    def retry(
        self,
        anchor_asset_id: str,
        render_id: str,
        ctx: JobContext,
        *,
        authorization: RenderAuthorization | None = None,
    ) -> Result[RenderOutcome, RenderFailure]:
        """Render again what failed or was cancelled (or whose file failed its checks)."""
        history = self._store.reports(anchor_asset_id, render_id=render_id)
        if not history:
            return Err(UnknownRender(render_id))
        last = history[0][0]
        if last.succeeded:
            return Err(RenderRefused([f"render {render_id} already succeeded; nothing to retry"]))
        r = last.request
        return self.render(
            RenderRequest(
                anchor_asset_id=r.anchor_asset_id,
                render_type=r.render_type,
                profile_id=r.profile_id,
                timeline_id=last.timeline_id,
                review_session_id=r.review_session_id,
                section_id=r.section_id,
                start_frame=r.start_frame,
                end_frame=r.end_frame,
                padding_seconds=r.padding_seconds,
                authorization=authorization,
                reference_render_id=r.reference_render_id,
                priority=r.priority,
            ),
            ctx,
        )


# --- pure helpers ------------------------------------------------------------------------------


def _frames(
    request: RenderRequest, view: TimelineView, identity: BackendIdentity
) -> tuple[FrameRange | None, list[str]]:
    """The stretch to render (with its padding), or ``None`` for the whole timeline."""
    wanted: tuple[int, int] | None = None
    if request.section_id:
        found = next((s for s in view.sections if s[0] == request.section_id), None)
        if found is None:
            return None, [f"the timeline has no section '{request.section_id}'"]
        wanted = (found[1], found[2])
    elif request.start_frame is not None and request.end_frame is not None:
        wanted = (request.start_frame, request.end_frame)
    elif request.start_frame is not None or request.end_frame is not None:
        return None, ["a frame range needs a start and an end"]
    if wanted is None:
        return None, []
    pad = round(request.padding_seconds * view.frame_rate.value)
    start, end = max(0, wanted[0] - pad), min(view.duration_frames, wanted[1] + pad)
    if end <= start or wanted[0] < 0 or wanted[1] > view.duration_frames:
        return None, [f"frames {wanted[0]}-{wanted[1]} are outside the timeline"]
    if (
        request.render_type is not RenderType.SECTION_PREVIEW
        and not identity.capabilities.range_render
    ):
        return None, [f"the {identity.name} backend renders the whole timeline only"]
    return FrameRange(start, end), []


def _limitations(profile: RenderProfile, identity: BackendIdentity) -> tuple[Limitation, ...]:
    """Settings the profile asks for that the backend does not apply (reported, never dropped)."""
    applies = identity.capabilities.applies
    v, a = profile.video, profile.audio
    wanted = {
        "codec": f"video codec {v.codec}",
        "pixel_format": f"pixel format {v.pixel_format}",
        "audio_codec": f"audio codec {a.codec}",
        "bitrate": f"video bitrate {v.bitrate_kbps} kbps" if v.bitrate_kbps else "",
        "audio_bitrate": f"audio bitrate {a.bitrate_kbps} kbps" if a.bitrate_kbps else "",
        "sample_rate": f"audio sample rate {a.sample_rate} Hz",
        "channels": f"{a.channels} audio channels",
        "color": f"colour space {v.color}",
        "size": f"{v.width}x{v.height}" if v.width else "",
        "metadata": ", ".join(f"{k}={val}" for k, val in profile.metadata),
    }
    return tuple(
        Limitation(name, text, f"the {identity.name} backend does not set this")
        for name, text in wanted.items()
        if text and name not in applies
    )
