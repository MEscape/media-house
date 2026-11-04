"""Use case: the edit's shots (+ stored picture analyses, reframing, inspection, style) -> one
stored ``vfx_plan`` and, when asked, the rendered derived clips.

Nothing is detected, tracked or probed here when its owner already knows it: tracks, faces,
bursts and quality come from the stored video analysis, framing windows from reframing, sizes,
colour and timecode from the stored inspection (a minimal probe runs only without one). Effects
are planned in the domain; rendering goes through the ``EffectRenderer`` port. Every render is a
derived ``vfx_render`` asset of its source (the original is never touched) and is reused when the
identical treatment was rendered before; the plan document itself is cached the same way.
"""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import cast

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentCatalog
from media_house.modules.media_inspection.application.contracts import (
    InspectionCatalog,
    InspectionResult,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.reframing_intelligence.application.contracts import (
    ReframingAnalysis,
    ReframingCatalog,
)
from media_house.modules.vfx_engine.application.ports import (
    EffectRenderer,
    Inpainter,
    OverlaySource,
    RenderJob,
    SourceMedia,
    SubjectMatter,
)
from media_house.modules.vfx_engine.application.results import VfxResults
from media_house.modules.vfx_engine.application.views import (
    aspect_name,
    source_facts,
    source_media,
    style_limits,
    with_bangs,
)
from media_house.modules.vfx_engine.domain.engine import (
    PlanMeta,
    RenderOutcome,
    apply_renders,
    build_plan,
    cache_key,
    digest,
)
from media_house.modules.vfx_engine.domain.errors import (
    InvalidVfxConfig,
    InvalidVfxPlan,
    InvalidVfxShots,
    NothingToTreat,
    VfxEngineError,
)
from media_house.modules.vfx_engine.domain.facts import (
    FULL_FRAME,
    SourceFacts,
    StyleLimits,
    VfxShots,
    fr,
)
from media_house.modules.vfx_engine.domain.model import (
    Effect,
    InputRecord,
    Mask,
    MaskKeyframe,
    OutputRef,
    QcIssue,
    StageReport,
    Treatment,
    VfxPlan,
)
from media_house.modules.vfx_engine.domain.planner import PlanInputs
from media_house.modules.vfx_engine.domain.profile import VfxConfig, VfxProfile
from media_house.modules.vfx_engine.domain.serialization import (
    plan_from_json,
    plan_to_json,
    qc_from_json,
    qc_to_json,
)
from media_house.modules.vfx_engine.domain.timing import frame_views
from media_house.modules.vfx_engine.domain.validation import (
    judge_render,
    mask_coverage,
    plan_problems,
    protected_boxes,
)
from media_house.modules.vfx_engine.domain.values import (
    PLANNER_VERSION,
    RENDER_OPERATION,
    RENDER_VERSION,
    RESULT_OPERATION,
    EffectType,
    InputHow,
    MaskKind,
    QcCategory,
    Severity,
    StageState,
    TreatmentStatus,
)
from media_house.modules.video_intelligence.application.contracts import (
    VideoAnalysis,
    VideoCatalog,
)
from media_house.modules.visual_style_engine.application.contracts import (
    ResolvedStyle,
    StyleRef,
    VisualStyleCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    Err,
    MediaHouseError,
    Ok,
    ProcessFailedError,
    ProcessTimeoutError,
    Result,
    ToolNotFoundError,
)
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type PlanVfxError = MediaError | VfxEngineError | MediaHouseError


@dataclass(frozen=True, slots=True)
class PlanVfxCommand:
    shots: VfxShots
    #: The channel style (a resolved one, or a reference read through the style catalog).
    style: ResolvedStyle | StyleRef | None = None
    config: VfxConfig = field(default_factory=VfxConfig)
    #: Also render the planned treatments into derived clips.
    render: bool = False
    #: Picture analyses the caller already holds (used as given).
    video_analyses: Mapping[str, VideoAnalysis] = field(default_factory=dict)
    use_reframing: bool = True


@dataclass(frozen=True, slots=True)
class VfxResult:
    plan: VfxPlan
    #: The stored plan document (a derived asset of the anchor recording).
    asset: MediaAssetDto
    #: ``False`` when the identical plan already existed and nothing was planned or rendered.
    created: bool


class PlanVfx:
    def __init__(
        self,
        library: MediaLibrary,
        video: VideoCatalog,
        inspections: InspectionCatalog,
        reframing: ReframingCatalog,
        styles: VisualStyleCatalog,
        renderer: EffectRenderer,
        matter: SubjectMatter,
        inpainter: Inpainter,
        paths: AppPaths,
        clock: Clock,
        audio: AudioCatalog | None = None,
        alignments: AlignmentCatalog | None = None,
    ) -> None:
        self._library = library
        self._audio = audio
        self._alignments = alignments
        self._video = video
        self._inspections = inspections
        self._reframing = reframing
        self._styles = styles
        self._renderer = renderer
        self._matter = matter
        self._inpainter = inpainter
        self._paths = paths
        self._clock = clock
        self._documents = DerivedDocuments(library, paths)
        self._results = VfxResults(library)

    def execute(self, command: PlanVfxCommand, ctx: JobContext) -> Result[VfxResult, PlanVfxError]:
        shots = command.shots
        if not shots.clips:
            return Err(NothingToTreat())
        problem = _shots_problem(shots)
        if problem:
            return Err(InvalidVfxShots(problem))
        try:
            profile = command.config.resolve(shots.format)
            for clip in shots.clips:
                command.config.for_section(profile, clip.section_id)
        except InvalidVfxConfig as exc:
            return Err(exc)
        anchor = shots.clips[0].asset_id
        records: list[InputRecord] = []
        ctx.progress.report(1, 5, "Reading the stored analyses")
        sources = self._sources(command, records)
        if isinstance(sources, Err):
            return sources
        style = self._style(command, records)
        if command.render:
            missing = self._renderer.unavailable()
            if missing is not None:
                return Err(ToolNotFoundError("ffmpeg"))
        available = self._available()
        renderer = self._renderer.identity() if command.render else ""
        key = cache_key(
            shots,
            sources.value,
            style,
            available,
            command.config,
            profile,
            command.render,
            renderer,
        )
        spec = DocumentSpec(RESULT_OPERATION, PLANNER_VERSION, _json(key))
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, plan_from_json)
        if cached is not None:
            _log.info("VFX plan reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(VfxResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(2, 5, "Planning the effects")
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        edit_id = None if shots.edit is None else shots.edit.edit_plan_id
        version, supersedes = self._history(anchor, edit_id, profile.name)
        meta = PlanMeta(
            plan_version=version,
            supersedes=supersedes,
            anchor_asset_id=anchor,
            inputs=tuple(records),
            stages=_stages(sources.value, shots, style, command.render),
            engines=(("renderer", renderer),) if renderer else (),
        )
        inputs = PlanInputs(
            shots=shots,
            sources=sources.value,
            style=style,
            available=available,
            config=command.config,
        )
        plan = build_plan(inputs, profile, now=self._clock.now(), meta=meta)
        problems = plan_problems(plan)
        if problems:
            _log.error("VFX plan rejected its own result", problems=problems[:5])
            return Err(InvalidVfxPlan(problems))

        if command.render:
            ctx.progress.report(3, 5, "Rendering the treatments")
            rendered = self._render_all(plan, sources.value, profile, style, ctx)
            if isinstance(rendered, Err):
                return rendered
            plan = rendered.value
            problems = plan_problems(plan)
            if problems:
                return Err(InvalidVfxPlan(problems))

        ctx.progress.report(4, 5, "Saving")
        stored = self._documents.store(
            anchor_asset.value,
            plan_to_json(plan),
            filename="vfx_plan.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (vfx {profile.name})",
            metadata={
                "profile": profile.name,
                "plan_version": plan.plan_version,
                "state": plan.state.value,
                "edit_plan_id": edit_id,
                "project_id": shots.project_id,
                "rendered": plan.rendered,
                "treatments": sum(
                    1 for t in plan.treatments if t.status is not TreatmentStatus.ORIGINAL
                ),
            },
        )
        if isinstance(stored, Err):
            return stored
        ctx.progress.report(5, 5, "Done")
        _log.info(
            "VFX plan created",
            asset_id=stored.value.id,
            profile=profile.name,
            treated=sum(1 for t in plan.treatments if t.status is not TreatmentStatus.ORIGINAL),
            state=plan.state.value,
        )
        return Ok(VfxResult(plan, stored.value, created=True))

    # --- inputs ----------------------------------------------------------------------------------
    def _available(self) -> frozenset[EffectType]:
        available = set(self._renderer.supports())
        if self._matter.unavailable() is None:
            available |= {EffectType.BACKGROUND_BLUR, EffectType.SUBJECT_ISOLATION}
        if self._inpainter.unavailable() is None:
            available.add(EffectType.OBJECT_REMOVAL)
        return frozenset(available)

    def _sources(
        self, command: PlanVfxCommand, records: list[InputRecord]
    ) -> Result[dict[str, SourceFacts], PlanVfxError]:
        out: dict[str, SourceFacts] = {}
        shots = command.shots
        target = aspect_name(shots.output_width, shots.output_height)
        for asset_id in dict.fromkeys(c.asset_id for c in shots.clips):
            found = self._library.get(asset_id)
            if isinstance(found, Err):
                return found
            video, video_id = self._video_of(command, asset_id, records)
            inspection = self._inspections.find(asset_id)
            records.append(
                InputRecord(
                    kind="inspection",
                    how=InputHow.REUSED if inspection else InputHow.NOT_AVAILABLE,
                    asset_id=asset_id,
                    identity=None if inspection is None else inspection.asset.id,
                    detail="" if inspection else "no stored inspection: probed minimally",
                )
            )
            reframing, reframing_id = self._reframing_of(command, asset_id, target, records)
            facts = source_facts(asset_id, video, video_id, inspection, reframing, reframing_id)
            if facts is not None:
                out[asset_id] = self._bangs(facts, records)
        return Ok(out)

    def _bangs(self, facts: SourceFacts, records: list[InputRecord]) -> SourceFacts:
        """Stored audio bangs as burst candidates (read-only; nothing is analysed here)."""
        asset_id = facts.asset_id
        stored = None if self._audio is None else self._audio.latest(asset_id)
        if not isinstance(stored, Ok) or stored.value is None:
            why = (
                "no stored audio analysis: bursts come from picture flashes only"
                if stored is None or isinstance(stored, Ok)
                else f"the library could not be read: {stored.error.user_message}"
            )
            records.append(
                InputRecord(
                    kind="audio_analysis", how=InputHow.NOT_AVAILABLE, asset_id=asset_id, detail=why
                )
            )
            return facts
        aligned = None if self._alignments is None else self._alignments.latest(asset_id)
        alignment = aligned.value if isinstance(aligned, Ok) else None
        records.append(
            InputRecord(
                kind="audio_analysis",
                how=InputHow.REUSED,
                asset_id=asset_id,
                identity=stored.value[1].id,
            )
        )
        records.append(
            InputRecord(
                kind="audio_visual_alignment",
                how=InputHow.REUSED if alignment else InputHow.NOT_AVAILABLE,
                asset_id=asset_id,
                identity=alignment[1].id if alignment else None,
                detail=""
                if alignment
                else "no stored alignment: audio bangs are placed with both clocks taken as"
                " equal (container start offsets between the streams are not corrected)",
            )
        )
        return with_bangs(facts, stored.value[0], alignment[0] if alignment else None)

    def _video_of(
        self, command: PlanVfxCommand, asset_id: str, records: list[InputRecord]
    ) -> tuple[VideoAnalysis | None, str | None]:
        if asset_id in command.video_analyses:
            records.append(
                InputRecord(kind="video_analysis", how=InputHow.SUPPLIED, asset_id=asset_id)
            )
            return command.video_analyses[asset_id], None
        stored = self._video.latest(asset_id)
        if isinstance(stored, Ok) and stored.value is not None:
            records.append(
                InputRecord(
                    kind="video_analysis",
                    how=InputHow.REUSED,
                    asset_id=asset_id,
                    identity=stored.value[1].id,
                )
            )
            return stored.value[0], stored.value[1].id
        detail = (
            "no stored picture analysis: no tracks, bursts or quality facts"
            if isinstance(stored, Ok)
            else f"the library could not be read: {stored.error.user_message}"
        )
        records.append(
            InputRecord(
                kind="video_analysis", how=InputHow.NOT_AVAILABLE, asset_id=asset_id, detail=detail
            )
        )
        return None, None

    def _reframing_of(
        self, command: PlanVfxCommand, asset_id: str, target: str, records: list[InputRecord]
    ) -> tuple[ReframingAnalysis | None, str | None]:
        if not command.use_reframing:
            return None, None
        stored = self._reframing.latest(asset_id, target=target)
        if isinstance(stored, Ok) and stored.value is not None:
            records.append(
                InputRecord(
                    kind="reframing_analysis",
                    how=InputHow.REUSED,
                    asset_id=asset_id,
                    identity=stored.value[1].id,
                )
            )
            return stored.value[0], stored.value[1].id
        records.append(
            InputRecord(
                kind="reframing_analysis",
                how=InputHow.NOT_AVAILABLE,
                asset_id=asset_id,
                detail=f"no stored {target} reframing: punch-ins centre on the subject or frame",
            )
        )
        return None, None

    def _style(self, command: PlanVfxCommand, records: list[InputRecord]) -> StyleLimits | None:
        style = command.style
        if style is None:
            records.append(
                InputRecord(
                    kind="visual_style",
                    how=InputHow.NOT_AVAILABLE,
                    detail="no channel style: the profile's own intensity ceiling applies",
                )
            )
            return None
        if isinstance(style, ResolvedStyle):
            records.append(
                InputRecord(kind="visual_style", how=InputHow.SUPPLIED, identity=style.fingerprint)
            )
            return style_limits(style, None)
        loaded = self._styles.load_ref(style)
        if isinstance(loaded, Err):
            records.append(
                InputRecord(
                    kind="visual_style",
                    how=InputHow.NOT_AVAILABLE,
                    identity=style.document_id,
                    detail=f"the referenced style could not be read: {loaded.error.user_message}",
                )
            )
            return None
        records.append(
            InputRecord(kind="visual_style", how=InputHow.REUSED, identity=style.document_id)
        )
        return style_limits(loaded.value, style.document_id)

    def _history(self, anchor: str, edit_id: str | None, profile: str) -> tuple[int, str | None]:
        earlier = [
            a
            for a in self._library.list_derived(anchor)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and a.metadata.get("edit_plan_id") == edit_id
            and a.metadata.get("profile") == profile
        ]
        if not earlier:
            return 1, None
        newest = max(earlier, key=lambda a: (a.created_at, a.id))
        return len(earlier) + 1, newest.id

    # --- rendering -------------------------------------------------------------------------------
    def _render_all(
        self,
        plan: VfxPlan,
        sources: Mapping[str, SourceFacts],
        profile: VfxProfile,
        style: StyleLimits | None,
        ctx: JobContext,
    ) -> Result[VfxPlan, PlanVfxError]:
        outcomes: dict[str, RenderOutcome] = {}
        todo = [t for t in plan.treatments if t.status is TreatmentStatus.PLANNED]
        for index, t in enumerate(todo, start=1):
            ctx.raise_if_cancelled()
            ctx.progress.report(index, len(todo), f"Rendering {t.clip_id}")
            outcome = self._render_one(plan, t, sources, profile, style, ctx)
            if isinstance(outcome, Err):
                return outcome
            if _failed(outcome.value) and _privacy_fallback_applies(t, profile):
                safer = _full_frame_privacy(t)
                retry = self._render_one(plan, safer, sources, profile, style, ctx)
                if isinstance(retry, Err):
                    return retry
                issue = QcIssue(
                    code="privacy_fallback",
                    severity=Severity.WARNING,
                    category=QcCategory.TECHNICAL,
                    message="tracked concealment failed QC: the whole picture is concealed instead",
                    item_id=t.treatment_id,
                    timeline_start=t.timeline_start,
                    timeline_end=t.timeline_end,
                )
                outcome = Ok(
                    replace(
                        retry.value,
                        replaced=safer,
                        issues=(*outcome.value.issues, issue, *retry.value.issues),
                    )
                )
            outcomes[t.treatment_id] = outcome.value
        return Ok(apply_renders(plan, outcomes))

    def _render_one(
        self,
        plan: VfxPlan,
        t: Treatment,
        sources: Mapping[str, SourceFacts],
        profile: VfxProfile,
        style: StyleLimits | None,
        ctx: JobContext,
    ) -> Result[RenderOutcome, PlanVfxError]:
        fps = fr(plan.frame_rate)
        tracks = {
            tr.track_id: tr for tr in (sources[t.asset_id].tracks if t.asset_id in sources else ())
        }
        tint = _tint(t, style)
        overlays = [
            e.warnings[0].split(":", 1)[1]
            for e in t.active()
            if e.effect_type is EffectType.COMPOSITE and e.warnings
        ]
        spec: dict[str, JsonValue] = {
            "treatment": digest(_render_identity(t)),
            "clip": t.clip_id,
            "frames": t.frames,
            "frame_rate": f"{fps.numerator}/{fps.denominator}",
            "renderer": self._renderer.identity(),
            "qc": digest(asdict(profile.qc)),
            "tint": tint,
            "overlays": cast("JsonValue", overlays),
        }
        fingerprint = self._library.fingerprint(t.asset_id, RENDER_OPERATION, spec, RENDER_VERSION)
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self._library.find_derived_asset(t.asset_id, fingerprint.value)
        if existing is not None:
            try:
                qc = qc_from_json(existing.metadata.get("qc"))
            except MediaHouseError:
                qc = None
            if qc is not None:
                return Ok(
                    RenderOutcome(
                        treatment_id=t.treatment_id,
                        render_fingerprint=fingerprint.value,
                        output=_output_ref(
                            existing, fingerprint.value, created=False, frames=t.frames
                        ),
                        qc=qc,
                        issues=judge_render(t, qc, profile.qc),
                        reused=True,
                    )
                )
        source_path = self._library.local_path(t.asset_id)
        if isinstance(source_path, Err):
            return source_path
        media = self._media(t.asset_id, source_path.value, ctx)
        if isinstance(media, Err):
            return media
        views = tuple(frame_views(t, fps))
        p = profile.privacy
        protected = protected_boxes(
            t, views, tracks, p.min_confidence, Fraction(p.max_gap).limit_denominator(1000)
        )
        overlay_sources = self._overlays(plan, t, ctx)
        if isinstance(overlay_sources, Err):
            return overlay_sources
        with self._paths.temporary_directory(prefix="vfx-") as tmp:
            job = RenderJob(
                treatment=t,
                source=source_path.value,
                media=media.value,
                frame_rate=fps,
                frames=views,
                output=tmp / f"vfx_{t.clip_id.replace(':', '_')[:40]}.mov",
                limits=profile.qc,
                protected=protected,
                overlays=tuple(overlay_sources.value),
                glow_tint=tint,
                seed=int(digest(t.treatment_id)[:8], 16) % (2**31),
                timeout_seconds=max(600.0, t.frames * 2.0),
            )
            try:
                rendered = self._renderer.render(job, ctx.cancellation)
            except (ProcessFailedError, ProcessTimeoutError, ToolNotFoundError) as exc:
                detail = f"{exc} {exc.details.get('stderr', '')}".strip()
                return Ok(_render_failed(t, fingerprint.value, detail))
            qc = replace(
                rendered.qc,
                coverage=mask_coverage(t, tracks, p.min_confidence),
            )
            issues = judge_render(t, qc, profile.qc)
            if any(i.severity in {Severity.ERROR, Severity.CRITICAL} for i in issues):
                _log.warning(
                    "VFX render refused by QC", clip=t.clip_id, issues=[i.code for i in issues]
                )
                return Ok(
                    RenderOutcome(
                        treatment_id=t.treatment_id,
                        render_fingerprint=fingerprint.value,
                        output=None,
                        qc=qc,
                        issues=issues,
                    )
                )
            registered = self._library.register_derived(
                t.asset_id,
                rendered.path,
                operation=RENDER_OPERATION,
                config=spec,
                version=RENDER_VERSION,
                display_name=f"vfx {t.clip_id}",
                metadata={
                    "treatment_id": t.treatment_id,
                    "clip_id": t.clip_id,
                    "effects": [e.effect_type.value for e in t.active()],
                    "engine": rendered.engine,
                    "qc": cast("JsonValue", qc_to_json(qc)),
                    "source_start": str(t.source_start),
                    "source_end": str(t.source_end),
                },
            )
        if isinstance(registered, Err):
            return registered
        return Ok(
            RenderOutcome(
                treatment_id=t.treatment_id,
                render_fingerprint=fingerprint.value,
                output=_output_ref(
                    registered.value.asset,
                    fingerprint.value,
                    created=registered.value.created,
                    frames=t.frames,
                ),
                qc=qc,
                issues=issues,
            )
        )

    def _media(
        self, asset_id: str, path: Path, ctx: JobContext
    ) -> Result[SourceMedia, PlanVfxError]:
        found: InspectionResult | None = self._inspections.find(asset_id)
        media = source_media(found)
        if media is not None:
            return Ok(media)
        try:
            return Ok(self._renderer.probe(path, ctx.cancellation))
        except (ProcessFailedError, ToolNotFoundError, ProcessTimeoutError) as exc:
            return Err(exc)

    def _overlays(
        self, plan: VfxPlan, t: Treatment, ctx: JobContext
    ) -> Result[list[OverlaySource], PlanVfxError]:
        out: list[OverlaySource] = []
        for e in t.active():
            if e.effect_type is not EffectType.COMPOSITE or not e.warnings:
                continue
            clip_id = e.warnings[0].split(":", 1)[1]
            other = plan.treatment(clip_id)
            if other is None:
                continue
            path = self._library.local_path(other.asset_id)
            if isinstance(path, Err):
                return path
            media = self._media(other.asset_id, path.value, ctx)
            if isinstance(media, Err):
                return media
            offset = fr(other.source_start) + fr(e.timeline_start) - fr(other.timeline_start)
            out.append(
                OverlaySource(
                    clip_id=clip_id, path=path.value, media=media.value, source_offset=offset
                )
            )
        return Ok(out)


# --- helpers -------------------------------------------------------------------------------------
def _json(key: Mapping[str, object]) -> Mapping[str, JsonValue]:
    return cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str)))


