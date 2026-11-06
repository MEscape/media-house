"""Compile an edit and its Layer 4 plans into a stored ``timeline_specification``.

Reads (never runs) every upstream result: the Layer 4 plans are handed in by the pipeline or read
through their catalogs, the style through its catalog, files through the library and the stored
inspection. The same inputs reuse the stored specification (``created=False``); changed inputs
make the next version, which names the one it supersedes and reuses its unchanged units.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from media_house.core.application.ports import Clock
from media_house.modules.caption_engine.application.contracts import CaptionCatalog, CaptionPlan
from media_house.modules.edit_plan_compiler.application.assets import AssetFactsReader
from media_house.modules.edit_plan_compiler.application.results import TimelineResults
from media_house.modules.edit_plan_compiler.application.views import (
    asset_ids_of,
    captions_facts,
    graphics_facts,
    placement_usage,
    sound_facts,
    style_facts,
    transitions_facts,
    vfx_facts,
)
from media_house.modules.edit_plan_compiler.domain.engine import (
    CompileMeta,
    cache_key,
    compile_timeline,
)
from media_house.modules.edit_plan_compiler.domain.errors import (
    EditPlanCompilerError,
    InvalidCompileSource,
    NothingToCompile,
    StyleRequired,
    TimelineBlocked,
)
from media_house.modules.edit_plan_compiler.domain.facts import (
    CompileInputs,
    CropFact,
    DriftFact,
    EditRef,
    ReframeFacts,
    TimelineSource,
)
from media_house.modules.edit_plan_compiler.domain.model import InputRef, TimelineSpecification
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.edit_plan_compiler.domain.serialization import spec_from_json, spec_to_json
from media_house.modules.edit_plan_compiler.domain.values import (
    COMPILER_VERSION,
    RESULT_OPERATION,
    DerivativePolicy,
    InputHow,
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
)
from media_house.modules.music_and_sound_design.application.contracts import (
    SoundDesignCatalog,
    SoundDesignPlan,
)
from media_house.modules.reframing_intelligence.application.contracts import (
    ReframingAnalysis,
    ReframingCatalog,
)
from media_house.modules.transition_engine.application.contracts import (
    TransitionCatalog,
    TransitionPlan,
)
from media_house.modules.vfx_engine.application.contracts import VfxCatalog, VfxPlan
from media_house.modules.visual_style_engine.application.contracts import (
    FontFiles,
    ResolvedStyle,
    StyleRef,
    StyleValidator,
    VisualStyleCatalog,
    VisualStyleError,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_STEPS = 5

type CompileError = MediaError | EditPlanCompilerError | VisualStyleError


@dataclass(frozen=True, slots=True)
class CompileTimelineCommand:
    source: TimelineSource
    #: The channel style: given resolved, or by reference, else the stored one of the anchor.
    style: ResolvedStyle | None = None
    style_ref: StyleRef | None = None
    config: CompilerConfig = field(default_factory=CompilerConfig)
    #: Layer 4 plans of this run, in memory. One not handed in is read from its catalog.
    graphics: GraphicsPlan | None = None
    captions: CaptionPlan | None = None
    transitions: TransitionPlan | None = None
    vfx: VfxPlan | None = None
    sound: SoundDesignPlan | None = None
    read_stored: bool = True
    #: Plans this run asked for that did not deliver: ``(kind, reason)``. Never compiled as
    #: if they had.
    undelivered: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineResult:
    spec: TimelineSpecification
    asset: MediaAssetDto
    #: ``False``: an identical specification was already stored and is returned as it is.
    created: bool


class CompileTimeline:
    def __init__(
        self,
        library: MediaLibrary,
        inspections: InspectionCatalog,
        styles: VisualStyleCatalog,
        validator: StyleValidator,
        fonts: FontFiles,
        graphics: GraphicsCatalog,
        captions: CaptionCatalog,
        transitions: TransitionCatalog,
        vfx: VfxCatalog,
        sound: SoundDesignCatalog,
        reframing: ReframingCatalog,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._styles = styles
        self._validator = validator
        self._fonts = fonts
        self._graphics = graphics
        self._captions = captions
        self._transitions = transitions
        self._vfx = vfx
        self._sound = sound
        self._reframing = reframing
        self._clock = clock
        self._assets = AssetFactsReader(library, inspections)
        self._documents = DerivedDocuments(library, paths)
        self._results = TimelineResults(library)

    def execute(
        self, command: CompileTimelineCommand, ctx: JobContext
    ) -> Result[TimelineResult, CompileError]:
        source = command.source
        if not source.clips:
            return Err(NothingToCompile())
        try:
            source.check()
            command.config.check()
        except (InvalidCompileSource, EditPlanCompilerError) as exc:
            return Err(exc)
        anchor = source.anchor_asset_id
        records: list[InputRef] = []
        ctx.progress.report(1, _STEPS, "Reading the style and the stored plans")
        style = self._style(command)
        if isinstance(style, Err):
            return style
        resolved, ref = style.value
        graphics = self._plan(
            command.graphics,
            command.read_stored,
            records,
            "graphics",
            source,
            lambda: self._graphics.latest(anchor, project_id=source.project_id),
            resolved,
        )
        captions = self._plan(
            command.captions,
            command.read_stored,
            records,
            "captions",
            source,
            lambda: self._captions.latest(anchor, project_id=source.project_id),
            resolved,
        )
        edit_id = source.edit.edit_plan_id if source.edit else None
        transitions = self._plan(
            command.transitions,
            command.read_stored,
            records,
            "transitions",
            source,
            lambda: self._transitions.latest(anchor, edit_plan_id=edit_id),
            resolved,
        )
        vfx = self._plan(
            command.vfx,
            command.read_stored,
            records,
            "vfx",
            source,
            lambda: self._vfx.latest(anchor, edit_plan_id=edit_id),
            resolved,
        )
        sound = self._plan(
            command.sound,
            command.read_stored,
            records,
            "sound_design",
            source,
            lambda: self._sound.latest(anchor, edit_plan_id=edit_id or "none"),
            resolved,
        )
        records.insert(
            0,
            InputRef(
                kind="visual_style",
                how=InputHow.SUPPLIED if command.style is not None else InputHow.REUSED,
                plan_id=ref.document_id if ref else None,
                fingerprint=resolved.fingerprint,
                detail=f"{resolved.identity_id} v{resolved.identity_version}, {resolved.profile}",
            ),
        )
        if source.edit is not None:
            records.insert(
                0,
                InputRef(
                    kind="edit_plan",
                    how=InputHow.SUPPLIED,
                    plan_id=source.edit.edit_plan_id,
                    plan_version=source.edit.plan_version,
                ),
            )

        drift = self._drift(resolved, graphics, captions)
        gf = None if graphics is None else graphics_facts(graphics)
        cf = None if captions is None else captions_facts(captions)
        tf = None if transitions is None else transitions_facts(transitions)
        vf = None if vfx is None else vfx_facts(vfx)
        sf = None if sound is None else sound_facts(sound)

        ctx.progress.report(2, _STEPS, "Verifying every file the timeline points at")
        wanted = {c.asset_id for c in source.clips} | set(asset_ids_of(gf, cf, tf, vf, sf))
        wanted |= {b.asset_id for b in resolved.brand_assets if b.asset_id}
        assets = self._assets.read(
            wanted,
            with_derivatives=command.config.derivative_policy is DerivativePolicy.PREFER_IMPROVED,
        )
        reframing = self._reframes(source)
        inputs = CompileInputs(
            source=source,
            style=style_facts(resolved, ref, self._fonts, drift),
            graphics=gf,
            captions=cf,
            transitions=tf,
            vfx=vf,
            sound=sf,
            assets=assets,
            reframing=reframing,
            undelivered=command.undelivered,
        )

        ctx.progress.report(3, _STEPS, "Fingerprinting the inputs")
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        key = cache_key(inputs, command.config)
        spec = DocumentSpec(RESULT_OPERATION, COMPILER_VERSION, _json(key))
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, spec_from_json)
        if cached is not None:
            _log.info("Timeline specification reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(TimelineResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(4, _STEPS, "Compiling the timeline")
        version, previous, supersedes = self._history(anchor, edit_id)
        meta = CompileMeta(
            compilation_version=version,
            supersedes=supersedes,
            compiled_at=self._clock.now(),
            inputs=tuple(records),
            sound_identity=sf.identity if sf else None,
            sound_identity_version=sf.identity_version if sf else None,
        )
        compiled = compile_timeline(inputs, command.config, meta=meta, previous=previous)
        if compiled.critical:
            problems = [i.message for i in compiled.critical]
            _log.error("Timeline blocked", problems=problems[:5], count=len(problems))
            return Err(TimelineBlocked(problems))

        ctx.progress.report(_STEPS, _STEPS, "Saving")
        result = compiled.spec
        stored = self._documents.store(
            anchor_asset.value,
            spec_to_json(result),
            filename="timeline_specification.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (timeline)",
            metadata={
                "edit_plan_id": edit_id,
                "timeline_id": result.timeline_id,
                "timeline_version": version,
                "spec_hash": result.compilation.spec_hash,
                "state": result.state.value,
                "warnings": len(result.warnings()),
                "conflicts": len(result.conflicts()),
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Timeline specification created",
            asset_id=stored.value.id,
            version=version,
            units=result.compilation.units_total,
            reused=result.compilation.units_reused,
            warnings=len(result.warnings()),
            conflicts=len(result.conflicts()),
        )
        return Ok(TimelineResult(result, stored.value, created=True))

    # --- evidence ---------------------------------------------------------------------------
    def _style(
        self, command: CompileTimelineCommand
    ) -> Result[tuple[ResolvedStyle, StyleRef | None], CompileError]:
        if command.style is not None:
            return Ok((command.style, command.style_ref))
        if command.style_ref is not None:
            loaded = self._styles.load_ref(command.style_ref)
            if isinstance(loaded, Err):
                return loaded
            return Ok((loaded.value, command.style_ref))
        latest = self._styles.latest(
            command.source.anchor_asset_id, project_id=command.source.project_id
        )
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(StyleRequired())
        found, asset = latest.value
        ref = StyleRef(
            document_id=asset.id,
            fingerprint=found.fingerprint,
            identity_version=found.identity_version,
            profile=found.profile,
        )
        return Ok((found, ref))

    def _plan[P](
        self,
        given: P | None,
        read_stored: bool,
        records: list[InputRef],
        kind: str,
        source: TimelineSource,
        read: object,
        style: ResolvedStyle,
    ) -> P | None:
        """The plan handed in, else the stored one when it belongs to this edit and this style
        (a stale stored plan is not an input: it is reported as not available)."""
        if given is not None:
            records.append(_record(kind, InputHow.SUPPLIED, given))
            return given
        if not read_stored:
            records.append(
                InputRef(kind=kind, how=InputHow.NOT_AVAILABLE, detail="not part of this run")
            )
            return None
        found = cast("Result[tuple[P, MediaAssetDto] | None, MediaError]", read())  # type: ignore[operator]
        if isinstance(found, Err) or found.value is None:
            records.append(InputRef(kind=kind, how=InputHow.NOT_AVAILABLE, detail="no stored plan"))
            return None
        plan, asset = found.value
        stale = _stale(plan, source, style)
        if stale:
            records.append(
                InputRef(kind=kind, how=InputHow.NOT_AVAILABLE, plan_id=asset.id, detail=stale)
            )
            return None
        records.append(_record(kind, InputHow.REUSED, plan, asset.id))
        return plan

    def _drift(
        self, style: ResolvedStyle, graphics: GraphicsPlan | None, captions: CaptionPlan | None
    ) -> tuple[DriftFact, ...]:
        """The style's OWN validator on what the plans placed. The compiler judges no token."""
        if graphics is None and captions is None:
            return ()
        usage = placement_usage(style, graphics, captions)
        return tuple(
            DriftFact(severity=i.severity.value, code=i.code, message=i.message, item_id=i.item_id)
            for i in self._validator.validate(style, usage)
        )

    def _reframes(self, source: TimelineSource) -> Mapping[str, ReframeFacts]:
        wanted = {
            (c.asset_id, f.recommendation_id, f.analysis_id, f.target)
            for c in source.clips
            for f in c.framing
            if f.executable
        }
        found: dict[str, ReframeFacts] = {}
        analyses: dict[tuple[str, str, str], ReframingAnalysis | None] = {}
        for asset_id, rec_id, analysis_id, target in sorted(wanted):
            analysis = analyses.get((asset_id, analysis_id, target))
            if (asset_id, analysis_id, target) not in analyses:
                analysis = self._analysis(asset_id, analysis_id, target)
                analyses[(asset_id, analysis_id, target)] = analysis
            rec = analysis.recommendation(rec_id) if analysis else None
            if analysis is None or rec is None:
                continue
            found[rec_id] = ReframeFacts(
                recommendation_id=rec_id,
                analysis_id=analysis_id,
                target=target,
                strategy=rec.strategy.value,
                executable=rec.executable,
                keyframes=tuple(
                    CropFact(
                        time=k.time,
                        x=k.crop.x,
                        y=k.crop.y,
                        w=k.crop.w,
                        h=k.crop.h,
                        interpolation=k.interpolation.value,
                    )
                    for k in rec.keyframes
                ),
            )
        return found

    def _analysis(self, asset_id: str, analysis_id: str, target: str) -> ReframingAnalysis | None:
        latest = self._reframing.latest(asset_id, target=target)
        if (
            isinstance(latest, Ok)
            and latest.value
            and latest.value[0].reframing_analysis_id == analysis_id
        ):
            return latest.value[0]
        for asset in self._reframing.history(asset_id):
            loaded = self._reframing.load(asset.id)
            if isinstance(loaded, Ok) and loaded.value.reframing_analysis_id == analysis_id:
                return loaded.value
        return None

    def _history(
        self, anchor: str, edit_id: str | None
    ) -> tuple[int, TimelineSpecification | None, str | None]:
        """1 for the first specification of this edit; later ones count up, name the one they
        supersede and are compared with it. Earlier ones are never changed or removed."""
        earlier = [
            a for a in self._results.history(anchor) if a.metadata.get("edit_plan_id") == edit_id
        ]
        if not earlier:
            return 1, None, None
        loaded = self._results.load(earlier[0].id)
        previous = loaded.value if isinstance(loaded, Ok) else None
        return len(earlier) + 1, previous, earlier[0].id


