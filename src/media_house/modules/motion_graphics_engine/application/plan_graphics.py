"""``MotionGraphicsPlanner``: brief -> stored ``graphics_plan`` document (+ renders)."""

import json
from dataclasses import dataclass, field, replace

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.motion_graphics_engine.application.render_graphics import (
    GraphicsProducer,
)
from media_house.modules.motion_graphics_engine.application.style import (
    binding,
    style_facts,
)
from media_house.modules.motion_graphics_engine.domain.brief import GraphicsBrief
from media_house.modules.motion_graphics_engine.domain.engine import (
    PlanMeta,
    assemble_plan,
    digest,
    validate_plan,
)
from media_house.modules.motion_graphics_engine.domain.errors import (
    InvalidGraphicsConfig,
    InvalidGraphicsPlan,
    StyleMismatch,
)
from media_house.modules.motion_graphics_engine.domain.model import (
    GraphicRequest,
    GraphicsPlan,
    PlannedGraphic,
)
from media_house.modules.motion_graphics_engine.domain.planner import ImageNeed, plan_requests
from media_house.modules.motion_graphics_engine.domain.profile import (
    DecisionAction,
    MotionGraphicsConfig,
)
from media_house.modules.motion_graphics_engine.domain.qc import issue
from media_house.modules.motion_graphics_engine.domain.serialization import (
    encode,
    plan_from_json,
    plan_to_json,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    COMPILER_VERSION,
    DOCUMENT_TYPE,
    PLANNER_VERSION,
    PROFILE_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    InputHow,
    InputRecord,
    ItemStatus,
    QcIssue,
    RenderQuality,
    Severity,
    StageReport,
    StageState,
)
from media_house.modules.visual_asset_generation.application.contracts import (
    AssetRequest,
    PrepareVisualAssetsCommand,
    VariantName,
    VisualAssetPreparer,
)
from media_house.modules.visual_asset_generation.application.contracts import (
    EditRef as AssetEditRef,
)
from media_house.modules.visual_style_engine.application.contracts import (
    ResolvedStyle,
    StyleRef,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths

LIMITATIONS: tuple[str, ...] = (
    "Graphics are drawn from declared facts only; spelled-out prices and specs the speech"
    " analysis did not read are not shown.",
    "Section-specific style looks (style section overrides) are not applied to graphics yet;"
    " the project look is used.",
    "No emoji font is bundled by the style: emoji punchlines cannot be drawn (font.glyphs).",
    "The logo sting, wolf, fire plate and membership plate need brand asset files that do not"
    " exist yet: missing plates are reported, the logo sting is not drawn.",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanGraphicsCommand:
    brief: GraphicsBrief
    style: ResolvedStyle | StyleRef
    config: MotionGraphicsConfig = field(default_factory=MotionGraphicsConfig)
    #: ``False`` plans without rendering (layout, animation, events and QC only).
    render: bool = True
    #: ``None`` = the profile's ``render.quality``.
    quality: RenderQuality | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanGraphicsResult:
    plan: GraphicsPlan
    document: MediaAssetDto
    #: ``False`` when the identical plan was already stored (nothing computed or rendered).
    created: bool


type PlanError = MediaError | InvalidGraphicsConfig | InvalidGraphicsPlan | StyleMismatch


class PlanGraphics:
    def __init__(
        self,
        library: MediaLibrary,
        producer: GraphicsProducer,
        paths: AppPaths,
        clock: Clock,
        preparer: VisualAssetPreparer | None = None,
    ) -> None:
        self._library = library
        self._producer = producer
        self._documents = DerivedDocuments(library, paths)
        self._clock = clock
        self._preparer = preparer

    def execute(
        self, command: PlanGraphicsCommand, ctx: JobContext
    ) -> Result[PlanGraphicsResult, PlanError]:
        brief = command.brief
        anchor = self._library.get(brief.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        source = self._producer.style(command.style)
        if isinstance(source, Err):
            return source
        style = source.value.style
        if (style.output.width, style.output.height) != (brief.width, brief.height):
            return Err(
                StyleMismatch(
                    f"style is for {style.output.width}x{style.output.height}, the edit is"
                    f" {brief.width}x{brief.height}"
                )
            )
        try:
            profile = command.config.resolve(brief.story_format)
        except InvalidGraphicsConfig as exc:
            return Err(exc)
        quality = command.quality or RenderQuality(profile.render.quality)
        renderer = self._producer.backend.identity()
        renderer_ready = self._producer.backend.unavailable() is None
        config_fp = digest(command.config.fingerprint(profile))
        key: dict[str, JsonValue] = json.loads(
            json.dumps(
                {
                    "brief": digest(encode(brief)),
                    "style": style.fingerprint,
                    "config": config_fp,
                    "render": command.render,
                    "quality": quality.value,
                    "renderer": [*renderer, renderer_ready],
                    "versions": [
                        PLANNER_VERSION,
                        COMPILER_VERSION,
                        SCHEMA_VERSION,
                        VOCABULARY_VERSION,
                    ],
                }
            )
        )
        spec = DocumentSpec(RESULT_OPERATION, PLANNER_VERSION, key)
        fingerprint = self._documents.fingerprint(anchor.value.id, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        hit = self._documents.load(anchor.value.id, fingerprint.value, plan_from_json)
        if hit is not None:
            return Ok(PlanGraphicsResult(plan=hit[0], document=hit[1], created=False))

        facts = style_facts(style)
        planned_requests = plan_requests(brief, command.config, profile, facts)
        checks: list[QcIssue] = list(planned_requests.issues)
        stages: list[StageReport] = [
            StageReport(
                stage="plan",
                state=StageState.OK,
                detail=f"{len(planned_requests.requests)} graphics",
            )
        ]
        inputs: list[InputRecord] = [
            InputRecord(kind="anchor", how=InputHow.SUPPLIED, asset_id=anchor.value.id),
            InputRecord(
                kind="style",
                how=InputHow.SUPPLIED if source.value.ref is None else InputHow.REUSED,
                asset_id=source.value.ref.document_id if source.value.ref else None,
                identity=style.fingerprint,
            ),
            InputRecord(
                kind="edit",
                how=InputHow.SUPPLIED if brief.edit else InputHow.NOT_AVAILABLE,
                identity=f"{brief.edit.edit_plan_id}@{brief.edit.plan_version}"
                if brief.edit
                else None,
                detail="" if brief.edit else "no edit plan (no-speech path)",
            ),
        ]
        requests, unavailable_items = self._images(
            planned_requests.requests, planned_requests.images, brief, style, ctx, inputs, stages
        )
        rejected = {
            d.graphic_id for d in command.config.decisions if d.action is DecisionAction.REJECT
        }
        active = tuple(
            r
            for r in requests
            if r.request_id not in rejected and r.request_id not in unavailable_items
        )
        produced = self._producer.produce(
            anchor.value,
            active,
            source.value,
            brief.frame_rate,
            quality,
            profile,
            ctx,
            render=command.render,
        )
        by_id = {g.graphic_id: g for g in produced}
        graphics: list[PlannedGraphic] = []
        for r in requests:
            if r.request_id in by_id:
                graphics.append(by_id[r.request_id])
                continue
            status = ItemStatus.REJECTED if r.request_id in rejected else ItemStatus.NOT_AVAILABLE
            detail = (
                "rejected by a person"
                if status is ItemStatus.REJECTED
                else unavailable_items.get(r.request_id, "")
            )
            graphics.append(
                PlannedGraphic(
                    graphic_id=r.request_id,
                    request=r,
                    status=status,
                    frames=None,
                    composition=None,
                    animation=None,
                    render=None,
                    checks=(
                        issue(
                            "item." + status.value,
                            Severity.INFO if status is ItemStatus.REJECTED else Severity.WARNING,
                            detail,
                            r.request_id,
                        ),
                    ),
                    fingerprint=digest(encode(r)),
                    detail=detail,
                )
            )
        if command.render:
            unavailable = self._producer.backend.unavailable()
            stages.append(
                StageReport(
                    stage="render",
                    state=StageState.OK if unavailable is None else StageState.NOT_AVAILABLE,
                    detail=unavailable or f"{renderer[0]} {renderer[1]}",
                )
            )
        else:
            stages.append(
                StageReport(stage="render", state=StageState.SKIPPED, detail="not requested")
            )
        version, supersedes = self._history(anchor.value.id, brief.project_id)
        plan = assemble_plan(
            meta=PlanMeta(
                plan_id=f"graphics:{fingerprint.value[:16]}",
                plan_version=version,
                supersedes=supersedes,
                created_at=self._clock.now(),
                profile=profile.name,
                profile_version=PROFILE_VERSION,
                configuration_fingerprint=config_fp,
                engines=(renderer,),
            ),
            project_id=brief.project_id,
            edit=brief.edit,
            style=binding(style, source.value.ref),
            anchor_asset_id=anchor.value.id,
            width=brief.width,
            height=brief.height,
            fps=brief.frame_rate,
            graphics=tuple(graphics),
            checks=tuple(checks),
            inputs=tuple(inputs),
            stages=tuple(stages),
            warnings=tuple(
                sorted(
                    {
                        i.message
                        for g in graphics
                        for i in g.checks
                        if i.severity is Severity.WARNING
                    }
                )
            ),
            limitations=LIMITATIONS,
        )
        critical = validate_plan(plan)
        if critical:
            return Err(InvalidGraphicsPlan([i.message for i in critical]))
        stored = self._documents.store(
            anchor.value,
            plan_to_json(plan),
            filename="graphics_plan.json",
            spec=spec,
            display_name=f"{anchor.value.display_name} (graphics plan v{version})",
            metadata={
                "document_type": DOCUMENT_TYPE,
                "project_id": brief.project_id,
                "edit_plan_id": brief.edit.edit_plan_id if brief.edit else None,
                "plan_version": version,
                "supersedes": supersedes,
                "state": plan.state.value,
                "style_fingerprint": style.fingerprint,
                "profile": profile.name,
            },
        )
        if isinstance(stored, Err):
            return stored
        return Ok(PlanGraphicsResult(plan=plan, document=stored.value, created=True))

    def _images(
        self,
        requests: tuple[GraphicRequest, ...],
        needs: tuple[ImageNeed, ...],
        brief: GraphicsBrief,
        style: ResolvedStyle,
        ctx: JobContext,
        inputs: list[InputRecord],
        stages: list[StageReport],
    ) -> tuple[tuple[GraphicRequest, ...], dict[str, str]]:
        """Product stickers / silhouettes from visual_asset_generation (never cut out here)."""
        if not needs:
            return requests, {}
        if self._preparer is None:
            stages.append(
                StageReport(stage="images", state=StageState.NOT_AVAILABLE, detail="no preparer")
            )
            return self._without(requests, needs, "visual_asset_generation is not installed")
        variant = {"sticker": VariantName.STICKER, "silhouette": VariantName.SILHOUETTE}
        prepared = self._preparer.execute(
            PrepareVisualAssetsCommand(
                requests=tuple(
                    AssetRequest(
                        request_id=n.request_id,
                        source_asset_id=n.source_asset_id,
                        variants=(variant[n.variant],),
                        purpose=f"motion graphic {n.request_id}",
                    )
                    for n in needs
                ),
                project_id=brief.project_id,
                edit=AssetEditRef(
                    edit_plan_id=brief.edit.edit_plan_id, plan_version=brief.edit.plan_version
                )
                if brief.edit
                else None,
                style=style,
                anchor_asset_id=brief.anchor_asset_id,
            ),
            ctx,
        )
        if isinstance(prepared, Err):
            detail = prepared.error.user_message
            stages.append(
                StageReport(stage="images", state=StageState.NOT_AVAILABLE, detail=detail)
            )
            inputs.append(InputRecord(kind="cutouts", how=InputHow.NOT_AVAILABLE, detail=detail))
            return self._without(requests, needs, f"cut-out not available: {detail}")
        document = prepared.value.document
        inputs.append(
            InputRecord(kind="cutouts", how=InputHow.REQUESTED, asset_id=prepared.value.asset.id)
        )
        stages.append(
            StageReport(stage="images", state=StageState.OK, detail=f"{len(needs)} cut-outs")
        )
        failed: dict[str, str] = {}
        replaced: dict[str, tuple[str, ...]] = {}
        for need in needs:
            usable = [
                i
                for i in document.usable(need.request_id)
                if i.variant == need.variant and i.asset_id
            ]
            if usable and usable[0].asset_id:
                replaced[need.request_id] = (usable[0].asset_id,)
            elif need.optional:
                replaced[need.request_id] = ()  # never show an uncut photo as a sticker
            else:
                failed[need.request_id] = (
                    f"no usable {need.variant} cut-out (visual_asset_generation)"
                )
        out = tuple(
            replace(r, content=replace(r.content, image_asset_ids=replaced[r.request_id]))
            if r.request_id in replaced
            else r
            for r in requests
        )
        return out, failed

    @staticmethod
    def _without(
        requests: tuple[GraphicRequest, ...], needs: tuple[ImageNeed, ...], why: str
    ) -> tuple[tuple[GraphicRequest, ...], dict[str, str]]:
        optional = {n.request_id for n in needs if n.optional}
        failed = {n.request_id: why for n in needs if not n.optional}
        out = tuple(
            replace(r, content=replace(r.content, image_asset_ids=()))
            if r.request_id in optional
            else r
            for r in requests
        )
        return out, failed

    def _history(self, anchor_id: str, project_id: str | None) -> tuple[int, str | None]:
        earlier = [
            a
            for a in self._library.list_derived(anchor_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and a.metadata.get("project_id") == project_id
        ]
        earlier.sort(key=lambda a: (a.created_at, a.id), reverse=True)
        return len(earlier) + 1, earlier[0].id if earlier else None