def _shots_problem(shots: VfxShots) -> str:
    if fr(shots.frame_rate) <= 0:
        return "the frame rate must be positive"
    if shots.output_width <= 0 or shots.output_height <= 0:
        return "the output size must be positive"
    ids = [c.clip_id for c in shots.clips]
    if len(ids) != len(set(ids)):
        return "clip ids must be unique"
    for c in shots.clips:
        if fr(c.source_end) <= fr(c.source_start):
            return f"clip {c.clip_id} has no source span"
    return ""


def _stages(
    sources: Mapping[str, SourceFacts],
    shots: VfxShots,
    style: StyleLimits | None,
    render: bool,
) -> tuple[StageReport, ...]:
    def stage(name: str, ok: bool, why: str) -> StageReport:
        return StageReport(
            stage=name,
            state=StageState.OK if ok else StageState.NOT_AVAILABLE,
            detail="" if ok else why,
        )

    assets = {c.asset_id for c in shots.clips}
    return (
        stage(
            "picture_analysis",
            all(sources.get(a) is not None and sources[a].analysed for a in assets),
            "some recordings have no stored picture analysis",
        ),
        stage(
            "reframing",
            any(s.crops for s in sources.values()),
            "no reframing window for this output",
        ),
        stage("style", style is not None, "no channel style"),
        stage("edit", shots.edit is not None, "no edit plan (no-speech path)"),
        StageReport(
            stage="render",
            state=StageState.OK if render else StageState.SKIPPED,
            detail="" if render else "planning only",
        ),
    )


