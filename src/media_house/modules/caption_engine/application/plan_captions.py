"""Plan (and optionally render) the captions of one edit; store a ``caption_plan`` document.

Flow: check timeline -> style (supplied or stored, pinned) -> profile -> read upstream results
(transcripts, sentences, tracks, crops, planned graphics; nothing is recomputed) -> cache
lookup (identical inputs = the stored plan, nothing computed) -> plan (pure domain) -> style
drift check -> render through motion graphics (optional) -> QC gate -> store.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Final

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.contracts import (
    AudioCatalog,
    AudioIntelligenceTimeline,
)
from media_house.modules.caption_engine.application.render import (
    apply_renders,
    motion_config,
    not_rendered,
    request_for,
)
from media_house.modules.caption_engine.application.results import CaptionResults
from media_house.modules.caption_engine.application.views import (
    burned_in_spans,
    covered_spans,
    full_screen_spans,
    style_facts,
    subject_boxes,
    transcript_facts,
)
from media_house.modules.caption_engine.domain.engine import (
    EMOJI_NOT_RENDERED,
    PlanMeta,
    cache_key,
    digest,
    plan_captions,
)
from media_house.modules.caption_engine.domain.errors import (
    InvalidCaptionConfig,
    InvalidCaptionPlan,
    InvalidCaptionTimeline,
    StyleNotAvailable,
)
from media_house.modules.caption_engine.domain.facts import (
    BurnedInSpan,
    CaptionInputs,
    CaptionTimeline,
    CoveredSpan,
    SubjectBox,
    TranscriptFacts,
)
from media_house.modules.caption_engine.domain.model import CaptionDecision, CaptionPlan
from media_house.modules.caption_engine.domain.profile import CaptionConfig, CaptionProfile
from media_house.modules.caption_engine.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.caption_engine.domain.values import (
    PLANNER_VERSION,
    RESULT_OPERATION,
    InputHow,
    InputRecord,
    QcCategory,
    QcIssue,
    RenderStatus,
    ResultState,
    Severity,
    StageReport,
    StageState,
)
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalysis,
    ContentCatalog,
)
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.motion_graphics_engine.application.contracts import (
    GraphicsCatalog,
    GraphicsPlan,
    GraphicsRenderer,
    RendererNotAvailable,
    RenderGraphicsCommand,
)
from media_house.modules.reframing_intelligence.application.contracts import ReframingCatalog
from media_house.modules.video_intelligence.application.contracts import VideoAnalysis, VideoCatalog
from media_house.modules.visual_style_engine.application.contracts import (
    ElementKind,
    PlacementUse,
    ResolvedStyle,
    StyleRef,
    StyleUsage,
    StyleValidator,
    VisualStyleCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths

LIMITATIONS: Final = (
    EMOJI_NOT_RENDERED,
    "subject avoidance uses video tracks on the video clock (no audio/video sync correction,"
    " sub-second) and only where the output shows the source uncropped or a stored reframing"
    " crop maps it",
    "segmentation and layout knobs apply per project; section overrides change selection,"
    " emphasis, emoji and animation",
    "speakers are named only when audio_intelligence ran diarization; no translation",
    "the caption block size is an estimate; motion graphics measures the real font",
    "validated on synthetic German transcripts and the MEsc style only (no real ASR here)",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanCaptionsCommand:
    """``style``: the ``ResolvedStyle`` or the ``StyleRef`` of the stored one (pinned).
    ``transcripts`` / ``contents``: results the caller already has (else the stored ones).
    ``render``: also draw every caption group through motion graphics."""

    timeline: CaptionTimeline
    style: ResolvedStyle | StyleRef
    config: CaptionConfig = field(default_factory=CaptionConfig)
    render: bool = False
    decisions: tuple[CaptionDecision, ...] = ()
    carry_decisions: bool = True
    transcripts: Mapping[str, AudioIntelligenceTimeline] = field(default_factory=dict)
    contents: Mapping[str, ContentAnalysis] = field(default_factory=dict)
    #: The graphics plan of this edit the caller already has (else the newest stored one).
    graphics: GraphicsPlan | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanCaptionsResult:
    plan: CaptionPlan
    asset: MediaAssetDto
    #: ``False`` when the identical plan was already stored (nothing computed).
    created: bool


type PlanCaptionsError = (
    MediaError
    | InvalidCaptionTimeline
    | InvalidCaptionConfig
    | StyleNotAvailable
    | InvalidCaptionPlan
)


@dataclass(slots=True)
class _Gathered:
    transcripts: list[TranscriptFacts] = field(default_factory=list)
    subjects: list[SubjectBox] = field(default_factory=list)
    covered: list[CoveredSpan] = field(default_factory=list)
    full_screen: list[tuple[Fraction, Fraction]] = field(default_factory=list)
    burned_in: list[BurnedInSpan] = field(default_factory=list)
    records: list[InputRecord] = field(default_factory=list)
    stages: list[StageReport] = field(default_factory=list)


class PlanCaptions:
    """``CaptionPlanner``."""

    def __init__(
        self,
        library: MediaLibrary,
        audio: AudioCatalog,
        content: ContentCatalog,
        video: VideoCatalog,
        reframing: ReframingCatalog,
        inspection: InspectionCatalog,
        graphics: GraphicsCatalog,
        styles: VisualStyleCatalog,
        validator: StyleValidator,
        renderer: GraphicsRenderer,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._audio = audio
        self._content = content
        self._video = video
        self._reframing = reframing
        self._inspection = inspection
        self._graphics = graphics
        self._styles = styles
        self._validator = validator
        self._renderer = renderer
        self._documents = DerivedDocuments(library, paths)
        self._results = CaptionResults(library)
        self._clock = clock

    def execute(
        self, command: PlanCaptionsCommand, ctx: JobContext
    ) -> Result[PlanCaptionsResult, PlanCaptionsError]:
        timeline = command.timeline
        problems = timeline.problems()
        if problems:
            return Err(InvalidCaptionTimeline("; ".join(problems)))
        anchor = self._library.get(timeline.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        loaded = self._style(command.style)
        if isinstance(loaded, Err):
            return loaded
        style, ref = loaded.value
        if (style.output.width, style.output.height) != (timeline.width, timeline.height):
            return Err(
                StyleNotAvailable(
                    f"the style is resolved for {style.output.width}x{style.output.height},"
                    f" the edit is {timeline.width}x{timeline.height}"
                )
            )
        try:
            profile = command.config.resolve(
                style_profile=style.profile,
                story_format=timeline.story_format,
                aspect=timeline.aspect,
            )
            facts = style_facts(style, ref, profile)
        except InvalidCaptionConfig as exc:
            return Err(exc)
        except (KeyError, StopIteration) as exc:
            return Err(StyleNotAvailable(f"the style has no usable caption look ({exc})"))
        ctx.raise_if_cancelled()
        gathered = self._gather(command, profile)
        inputs = CaptionInputs(
            timeline=timeline,
            transcripts=tuple(gathered.transcripts),
            style=facts,
            subjects=tuple(gathered.subjects),
            covered=tuple(gathered.covered),
            full_screen=tuple(gathered.full_screen),
            burned_in=tuple(gathered.burned_in),
        )
        previous = self._results.latest(timeline.anchor_asset_id, project_id=timeline.project_id)
        prior = previous.value if isinstance(previous, Ok) else None
        decisions = _merge(command.decisions, prior[0].decisions if prior else ())
        if not command.carry_decisions:
            decisions = command.decisions
        configuration = command.config.fingerprint(profile)
        key = cache_key(inputs, configuration, decisions, render=command.render, renderer="")
        spec = DocumentSpec(RESULT_OPERATION, PLANNER_VERSION, _json(key))
        fingerprint = self._documents.fingerprint(timeline.anchor_asset_id, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        hit = self._documents.load(timeline.anchor_asset_id, fingerprint.value, plan_from_json)
        while hit is not None:
            if not command.render or hit[0].fully_rendered:
                return Ok(PlanCaptionsResult(plan=hit[0], asset=hit[1], created=False))
            # Rendering was not possible last time: try again as a new version of the plan.
            spec = DocumentSpec(
                RESULT_OPERATION, PLANNER_VERSION, _json({**key, "retry_of": hit[1].id})
            )
            retry = self._documents.fingerprint(timeline.anchor_asset_id, spec)
            if isinstance(retry, Err):
                return retry
            hit = self._documents.load(timeline.anchor_asset_id, retry.value, plan_from_json)
        history = [
            a
            for a in self._results.history(timeline.anchor_asset_id)
            if a.metadata.get("project_id") == timeline.project_id
        ]
        plan = plan_captions(
            inputs,
            command.config,
            profile,
            decisions,
            now=self._clock.now(),
            meta=PlanMeta(
                plan_id=f"captions:{digest(key)[:16]}",
                plan_version=len(history) + 1,
                supersedes=history[0].id if history else None,
                style=facts.binding,
                configuration_fingerprint=digest(configuration),
                inputs=tuple(gathered.records),
                stages=tuple(gathered.stages),
                limitations=LIMITATIONS,
                engines=(("caption_engine", str(PLANNER_VERSION)),),
            ),
        )
        plan = self._drift(plan, style)
        plan = self._render(plan, command, profile, ctx)
        critical = [c.message for c in plan.checks if c.severity is Severity.CRITICAL]
        if critical:
            return Err(InvalidCaptionPlan(critical))
        stored = self._documents.store(
            anchor.value,
            plan_to_json(plan),
            filename="caption_plan.json",
            spec=spec,
            display_name=f"{anchor.value.display_name} (captions {plan.mode.value})",
            metadata={
                "project_id": timeline.project_id,
                "edit_plan_id": timeline.edit.edit_plan_id if timeline.edit else None,
                "plan_version": plan.plan_version,
                "profile": profile.name,
                "mode": plan.mode.value,
                "state": plan.state.value,
                "segments": len(plan.segments),
                "style_fingerprint": facts.binding.fingerprint,
            },
        )
        if isinstance(stored, Err):
            return stored
        return Ok(PlanCaptionsResult(plan=plan, asset=stored.value, created=True))

    # --- inputs ---------------------------------------------------------------------------

    def _style(
        self, style: ResolvedStyle | StyleRef
    ) -> Result[tuple[ResolvedStyle, StyleRef | None], StyleNotAvailable]:
        if isinstance(style, ResolvedStyle):
            return Ok((style, None))
        loaded = self._styles.load_ref(style)
        if isinstance(loaded, Err):
            return Err(StyleNotAvailable(str(loaded.error)))
        return Ok((loaded.value, style))

    def _gather(self, command: PlanCaptionsCommand, profile: CaptionProfile) -> _Gathered:
        out = _Gathered()
        timeline = command.timeline
        heard = list(dict.fromkeys(c.asset_id for c in timeline.clips if c.audio))
        shown = list(dict.fromkeys(c.asset_id for c in timeline.clips if c.picture))
        for asset_id in heard:
            audio = _read(command.transcripts, asset_id, self._audio.latest, "transcript", out)
            if audio is None:
                continue
            document = (
                None
                if asset_id in command.transcripts
                else next(
                    (
                        r.identity
                        for r in out.records
                        if (r.asset_id, r.kind) == (asset_id, "transcript")
                    ),
                    None,
                )
            )
            content = _read(command.contents, asset_id, self._content_latest, "content", out)
            out.transcripts.append(
                transcript_facts(audio, asset_id, document, content, profile.transcript.language)
            )
        for asset_id in shown:
            video = _read({}, asset_id, self._video.latest, "video_analysis", out)
            if video is None:
                continue
            # the source may already be captioned (a published video re-edited): never twice
            out.burned_in += burned_in_spans(video)
            if profile.layout.avoid_subjects:
                self._subjects(video, asset_id, timeline, out)
        if not profile.layout.avoid_subjects:
            out.stages.append(StageReport(stage="subjects", state=StageState.SKIPPED))
        if timeline.edit is not None:
            self._covered(timeline, command.graphics, out)
        return out

    def _content_latest(
        self, asset_id: str
    ) -> Result[tuple[ContentAnalysis, MediaAssetDto] | None, MediaHouseError]:
        return self._content.latest(asset_id)

    def _subjects(
        self, video: VideoAnalysis, asset_id: str, timeline: CaptionTimeline, out: _Gathered
    ) -> None:
        size = self._source_size(asset_id)
        framing = None
        if size is None or abs(size[0] / size[1] - timeline.width / timeline.height) >= 0.01:
            found = self._reframing.latest(asset_id, target=timeline.aspect)
            if not (isinstance(found, Ok) and found.value):
                # one analysis covers all the story's recordings and is stored under the anchor
                found = self._reframing.latest(timeline.anchor_asset_id, target=timeline.aspect)
            framing = found.value[0] if isinstance(found, Ok) and found.value else None
            out.records.append(
                InputRecord(
                    kind="reframing",
                    how=InputHow.REUSED if framing else InputHow.NOT_AVAILABLE,
                    asset_id=asset_id,
                    identity=found.value[1].id if isinstance(found, Ok) and found.value else None,
                )
            )
        boxes, why = subject_boxes(video, size, (timeline.width, timeline.height), framing)
        out.subjects += boxes
        out.stages.append(
            StageReport(
                stage=f"subjects:{asset_id}",
                state=StageState.OK if why is None else StageState.NOT_AVAILABLE,
                detail=why or f"{len(boxes)} subject positions",
            )
        )

    def _source_size(self, asset_id: str) -> tuple[int, int] | None:
        found = self._inspection.find(asset_id)
        video = found.inspection.primary_video if found is not None else None
        if video is not None:
            return video.geometry.display_size
        asset = self._library.get(asset_id)
        if isinstance(asset, Ok) and asset.value.width and asset.value.height:
            return asset.value.width, asset.value.height
        return None

    def _covered(
        self, timeline: CaptionTimeline, given: GraphicsPlan | None, out: _Gathered
    ) -> None:
        plan = given
        if plan is None:
            found = self._graphics.latest(timeline.anchor_asset_id, project_id=timeline.project_id)
            plan = found.value[0] if isinstance(found, Ok) and found.value else None
        edit = timeline.edit
        usable = (
            plan is not None
            and edit is not None
            and plan.edit is not None
            and (plan.edit.edit_plan_id, plan.edit.plan_version)
            == (edit.edit_plan_id, edit.plan_version)
        )
        out.records.append(
            InputRecord(
                kind="graphics_plan",
                how=InputHow.REUSED if usable else InputHow.NOT_AVAILABLE,
                asset_id=timeline.anchor_asset_id,
                identity=plan.plan_id if plan is not None and usable else None,
                detail="" if usable else "no graphics plan of this edit: facts may repeat",
            )
        )
        if usable and plan is not None:
            out.covered += covered_spans(plan)
            out.full_screen += full_screen_spans(plan)

    # --- after planning ----------------------------------------------------------------------

    def _drift(self, plan: CaptionPlan, style: ResolvedStyle) -> CaptionPlan:
        if not plan.segments:
            return plan
        usage = StyleUsage(
            module="caption_engine",
            fingerprint=style.fingerprint,
            width=plan.width,
            height=plan.height,
            placements=tuple(
                PlacementUse(
                    element_id=s.segment_id,
                    kind=ElementKind.CAPTION,
                    zone=s.layout.zone,
                    left=s.layout.left,
                    top=s.layout.top,
                    width=s.layout.width,
                    height=s.layout.height,
                    reason="speech caption group",
                )
                for s in plan.segments
            ),
        )
        found = tuple(
            QcIssue(
                code=f"style.{i.code}",
                severity=Severity(i.severity.value),
                category=QcCategory(i.category.value),
                message=i.message,
                item_id=i.item_id,
                evidence=i.evidence,
            )
            for i in self._validator.validate(style, usage)
        )
        return _settle(replace(plan, checks=(*plan.checks, *found)))

    def _render(
        self,
        plan: CaptionPlan,
        command: PlanCaptionsCommand,
        profile: CaptionProfile,
        ctx: JobContext,
    ) -> CaptionPlan:
        segments = list(plan.segments)
        eligible = [s for s in segments if s.render.status is RenderStatus.NOT_REQUESTED]
        if not command.render or not eligible:
            stage = StageReport(
                stage="render",
                state=StageState.SKIPPED,
                detail="not requested" if not command.render else "nothing to draw",
            )
            return replace(plan, stages=(*plan.stages, stage))
        rendered = self._renderer.render(
            RenderGraphicsCommand(
                anchor_asset_id=plan.anchor_asset_id,
                requests=tuple(request_for(s) for s in eligible),
                style=command.style,
                fps=plan.fps,
                config=motion_config(profile),
                story_format=command.timeline.story_format,
            ),
            ctx,
        )
        if isinstance(rendered, Err):
            error = rendered.error
            status = (
                RenderStatus.NOT_AVAILABLE
                if isinstance(error, RendererNotAvailable)
                else RenderStatus.FAILED
            )
            stage = StageReport(
                stage="render", state=StageState.NOT_AVAILABLE, detail=error.user_message
            )
            return _settle(
                replace(
                    plan,
                    segments=tuple(not_rendered(segments, status, error.user_message)),
                    stages=(*plan.stages, stage),
                    warnings=(*plan.warnings, f"captions not rendered: {error.user_message}"),
                )
            )
        updated, issues = apply_renders(segments, rendered.value)
        name, version = rendered.value.renderer
        return _settle(
            replace(
                plan,
                segments=tuple(updated),
                checks=(*plan.checks, *issues),
                stages=(*plan.stages, StageReport(stage="render", state=StageState.OK)),
                provenance=replace(
                    plan.provenance, engines=(*plan.provenance.engines, (name, version))
                ),
            )
        )


def _settle(plan: CaptionPlan) -> CaptionPlan:
    """PARTIAL when one caption is unusable (QC error) or a requested render is missing."""
    errors = any(c.severity is Severity.ERROR for c in plan.checks)
    missing = any(
        s.render.status in (RenderStatus.FAILED, RenderStatus.NOT_AVAILABLE) for s in plan.segments
    )
    return replace(plan, state=ResultState.PARTIAL if errors or missing else ResultState.OK)


def _merge(
    new: tuple[CaptionDecision, ...], old: tuple[CaptionDecision, ...]
) -> tuple[CaptionDecision, ...]:
    """New decisions win per (item, action); earlier ones are carried over."""
    seen = {(d.item_id, d.action) for d in new}
    return (*new, *(d for d in old if (d.item_id, d.action) not in seen))


def _read[T](
    supplied: Mapping[str, T],
    asset_id: str,
    latest: Callable[[str], Result[tuple[T, MediaAssetDto] | None, MediaHouseError]],
    kind: str,
    out: _Gathered,
) -> T | None:
    """The result the caller supplied, else the stored one, else ``None`` (recorded as such)."""
    if asset_id in supplied:
        out.records.append(InputRecord(kind=kind, how=InputHow.SUPPLIED, asset_id=asset_id))
        return supplied[asset_id]
    found = latest(asset_id)
    if isinstance(found, Ok) and found.value is not None:
        value, asset = found.value
        out.records.append(
            InputRecord(kind=kind, how=InputHow.REUSED, asset_id=asset_id, identity=asset.id)
        )
        return value
    detail = found.error.user_message if isinstance(found, Err) else f"no stored {kind}"
    out.records.append(
        InputRecord(kind=kind, how=InputHow.NOT_AVAILABLE, asset_id=asset_id, detail=detail)
    )
    return None


def _json(value: object) -> dict[str, JsonValue]:
    loaded: dict[str, JsonValue] = json.loads(json.dumps(value, sort_keys=True, default=str))
    return loaded
