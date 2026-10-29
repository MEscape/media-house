"""Task executors: one thin adapter per task kind over the owning module's public contract.

Nothing here computes media results. Each executor calls the owner, which already reuses what it
stored, then states honestly whether it computed (``SUCCEEDED``), served a stored result
(``REUSED``), or produced a result with a missing part (``PARTIAL``). Typed results travel to the
tasks that need them as ``payload``, so downstream owners are never asked to find them again.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from media_house.core.application.ports import Clock
from media_house.modules.asset_relationships.application.contracts import (
    DETECTOR_IDS,
    AnalyzeAssetCommand,
    RelationshipDetection,
)
from media_house.modules.audio_improvement.application.contracts import (
    AudioImprover,
    ImproveAudioCommand,
)
from media_house.modules.audio_intelligence.application.contracts import (
    AnalyzeAudioCommand,
    AudioAnalyzer,
    AudioIntelligenceConfig,
    AudioIntelligenceTimeline,
    TranscriptionConfig,
)
from media_house.modules.audio_visual_alignment.application.contracts import (
    Acquire,
    AlignCommand,
    AlignmentDocument,
    AudioVisualAligner,
)
from media_house.modules.broll_intelligence.application.contracts import (
    AnalyzeBrollCommand,
    BrollAnalysis,
    BrollAnalyzer,
    BrollConfig,
)
from media_house.modules.broll_intelligence.application.contracts import (
    ResultState as BrollState,
)
from media_house.modules.content_intelligence.application.contracts import (
    AnalyzeContentCommand,
    AudioUse,
    ContentAnalysis,
    ContentAnalyzer,
    ContentConfig,
    EmbeddingSettings,
)
from media_house.modules.continuity_intelligence.application.contracts import (
    AnalyzeContinuityCommand,
    ContinuityAnalyzer,
    ContinuityConfig,
)
from media_house.modules.continuity_intelligence.application.contracts import (
    ResultState as ContinuityState,
)
from media_house.modules.editing_intelligence.application.contracts import (
    Acquire as EditAcquire,
)
from media_house.modules.editing_intelligence.application.contracts import (
    DurationMode,
    EditConfig,
    EditPlan,
    EditPlanner,
    PlanEditCommand,
    PlanState,
    TargetDuration,
)
from media_house.modules.media_inspection.application.contracts import (
    InspectionResult,
    InspectMediaCommand,
    MediaInspector,
)
from media_house.modules.media_library.application.contracts import JsonValue, MediaLibrary
from media_house.modules.media_pipeline.application.sequence import sequence_of
from media_house.modules.media_pipeline.application.tasks import (
    TaskExecutor,
    TaskJob,
    TaskPeek,
    TaskResult,
)
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.modules.pacing_intelligence.application.contracts import (
    AnalyzePacingCommand,
    PacingAnalysis,
    PacingAnalyzer,
    PacingConfig,
    Strategy,
)
from media_house.modules.pacing_intelligence.application.contracts import (
    ResultState as PacingState,
)
from media_house.modules.pacing_intelligence.application.contracts import (
    TargetDuration as PacingTarget,
)
from media_house.modules.proxy_manager.application.contracts import (
    EnsureProxyCommand,
    ProxyDto,
    ProxyManager,
)
from media_house.modules.story_architecture.application.contracts import (
    Acquire as StoryAcquire,
)
from media_house.modules.story_architecture.application.contracts import (
    PlanStoryCommand,
    StageState,
    StoryConfig,
    StoryPlan,
    StoryPlanner,
)
from media_house.modules.video_improvement.application.contracts import (
    ImproveVideoCommand,
    VideoImprover,
)
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzerState,
    AnalyzeVideoCommand,
    DeviceKind,
    RuntimeConfig,
    VideoAnalysis,
    VideoAnalyzer,
)
from media_house.shared.errors import Err, Ok

_UNAVAILABLE_CODES = ("no_audio_track", "no_video_stream", "nothing_to_cover", "nothing_to_judge")
_DEGRADED = {"not_available", "failed"}
_NO_AUDIO = TaskResult(TaskStatus.SKIPPED_UNAVAILABLE, "the inspection found no audio stream")


@dataclass(frozen=True, slots=True)
class StageSettings:
    """Per-stage choices a run can make. ``None`` keeps the owning module's own default."""

    video_analysis_profile: str | None = None
    proxy_profile: str | None = None
    audio_improvement_profile: str | None = None
    video_source_profile: str | None = None
    video_processing_profile: str | None = None
    #: A story format name (``review``, ``tutorial`` ...); ``None`` keeps the planner's default.
    story_format: str | None = None
    #: Let a language model reword the story plan (off: the plan is fully deterministic).
    story_llm: bool = False
    #: An editing profile name (``talking_head``, ``tutorial`` ...); ``None`` = the one named like
    #: the story's format.
    edit_profile: str | None = None
    #: A target length for the edit in seconds (an upper bound); ``None`` = the story's own.
    edit_target_seconds: float | None = None
    #: Treat the target as a hard constraint instead of a preference.
    edit_target_hard: bool = False
    #: A pacing profile name (``tutorial``, ``talking_head`` ...); ``None`` = by the story's format.
    pacing_profile: str | None = None
    #: ``concise``, ``balanced``, ``detailed``, ``high_energy`` or ``cinematic``.
    pacing_strategy: str | None = None
    #: A B-roll profile name (``tutorial``, ``documentary`` ...); ``None`` = by the story's format.
    broll_profile: str | None = None
    #: A continuity profile name (``cinematic_dialogue`` ...); ``None`` = by the story's format.
    continuity_profile: str | None = None
    #: 0.5 lenient .. 1.5 strict.
    continuity_strictness: float = 1.0
    #: Never use the GPU. Only the device settings change; results keep their identity because
    #: the owners leave the device out of their cache keys.
    cpu_only: bool = False