# --- helpers ------------------------------------------------------------------------------------
def _json(key: Mapping[str, object]) -> Mapping[str, JsonValue]:
    return cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str)))


def _record(kind: str, how: InputHow, plan: object, document: str | None = None) -> InputRef:
    style = getattr(plan, "style", None)
    return InputRef(
        kind=kind,
        how=how,
        plan_id=document or str(getattr(plan, "plan_id", "")),
        plan_version=int(getattr(plan, "plan_version", 0)) or None,
        fingerprint=getattr(style, "fingerprint", None) if not isinstance(style, str) else None,
    )


def _stale(plan: object, source: TimelineSource, style: ResolvedStyle) -> str:
    edit = getattr(plan, "edit", None)
    mine = source.edit
    if (edit is None) != (mine is None) or (
        edit is not None
        and mine is not None
        and (edit.edit_plan_id, edit.plan_version) != (mine.edit_plan_id, mine.plan_version)
    ):
        return "the stored plan was made for another edit"
    bound = getattr(plan, "style", None)
    fingerprint = (
        getattr(bound, "fingerprint", None)
        if bound is not None and not isinstance(bound, str)
        else None
    )
    if fingerprint is not None and fingerprint != style.fingerprint:
        return "the stored plan was made with another channel style"
    return ""


__all__ = ["CompileTimeline", "CompileTimelineCommand", "EditRef", "TimelineResult"]
