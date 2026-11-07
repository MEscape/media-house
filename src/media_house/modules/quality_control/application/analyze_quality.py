"""Quality control of one timeline: look at it, look at its preview, report what is wrong.

It reads, it measures, it reports. It changes no timeline, decides nothing creative and approves
nothing: the report says what was found with the evidence, and a person decides what happens next.
Every optional input (the specification, the mix result, the continuity analysis, the story plan,
the Resolve binding, the preview) improves the report when present and is named as "not run" when
it is not; a measurement that fails costs only itself.
"""

from dataclasses import dataclass

from media_house.core.application.ports import Clock, MediaAnalyzer
from media_house.modules.audio_mix_engine.application.contracts import MixCatalog
from media_house.modules.continuity_intelligence.application.contracts import ContinuityCatalog
from media_house.modules.edit_plan_compiler.application.contracts import TimelineCatalog
from media_house.modules.media_inspection.application.contracts import MediaInspector
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.quality_control.application.events import QualityControlCompleted
from media_house.modules.quality_control.application.evidence import EvidenceRegistrar
from media_house.modules.quality_control.application.measure import PreviewMeasurer
from media_house.modules.quality_control.application.ports import LiveChanges, LiveTimelineProbe
from media_house.modules.quality_control.application.store import QualityStore
from media_house.modules.quality_control.application.views import (
    continuity_facts,
    mix_facts,
    resolve_facts,
    story_facts,
    timeline_facts,
)
from media_house.modules.quality_control.domain import facts as f
from media_house.modules.quality_control.domain.checks_editing import check_edit
from media_house.modules.quality_control.domain.checks_external import (
    carry_continuity,
    carry_mix,
    compare_story,
)
from media_house.modules.quality_control.domain.checks_render import run_render_checks
from media_house.modules.quality_control.domain.checks_structure import run_structure_checks
from media_house.modules.quality_control.domain.checks_text import check_captions, check_graphics
from media_house.modules.quality_control.domain.compare import ReportComparison, compare_reports
from media_house.modules.quality_control.domain.errors import NothingToAnalyze, QualityControlError
from media_house.modules.quality_control.domain.measurements import RenderMeasurements
from media_house.modules.quality_control.domain.model import (
    CheckRecord,
    QualityIssue,
    QualityReport,
)
from media_house.modules.quality_control.domain.profile import (
    QualityProfile,
    profile_for,
    with_delivery,
)
from media_house.modules.quality_control.domain.report import assemble, report_identity
from media_house.modules.quality_control.domain.values import QC_VERSION, Area, CheckState
from media_house.modules.resolve_adapter.application.contracts import ResolveCatalog
from media_house.modules.review_orchestrator.application.contracts import (
    Actor,
    ArtifactKind,
    ReviewSessions,
    ReviewState,
    TimelineRef,
)
from media_house.modules.story_architecture.application.contracts import StoryCatalog
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_ACTOR = Actor.system("quality_control")

type QualityError = QualityControlError | MediaError | StaleStep


class StaleStep(QualityControlError):
    """A review step the session's state or version does not allow."""

    code = "quality_control.review_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(reason, user_message=reason)


@dataclass(frozen=True, slots=True)
class AnalyzeQualityCommand:
    anchor_asset_id: str
    timeline_id: str | None = None
    #: A rendered preview of the timeline (a library asset). Without one only the timeline itself
    #: is checked and the checks that need pictures and sound are reported as not run.
    preview_asset_id: str | None = None
    #: The content hash of the timeline version the preview was rendered from. When given and not
    #: the hash of the timeline analysed, the analysis is refused: a preview of another version
    #: would be presented as evidence of this one.
    preview_timeline_hash: str | None = None
    review_session_id: str | None = None
    profile: str | None = None
    #: Also look at the live timeline in the editing application (never records anything).
    check_live: bool = False
    #: Return the stored report of identical inputs instead of analysing again.
    reuse: bool = True


@dataclass(frozen=True, slots=True)
class QualityResult:
    report: QualityReport
    asset: MediaAssetDto
    #: ``False``: an identical report already existed.
    created: bool
    #: Against the report before this one for the same timeline, when there was one.
    comparison: ReportComparison | None


@dataclass(frozen=True, slots=True)
class _Inputs:
    timeline: BuiltTimeline
    build_asset: MediaAssetDto
    facts: f.TimelineFacts
    mix: f.MixFacts | None
    continuity: f.ContinuityFacts | None
    story: f.StoryFacts | None
    resolve: f.ResolveFacts | None
    live: LiveChanges | None
    warnings: tuple[str, ...]