def _given[T](asset_id: str, result: T | None) -> dict[str, T]:
    return {} if result is None else {asset_id: result}


def _outcome(created: bool, detail: str = "", **facts: JsonValue) -> TaskResult:
    return TaskResult(TaskStatus.SUCCEEDED if created else TaskStatus.REUSED, detail, facts=facts)


def _from_error(error: object) -> TaskResult:
    code = str(getattr(error, "code", "error"))
    message = str(getattr(error, "user_message", error))
    if code.endswith(_UNAVAILABLE_CODES):
        return TaskResult(TaskStatus.SKIPPED_UNAVAILABLE, message)
    return TaskResult(TaskStatus.FAILED, f"{code}: {message}")


def _inspection_outcome(result: InspectionResult, *, created: bool) -> TaskResult:
    """The inspection ran (or was found); what it says about the MEDIA is reported honestly.

    An ``error`` verdict (damaged or unusable media) fails the task, so the run is not ``ok`` and
    nothing treats the media as sound. Warnings travel with a successful task."""
    status = result.inspection.status
    problems = tuple(
        f"{f.code}: {f.message}"
        for f in result.inspection.findings
        if f.severity.value in ("warning", "error")
    )
    facts: dict[str, JsonValue] = {
        "inspection_asset_id": result.asset.id,
        "verdict": status.verdict.value,
    }
    if status.verdict.value == "error" or not status.usable:
        return TaskResult(
            TaskStatus.FAILED,
            f"inspection verdict {status.verdict.value}: the media is damaged or unusable",
            warnings=problems[:5],
            facts=facts,
        )
    return TaskResult(
        TaskStatus.SUCCEEDED if created else TaskStatus.REUSED,
        warnings=problems[:5],
        facts=facts,
        payload=result,
    )