def _render_identity(t: Treatment) -> dict[str, object]:
    """What changes the rendered pixels (statuses, outputs and reasons do not)."""
    return {
        "asset": t.asset_id,
        "span": [
            str(t.source_start),
            str(t.source_end),
            str(t.timeline_start),
            t.start_frame,
            t.end_frame,
        ],
        "time_map": None if t.time_map is None else asdict(t.time_map),
        "effects": [
            {
                "type": e.effect_type.value,
                "mode": e.mode,
                "parameters": [(p.name, p.value) for p in e.parameters],
                "span": [e.start_frame, e.end_frame],
                "masks": digest([asdict(m) for m in e.masks]),
            }
            for e in t.active()
        ],
    }


def _tint(t: Treatment, style: StyleLimits | None) -> str | None:
    if style is None:
        return None
    colours = dict(style.palette)
    for e in t.active():
        if e.effect_type is EffectType.GLOW and e.mode in colours:
            return colours[e.mode]
    return None


def _output_ref(asset: MediaAssetDto, fingerprint: str, *, created: bool, frames: int) -> OutputRef:
    return OutputRef(
        asset_id=asset.id,
        created=created,
        processing_fingerprint=fingerprint,
        width=asset.width or 0,
        height=asset.height or 0,
        frames=frames,
    )