class AnalyzeQuality:
    def __init__(
        self,
        library: MediaLibrary,
        builds: BuiltTimelineCatalog,
        specifications: TimelineCatalog,
        mixes: MixCatalog,
        continuity: ContinuityCatalog,
        stories: StoryCatalog,
        resolve: ResolveCatalog,
        inspector: MediaInspector,
        analyzer: MediaAnalyzer,
        live: LiveTimelineProbe,
        sessions: ReviewSessions,
        store: QualityStore,
        events: EventPublisher,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._builds = builds
        self._specifications = specifications
        self._mixes = mixes
        self._continuity = continuity
        self._stories = stories
        self._resolve = resolve
        self._live = live
        self._sessions = sessions
        self._store = store
        self._events = events
        self._paths = paths
        self._clock = clock
        self._measurer = PreviewMeasurer(analyzer, inspector)
        self._evidence = EvidenceRegistrar(library, analyzer)

    # --- the use case ---------------------------------------------------------------------------
    def execute(
        self, command: AnalyzeQualityCommand, ctx: JobContext
    ) -> Result[QualityResult, QualityError]:
        anchor = self._library.get(command.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        latest = self._builds.latest(command.anchor_asset_id, timeline_id=command.timeline_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(NothingToAnalyze())
        timeline, build_asset = latest.value
        if (
            command.preview_timeline_hash is not None
            and command.preview_timeline_hash != timeline.build.build_hash
        ):
            return Err(
                StaleStep(
                    "The preview was rendered from another version of the timeline than the one "
                    "to be checked; render the current version first."
                )
            )
        begun = self._begin(command, timeline)
        if isinstance(begun, Err):
            return begun
        try:
            result = self._analyze(command, anchor.value, timeline, build_asset, ctx)
        except BaseException:
            self._abandon(command)
            raise
        if isinstance(result, Err):
            self._abandon(command)
            return result
        return self._finish(command, result.value)

    # --- the review ------------------------------------------------------------------------------
    def _begin(
        self, command: AnalyzeQualityCommand, timeline: BuiltTimeline
    ) -> Result[None, QualityError]:
        if command.review_session_id is None:
            return Ok(None)
        synced = self._sessions.sync_timeline(command.review_session_id)
        if isinstance(synced, Err):
            return Err(StaleStep(str(synced.error.user_message)))
        session = synced.value
        if session.timeline.content_hash != timeline.build.build_hash:
            return Err(StaleStep("The review is not at the current version of the timeline."))
        if session.state in (ReviewState.ANALYSIS_PENDING, ReviewState.REVALIDATION_PENDING):
            return Ok(None)
        moved = self._sessions.advance(
            session.session_id, ReviewState.ANALYSIS_PENDING, actor=_ACTOR, reason="quality control"
        )
        if isinstance(moved, Err):
            return Err(StaleStep(str(moved.error.user_message)))
        return Ok(None)

    def _abandon(self, command: AnalyzeQualityCommand) -> None:
        """An analysis that did not complete leaves the review where a new one can start."""
        if command.review_session_id is None:
            return
        current = self._sessions.advance(
            command.review_session_id, ReviewState.CREATED, actor=_ACTOR, reason="analysis failed"
        )
        if isinstance(current, Err):
            _log.warning("Review left in place after a failed analysis", reason=str(current.error))

    def _finish(
        self, command: AnalyzeQualityCommand, result: QualityResult
    ) -> Result[QualityResult, QualityError]:
        report = result.report
        if command.review_session_id is not None:
            ref = TimelineRef(
                timeline_id=report.timeline_id,
                version=report.timeline_version,
                content_hash=report.timeline_content_hash,
                anchor_asset_id=report.anchor_asset_id,
            )
            attached = self._sessions.attach(
                command.review_session_id,
                ArtifactKind.QUALITY_REPORT,
                report.quality_report_id,
                timeline=ref,
                actor=_ACTOR,
            )
            if isinstance(attached, Err):
                return Err(StaleStep(str(attached.error.user_message)))
            if attached.value.state is ReviewState.ANALYSIS_COMPLETE:
                self._sessions.advance(
                    command.review_session_id,
                    ReviewState.REVIEW_READY,
                    actor=_ACTOR,
                    reason=f"quality report {report.quality_report_id}",
                )
        self._events.publish(
            QualityControlCompleted(
                occurred_at=self._clock.now(),
                quality_report_id=report.quality_report_id,
                timeline_id=report.timeline_id,
                timeline_version=report.timeline_version,
                review_session_id=report.review_session_id,
                critical=report.severity_summary.critical,
                high=report.severity_summary.high,
                total=report.severity_summary.total,
                created=result.created,
            )
        )
        return Ok(result)

    # --- the analysis ----------------------------------------------------------------------------
    def _analyze(
        self,
        command: AnalyzeQualityCommand,
        anchor: MediaAssetDto,
        timeline: BuiltTimeline,
        build_asset: MediaAssetDto,
        ctx: JobContext,
    ) -> Result[QualityResult, QualityError]:
        ctx.progress.report(0, 6, "Reading the timeline and what is known about it")
        inputs = self._gather(command, timeline, build_asset, ctx)
        facts = inputs.facts
        preview_checksum = ""
        preview_path = None
        if command.preview_asset_id:
            preview = self._library.get(command.preview_asset_id)
            if isinstance(preview, Err):
                return preview
            preview_checksum = preview.value.checksum
            located = self._library.local_path(command.preview_asset_id)
            if isinstance(located, Err):
                return located
            preview_path = located.value
        profile = self._profile(command, facts, inputs.mix)
        refs = self._input_refs(inputs, command, preview_checksum)
        report_id = report_identity(
            timeline_hash=facts.content_hash,
            timeline_id=facts.timeline_id,
            preview_asset_id=command.preview_asset_id or "",
            profile=profile,
            inputs=refs,
        )
        if command.reuse and not command.check_live:
            stored = self._store.find(anchor.id, report_id)
            if isinstance(stored, Err):
                return stored
            if stored.value is not None:
                report, asset = stored.value
                _log.info("Quality report reused", report_id=report_id)
                return Ok(QualityResult(report, asset, created=False, comparison=None))
        issues: list[QualityIssue] = []
        scope: list[CheckRecord] = []
        warnings = list(inputs.warnings)
        ctx.progress.report(1, 6, "Checking the timeline")
        self._timeline_checks(facts, profile, inputs, issues, scope)
        measurements: RenderMeasurements | None = None
        with self._paths.temporary_directory(prefix="quality-") as work:
            if preview_path is not None and command.preview_asset_id:
                ctx.progress.report(2, 6, "Measuring the preview render")
                measurements, sampled = self._measurer.measure(
                    command.preview_asset_id, preview_path, facts, profile, work / "frames", ctx
                )
                self._render_checks(facts, profile, measurements, issues, scope, warnings)
                ctx.progress.report(4, 6, "Keeping the evidence")
                issues = self._evidence.attach(
                    issues,
                    facts,
                    preview_asset_id=command.preview_asset_id,
                    preview_path=preview_path,
                    sampled=sampled,
                    work_dir=work,
                    ctx=ctx,
                )
            else:
                scope.extend(_not_run_without_preview())
        if command.preview_timeline_hash is None and command.preview_asset_id:
            warnings.append(
                "The version the preview was rendered from was not stated; "
                "the picture and sound checks assume it is this one."
            )
        ctx.progress.report(5, 6, "Writing the report")
        previous = self._previous(anchor.id, facts)
        if isinstance(previous, Err):
            return previous
        report = assemble(
            timeline=facts,
            profile=profile,
            issues=issues,
            scope=scope,
            created_at=self._clock.now().isoformat(),
            review_session_id=command.review_session_id,
            preview_asset_id=command.preview_asset_id or "",
            previous=previous.value,
            inputs=refs,
            metrics=_metrics(facts, measurements, inputs),
            warnings=tuple(warnings),
            limits=_LIMITS,
        )
        saved = self._store.save(anchor, report)
        if isinstance(saved, Err):
            return saved
        comparison = compare_reports(previous.value, report) if previous.value is not None else None
        ctx.progress.report(6, 6, "Done")
        return Ok(QualityResult(report, saved.value, created=True, comparison=comparison))

    def _previous(
        self, anchor_id: str, facts: f.TimelineFacts
    ) -> Result[QualityReport | None, MediaError]:
        latest = self._store.latest(anchor_id, timeline_id=facts.timeline_id)
        if isinstance(latest, Err):
            return latest
        return Ok(latest.value[0] if latest.value else None)

    def _profile(
        self, command: AnalyzeQualityCommand, facts: f.TimelineFacts, mix: f.MixFacts | None
    ) -> QualityProfile:
        profile = profile_for(command.profile, facts.identity.format_profile)
        if mix is None:
            return profile
        return with_delivery(
            profile,
            target_lufs=mix.integrated_target_lufs,
            tolerance_lu=mix.tolerance_lu,
            true_peak=mix.true_peak_ceiling_dbtp,
            range_limit=mix.loudness_range_limit_lu,
        )

    # --- inputs ----------------------------------------------------------------------------------
    def _gather(
        self,
        command: AnalyzeQualityCommand,
        timeline: BuiltTimeline,
        build_asset: MediaAssetDto,
        ctx: JobContext,
    ) -> _Inputs:
        warnings: list[str] = []
        anchor_id, timeline_id = command.anchor_asset_id, timeline.timeline_id
        specification = None
        if timeline.build.spec_asset_id:
            loaded = self._specifications.load(timeline.build.spec_asset_id)
            if isinstance(loaded, Ok):
                specification = loaded.value
            else:
                warnings.append(
                    "The compiled specification could not be read; it was not compared."
                )
        facts = timeline_facts(timeline, specification=specification)
        mix = None
        mixed = self._mixes.latest_result(anchor_id, timeline_id)
        if isinstance(mixed, Ok) and mixed.value is not None:
            mix = mix_facts(mixed.value[0])
            if mixed.value[0].timeline_version != timeline.build.build_version:
                warnings.append(
                    f"The stored audio mix was made for timeline version "
                    f"{mixed.value[0].timeline_version}, not {timeline.build.build_version}."
                )
        continuity = None
        found = self._continuity.latest(
            anchor_id, edit_plan_id=timeline.edit.edit_plan_id if timeline.edit else None
        )
        if isinstance(found, Ok) and found.value is not None:
            continuity = continuity_facts(found.value[0])
        story = None
        planned = self._stories.latest(anchor_id)
        if isinstance(planned, Ok) and planned.value is not None:
            story = story_facts(planned.value[0])
        bound = self._resolve.latest_binding(anchor_id, timeline_id)
        state = bound.value[0] if isinstance(bound, Ok) and bound.value is not None else None
        live = (
            self._live.look(anchor_id, timeline_id, ctx) if command.check_live and state else None
        )
        resolve = resolve_facts(
            state,
            timeline,
            live_changes=live.changes if live else None,
            live_note=live.note if live else "",
            live_checked=bool(live and live.checked),
        )
        return _Inputs(
            timeline, build_asset, facts, mix, continuity, story, resolve, live, tuple(warnings)
        )

    def _input_refs(
        self, inputs: _Inputs, command: AnalyzeQualityCommand, preview_checksum: str
    ) -> tuple[tuple[str, str], ...]:
        facts = inputs.facts
        resolve = inputs.resolve
        refs = [
            ("timeline", f"{facts.timeline_id}@{facts.version}:{facts.content_hash}"),
            ("build_asset", inputs.build_asset.id),
            ("preview", f"{command.preview_asset_id or ''}:{preview_checksum}"),
            ("analyzer", self._measurer.identity() if command.preview_asset_id else ""),
            ("mix", inputs.mix.mix_result_id if inputs.mix else ""),
            ("continuity", inputs.continuity.analysis_id if inputs.continuity else ""),
            ("story", inputs.story.plan_id if inputs.story else ""),
            (
                "resolve",
                f"{resolve.bound_version}:{resolve.bound_hash_matches}:{sorted(resolve.open_conflicts)}"
                if resolve
                else "",
            ),
            ("specification", str(len(facts.specification_items or ()))),
            ("live", str(command.check_live)),
            ("quality_control", str(QC_VERSION)),
        ]
        return tuple(refs)

    # --- checks ----------------------------------------------------------------------------------
    def _timeline_checks(
        self,
        facts: f.TimelineFacts,
        profile: QualityProfile,
        inputs: _Inputs,
        issues: list[QualityIssue],
        scope: list[CheckRecord],
    ) -> None:
        issues.extend(run_structure_checks(facts, inputs.resolve, profile))
        scope.append(
            CheckRecord(name="timeline structure", area=Area.TECHNICAL, state=CheckState.RAN)
        )
        scope.append(
            _record(
                "specification comparison",
                Area.TECHNICAL,
                facts.specification_items is not None,
                "no stored specification could be read",
            )
        )
        scope.append(
            _record(
                "Resolve binding",
                Area.TECHNICAL,
                inputs.resolve is not None,
                "the timeline was not carried out in Resolve",
            )
        )
        live = inputs.live
        scope.append(
            _record(
                "live Resolve timeline",
                Area.TECHNICAL,
                bool(live and live.checked),
                (live.note if live and live.note else "not asked for or Resolve not reachable"),
            )
        )
        issues.extend(check_edit(facts, profile))
        scope.append(CheckRecord(name="editing", area=Area.EDITING, state=CheckState.RAN))
        issues.extend(check_captions(facts, profile))
        scope.append(CheckRecord(name="captions", area=Area.VISUAL, state=CheckState.RAN))
        issues.extend(check_graphics(facts, profile))
        scope.append(CheckRecord(name="graphics", area=Area.VISUAL, state=CheckState.RAN))
        issues.extend(carry_continuity(facts, inputs.continuity))
        scope.append(
            _record(
                "continuity analysis",
                Area.EDITING,
                inputs.continuity is not None,
                "no stored continuity analysis",
            )
        )
        outcome = compare_story(facts, inputs.story)
        issues.extend(outcome.issues)
        scope.append(
            _record("story plan comparison", Area.STORY, outcome.comparable, outcome.detail)
        )
        issues.extend(carry_mix(facts, inputs.mix))
        scope.append(
            _record(
                "audio mix validation", Area.AUDIO, inputs.mix is not None, "no stored audio mix"
            )
        )

    def _render_checks(
        self,
        facts: f.TimelineFacts,
        profile: QualityProfile,
        measurements: RenderMeasurements,
        issues: list[QualityIssue],
        scope: list[CheckRecord],
        warnings: list[str],
    ) -> None:
        gaps = [
            (facts.seconds(i.start_frame), facts.seconds(i.end_frame))
            for i in issues
            if i.code == "structure.gap" and i.start_frame is not None and i.end_frame is not None
        ]
        issues.extend(run_render_checks(facts, profile, measurements, gaps))
        skipped = dict(measurements.skipped)
        for name, area, key in _RENDER_CHECKS:
            reason = skipped.get(key)
            state = CheckState.FAILED if reason else CheckState.RAN
            if key == "frames" and not measurements.frames and not reason:
                state = CheckState.NOT_RUN
                reason = "no picture could be taken from the preview"
            scope.append(CheckRecord(name=name, area=area, state=state, detail=reason or ""))
        warnings.extend(f"{n}: {why}" for n, why in measurements.skipped)


def _record(name: str, area: Area, ran: bool, reason: str) -> CheckRecord:
    return CheckRecord(
        name=name,
        area=area,
        state=CheckState.RAN if ran else CheckState.NOT_RUN,
        detail="" if ran else reason,
    )


_RENDER_CHECKS: tuple[tuple[str, Area, str], ...] = (
    ("preview streams", Area.TECHNICAL, "streams"),
    ("preview decoding", Area.TECHNICAL, "decode"),
    ("black frames", Area.TECHNICAL, "black frames"),
    ("frozen frames", Area.TECHNICAL, "frozen frames"),
    ("silence", Area.AUDIO, "silence"),
    ("loudness and peaks", Area.AUDIO, "loudness"),
    ("dialogue levels", Area.AUDIO, "dialogue levels"),
    ("sound at the end", Area.AUDIO, "end level"),
    ("pictures", Area.VISUAL, "frames"),
)
_LIMITS = (
    "Focus, composition, subject clipping, flicker, spelling of captions and text inside "
    "rendered graphics are not measured.",
    "Pictures are judged from a bounded sample of frames, not every frame.",
    "Subjective findings are opinions and never block an export.",
    "A report is an automated assessment and never an approval.",
)


def _not_run_without_preview() -> list[CheckRecord]:
    return [
        CheckRecord(
            name=name,
            area=area,
            state=CheckState.NOT_RUN,
            detail="no preview render was supplied",
        )
        for name, area, _ in _RENDER_CHECKS
    ]


def _metrics(
    facts: f.TimelineFacts, measured: RenderMeasurements | None, inputs: _Inputs
) -> dict[Area, tuple[tuple[str, str], ...]]:
    primary = facts.primary()
    fps = float(facts.settings.fps)
    lengths = [(c.end_frame - c.start_frame) / fps for c in primary]
    loud = measured.loudness if measured else None
    return {
        Area.TECHNICAL: (
            ("duration", f"{facts.settings.duration_frames / fps:.1f} s"),
            ("frame rate", f"{fps:g}"),
            ("size", f"{facts.settings.width}x{facts.settings.height}"),
            ("clips", str(len(facts.clips) + len(facts.sounds))),
        ),
        Area.EDITING: (
            ("shots", str(len(primary))),
            ("mean shot", f"{sum(lengths) / len(lengths):.1f} s" if lengths else "-"),
        ),
        Area.AUDIO: (
            (
                "integrated loudness",
                f"{loud.integrated_lufs:.1f} LUFS"
                if loud and loud.integrated_lufs is not None
                else "-",
            ),
            (
                "true peak",
                f"{loud.true_peak_dbtp:.1f} dBTP"
                if loud and loud.true_peak_dbtp is not None
                else "-",
            ),
        ),
        Area.VISUAL: (("frames looked at", str(len(measured.frames)) if measured else "0"),),
        Area.STORY: (("plan", inputs.story.plan_id if inputs.story else "-"),),
    }