class ModuleExecutors:
    """Builds the executor table from the module contracts (the only place that knows them)."""

    def __init__(
        self,
        *,
        library: MediaLibrary,
        clock: Clock,
        inspector: MediaInspector,
        proxies: ProxyManager,
        audio_improver: AudioImprover,
        video_improver: VideoImprover,
        audio_analyzer: AudioAnalyzer,
        video_analyzer: VideoAnalyzer,
        content_analyzer: ContentAnalyzer,
        aligner: AudioVisualAligner,
        story_planner: StoryPlanner,
        pacing_analyzer: PacingAnalyzer,
        broll_analyzer: BrollAnalyzer,
        continuity_analyzer: ContinuityAnalyzer,
        edit_planner: EditPlanner,
        relationships: RelationshipDetection,
        settings: StageSettings | None = None,
    ) -> None:
        self._library = library
        self._clock = clock
        self._inspector = inspector
        self._proxies = proxies
        self._audio_improver = audio_improver
        self._video_improver = video_improver
        self._audio_analyzer = audio_analyzer
        self._video_analyzer = video_analyzer
        self._content_analyzer = content_analyzer
        self._aligner = aligner
        self._story_planner = story_planner
        self._pacing_analyzer = pacing_analyzer
        self._broll_analyzer = broll_analyzer
        self._continuity_analyzer = continuity_analyzer
        self._edit_planner = edit_planner
        self._relationships = relationships
        self._settings = settings or StageSettings()

    def table(self) -> Mapping[TaskKind, TaskExecutor]:
        return {
            TaskKind.INSPECT: self.inspect,
            TaskKind.THUMBNAIL: self.thumbnail,
            TaskKind.PROXY: self.proxy,
            TaskKind.AUDIO_IMPROVEMENT: self.improve_audio,
            TaskKind.VIDEO_IMPROVEMENT: self.improve_video,
            TaskKind.AUDIO_ANALYSIS: self.analyze_audio,
            TaskKind.VIDEO_ANALYSIS: self.analyze_video,
            TaskKind.CONTENT_ANALYSIS: self.analyze_content,
            TaskKind.ALIGNMENT: self.align,
            TaskKind.STORY: self.story,
            TaskKind.PACING: self.pacing,
            TaskKind.BROLL: self.broll,
            TaskKind.CONTINUITY: self.continuity,
            TaskKind.EDIT_PLAN: self.edit_plan,
            TaskKind.RELATIONSHIPS: self.relationships,
        }

    def peeks(self) -> Mapping[TaskKind, TaskPeek]:
        """Pre-flight cache checks, only where an owner offers an exact, side-effect-free one."""
        return {
            TaskKind.INSPECT: self.peek_inspect,
            TaskKind.THUMBNAIL: self.peek_thumbnail,
            TaskKind.PROXY: self.peek_proxy,
        }

    def peek_inspect(self, job: TaskJob) -> TaskResult | None:
        found = self._inspector.find(job.id.asset_id)
        return None if found is None else _inspection_outcome(found, created=False)

    def peek_thumbnail(self, job: TaskJob) -> TaskResult | None:
        found = self._library.thumbnail(job.id.asset_id)
        if isinstance(found, Ok) and found.value is not None:
            return _outcome(False, thumbnail_asset_id=found.value.id)
        return None

    def peek_proxy(self, job: TaskJob) -> TaskResult | None:
        proxy = self._proxies.peek(self._proxy_command(job))
        return None if proxy is None else self._proxy_result(proxy)

    # --- foundation -----------------------------------------------------------------------
    def inspect(self, job: TaskJob) -> TaskResult:
        match self._inspector.execute(InspectMediaCommand(job.id.asset_id), job.ctx):
            case Ok(result):
                return _inspection_outcome(result, created=result.created)
            case Err(error):
                return _from_error(error)

    def thumbnail(self, job: TaskJob) -> TaskResult:
        asset_id = job.id.asset_id
        match self._library.thumbnail(asset_id):
            case Ok(existing) if existing is not None:
                return _outcome(False, thumbnail_asset_id=existing.id)
            case Err(error):
                return _from_error(error)
        began = self._clock.now()
        made = self._library.ensure_thumbnail(asset_id)
        if isinstance(made, Err):
            return _from_error(made.error)
        # ensure_thumbnail does not say whether it created the file; one that already existed
        # when this call began (made by a concurrent run) is a reuse, not new work.
        return _outcome(made.value.created_at >= began, thumbnail_asset_id=made.value.id)

    def _proxy_command(self, job: TaskJob) -> EnsureProxyCommand:
        profile = self._settings.proxy_profile
        if profile is None:
            return EnsureProxyCommand(job.id.asset_id)
        return EnsureProxyCommand(job.id.asset_id, profile=profile)

    @staticmethod
    def _proxy_result(proxy: ProxyDto) -> TaskResult:
        return TaskResult(
            TaskStatus.REUSED if proxy.reused else TaskStatus.SUCCEEDED,
            warnings=tuple(proxy.warnings),
            facts={
                "proxy_asset_id": proxy.proxy_asset_id,
                "profile": proxy.profile,
                "verdict": proxy.verdict.value,
                "evidence": proxy.evidence.value,
            },
        )

    def proxy(self, job: TaskJob) -> TaskResult:
        match self._proxies.execute(self._proxy_command(job), job.ctx):
            case Ok(proxy):
                return self._proxy_result(proxy)
            case Err(error):
                return _from_error(error)

    # --- improvement (derives new assets; nothing downstream reads them) --------------------
    def improve_audio(self, job: TaskJob) -> TaskResult:
        if self._lacks_audio(job):
            return _NO_AUDIO
        profile = self._settings.audio_improvement_profile
        command = (
            ImproveAudioCommand(job.id.asset_id)
            if profile is None
            else ImproveAudioCommand(job.id.asset_id, profile=profile)
        )
        match self._audio_improver.execute(command, job.ctx):
            case Ok(result):
                return TaskResult(
                    TaskStatus.SUCCEEDED if result.created else TaskStatus.REUSED,
                    warnings=tuple(result.warnings),
                    facts={"improved_asset_id": result.asset.id},
                )
            case Err(error):
                return _from_error(error)

    def improve_video(self, job: TaskJob) -> TaskResult:
        command = ImproveVideoCommand(
            job.id.asset_id,
            source_profile=self._settings.video_source_profile,
            processing_profile=self._settings.video_processing_profile,
        )
        match self._video_improver.execute(command, job.ctx):
            case Ok(result):
                return TaskResult(
                    TaskStatus.SUCCEEDED if result.created else TaskStatus.REUSED,
                    detail="" if result.changed else "footage needed nothing; source kept",
                    facts={"asset_id": result.asset.id, "changed": result.changed},
                )
            case Err(error):
                return _from_error(error)

    # --- analysis -------------------------------------------------------------------------
    def analyze_audio(self, job: TaskJob) -> TaskResult:
        if self._lacks_audio(job):
            return _NO_AUDIO
        command = AnalyzeAudioCommand(job.id.asset_id)
        if self._settings.cpu_only:
            command = AnalyzeAudioCommand(
                job.id.asset_id,
                AudioIntelligenceConfig(transcription=TranscriptionConfig(device="cpu")),
            )
        match self._audio_analyzer.execute(command, job.ctx):
            case Ok(result):
                return TaskResult(
                    TaskStatus.SUCCEEDED if result.created else TaskStatus.REUSED,
                    facts={
                        "timeline_asset_id": result.asset.id,
                        "transcript_asset_id": result.transcript_asset.id,
                    },
                    payload=result.timeline,
                )
            case Err(error):
                return _from_error(error)

    def analyze_video(self, job: TaskJob) -> TaskResult:
        profile = self._settings.video_analysis_profile
        runtime = RuntimeConfig(
            device=DeviceKind.CPU if self._settings.cpu_only else DeviceKind.AUTO
        )
        command = (
            AnalyzeVideoCommand(job.id.asset_id, runtime=runtime)
            if profile is None
            else AnalyzeVideoCommand(job.id.asset_id, profile=profile, runtime=runtime)
        )
        match self._video_analyzer.execute(command, job.ctx):
            case Ok(result):
                degraded = tuple(
                    f"{r.analyzer.value}: {r.state.value}"
                    for r in result.analysis.analyzers
                    if r.state in (AnalyzerState.NOT_AVAILABLE, AnalyzerState.FAILED)
                )
                base = _outcome(result.created, analysis_asset_id=result.asset.id)
                return TaskResult(
                    TaskStatus.PARTIAL if degraded else base.status,
                    "some analyzers did not run" if degraded else "",
                    warnings=degraded,
                    facts=base.facts,
                    payload=result.analysis,
                )
            case Err(error):
                return _from_error(error)

    def analyze_content(self, job: TaskJob) -> TaskResult:
        command = AnalyzeContentCommand(
            source_asset_id=job.id.asset_id,
            audio=AudioUse.TRANSCRIPT,
            config=ContentConfig(
                embedding=EmbeddingSettings(device="cpu" if self._settings.cpu_only else "auto")
            ),
            audio_timeline=self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline),
            video_analysis=self._upstream(job, TaskKind.VIDEO_ANALYSIS, VideoAnalysis),
        )
        match self._content_analyzer.execute(command, job.ctx):
            case Ok(result):
                degraded = tuple(
                    f"{s.stage}: {s.state.value}"
                    for s in result.analysis.stages
                    if s.state.value in _DEGRADED
                )
                base = _outcome(
                    result.created,
                    analysis_asset_id=result.asset.id if result.asset else None,
                )
                return TaskResult(
                    TaskStatus.PARTIAL if degraded else base.status,
                    "some stages did not run" if degraded else "",
                    warnings=degraded,
                    facts=base.facts,
                    payload=result.analysis,
                )
            case Err(error):
                return _from_error(error)

    def align(self, job: TaskJob) -> TaskResult:
        command = AlignCommand(
            job.id.asset_id,
            acquire=Acquire.STORED,
            audio_timeline=self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline),
            video_analysis=self._upstream(job, TaskKind.VIDEO_ANALYSIS, VideoAnalysis),
            content_analysis=self._upstream(job, TaskKind.CONTENT_ANALYSIS, ContentAnalysis),
        )
        match self._aligner.execute(command, job.ctx):
            case Ok(result):
                degraded = tuple(
                    f"{s.stage}: {s.state.value}"
                    for s in result.document.stages
                    if s.state.value in _DEGRADED
                )
                base = _outcome(result.created, alignment_asset_id=result.asset.id)
                return TaskResult(
                    TaskStatus.PARTIAL if degraded else base.status,
                    "some stages did not run" if degraded else "",
                    warnings=degraded,
                    facts=base.facts,
                    payload=result.document,
                )
            case Err(error):
                return _from_error(error)

    def relationships(self, job: TaskJob) -> TaskResult:
        """Propose claims for this asset from its STORED inspection (nothing is probed).

        Detection proposes only; a person decides. The result is the claims' business, not this
        task's: it reports how many were proposed or refreshed and whether the asset was new.
        """
        match self._relationships.analyze(
            AnalyzeAssetCommand(job.id.asset_id, frozenset(DETECTOR_IDS)), job.ctx
        ):
            case Ok(analysis):
                if not analysis.inspection_available:
                    return TaskResult(
                        TaskStatus.SKIPPED_UNAVAILABLE, "no stored inspection to read (not probed)"
                    )
                fresh = [r for r in analysis.results if not r.unchanged]
                base = _outcome(
                    bool(fresh),
                    proposed=sum(r.proposed for r in analysis.results),
                    made_stale=sum(r.made_stale for r in analysis.results),
                    capped=sum(r.capped for r in analysis.results),
                )
                capped = tuple(f"{r.detector}: capped" for r in analysis.results if r.capped)
                return TaskResult(
                    TaskStatus.PARTIAL if capped else base.status,
                    "more candidates than the cap allows" if capped else "",
                    warnings=capped,
                    facts=base.facts,
                )
            case Err(error):
                return _from_error(error)

    def story(self, job: TaskJob) -> TaskResult:
        asset_id = job.id.asset_id
        content = self._upstream(job, TaskKind.CONTENT_ANALYSIS, ContentAnalysis)
        alignment = self._upstream(job, TaskKind.ALIGNMENT, AlignmentDocument)
        command = PlanStoryCommand(
            (asset_id,),
            config=StoryConfig(self._settings.story_format or StoryConfig().format),
            acquire=StoryAcquire.STORED,
            llm=self._settings.story_llm,
            content_analyses={} if content is None else {asset_id: content},
            alignments={} if alignment is None else {asset_id: alignment},
        )
        match self._story_planner.execute(command, job.ctx):
            case Ok(result):
                # The picture side is optional (an audio-only recording has none), so only a
                # stage that FAILED, or a model that was asked for and did not deliver, is a gap.
                degraded = tuple(
                    f"{s.stage}: {s.state.value}"
                    for s in result.plan.stages
                    if s.state is StageState.FAILED
                    or (s.stage == "refinement" and s.state is not StageState.OK)
                )
                base = _outcome(
                    result.created,
                    story_asset_id=result.asset.id,
                    plan_version=result.plan.plan_version,
                    sections=len(result.plan.sections),
                    issues=len(result.plan.completeness_issues),
                )
                return TaskResult(
                    TaskStatus.PARTIAL if degraded else base.status,
                    "some stages did not run" if degraded else "",
                    warnings=degraded,
                    facts=base.facts,
                    payload=result.plan,
                )
            case Err(error):
                return _from_error(error)

    def pacing(self, job: TaskJob) -> TaskResult:
        """Analyse the rhythm from the in-memory upstream results (nothing is recomputed). The
        story plan is read when one was made; pacing never asks for it. A target length given for
        the edit is the pacing target's maximum."""
        asset_id = job.id.asset_id
        seconds = self._settings.edit_target_seconds
        command = AnalyzePacingCommand(
            (asset_id,),
            config=PacingConfig(
                profile=self._settings.pacing_profile,
                strategy=Strategy(self._settings.pacing_strategy or Strategy.BALANCED.value),
                target=None if seconds is None else PacingTarget(maximum=seconds),
            ),
            story_format=self._settings.story_format,
            story_plan=self._upstream(job, TaskKind.STORY, StoryPlan),
            audio_timelines=_given(
                asset_id, self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline)
            ),
            content_analyses=_given(
                asset_id, self._upstream(job, TaskKind.CONTENT_ANALYSIS, ContentAnalysis)
            ),
            video_analyses=_given(
                asset_id, self._upstream(job, TaskKind.VIDEO_ANALYSIS, VideoAnalysis)
            ),
            alignments=_given(asset_id, self._upstream(job, TaskKind.ALIGNMENT, AlignmentDocument)),
        )
        match self._pacing_analyzer.execute(command, job.ctx):
            case Ok(result):
                analysis = result.analysis
                partial = analysis.state is PacingState.PARTIAL
                base = _outcome(
                    result.created,
                    pacing_asset_id=result.asset.id,
                    analysis_version=analysis.analysis_version,
                    profile=analysis.profile,
                    recommendations=len(analysis.recommendations),
                )
                return TaskResult(
                    TaskStatus.PARTIAL if partial else base.status,
                    "an input a whole level needs was missing" if partial else "",
                    warnings=tuple(
                        i.detail for i in analysis.issues if i.severity.value == "unresolved"
                    )[:5]
                    if partial
                    else (),
                    facts=base.facts,
                    payload=analysis,
                )
            case Err(error):
                return _from_error(error)

    def broll(self, job: TaskJob) -> TaskResult:
        """Find supplementary footage for the passages from the in-memory upstream results and the
        library's stored shot indexes (nothing is analysed). The story plan and pacing analysis are
        read when this run made them; B-roll never asks for them."""
        asset_id = job.id.asset_id
        command = AnalyzeBrollCommand(
            (asset_id,),
            config=BrollConfig(profile=self._settings.broll_profile),
            story_format=self._settings.story_format,
            story_plan=self._upstream(job, TaskKind.STORY, StoryPlan),
            content_analyses=_given(
                asset_id, self._upstream(job, TaskKind.CONTENT_ANALYSIS, ContentAnalysis)
            ),
            audio_timelines=_given(
                asset_id, self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline)
            ),
            video_analyses=_given(
                asset_id, self._upstream(job, TaskKind.VIDEO_ANALYSIS, VideoAnalysis)
            ),
            alignments=_given(asset_id, self._upstream(job, TaskKind.ALIGNMENT, AlignmentDocument)),
            pacing=self._upstream(job, TaskKind.PACING, PacingAnalysis),
        )
        match self._broll_analyzer.execute(command, job.ctx):
            case Ok(result):
                analysis = result.analysis
                partial = analysis.state is BrollState.PARTIAL
                base = _outcome(
                    result.created,
                    broll_asset_id=result.asset.id,
                    analysis_version=analysis.analysis_version,
                    profile=analysis.profile,
                    recommendations=len(analysis.recommendations),
                    covered=len(analysis.satisfied()),
                    gaps=len(analysis.gaps),
                    searched_assets=analysis.pool.assets,
                )
                return TaskResult(
                    TaskStatus.PARTIAL if partial else base.status,
                    "part of the library or a search model could not be used" if partial else "",
                    warnings=(
                        *(g.reason for g in analysis.gaps[:3]),
                        *(f"{a}: {why}" for a, why in analysis.pool.skipped[:2]),
                    ),
                    facts=base.facts,
                    payload=analysis,
                )
            case Err(error):
                return _from_error(error)

    def continuity(self, job: TaskJob) -> TaskResult:
        """Judge the cuts of the edit plan made in this run, from the in-memory upstream results
        (nothing is recomputed). The editing stage reads the stored result on its next planning."""
        asset_id = job.id.asset_id
        plan = self._upstream(job, TaskKind.EDIT_PLAN, EditPlan)
        if plan is None:
            return TaskResult(TaskStatus.SKIPPED_DEPENDENCY, "no edit plan to judge")
        command = AnalyzeContinuityCommand(
            sequence_of(plan),
            config=ContinuityConfig(
                profile=self._settings.continuity_profile,
                strictness=self._settings.continuity_strictness,
            ),
            video_analyses=_given(
                asset_id, self._upstream(job, TaskKind.VIDEO_ANALYSIS, VideoAnalysis)
            ),
            audio_timelines=_given(
                asset_id, self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline)
            ),
            alignments=_given(asset_id, self._upstream(job, TaskKind.ALIGNMENT, AlignmentDocument)),
            broll=self._upstream(job, TaskKind.BROLL, BrollAnalysis),
        )
        match self._continuity_analyzer.execute(command, job.ctx):
            case Ok(result):
                analysis = result.analysis
                partial = analysis.state is ContinuityState.PARTIAL
                base = _outcome(
                    result.created,
                    continuity_asset_id=result.asset.id,
                    analysis_version=analysis.analysis_version,
                    profile=analysis.profile,
                    transitions=analysis.metrics.transitions,
                    evaluated=analysis.metrics.evaluated,
                    reused_cuts=analysis.metrics.reused,
                    issues=len(analysis.issues),
                    surfaced=len(analysis.surfaced()),
                    unresolved=len(analysis.unresolved),
                )
                return TaskResult(
                    TaskStatus.PARTIAL if partial else base.status,
                    "some cuts had no picture evidence" if partial else "",
                    warnings=tuple(i.explanation for i in analysis.surfaced()[:3]),
                    facts=base.facts,
                    payload=analysis,
                )
            case Err(error):
                return _from_error(error)

    def edit_plan(self, job: TaskJob) -> TaskResult:
        """Plan the edit from the story plan and the in-memory upstream results (nothing is
        recomputed). A hard target the material cannot meet is PARTIAL: a plan exists, a hard
        constraint is not met."""
        asset_id = job.id.asset_id
        story = self._upstream(job, TaskKind.STORY, StoryPlan)
        content = self._upstream(job, TaskKind.CONTENT_ANALYSIS, ContentAnalysis)
        audio = self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline)
        video = self._upstream(job, TaskKind.VIDEO_ANALYSIS, VideoAnalysis)
        alignment = self._upstream(job, TaskKind.ALIGNMENT, AlignmentDocument)
        seconds = self._settings.edit_target_seconds
        target = (
            None
            if seconds is None
            else TargetDuration(
                high=seconds,
                mode=DurationMode.HARD if self._settings.edit_target_hard else DurationMode.SOFT,
            )
        )
        command = PlanEditCommand(
            (asset_id,),
            config=EditConfig(profile=self._settings.edit_profile, target=target),
            story_format=self._settings.story_format,
            story_plan=story,
            acquire=EditAcquire.STORED,
            content_analyses={} if content is None else {asset_id: content},
            audio_timelines={} if audio is None else {asset_id: audio},
            video_analyses={} if video is None else {asset_id: video},
            alignments={} if alignment is None else {asset_id: alignment},
            pacing=self._upstream(job, TaskKind.PACING, PacingAnalysis),
            broll=self._upstream(job, TaskKind.BROLL, BrollAnalysis),
        )
        match self._edit_planner.execute(command, job.ctx):
            case Ok(result):
                plan = result.plan
                unmet = plan.state is PlanState.UNMET_CONSTRAINTS
                base = _outcome(
                    result.created,
                    edit_plan_asset_id=result.asset.id,
                    plan_version=plan.plan_version,
                    profile=plan.profile,
                    placements=len(plan.placements),
                    decisions=len(plan.decisions),
                    issues=len(plan.issues),
                )
                return TaskResult(
                    TaskStatus.PARTIAL if unmet else base.status,
                    "a hard constraint is not met" if unmet else "",
                    warnings=tuple(
                        i.detail for i in plan.issues if i.kind.value == "target_not_met"
                    )
                    if unmet
                    else (),
                    facts=base.facts,
                    payload=plan,
                )
            case Err(error):
                return _from_error(error)

    @classmethod
    def _lacks_audio(cls, job: TaskJob) -> bool:
        """True only when a usable inspection PROVES there is no audio stream (never a guess).

        A missing, failed or unusable inspection proves nothing, so the owner decides and a
        broken stream is reported as a failure, not as "no audio"."""
        found = cls._upstream(job, TaskKind.INSPECT, InspectionResult)
        return (
            found is not None
            and found.inspection.status.usable
            and not found.inspection.audio_streams
        )

    @staticmethod
    def _upstream[T](job: TaskJob, kind: TaskKind, expected: type[T]) -> T | None:
        return job.payload_as(TaskId(job.id.asset_id, kind), expected)
