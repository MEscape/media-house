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
from media_house.modules.caption_engine.application.contracts import (
    DOCUMENT_TYPE as CAPTION_DOCUMENT,
)
from media_house.modules.caption_engine.application.contracts import (
    CaptionConfig,
    CaptionPlan,
    CaptionPlanner,
    PlanCaptionsCommand,
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
from media_house.modules.media_library.application.contracts import (
    JsonValue,
    MediaAssetDto,
    MediaLibrary,
)
from media_house.modules.media_pipeline.application.edit_view import (
    EditView,
    no_speech_view,
    view_of,
)
from media_house.modules.media_pipeline.application.layer4_inputs import (
    caption_timeline,
    graphics_brief,
    soundtrack_timeline,
    sync_events,
    transition_sequence,
    vfx_shots,
)
from media_house.modules.media_pipeline.application.sequence import sequence_of
from media_house.modules.media_pipeline.application.tasks import (
    TaskExecutor,
    TaskJob,
    TaskPeek,
    TaskResult,
)
from media_house.modules.media_pipeline.domain.values import TaskId, TaskKind, TaskStatus
from media_house.modules.motion_graphics_engine.application.contracts import (
    DOCUMENT_TYPE as GRAPHICS_DOCUMENT,
)
from media_house.modules.motion_graphics_engine.application.contracts import (
    STORY_FORMAT_PROFILE as GRAPHICS_PROFILE_OF,
)
from media_house.modules.motion_graphics_engine.application.contracts import (
    GraphicsPlan,
    ItemStatus,
    MotionGraphicsConfig,
    MotionGraphicsPlanner,
    PlanGraphicsCommand,
)
from media_house.modules.music_and_sound_design.application.contracts import (
    PROFILES as SOUND_PROFILES,
)
from media_house.modules.music_and_sound_design.application.contracts import (
    SOUND_DESIGN_PLAN,
    PlanSoundDesignCommand,
    RenderSoundStemCommand,
    SoundDesignConfig,
    SoundDesignPlanner,
    SoundStemRenderer,
)
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
from media_house.modules.reframing_intelligence.application.contracts import (
    AnalyzeReframingCommand,
    ReframingAnalysis,
    ReframingAnalyzer,
    ReframingConfig,
    SafeArea,
    TargetFormat,
)
from media_house.modules.reframing_intelligence.application.contracts import (
    ResultState as ReframingState,
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
from media_house.modules.transition_engine.application.contracts import (
    DOCUMENT_TYPE as TRANSITION_DOCUMENT,
)
from media_house.modules.transition_engine.application.contracts import (
    STYLE_PROFILE as TRANSITION_PROFILE_OF,
)
from media_house.modules.transition_engine.application.contracts import (
    PlanTransitionsCommand,
    TransitionConfig,
    TransitionPlan,
    TransitionPlanner,
)
from media_house.modules.vfx_engine.application.contracts import (
    DOCUMENT_TYPE as VFX_DOCUMENT,
)
from media_house.modules.vfx_engine.application.contracts import (
    FORMAT_PROFILE as VFX_PROFILE_OF,
)
from media_house.modules.vfx_engine.application.contracts import (
    PlanVfxCommand,
    VfxConfig,
    VfxPlanner,
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
from media_house.modules.visual_style_engine.application.contracts import (
    DEFAULT_OUTPUT,
    ResolveStyleCommand,
    ResolveStyleResult,
    StyleConfig,
    StyleResolver,
)
from media_house.shared.errors import Err, InvariantViolation, Ok

_UNAVAILABLE_CODES = (
    "no_audio_track",
    "no_video_stream",
    "nothing_to_cover",
    "nothing_to_judge",
    "nothing_to_frame",
    "nothing_to_plan",  # no speech: a meme, a compilation or an uncut take has no story to plan
    "nothing_to_edit",
    "nothing_to_score",  # Layer 4: a timeline with nothing to place (no clip)
    "nothing_to_treat",
    "nothing_to_transition",
)
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
    #: The output shape (``9:16``, ``16:9``, ``1:1`` or a custom ``W:H``) the reframing stage plans
    #: crops for. There is no default: a crop means nothing without a chosen format, so the stage
    #: is skipped (and says so) until one is given.
    reframing_target: str | None = None
    #: The output size in pixels (width, height); ``None`` = the standard size of the format.
    reframing_resolution: tuple[int, int] | None = None
    #: A reframing profile (``talking_head``, ``shorts_reels`` ...); ``None`` = by the story format.
    reframing_profile: str | None = None
    #: Reserved rectangles of the OUTPUT picture (captions, a title, an interface margin). Nothing
    #: is assumed: with none listed there is no safe-area constraint.
    reframing_safe_areas: tuple[SafeArea, ...] = ()
    #: Pin the transcription language (ISO code such as ``de``); ``None`` = auto-detect.
    transcription_language: str | None = None
    # --- Layer 4 ---------------------------------------------------------------------------
    #: The delivery shape every Layer 4 plan is made for (``16:9``, ``9:16`` or ``1:1``: the
    #: shapes the channel style defines). ``None`` = the reframing target, else the format
    #: profile's own shape.
    output_format: str | None = None
    #: The delivery size in pixels (width, height); ``None`` = the standard size of the shape.
    output_resolution: tuple[int, int] | None = None
    #: The channel format profile (``shorts_haul``, ``ranking`` ...: visual_style_engine's). The
    #: ONE place the format is chosen: every Layer 4 module maps it to its own profile.
    #: ``None`` = by the story format, then the output shape.
    format_profile: str | None = None
    #: A sponsored video (the "Werbung" label is mandatory over the whole timeline).
    sponsored: bool = False
    #: Per-module profiles; ``None`` = the module's own mapping of the style's format profile.
    graphics_profile: str | None = None
    caption_profile: str | None = None
    transition_profile: str | None = None
    vfx_profile: str | None = None
    sound_profile: str | None = None
    #: Render Layer 4 assets (graphics, captions, transition overlays, VFX clips, the sound
    #: stem). Off = plans only (every plan is still complete and stored).
    layer4_render: bool = True
    #: Never use the GPU. Only the device settings change; results keep their identity because
    #: the owners leave the device out of their cache keys.
    cpu_only: bool = False
    #: Video intelligence may download a model that is not on disk (pinned checksum). Off by
    #: default: without it a missing model makes that analyzer ``not_available``.
    allow_model_download: bool = False


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


@dataclass(frozen=True, slots=True)
class _Basis:
    """What every Layer 4 task works on: the edit (or the whole recording) and the style."""

    view: EditView
    style: ResolveStyleResult
    #: Why there is no edit (empty when there is one).
    note: str = ""

    @property
    def path(self) -> str:
        return "edit" if self.view.has_edit else "no_speech"


def _pick(explicit: str | None, mapping: Mapping[str, str], style_profile: str) -> str | None:
    """A module's profile: the explicit choice, else its mapping of the style's format profile
    (``None`` = the module decides from the story format)."""
    return explicit or mapping.get(style_profile)


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
        reframing_analyzer: ReframingAnalyzer,
        edit_planner: EditPlanner,
        relationships: RelationshipDetection,
        style_resolver: StyleResolver,
        graphics_planner: MotionGraphicsPlanner,
        caption_planner: CaptionPlanner,
        transition_planner: TransitionPlanner,
        vfx_planner: VfxPlanner,
        sound_planner: SoundDesignPlanner,
        stem_renderer: SoundStemRenderer,
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
        self._reframing_analyzer = reframing_analyzer
        self._edit_planner = edit_planner
        self._relationships = relationships
        self._style_resolver = style_resolver
        self._graphics_planner = graphics_planner
        self._caption_planner = caption_planner
        self._transition_planner = transition_planner
        self._vfx_planner = vfx_planner
        self._sound_planner = sound_planner
        self._stem_renderer = stem_renderer
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
            TaskKind.REFRAMING: self.reframing,
            TaskKind.EDIT_PLAN: self.edit_plan,
            TaskKind.RELATIONSHIPS: self.relationships,
            TaskKind.VISUAL_STYLE: self.visual_style,
            TaskKind.GRAPHICS: self.graphics,
            TaskKind.CAPTIONS: self.captions,
            TaskKind.TRANSITIONS: self.transitions,
            TaskKind.VFX: self.vfx,
            TaskKind.SOUND_DESIGN: self.sound_design,
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
        transcription = TranscriptionConfig(
            language=self._settings.transcription_language,
            device="cpu" if self._settings.cpu_only else "auto",
        )
        command = AnalyzeAudioCommand(
            job.id.asset_id, AudioIntelligenceConfig(transcription=transcription)
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
            device=DeviceKind.CPU if self._settings.cpu_only else DeviceKind.AUTO,
            allow_model_download=self._settings.allow_model_download,
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

    def reframing(self, job: TaskJob) -> TaskResult:
        """Recommend a crop per shot for the chosen target format from the in-memory upstream
        results (nothing is recomputed). The editing stage consumes the result of this run."""
        asset_id = job.id.asset_id
        s = self._settings
        if s.reframing_target is None:
            return TaskResult(
                TaskStatus.SKIPPED_UNAVAILABLE,
                "no target format chosen: framing means nothing without one (reframing_target)",
            )
        try:
            width, height = s.reframing_resolution or (None, None)
            target = TargetFormat.of(s.reframing_target, width=width, height=height)
        except InvariantViolation as exc:
            return TaskResult(TaskStatus.FAILED, exc.user_message)
        command = AnalyzeReframingCommand(
            (asset_id,),
            target,
            config=ReframingConfig(profile=s.reframing_profile, safe_areas=s.reframing_safe_areas),
            story_format=s.story_format,
            story_plan=self._upstream(job, TaskKind.STORY, StoryPlan),
            video_analyses=_given(
                asset_id, self._upstream(job, TaskKind.VIDEO_ANALYSIS, VideoAnalysis)
            ),
            alignments=_given(asset_id, self._upstream(job, TaskKind.ALIGNMENT, AlignmentDocument)),
        )
        match self._reframing_analyzer.execute(command, job.ctx):
            case Ok(result):
                analysis = result.analysis
                partial = analysis.state is ReframingState.PARTIAL
                base = _outcome(
                    result.created,
                    reframing_asset_id=result.asset.id,
                    analysis_version=analysis.analysis_version,
                    target=analysis.target.name,
                    profile=analysis.profile,
                    shots=analysis.metrics.shots,
                    reused_shots=analysis.metrics.reused,
                    unreframeable=analysis.metrics.unreframeable,
                    uncertain=analysis.metrics.uncertain,
                )
                return TaskResult(
                    TaskStatus.PARTIAL if partial else base.status,
                    "some shots had no subject or quality evidence" if partial else "",
                    warnings=tuple(r.reasons[0] for r in analysis.unreframeable() if r.reasons)[:3],
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
            reframing=self._upstream(job, TaskKind.REFRAMING, ReframingAnalysis),
            reframing_target=self._settings.reframing_target,
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

    # --- Layer 4: stored plans and rendered assets ------------------------------------------
    def _edit_state(self, job: TaskJob) -> EditPlan | TaskResult | str:
        """The edit plan of this run; else WHY there is none (a ``str`` note: no speech, or no
        edit was asked for). An edit that was asked for and did not deliver for any other reason
        (failed, cancelled, a failed upstream) is a ``TaskResult``: the whole recording never
        silently stands in for a failed edit."""
        found = job.upstream.get(TaskId(job.id.asset_id, TaskKind.EDIT_PLAN))
        if found is None:
            return "no edit plan in this run: planned on the whole recording (no-speech path)"
        plan = self._upstream(job, TaskKind.EDIT_PLAN, EditPlan)
        if plan is not None:
            return plan
        if TaskStatus.SKIPPED_UNAVAILABLE in (found.status, found.cause):
            why = found.detail or found.status.value
            return f"no edit plan ({why}): planned on the whole recording (no-speech path)"
        return TaskResult(
            TaskStatus.SKIPPED_DEPENDENCY,
            f"edit_plan {found.status.value}"
            + (f": {found.detail}" if found.detail else "")
            + "; the whole recording is never used in place of a failed edit",
        )

    def _basis(self, job: TaskJob) -> _Basis | TaskResult:
        style = self._upstream(job, TaskKind.VISUAL_STYLE, ResolveStyleResult)
        if style is None:
            return TaskResult(TaskStatus.SKIPPED_DEPENDENCY, "no resolved visual style")
        state = self._edit_state(job)
        if isinstance(state, TaskResult):
            return state
        if isinstance(state, EditPlan):
            return _Basis(view_of(state, self._upstream(job, TaskKind.STORY, StoryPlan)), style)
        inspection = self._upstream(job, TaskKind.INSPECT, InspectionResult)
        view = (
            None
            if inspection is None
            else no_speech_view(job.id.asset_id, inspection, self._settings.story_format)
        )
        if view is None:
            return TaskResult(
                TaskStatus.SKIPPED_UNAVAILABLE,
                "no edit plan and no inspected duration: there is nothing to place",
            )
        return _Basis(view, style, note=state)

    def _output_size(self) -> tuple[int, int] | None:
        """The delivery size: explicit, else the standard size of the delivery shape (the
        output format, else the reframing target), else ``None`` (the profile's own shape)."""
        s = self._settings
        if s.output_resolution is not None:
            return s.output_resolution
        shape = s.output_format or s.reframing_target
        if shape is None:
            return None
        size = DEFAULT_OUTPUT.get(shape)
        if size is None:
            raise InvariantViolation(
                f"unsupported output format {shape!r}",
                user_message=f"The channel style defines {', '.join(DEFAULT_OUTPUT)}, not {shape}.",
            )
        return size

    def visual_style(self, job: TaskJob) -> TaskResult:
        """Resolve (or reuse) the channel style for the delivery format: the one place the format
        profile is chosen. Its reference goes to every other Layer 4 task."""
        state = self._edit_state(job)
        if isinstance(state, TaskResult):
            return state
        plan = state if isinstance(state, EditPlan) else None
        s = self._settings
        try:
            size = self._output_size()
        except InvariantViolation as exc:
            return TaskResult(TaskStatus.FAILED, exc.user_message)
        command = ResolveStyleCommand(
            anchor_asset_id=job.id.asset_id,
            project_id=plan.project_id if plan else None,
            width=size[0] if size else None,
            height=size[1] if size else None,
            config=StyleConfig(profile=s.format_profile, sponsored=s.sponsored),
            story_format=plan.target_format if plan else s.story_format,
        )
        match self._style_resolver.execute(command):
            case Ok(result):
                style = result.style
                errors = tuple(c.message for c in style.checks if c.severity.value == "error")
                return TaskResult(
                    TaskStatus.SUCCEEDED if result.created else TaskStatus.REUSED,
                    "" if plan else str(state),
                    warnings=errors[:3],
                    facts={
                        "plan_asset_id": result.document.id,
                        "document_type": "visual_style",
                        "plan_version": style.style_version,
                        "fingerprint": style.fingerprint,
                        "profile": style.profile,
                        "identity_version": style.identity_version,
                        "output": f"{style.output.width}x{style.output.height}",
                        "path": "edit" if plan else "no_speech",
                    },
                    payload=result,
                )
            case Err(error):
                return _from_error(error)

    @staticmethod
    def _planned(
        basis: _Basis,
        *,
        created: bool,
        state: str,
        document: MediaAssetDto,
        document_type: str,
        plan_version: int,
        rendered: list[str],
        payload: object,
        warnings: tuple[str, ...] = (),
        **facts: JsonValue,
    ) -> TaskResult:
        """One shape for every Layer 4 plan: the report's "Layer 4 package" reads these facts."""
        partial = state == "partial"
        view, ref = basis.view, basis.style.ref
        why = "some items fell back or could not be made" if partial else ""
        status = TaskStatus.SUCCEEDED if created else TaskStatus.REUSED
        return TaskResult(
            TaskStatus.PARTIAL if partial else status,
            "; ".join(d for d in (why, basis.note) if d),
            warnings=warnings[:5],
            facts={
                "plan_asset_id": document.id,
                "document_type": document_type,
                "plan_version": plan_version,
                "state": state,
                "path": basis.path,
                "anchor_asset_id": view.anchor_asset_id,
                "edit_plan_id": view.edit_plan_id,
                "edit_plan_version": view.plan_version,
                "style_asset_id": ref.document_id,
                "style_fingerprint": ref.fingerprint,
                "rendered_asset_ids": list(dict.fromkeys(rendered)),
                **facts,
            },
            payload=payload,
        )

    @staticmethod
    def _size(basis: _Basis) -> tuple[int, int]:
        output = basis.style.style.output
        return output.width, output.height

    def graphics(self, job: TaskJob) -> TaskResult:
        """Plan (and render) the graphics of the edit from its sections, editorial requests and
        the commerce facts heard in each section, in the resolved style."""
        basis = self._basis(job)
        if isinstance(basis, TaskResult):
            return basis
        s = self._settings
        width, height = self._size(basis)
        brief = graphics_brief(
            basis.view,
            width=width,
            height=height,
            sponsored=s.sponsored,
            transcript=self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline),
            video=self._upstream(job, TaskKind.VIDEO_ANALYSIS, VideoAnalysis),
        )
        profile = _pick(s.graphics_profile, GRAPHICS_PROFILE_OF, basis.style.style.profile)
        command = PlanGraphicsCommand(
            brief=brief,
            style=basis.style.ref,
            config=MotionGraphicsConfig(profile=profile),
            render=s.layer4_render,
        )
        match self._graphics_planner.execute(command, job.ctx):
            case Ok(result):
                plan = result.plan
                return self._planned(
                    basis,
                    created=result.created,
                    state=plan.state.value,
                    document=result.document,
                    document_type=GRAPHICS_DOCUMENT,
                    plan_version=plan.plan_version,
                    rendered=[
                        g.render.asset_id for g in plan.graphics if g.render and g.render.asset_id
                    ],
                    payload=plan,
                    warnings=plan.warnings,
                    profile=plan.provenance.profile,
                    graphics=len(plan.graphics),
                    drawn=sum(g.status is ItemStatus.OK for g in plan.graphics),
                )
            case Err(error):
                return _from_error(error)

    def captions(self, job: TaskJob) -> TaskResult:
        """Plan (and render) the captions from the transcript and content analysis of this run,
        after the graphics (a fact a graphic shows is not captioned again)."""
        basis = self._basis(job)
        if isinstance(basis, TaskResult):
            return basis
        asset_id, s = job.id.asset_id, self._settings
        width, height = self._size(basis)
        command = PlanCaptionsCommand(
            timeline=caption_timeline(basis.view, width=width, height=height),
            style=basis.style.ref,
            config=CaptionConfig(profile=s.caption_profile),
            render=s.layer4_render,
            transcripts=_given(
                asset_id, self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline)
            ),
            contents=_given(
                asset_id, self._upstream(job, TaskKind.CONTENT_ANALYSIS, ContentAnalysis)
            ),
        )
        match self._caption_planner.execute(command, job.ctx):
            case Ok(result):
                plan = result.plan
                return self._planned(
                    basis,
                    created=result.created,
                    state=plan.state.value,
                    document=result.asset,
                    document_type=CAPTION_DOCUMENT,
                    plan_version=plan.plan_version,
                    rendered=[c.render.asset_id for c in plan.segments if c.render.asset_id],
                    payload=plan,
                    warnings=plan.warnings,
                    mode=plan.mode.value,
                    mode_reason=plan.mode_reason,
                    captions=len(plan.segments),
                )
            case Err(error):
                return _from_error(error)

    def transitions(self, job: TaskJob) -> TaskResult:
        """Plan the transition (and the audio transition) of every cut of the edit, bookending
        what the graphics plan placed; graphic overlays are rendered through motion graphics."""
        basis = self._basis(job)
        if isinstance(basis, TaskResult):
            return basis
        s = self._settings
        sequence = transition_sequence(
            basis.view, graphics=self._upstream(job, TaskKind.GRAPHICS, GraphicsPlan)
        )
        profile = _pick(s.transition_profile, TRANSITION_PROFILE_OF, basis.style.style.profile)
        command = PlanTransitionsCommand(
            sequence,
            style=basis.style.ref,
            config=TransitionConfig(profile=profile),
            render=s.layer4_render,
        )
        match self._transition_planner.execute(command, job.ctx):
            case Ok(result):
                plan = result.plan
                return self._planned(
                    basis,
                    created=result.created,
                    state=plan.state.value,
                    document=result.asset,
                    document_type=TRANSITION_DOCUMENT,
                    plan_version=plan.plan_version,
                    rendered=[
                        t.overlay.asset_id
                        for t in plan.transitions
                        if t.overlay is not None and t.overlay.asset_id
                    ],
                    payload=plan,
                    warnings=plan.warnings,
                    profile=plan.profile,
                    boundaries=len(plan.transitions),
                    events=len(plan.events),
                )
            case Err(error):
                return _from_error(error)

    def vfx(self, job: TaskJob) -> TaskResult:
        """Plan (and render) shot effects with restraint, from the video analysis of this run
        (handed in memory) and the speech each clip carries (never retimed)."""
        basis = self._basis(job)
        if isinstance(basis, TaskResult):
            return basis
        asset_id, s = job.id.asset_id, self._settings
        width, height = self._size(basis)
        shots = vfx_shots(
            basis.view,
            width=width,
            height=height,
            transcript=self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline),
        )
        command = PlanVfxCommand(
            shots,
            style=basis.style.ref,
            config=VfxConfig(
                profile=_pick(s.vfx_profile, VFX_PROFILE_OF, basis.style.style.profile)
            ),
            render=s.layer4_render,
            video_analyses=_given(
                asset_id, self._upstream(job, TaskKind.VIDEO_ANALYSIS, VideoAnalysis)
            ),
        )
        match self._vfx_planner.execute(command, job.ctx):
            case Ok(result):
                plan = result.plan
                return self._planned(
                    basis,
                    created=result.created,
                    state=plan.state.value,
                    document=result.asset,
                    document_type=VFX_DOCUMENT,
                    plan_version=plan.plan_version,
                    rendered=[t.output.asset_id for t in plan.treatments if t.output is not None],
                    payload=plan,
                    warnings=plan.warnings,
                    profile=plan.provenance.profile,
                    treatments=len(plan.treatments),
                )
            case Err(error):
                return _from_error(error)

    def sound_design(self, job: TaskJob) -> TaskResult:
        """Plan music, effects and mix intent from the owner's sound library, synced to the
        graphics, transition and caption events of this run, then render the stem."""
        basis = self._basis(job)
        if isinstance(basis, TaskResult):
            return basis
        asset_id, s = job.id.asset_id, self._settings
        graphics = self._upstream(job, TaskKind.GRAPHICS, GraphicsPlan)
        events = sync_events(
            basis.view,
            graphics=graphics,
            transitions=self._upstream(job, TaskKind.TRANSITIONS, TransitionPlan),
            captions=self._upstream(job, TaskKind.CAPTIONS, CaptionPlan),
        )
        style_profile = basis.style.style.profile
        # Music profiles share the channel's format names; ``None`` = by the story format.
        profile = s.sound_profile or (style_profile if style_profile in SOUND_PROFILES else None)
        command = PlanSoundDesignCommand(
            soundtrack_timeline(
                basis.view, events, graphics.timeline_end_with_bookends if graphics else None
            ),
            config=SoundDesignConfig(profile=profile),
            audio_timelines=_given(
                asset_id, self._upstream(job, TaskKind.AUDIO_ANALYSIS, AudioIntelligenceTimeline)
            ),
            story_plan=self._upstream(job, TaskKind.STORY, StoryPlan),
            pacing=self._upstream(job, TaskKind.PACING, PacingAnalysis),
        )
        planned = self._sound_planner.execute(command, job.ctx)
        if isinstance(planned, Err):
            return _from_error(planned.error)
        result = planned.value
        plan = result.plan
        created, state, warnings = result.created, plan.state.value, plan.warnings
        rendered: list[str] = []
        if s.layer4_render and plan.used_asset_ids():
            match self._stem_renderer.execute(RenderSoundStemCommand(result.asset.id), job.ctx):
                case Ok(stem):
                    created = created or stem.created
                    rendered.append(stem.asset.id)
                case Err(error):
                    state = "partial"
                    warnings = (f"stem not rendered: {getattr(error, 'user_message', error)}",)
        return self._planned(
            basis,
            created=created,
            state=state,
            document=result.asset,
            document_type=SOUND_DESIGN_PLAN,
            plan_version=plan.plan_version,
            rendered=rendered,
            payload=plan,
            warnings=warnings,
            profile=plan.profile,
            music_cues=len(plan.music),
            sound_effects=len(plan.sfx),
            missing_assets=len(plan.missing_assets),
            sync_events=len(events),
            credits=plan.credit_lines,
        )

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
