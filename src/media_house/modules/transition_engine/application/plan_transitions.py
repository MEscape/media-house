"""Use case: an edit (``TransitionSequence``) + stored upstream results -> one stored
``transition_plan`` and, when asked, the rendered graphic transition overlays.

Nothing is detected, probed or transcribed here: recording lengths come from the stored
inspection (the library's own duration when there is none; never a re-probe), words from the
stored audio timeline, the picture at each cut from the stored video analysis, cut judgements
from the stored continuity analysis and pacing cues from the stored pacing analysis. A missing
optional input is recorded and the plan degrades (safe hard cuts), it never fails. Graphic
overlays are rendered by ``motion_graphics_engine`` through its ``GraphicsRenderer``
contract; drift is checked by ``visual_style_engine``'s ``StyleValidator``.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import cast

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.continuity_intelligence.application.contracts import ContinuityCatalog
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
    AnimationRequest,
    GraphicContent,
    GraphicKind,
    GraphicRequest,
    GraphicsRenderer,
    ItemStatus,
    Origin,
    OverlayDirection,
    OverlayKind,
    RendererNotAvailable,
    RenderGraphicsCommand,
    Timing,
)
from media_house.modules.pacing_intelligence.application.contracts import PacingCatalog
from media_house.modules.transition_engine.application.results import TransitionResults
from media_house.modules.transition_engine.application.views import (
    continuity_facts,
    edge_fact,
    pacing_cues,
    source_facts,
    style_limits,
)
from media_house.modules.transition_engine.domain.engine import (
    PlanMeta,
    RenderOutcome,
    apply_renders,
    cache_key,
    plan_transitions,
    with_checks,
)
from media_house.modules.transition_engine.domain.errors import (
    InvalidTransitionConfig,
    InvalidTransitionPlan,
    InvalidTransitionSequence,
    NothingToTransition,
    TransitionEngineError,
)
from media_house.modules.transition_engine.domain.facts import (
    ContinuityFact,
    EdgeFact,
    PacingCue,
    SourceFacts,
    StyleLimits,
    TransitionSequence,
    fr,
)
from media_house.modules.transition_engine.domain.model import (
    InputRecord,
    QcIssue,
    StageReport,
    StyleBinding,
    TransitionPlan,
)
from media_house.modules.transition_engine.domain.planner import PlanInputs
from media_house.modules.transition_engine.domain.profile import (
    TransitionConfig,
    TransitionDecision,
    TransitionProfile,
)
from media_house.modules.transition_engine.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.transition_engine.domain.values import (
    CUT_KINDS,
    PLANNER_VERSION,
    RESULT_OPERATION,
    InputHow,
    QcCategory,
    RenderState,
    Severity,
    StageState,
)
from media_house.modules.video_intelligence.application.contracts import (
    VideoAnalysis,
    VideoCatalog,
)
from media_house.modules.visual_style_engine.application.contracts import (
    MotionPhase,
    MotionUse,
    ResolvedStyle,
    StyleRef,
    StyleUsage,
    StyleValidator,
    VisualStyleCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type PlanTransitionsError = MediaError | TransitionEngineError | MediaHouseError

_STEPS = 5


@dataclass(frozen=True, slots=True)
class PlanTransitionsCommand:
    sequence: TransitionSequence
    #: The channel style (a ``ResolvedStyle`` or the ``StyleRef`` of a stored one).
    style: ResolvedStyle | StyleRef | None = None
    config: TransitionConfig = field(default_factory=TransitionConfig)
    #: Render the graphic overlays through motion_graphics_engine (else they stay planned).
    render: bool = False
    #: A person's choices; those of the previous plan are carried unless replaced.
    decisions: tuple[TransitionDecision, ...] = ()
    carry_decisions: bool = True


@dataclass(frozen=True, slots=True)
class TransitionsResult:
    plan: TransitionPlan
    #: The stored document (a derived asset of the edit's anchor asset).
    asset: MediaAssetDto
    #: ``False`` when the identical plan already existed and nothing was planned.
    created: bool


@dataclass(frozen=True, slots=True)
class _Style:
    style: ResolvedStyle
    limits: StyleLimits
    binding: StyleBinding


class PlanTransitions:
    def __init__(
        self,
        library: MediaLibrary,
        inspections: InspectionCatalog,
        audio: AudioCatalog,
        video: VideoCatalog,
        continuity: ContinuityCatalog,
        pacing: PacingCatalog,
        styles: VisualStyleCatalog,
        validator: StyleValidator,
        renderer: GraphicsRenderer,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._inspections = inspections
        self._audio = audio
        self._video = video
        self._continuity = continuity
        self._pacing = pacing
        self._styles = styles
        self._validator = validator
        self._renderer = renderer
        self._clock = clock
        self._documents = DerivedDocuments(library, paths)
        self._results = TransitionResults(library)

    def execute(
        self, command: PlanTransitionsCommand, ctx: JobContext
    ) -> Result[TransitionsResult, PlanTransitionsError]:
        seq = command.sequence
        if not seq.clips or not seq.main():
            return Err(NothingToTransition())
        try:
            seq.check()
        except InvalidTransitionSequence as exc:
            return Err(exc)
        anchor = seq.anchor_asset_id
        records: list[InputRecord] = []
        ctx.progress.report(1, _STEPS, "Reading the stored analyses")
        style = self._style(command, records)
        try:
            profile = command.config.resolve(seq.format, style.style.profile if style else None)
        except InvalidTransitionConfig as exc:
            return Err(exc)
        upstream: dict[str, str | None] = {}
        sources, videos = self._sources(seq, records, upstream)
        edges = _edges(seq, videos)
        continuity = self._continuity_facts(seq, anchor, records, upstream)
        pacing = self._pacing_cues(anchor, records, upstream)

        ctx.progress.report(2, _STEPS, "Fingerprinting the edit")
        edit_id = seq.edit.edit_plan_id if seq.edit else None
        previous = self._results.latest(anchor, edit_plan_id=edit_id, profile=profile.name)
        prior = previous.value[0] if isinstance(previous, Ok) and previous.value else None
        decisions = _decisions(command, prior)
        inputs = PlanInputs(
            sequence=seq,
            sources=sources,
            edges=edges,
            continuity=continuity,
            pacing=pacing,
            style=style.limits if style else None,
            decisions=decisions,
        )
        key = cache_key(inputs, command.config, profile, render=command.render, upstream=upstream)
        spec = DocumentSpec(RESULT_OPERATION, PLANNER_VERSION, _json(key))
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, plan_from_json)
        if cached is not None:
            _log.info("Transition plan reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(TransitionsResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(3, _STEPS, "Deciding the transitions")
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        version, supersedes = self._history(anchor, edit_id, profile.name)
        meta = PlanMeta(
            plan_version=version,
            supersedes=supersedes,
            style=style.binding if style else None,
            inputs=tuple(records),
            stages=_stages(records, command, style is not None),
        )
        plan = plan_transitions(
            inputs, profile, command.config, key=key, now=self._clock.now(), meta=meta
        )
        plan = with_checks(plan, self._drift(plan, style))
        critical = [i.message for i in plan.checks if i.severity is Severity.CRITICAL]
        if critical:
            _log.error("Transition plan rejected its own result", problems=critical[:5])
            return Err(InvalidTransitionPlan(critical))

        ctx.progress.report(4, _STEPS, "Rendering graphic transitions")
        if command.render:
            outcomes, engine = self._render(plan, command, ctx)
            plan = apply_renders(plan, outcomes)
            if engine is not None:
                plan = _with_engine(plan, engine)

        ctx.progress.report(_STEPS, _STEPS, "Saving")
        stored = self._documents.store(
            anchor_asset.value,
            plan_to_json(plan),
            filename="transition_plan.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (transitions {profile.name})",
            metadata={
                "edit_plan_id": edit_id,
                "profile": profile.name,
                "plan_version": plan.plan_version,
                "transitions": plan.metrics.transitions,
                "state": plan.state.value,
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Transition plan created",
            asset_id=stored.value.id,
            profile=profile.name,
            boundaries=plan.metrics.boundaries,
            transitions=plan.metrics.transitions,
            fallbacks=plan.metrics.fallbacks,
        )
        return Ok(TransitionsResult(plan, stored.value, created=True))

    # --- evidence ---------------------------------------------------------------------------
    def _style(self, command: PlanTransitionsCommand, records: list[InputRecord]) -> _Style | None:
        given = command.style
        if given is None:
            records.append(
                InputRecord(
                    kind="visual_style",
                    how=InputHow.NOT_AVAILABLE,
                    detail="no channel style: the profile's ceiling and bookends apply",
                )
            )
            return None
        if isinstance(given, ResolvedStyle):
            records.append(
                InputRecord(kind="visual_style", how=InputHow.SUPPLIED, identity=given.fingerprint)
            )
            return _Style(given, style_limits(given, None), _binding(given, None))
        loaded = self._styles.load_ref(given)
        if isinstance(loaded, Err):
            records.append(
                InputRecord(
                    kind="visual_style",
                    how=InputHow.NOT_AVAILABLE,
                    identity=given.document_id,
                    detail=f"the referenced style could not be read: {loaded.error.user_message}",
                )
            )
            return None
        records.append(
            InputRecord(kind="visual_style", how=InputHow.REUSED, identity=given.document_id)
        )
        return _Style(
            loaded.value,
            style_limits(loaded.value, given.document_id),
            _binding(loaded.value, given.document_id),
        )

    def _sources(
        self, seq: TransitionSequence, records: list[InputRecord], upstream: dict[str, str | None]
    ) -> tuple[dict[str, SourceFacts], dict[str, VideoAnalysis]]:
        sources: dict[str, SourceFacts] = {}
        videos: dict[str, VideoAnalysis] = {}
        for asset_id in dict.fromkeys(c.asset_id for c in seq.main()):
            inspection = self._inspections.find(asset_id)
            records.append(
                InputRecord(
                    kind="inspection",
                    how=InputHow.REUSED if inspection else InputHow.NOT_AVAILABLE,
                    asset_id=asset_id,
                    identity=inspection.asset.id if inspection else None,
                    detail=""
                    if inspection
                    else "no stored inspection: the library's duration"
                    " (or none) bounds the handles",
                )
            )
            upstream[f"inspection:{asset_id}"] = inspection.asset.id if inspection else None
            asset = self._library.get(asset_id)
            audio = _read(asset_id, self._audio.latest, "audio_timeline", records, upstream)
            video = _read(asset_id, self._video.latest, "video_analysis", records, upstream)
            if video is not None:
                videos[asset_id] = video
            sources[asset_id] = source_facts(
                asset_id,
                inspection,
                asset.value if isinstance(asset, Ok) else None,
                audio,
            )
        return sources, videos

    def _continuity_facts(
        self,
        seq: TransitionSequence,
        anchor: str,
        records: list[InputRecord],
        upstream: dict[str, str | None],
    ) -> dict[tuple[str, str], ContinuityFact]:
        if seq.edit is None:
            records.append(
                InputRecord(
                    kind="continuity_analysis",
                    how=InputHow.NOT_AVAILABLE,
                    detail="no edit plan (no-speech path): continuity judges edits only",
                )
            )
            return {}
        found = self._continuity.latest(anchor, edit_plan_id=seq.edit.edit_plan_id)
        if isinstance(found, Ok) and found.value is not None:
            analysis, asset = found.value
            if analysis.edit_plan_version == seq.edit.plan_version:
                records.append(
                    InputRecord(kind="continuity_analysis", how=InputHow.REUSED, identity=asset.id)
                )
                upstream["continuity"] = asset.id
                return continuity_facts(analysis)
            detail = "the stored continuity analysis judged another version of the edit"
        else:
            detail = "no stored continuity analysis for this edit"
        records.append(
            InputRecord(kind="continuity_analysis", how=InputHow.NOT_AVAILABLE, detail=detail)
        )
        return {}

    def _pacing_cues(
        self, anchor: str, records: list[InputRecord], upstream: dict[str, str | None]
    ) -> tuple[PacingCue, ...]:
        found = self._pacing.latest(anchor)
        if isinstance(found, Ok) and found.value is not None:
            records.append(
                InputRecord(kind="pacing_analysis", how=InputHow.REUSED, identity=found.value[1].id)
            )
            upstream["pacing"] = found.value[1].id
            return pacing_cues(found.value[0])
        records.append(
            InputRecord(
                kind="pacing_analysis",
                how=InputHow.NOT_AVAILABLE,
                detail="no stored pacing analysis: no section breath or reveal hold is known",
            )
        )
        return ()

    # --- drift and render -------------------------------------------------------------------
    def _drift(self, plan: TransitionPlan, style: _Style | None) -> list[QcIssue]:
        if style is None:
            return []
        found: list[QcIssue] = []
        for t in plan.transitions:
            if t.kind in CUT_KINDS:
                continue
            usage = StyleUsage(
                module="transition_engine",
                fingerprint=style.limits.fingerprint,
                width=style.limits.width,
                height=style.limits.height,
                section_id=t.section_id,
                motions=(
                    MotionUse(
                        element_id=t.transition_id,
                        phase=MotionPhase.TRANSITION,
                        easing=style.limits.move_easing,
                        duration=t.duration,
                        intensity=t.intensity,
                        reason=t.reasons[0] if t.status.value in ("brand", "overridden") else "",
                    ),
                ),
            )
            for issue in self._validator.validate(style.style, usage):
                found.append(
                    QcIssue(
                        code=f"style.{issue.code}",
                        severity=Severity(issue.severity.value),
                        category=QcCategory(issue.category.value),
                        message=issue.message,
                        item_id=t.transition_id,
                        timeline_start=t.timeline_start,
                        timeline_end=t.timeline_end,
                        evidence=issue.evidence,
                    )
                )
        return found

    def _render(
        self, plan: TransitionPlan, command: PlanTransitionsCommand, ctx: JobContext
    ) -> tuple[dict[str, RenderOutcome], tuple[str, str] | None]:
        overlays = [t for t in plan.transitions if t.overlay is not None]
        if not overlays:
            return {}, None
        ids = [t.overlay.request_id for t in overlays if t.overlay is not None]
        if command.style is None:
            reason = "no channel style: graphic overlays are drawn from the identity"
            return _all(ids, RenderState.NOT_AVAILABLE, reason), None
        requests = tuple(
            GraphicRequest(
                request_id=t.overlay.request_id,
                kind=GraphicKind.TRANSITION_OVERLAY,
                content=GraphicContent(
                    overlay=OverlayKind(t.overlay.overlay),
                    direction=_WIPE_DIRECTION.get(t.direction)
                    if t.overlay.overlay == OverlayKind.WIPE
                    else None,
                    source=f"transition {t.transition_id}",
                ),
                timing=Timing(
                    local_duration=t.overlay.local_duration,
                    timeline_start=t.overlay.timeline_start,
                ),
                animation=AnimationRequest(intensity=t.intensity),
                priority=0.5,
                reason="; ".join(t.reasons),
                origin=Origin.TRANSITION,
                section_id=t.section_id,
            )
            for t in overlays
            if t.overlay is not None
        )
        result = self._renderer.render(
            RenderGraphicsCommand(
                anchor_asset_id=command.sequence.anchor_asset_id,
                requests=requests,
                style=command.style,
                fps=command.sequence.frame_rate,
                story_format=command.sequence.format,
            ),
            ctx,
        )
        if isinstance(result, Err):
            error = result.error
            state = (
                RenderState.NOT_AVAILABLE
                if isinstance(error, RendererNotAvailable)
                else RenderState.FAILED
            )
            return _all(ids, state, error.user_message or str(error)), None
        outcomes: dict[str, RenderOutcome] = {}
        for item in result.value.items:
            if item.status is ItemStatus.OK and item.asset_id is not None:
                state = RenderState.RENDERED if item.created else RenderState.REUSED
            elif item.status is ItemStatus.NOT_AVAILABLE:
                state = RenderState.NOT_AVAILABLE
            else:
                state = RenderState.FAILED
            outcomes[item.request_id] = RenderOutcome(
                request_id=item.request_id,
                state=state,
                asset_id=item.asset_id,
                detail=item.detail,
            )
        return outcomes, result.value.renderer

    def _history(self, anchor: str, edit_id: str | None, profile: str) -> tuple[int, str | None]:
        """1 for the first plan of this edit and profile; later ones count up and name their
        predecessor. Earlier plans are never changed or removed."""
        earlier = [
            a
            for a in self._results.history(anchor)
            if a.metadata.get("edit_plan_id") == edit_id and a.metadata.get("profile") == profile
        ]
        if not earlier:
            return 1, None
        return len(earlier) + 1, earlier[0].id


# --- helpers ------------------------------------------------------------------------------------
def _json(key: Mapping[str, object]) -> Mapping[str, JsonValue]:
    return cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str)))


#: The plan's ``direction`` of a wipe -> where motion's wipe overlay travels.
_WIPE_DIRECTION = {"left": OverlayDirection.LEFT, "right": OverlayDirection.RIGHT}


def _binding(style: ResolvedStyle, document_id: str | None) -> StyleBinding:
    return StyleBinding(
        document_id=document_id,
        fingerprint=style.fingerprint,
        identity_version=style.identity_version,
        profile=style.profile,
    )


def _edges(
    seq: TransitionSequence, videos: Mapping[str, VideoAnalysis]
) -> dict[tuple[str, str], EdgeFact]:
    """The picture at the out point (its last frame) and the in point of every main clip."""
    frame = Fraction(1) / fr(seq.frame_rate)
    out: dict[tuple[str, str], EdgeFact] = {}
    for clip in seq.main():
        video = videos.get(clip.asset_id)
        if video is None:
            continue
        for side, at in (
            ("in", fr(clip.source_start)),
            ("out", max(fr(clip.source_start), fr(clip.source_end) - frame)),
        ):
            found = edge_fact(video, float(at))
            if found is not None:
                out[(clip.clip_id, side)] = found
    return out


def _decisions(
    command: PlanTransitionsCommand, previous: TransitionPlan | None
) -> dict[str, TransitionDecision]:
    """The command's decisions over the previous plan's (automation never drops a person's)."""
    chosen = {d.transition_id: d for d in command.decisions}
    if command.carry_decisions and previous is not None:
        for d in previous.decisions:
            chosen.setdefault(d.transition_id, d)
    return chosen


def _read[T](
    asset_id: str,
    latest: Callable[[str], Result[tuple[T, MediaAssetDto] | None, MediaError]],
    kind: str,
    records: list[InputRecord],
    upstream: dict[str, str | None],
) -> T | None:
    stored = latest(asset_id)
    if isinstance(stored, Ok) and stored.value is not None:
        records.append(
            InputRecord(
                kind=kind, how=InputHow.REUSED, asset_id=asset_id, identity=stored.value[1].id
            )
        )
        upstream[f"{kind}:{asset_id}"] = stored.value[1].id
        return stored.value[0]
    detail = (
        f"no stored {kind.replace('_', ' ')}"
        if isinstance(stored, Ok)
        else f"the library could not be read: {stored.error.user_message}"
    )
    records.append(
        InputRecord(kind=kind, how=InputHow.NOT_AVAILABLE, asset_id=asset_id, detail=detail)
    )
    upstream[f"{kind}:{asset_id}"] = None
    return None


def _stages(
    records: list[InputRecord], command: PlanTransitionsCommand, styled: bool
) -> tuple[StageReport, ...]:
    def stage(name: str, kinds: tuple[str, ...], reason: str) -> StageReport:
        found = [r for r in records if r.kind in kinds]
        ok = bool(found) and all(r.how is not InputHow.NOT_AVAILABLE for r in found)
        return StageReport(
            stage=name,
            state=StageState.OK if ok else StageState.NOT_AVAILABLE,
            detail="" if ok else reason,
        )

    return (
        stage("handles", ("inspection",), "recording lengths partly unknown: overlaps fall back"),
        stage(
            "speech", ("audio_timeline",), "no words: audio fades are not checked against speech"
        ),
        stage("picture", ("video_analysis",), "no picture analysis: motion/composition unknown"),
        stage("continuity", ("continuity_analysis",), "no continuity judgement of the cuts"),
        stage("pacing", ("pacing_analysis",), "no pacing cues"),
        StageReport(
            stage="style",
            state=StageState.OK if styled else StageState.NOT_AVAILABLE,
            detail="" if styled else "no channel style",
        ),
        StageReport(
            stage="render",
            state=StageState.OK if command.render else StageState.SKIPPED,
            detail="" if command.render else "overlays planned, not rendered (render=False)",
        ),
    )


def _all(ids: list[str], state: RenderState, detail: str) -> dict[str, RenderOutcome]:
    return {i: RenderOutcome(request_id=i, state=state, detail=detail) for i in ids}


def _with_engine(plan: TransitionPlan, engine: tuple[str, str]) -> TransitionPlan:
    provenance = replace(
        plan.provenance, engines=(*plan.provenance.engines, (f"renderer:{engine[0]}", engine[1]))
    )
    return replace(plan, provenance=provenance)


__all__ = [
    "PlanTransitions",
    "PlanTransitionsCommand",
    "PlanTransitionsError",
    "TransitionProfile",
    "TransitionsResult",
]