def _render_failed(t: Treatment, fingerprint: str, detail: str) -> RenderOutcome:
    issue = QcIssue(
        code="render_failed",
        severity=Severity.ERROR,
        category=QcCategory.TECHNICAL,
        message="the render failed; the original footage is used",
        item_id=t.treatment_id,
        timeline_start=t.timeline_start,
        timeline_end=t.timeline_end,
        evidence=detail[:400],
    )
    return RenderOutcome(
        treatment_id=t.treatment_id,
        render_fingerprint=fingerprint,
        output=None,
        qc=None,
        issues=(issue,),
    )


def _failed(outcome: RenderOutcome) -> bool:
    return outcome.output is None


def _privacy_fallback_applies(t: Treatment, profile: VfxProfile) -> bool:
    privacy = [e for e in t.active() if e.effect_type is EffectType.PRIVACY_BLUR]
    return (
        bool(privacy)
        and profile.privacy.fallback == "full_frame"
        and not all(m.kind is MaskKind.FULL_FRAME for e in privacy for m in e.masks)
    )


def _full_frame_privacy(t: Treatment) -> Treatment:
    """The safe option: conceal the whole picture over the clip (recorded in the plan)."""

    def safer(e: Effect) -> Effect:
        if e.effect_type is not EffectType.PRIVACY_BLUR:
            return e
        mask = Mask(
            mask_id=f"{e.effect_id}:full",
            kind=MaskKind.FULL_FRAME,
            keyframes=(MaskKeyframe(source_time=t.source_start, box=FULL_FRAME),),
            track_refs=tuple(r for m in e.masks for r in m.track_refs),
        )
        return replace(
            e,
            masks=(mask,),
            reasons=(*e.reasons, "fallback: tracked concealment failed QC; whole picture"),
        )

    return replace(t, effects=tuple(safer(e) for e in t.effects))
