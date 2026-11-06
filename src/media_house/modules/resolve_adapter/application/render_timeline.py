"""Render the bound timeline with Resolve and check what came out.

A render job that reports ``Complete`` is not a deliverable. Before it starts the project is
checked for what would make the file wrong (an open conflict, a pool item that is not the
intended file, nothing on the timeline); after it ends the file is registered in the Media Library
as a derived asset of the recording (never overwriting an earlier one) and inspected by the
inspection module, then compared with what the timeline promises.

An unchanged timeline (same build, same live state of every clip, same profile) is not rendered
again: the stored deliverable is verified and returned.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction

from media_house.core.application.ports import Clock
from media_house.core.domain import Rational, json_codec
from media_house.modules.media_inspection.application.contracts import (
    InspectMediaCommand,
    MediaInspection,
    MediaInspector,
)
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaLibrary,
)
from media_house.modules.resolve_adapter.application.execute_timeline import ExecuteError
from media_house.modules.resolve_adapter.application.ledger import Ledger
from media_house.modules.resolve_adapter.application.ports import ResolveBackend
from media_house.modules.resolve_adapter.application.reporting import make_report
from media_house.modules.resolve_adapter.application.session import open_bound
from media_house.modules.resolve_adapter.application.store import ResolveStore
from media_house.modules.resolve_adapter.domain.binding import SyncState
from media_house.modules.resolve_adapter.domain.config import (
    RenderConfig,
    RenderProfile,
    ResolveAdapterConfig,
    render_profile,
)
from media_house.modules.resolve_adapter.domain.errors import (
    NothingBound,
    RenderBlocked,
    ResolveAdapterError,
    ResolveConnectionLost,
    ResolveUnavailable,
)
from media_house.modules.resolve_adapter.domain.live import RenderStatus, TimelineSnapshot
from media_house.modules.resolve_adapter.domain.render import (
    OutputExpectation,
    OutputFacts,
    check_output,
    render_settings,
)
from media_house.modules.resolve_adapter.domain.report import (
    CheckRecord,
    ExecutionReport,
    RenderRecord,
    SyncRecord,
)
from media_house.modules.resolve_adapter.domain.values import (
    ADAPTER_VERSION,
    RENDER_OPERATION,
    Area,
    ExecutionState,
    OperationStatus,
    Resolution,
    Strategy,
    Verification,
)
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, OperationCancelledError, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger
from media_house.shared.types import new_id

_log = get_logger(__name__)
_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


@dataclass(frozen=True, slots=True)
class RenderCommand:
    anchor_asset_id: str
    timeline_id: str | None = None
    config: ResolveAdapterConfig = field(default_factory=ResolveAdapterConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    #: Verify and return the stored deliverable of an identical timeline instead of rendering.
    reuse: bool = True


@dataclass(frozen=True, slots=True)
class RenderResult:
    report: ExecutionReport
    report_asset: MediaAssetDto
    output: MediaAssetDto | None
    reused: bool


class RenderTimeline:
    def __init__(
        self,
        backend: ResolveBackend,
        library: MediaLibrary,
        catalog: BuiltTimelineCatalog,
        inspector: MediaInspector,
        store: ResolveStore,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._backend = backend
        self._library = library
        self._catalog = catalog
        self._inspector = inspector
        self._store = store
        self._paths = paths
        self._clock = clock

    def execute(
        self, command: RenderCommand, ctx: JobContext
    ) -> Result[RenderResult, ExecuteError]:
        anchor = self._library.get(command.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        latest = self._catalog.latest(command.anchor_asset_id, timeline_id=command.timeline_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(NothingBound())
        timeline = latest.value[0]
        bound = self._store.latest_binding(command.anchor_asset_id, timeline.timeline_id)
        if isinstance(bound, Err):
            return bound
        if bound.value is None:
            return Err(NothingBound())
        state = bound.value[0]
        status = self._backend.connect()
        if not status.connected:
            return Err(ResolveUnavailable(status.state.value, status.detail))

        started = self._clock.now()
        ledger = Ledger()
        ledger.enter(ExecutionState.INITIALIZING)
        record: RenderRecord | None = None
        output: MediaAssetDto | None = None
        reused = False
        cancelled: OperationCancelledError | None = None
        try:
            with self._backend.exclusive():
                record, output, reused = self._run(
                    command, anchor.value, timeline, state, ledger, ctx
                )
        except OperationCancelledError as exc:
            self._backend.stop_render()
            ledger.enter(ExecutionState.CANCELLED)
            cancelled = exc
        except ResolveConnectionLost as exc:
            ledger.fail(str(exc))
            ledger.enter(ExecutionState.INTERRUPTED)
        except ResolveAdapterError as exc:
            ledger.fail(str(exc))
            ledger.enter(ExecutionState.FAILED)
        report = make_report(
            ledger=ledger,
            status=status,
            execution_id=new_id(),
            started=started,
            finished=self._clock.now(),
            anchor_asset_id=command.anchor_asset_id,
            timeline_id=timeline.timeline_id,
            project_id=timeline.project_id,
            build_version=timeline.build.build_version,
            build_hash=timeline.build.build_hash,
            schema=timeline.schema_version,
            policy=command.config.policy,
            config_hash=json_codec.digest((command.config, command.render)),
            project_name=state.project.project_name,
            project_resolve_id=state.project.project_id or "",
            database=state.project.database or "",
            timeline_name=state.project.timeline_name,
            timeline_resolve_id=state.project.timeline_resolve_id or "",
            capabilities=(),
            media=(),
            sync=SyncRecord("render", conflicts=state.conflicts),
            render=record,
        )
        stored = self._store.save_report(anchor.value, report)
        if isinstance(stored, Err):
            return stored
        if cancelled is not None:
            raise cancelled
        _log.info("Resolve render finished", outcome=report.outcome.value, reused=reused)
        return Ok(RenderResult(report, stored.value, output, reused))

    # ---------------------------------------------------------------------------------------
    def _run(
        self,
        command: RenderCommand,
        anchor: MediaAssetDto,
        timeline: BuiltTimeline,
        state: SyncState,
        ledger: Ledger,
        ctx: JobContext,
    ) -> tuple[RenderRecord, MediaAssetDto | None, bool]:
        profile = render_profile(command.render.profile)
        snapshot = open_bound(self._backend, state, command.config, ledger)
        problems = self._guard(state, snapshot)
        if problems:
            for problem in problems:
                ledger.fail(problem)
            if command.render.require_final_quality:
                raise RenderBlocked(problems)
        if command.config.save:
            self._backend.save_project()
        ledger.enter(ExecutionState.READY_TO_RENDER)

        key = {
            "timeline_id": timeline.timeline_id,
            "build_hash": timeline.build.build_hash,
            "profile": profile.key,
            "live": json_codec.digest(snapshot.items),
        }
        fingerprint = self._library.fingerprint(anchor.id, RENDER_OPERATION, key, ADAPTER_VERSION)
        if isinstance(fingerprint, Err):
            raise ResolveAdapterError(str(fingerprint.error))
        existing = (
            self._library.find_derived_asset(anchor.id, fingerprint.value)
            if command.reuse
            else None
        )
        expect = OutputExpectation(
            width=timeline.settings.width,
            height=timeline.settings.height,
            frame_rate=timeline.settings.frame_rate,
            duration_frames=snapshot.duration,
            audio_required=any(i.track_kind == "audio" for i in snapshot.items),
        )
        if existing is not None:
            checks = self._verify(existing, profile, expect, ctx)
            ledger.checks.extend(checks)
            ledger.op(
                "render",
                Area.RENDER,
                "reuse the stored render",
                OperationStatus.SKIPPED,
                verification=_overall(checks),
                detail="the timeline is unchanged since this render",
                ref=existing.id,
            )
            return (
                RenderRecord(
                    profile.key,
                    "",
                    "reused",
                    self._path(existing),
                    existing.id,
                    100.0,
                    None,
                    "",
                    checks,
                ),
                existing,
                True,
            )
        return self._render(command, anchor, timeline, snapshot, profile, expect, key, ledger, ctx)

    def _render(
        self,
        command: RenderCommand,
        anchor: MediaAssetDto,
        timeline: BuiltTimeline,
        snapshot: TimelineSnapshot,
        profile: RenderProfile,
        expect: OutputExpectation,
        key: Mapping[str, object],
        ledger: Ledger,
        ctx: JobContext,
    ) -> tuple[RenderRecord, MediaAssetDto | None, bool]:
        formats = {i for _, i in self._backend.render_options(None).formats}
        format_id = next((f for f in profile.formats if f in formats), None)
        if format_id is None:
            raise RenderBlocked([f"this Resolve cannot render {', '.join(profile.formats)}"])
        codecs = {i for _, i in self._backend.render_options(format_id).codecs}
        codec_id = next((c for c in profile.codecs if c in codecs), None)
        if codec_id is None:
            raise RenderBlocked([f"no codec of {', '.join(profile.codecs)} for {format_id}"])
        name = _UNSAFE.sub("_", command.render.file_name or timeline.timeline_id)[:80]
        with self._paths.temporary_directory(prefix="resolve-render-") as tmp:
            settings = render_settings(
                profile,
                width=expect.width,
                height=expect.height,
                frame_rate=timeline.settings.frame_rate,
                target_dir=str(tmp),
                file_name=name,
            )
            applied = self._backend.configure_render(format_id, codec_id, settings)
            now = self._backend.render_options(format_id)
            ok = (now.current_format, now.current_codec) == (format_id, codec_id) and all(
                applied.values()
            )
            ledger.op(
                "render",
                Area.RENDER,
                "configure the render",
                OperationStatus.EXECUTED if ok else OperationStatus.FAILED,
                strategy=Strategy.NATIVE_API,
                verification=Verification.VERIFIED if ok else Verification.FAILED,
                detail=f"{format_id}/{codec_id}, {expect.width}x{expect.height}",
            )
            if not ok:
                raise RenderBlocked(["Resolve did not accept the render format and codec"])
            ledger.confirm("render.configure")
            ledger.enter(ExecutionState.RENDERING)
            job_id = self._backend.submit_render()
            ledger.confirm("render.submit")
            ledger.op(
                "render", Area.RENDER, "start the render", OperationStatus.EXECUTED, ref=job_id
            )
            final = self._wait(job_id, command.render, ctx)
            ledger.confirm("render.monitor")
            if final.status.lower() != "complete":
                ledger.fail(f"the render ended as {final.status}: {final.error or 'no message'}")
                ledger.op(
                    "render",
                    Area.RENDER,
                    "render",
                    OperationStatus.FAILED,
                    verification=Verification.FAILED,
                    detail=final.error,
                    ref=job_id,
                )
                return (
                    RenderRecord(
                        profile.key,
                        job_id,
                        final.status.lower(),
                        "",
                        None,
                        final.percent,
                        final.seconds,
                        final.error,
                    ),
                    None,
                    False,
                )
            ledger.enter(ExecutionState.VERIFYING_RENDER)
            produced = sorted(
                p for p in tmp.iterdir() if p.suffix.lower() == f".{profile.extension}"
            )
            if not produced:
                ledger.fail("Resolve reported a complete render, but no output file exists")
                missing = (
                    CheckRecord(
                        Area.RENDER,
                        "file exists",
                        Verification.FAILED,
                        "the output file is missing",
                    ),
                )
                ledger.checks.extend(missing)
                return (
                    RenderRecord(
                        profile.key,
                        job_id,
                        "complete",
                        "",
                        None,
                        final.percent,
                        final.seconds,
                        "no output file",
                        missing,
                    ),
                    None,
                    False,
                )
            registered = self._library.register_derived(
                anchor.id,
                produced[0],
                operation=RENDER_OPERATION,
                config=json_codec.encode(key),
                version=ADAPTER_VERSION,
                display_name=f"{anchor.display_name} (Resolve render, {profile.key})",
                metadata={"timeline_id": timeline.timeline_id, "profile": profile.key},
            )
            if isinstance(registered, Err):
                raise ResolveAdapterError(f"the render could not be stored: {registered.error}")
            asset = registered.value.asset
        checks = self._verify(asset, profile, expect, ctx)
        ledger.checks.extend(checks)
        ledger.op(
            "render",
            Area.RENDER,
            "render",
            OperationStatus.EXECUTED,
            verification=_overall(checks),
            detail=f"{profile.key} · {asset.display_name}",
            ref=asset.id,
        )
        return (
            RenderRecord(
                profile.key,
                job_id,
                "complete",
                self._path(asset),
                asset.id,
                final.percent,
                final.seconds,
                "",
                checks,
            ),
            asset,
            False,
        )

    def _wait(self, job_id: str, config: RenderConfig, ctx: JobContext) -> RenderStatus:
        started = self._clock.now()
        while True:
            ctx.raise_if_cancelled()
            status = self._backend.render_status(job_id)
            ctx.progress.report(int(status.percent or 0), 100, f"Resolve render: {status.status}")
            if status.finished:
                return status
            if (self._clock.now() - started).total_seconds() > config.timeout_seconds:
                self._backend.stop_render()
                raise RenderBlocked(
                    [f"the render did not finish within {config.timeout_seconds:.0f}s"]
                )
            ctx.cancellation.wait(config.poll_seconds)

    def _verify(
        self,
        asset: MediaAssetDto,
        profile: RenderProfile,
        expect: OutputExpectation,
        ctx: JobContext,
    ) -> tuple[CheckRecord, ...]:
        path = self._library.local_path(asset.id)
        exists = not isinstance(path, Err) and path.value.is_file()
        size = path.value.stat().st_size if exists and not isinstance(path, Err) else 0
        inspected = self._inspector.execute(InspectMediaCommand(asset.id), ctx)
        if isinstance(inspected, Err):
            facts = OutputFacts(exists, size, False, None, None, None, None, None, None, 0)
        else:
            facts = _facts(exists, size, inspected.value.inspection)
        return check_output(profile, expect, facts)

    def _path(self, asset: MediaAssetDto) -> str:
        path = self._library.local_path(asset.id)
        return "" if isinstance(path, Err) else str(path.value)

    def _guard(self, state: SyncState, snapshot: TimelineSnapshot) -> list[str]:
        """What would make the deliverable wrong, found before anything is rendered."""
        problems: list[str] = []
        if not snapshot.items:
            problems.append("the Resolve timeline is empty")
        open_conflicts = [c for c in state.conflicts if c.resolution is Resolution.OPEN]
        if open_conflicts:
            problems.append(
                f"{len(open_conflicts)} conflict(s) between Media House and Resolve are open"
            )
        pool = {m.pool_item_id: m for m in state.media}
        pool_now = {p.item_id: p for p in self._backend.pool_items()}
        seen: set[str] = set()
        for item in snapshot.items:
            media_id = item.media_item_id
            if media_id is None or media_id in seen:
                continue
            seen.add(media_id)
            live = pool_now.get(media_id)
            bound = pool.get(media_id)
            if live is None:
                problems.append(f"{item.name}: its media is not in the media pool")
            elif live.offline:
                problems.append(f"{live.name} is offline")
            elif bound is not None:
                wanted = self._library.local_path(bound.asset_id)
                if isinstance(wanted, Err):
                    problems.append(f"{live.name}: the library no longer has the file")
                elif (
                    live.path.replace("\\", "/").casefold()
                    != str(wanted.value).replace("\\", "/").casefold()
                ):
                    problems.append(f"{live.name} is not the intended file (it is not the source)")
        return problems


def _overall(checks: tuple[CheckRecord, ...]) -> Verification:
    if any(c.state is Verification.FAILED for c in checks):
        return Verification.FAILED
    if any(c.state is Verification.NOT_VERIFIABLE for c in checks):
        return Verification.PARTIALLY_VERIFIED
    return Verification.VERIFIED


def _facts(exists: bool, size: int, inspection: MediaInspection) -> OutputFacts:
    video = inspection.primary_video
    audio = inspection.audio_stream(0)
    duration = inspection.container.duration
    frame_rate = video.frame_rate.value if video else None
    return OutputFacts(
        exists=exists,
        size_bytes=inspection.container.size_bytes or size,
        readable=inspection.status.usable,
        video_codec=video.codec if video else None,
        audio_codec=audio.codec if audio else None,
        width=video.geometry.width if video else None,
        height=video.geometry.height if video else None,
        frame_rate=frame_rate,
        duration=_seconds(duration),
        audio_streams=len(inspection.audio_streams),
    )


def _seconds(value: float | None) -> Rational | None:
    if value is None:
        return None
    f = Fraction(value).limit_denominator(1000)
    return Rational(f.numerator, f.denominator)
