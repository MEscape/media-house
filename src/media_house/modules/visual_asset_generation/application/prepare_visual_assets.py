"""Use case: a batch of picture requests -> library assets + one stored ``visual_asset_set``.

Per request: (video -> exact frame ->) clean cutout -> enhanced variants of the clean cutout ->
optional delivery copies. Each output is a derived library asset of its direct parent (frame of
the video, cutout of the image, variant of the clean cutout, export of what it exports), so the
library's derivation records ARE the relationships. Every output is measured and judged before it
is registered; an output with an ERROR is not registered and its item names its safe fallback
(variant -> clean cutout -> original). The whole set is cached by the library fingerprint of its
inputs: an identical batch returns the stored document and computes nothing.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.modules.visual_asset_generation.application.adjust_image import (
    cutout_display_name,
    cutout_metadata,
)
from media_house.modules.visual_asset_generation.application.derive import (
    Derivation,
    Derived,
    DeriveError,
    Deriver,
)
from media_house.modules.visual_asset_generation.application.grab_frame import (
    GrabFrame,
    GrabFrameCommand,
)
from media_house.modules.visual_asset_generation.application.ports import (
    CutoutEnhancer,
    EnhanceReport,
    ExportReport,
    ImageExporter,
    ImageRenderer,
    RenderReport,
)
from media_house.modules.visual_asset_generation.application.style import identity_layer, style_ref
from media_house.modules.visual_asset_generation.domain.engine import (
    digest,
    set_cache_key,
    state_of,
)
from media_house.modules.visual_asset_generation.domain.errors import (
    AdjustmentFailed,
    InvalidVisualAssetRequest,
)
from media_house.modules.visual_asset_generation.domain.model import (
    ENHANCEMENT_OPERATION,
    ENHANCEMENT_VERSION,
    EXPORT_OPERATION,
    EXPORT_VERSION,
    PLANNER_VERSION,
    SCHEMA_VERSION,
    SET_OPERATION,
    AssetKind,
    EditRef,
    FrameGrab,
    InputHow,
    InputRecord,
    ItemStatus,
    PreparedAsset,
    Provenance,
    StageReport,
    StageState,
    StyleRef,
    VisualAssetSet,
)
from media_house.modules.visual_asset_generation.domain.profile import (
    Enhancement,
    VariantName,
    VisualAssetConfig,
    VisualAssetProfile,
    fingerprint,
)
from media_house.modules.visual_asset_generation.domain.qc import (
    CutoutFacts,
    ImageFacts,
    QcCategory,
    QcIssue,
    Severity,
    check_cutout,
    check_file,
    check_transparency,
)
from media_house.modules.visual_asset_generation.domain.requests import (
    AssetRequest,
    ExportSettings,
    ImageFormat,
)
from media_house.modules.visual_asset_generation.domain.serialization import (
    set_from_json,
    set_to_json,
)
from media_house.modules.visual_asset_generation.domain.values import (
    ADJUSTMENT_OPERATION,
    ADJUSTMENT_VERSION,
    Canvas,
)
from media_house.modules.visual_style_engine.application.contracts import (
    ResolvedStyle,
    VisualStyleCatalog,
)
from media_house.modules.visual_style_engine.application.contracts import (
    StyleRef as StoredStyleRef,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, ExternalSystemError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

#: Whole-batch failures only: a library failure, an invalid batch or an unusable environment
#: (missing model or tool; nothing is stored, so the batch runs again once it is fixed).
type PrepareError = MediaError | AdjustmentFailed | ExternalSystemError

_LIMITATIONS = (
    "Cutouts and variants are 8-bit RGBA PNG: the cutout renderer decodes sources at 8 bits per "
    "channel (frame grabs themselves are stored as 16-bit RGB PNG).",
    "Background removal needs a local rembg model; nothing is downloaded during a run.",
    "No manual mask correction exists yet: a rejected or poor cutout is reported, not repaired.",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class PrepareVisualAssetsCommand:
    """Prepare every picture in ``requests``; the set is a document of the anchor asset.

    ``style`` (or ``style_ref``, read through the style catalog) supplies the channel identity
    for the presets; without either the profile's explicit values are used and recorded.
    """

    requests: tuple[AssetRequest, ...]
    config: VisualAssetConfig = field(default_factory=VisualAssetConfig)
    project_id: str | None = None
    edit: EditRef | None = None
    style: ResolvedStyle | None = None
    style_ref: StoredStyleRef | None = None
    #: The asset the set is stored under; default: the first request's source.
    anchor_asset_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedVisualAssets:
    document: VisualAssetSet
    #: The stored ``visual_asset_set`` document.
    asset: MediaAssetDto
    #: ``False`` when the identical batch was already prepared (nothing computed).
    created: bool


@dataclass
class _Run:
    """What one batch collects while it runs."""

    profile: VisualAssetProfile
    items: list[PreparedAsset] = field(default_factory=list)
    frames: list[FrameGrab] = field(default_factory=list)
    checks: list[QcIssue] = field(default_factory=list)
    inputs: list[InputRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    engines: dict[str, str] = field(default_factory=dict)
    stages: dict[str, StageState] = field(default_factory=dict)

    def stage(self, name: str, state: StageState) -> None:
        order = (StageState.SKIPPED, StageState.OK, StageState.NOT_AVAILABLE, StageState.FAILED)
        current = self.stages.get(name, StageState.SKIPPED)
        self.stages[name] = max(current, state, key=order.index)


@dataclass(frozen=True, slots=True)
class _Parent:
    """A produced picture that later steps build on."""

    variant: str
    asset: MediaAssetDto
    facts: ImageFacts


class PrepareVisualAssets:
    def __init__(
        self,
        library: MediaLibrary,
        paths: AppPaths,
        clock: Clock,
        deriver: Deriver,
        frames: GrabFrame,
        renderer: ImageRenderer,
        enhancer: CutoutEnhancer,
        exporter: ImageExporter,
        styles: VisualStyleCatalog | None,
    ) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)
        self._clock = clock
        self._deriver = deriver
        self._frames = frames
        self._renderer = renderer
        self._enhancer = enhancer
        self._exporter = exporter
        self._styles = styles

    def execute(
        self, command: PrepareVisualAssetsCommand, ctx: JobContext
    ) -> Result[PreparedVisualAssets, PrepareError]:
        invalid = _request_problems(command.requests)
        if invalid:
            return Err(InvalidVisualAssetRequest("; ".join(invalid)))
        anchor = self._library.get(command.anchor_asset_id or command.requests[0].source_asset_id)
        if isinstance(anchor, Err):
            return anchor
        style, ref, style_input = self._style(command)
        profile = command.config.resolve(identity_layer(style) if style is not None else None)
        configuration = digest(fingerprint(profile))
        key = set_cache_key(
            command.requests,
            project_id=command.project_id,
            edit=command.edit,
            style_fingerprint=None if ref is None else ref.fingerprint,
            configuration_fingerprint=configuration,
        )
        spec = DocumentSpec(SET_OPERATION, PLANNER_VERSION, _json(key))
        stored_fp = self._documents.fingerprint(anchor.value.id, spec)
        if isinstance(stored_fp, Err):
            return stored_fp
        hit = self._documents.load(anchor.value.id, stored_fp.value, set_from_json)
        if hit is not None:
            _log.info("Visual asset set reused", asset_id=hit[1].id)
            return Ok(PreparedVisualAssets(document=hit[0], asset=hit[1], created=False))

        run = _Run(profile=profile)
        run.inputs.append(style_input)
        run.stage("style", StageState.OK if style is not None else StageState.NOT_AVAILABLE)
        for index, request in enumerate(command.requests):
            ctx.raise_if_cancelled()
            ctx.progress.report(index, len(command.requests), f"Preparing {request.request_id}")
            done = self._request(request, run, ctx)
            if isinstance(done, Err):
                return done
        ctx.progress.report(len(command.requests), len(command.requests), "Storing the set")
        return self._store(command, anchor.value, spec, run, ref, configuration)

    # --- one request -------------------------------------------------------------------------

    def _request(
        self, request: AssetRequest, run: _Run, ctx: JobContext
    ) -> Result[None, PrepareError]:
        found = self._library.get(request.source_asset_id)
        if isinstance(found, Err):
            self._fail_all(request, run, None, f"source asset not found: {found.error}")
            return Ok(None)
        source = found.value
        run.inputs.append(
            InputRecord(
                kind="source", how=InputHow.SUPPLIED, asset_id=source.id, identity=source.checksum
            )
        )
        if source.media_type is MediaType.VIDEO:
            framed = self._frame(request, source, run, ctx)
            if isinstance(framed, Err):
                return framed
            if framed.value is None:
                return Ok(None)
            image = framed.value
        elif source.media_type is MediaType.IMAGE:
            image = source
            if request.source_time is not None:
                run.warnings.append(f"{request.request_id}: source_time ignored for an image")
        else:
            self._fail_all(
                request, run, None, f"{source.media_type.value} is not an image or video"
            )
            return Ok(None)

        clean = self._clean(request, source, image, run, ctx)
        if isinstance(clean, Err):
            return clean
        if clean.value is None:
            return Ok(None)
        parents = [clean.value]
        for variant in request.wanted()[1:]:
            enhanced = self._variant(request, source, clean.value, variant, run, ctx)
            if isinstance(enhanced, Err):
                return enhanced
            if enhanced.value is not None:
                parents.append(enhanced.value)
        if request.export is not None:
            for parent in parents:
                exported = self._export(request, source, parent, request.export, run, ctx)
                if isinstance(exported, Err):
                    return exported
        return Ok(None)

    def _frame(
        self, request: AssetRequest, video: MediaAssetDto, run: _Run, ctx: JobContext
    ) -> Result[MediaAssetDto | None, PrepareError]:
        if request.source_time is None:
            self._fail_all(request, run, None, "a video source needs source_time")
            return Ok(None)
        grabbed = self._frames.execute(
            GrabFrameCommand(video_asset_id=video.id, source_time=request.source_time), ctx
        )
        if isinstance(grabbed, Err):
            if not isinstance(grabbed.error, AdjustmentFailed):
                return Err(grabbed.error)
            run.stage("frame_grab", StageState.FAILED)
            self._fail_all(request, run, None, f"frame grab failed: {grabbed.error.user_message}")
            return Ok(None)
        frame = grabbed.value
        run.stage("frame_grab", StageState.OK)
        run.inputs.append(frame.inspection)
        run.checks.extend(frame.checks)
        if frame.engine is not None:
            run.engines[frame.engine[0]] = frame.engine[1]
        if not frame.position.exact:
            run.warnings.append(f"{request.request_id}: {frame.position.detail}")
        run.frames.append(
            FrameGrab(
                video_asset_id=video.id,
                source_time=frame.position.source_time,
                frame_index=frame.position.frame_index,
                frame_time=frame.position.frame_time,
                exact=frame.position.exact,
                asset_id=frame.asset.id,
            )
        )
        return Ok(frame.asset)

    def _clean(
        self,
        request: AssetRequest,
        original: MediaAssetDto,
        image: MediaAssetDto,
        run: _Run,
        ctx: JobContext,
    ) -> Result[_Parent | None, PrepareError]:
        settings = run.profile.cutout.settings()
        config = settings.to_config()
        item_id = f"{request.request_id}:{VariantName.CLEAN.value}"
        path = self._library.local_path(image.id)
        if isinstance(path, Err):
            return path
        if settings.background_removal.enabled:
            run.engines["background_removal"] = settings.background_removal.model

        def judge(
            facts: ImageFacts, report: RenderReport | None, hit: MediaAssetDto | None
        ) -> list[QcIssue]:
            issues = check_file(facts, item_id, expected_format="PNG")
            if settings.background_removal.enabled:
                issues += check_transparency(facts, item_id, clipped_is_error=False)
            return issues + check_cutout(facts, _cutout_facts(report, hit), run.profile.qc, item_id)

        derived = self._deriver.derive(
            Derivation(
                source=image,
                operation=ADJUSTMENT_OPERATION,
                version=ADJUSTMENT_VERSION,
                config=config,
                filename="adjusted.png",
                display_name=cutout_display_name(image, settings),
            ),
            lambda out: self._renderer.render(path.value, out, settings),
            lambda report: cutout_metadata(image, settings, report),
            judge,
            ctx,
        )
        run.engines["cutout"] = f"{ADJUSTMENT_OPERATION}/{ADJUSTMENT_VERSION}"
        made = self._outcome(
            request,
            original,
            image,
            VariantName.CLEAN.value,
            AssetKind.CLEAN_CUTOUT,
            ADJUSTMENT_OPERATION,
            config,
            None,
            derived,
            run,
            fallback=image.id,
            stage="cutout",
        )
        if isinstance(made, Err) or made.value is not None:
            return made
        # No clean cutout: no variant can be made; each names the original picture instead.
        for variant in request.wanted()[1:]:
            self._failed(
                request,
                original.id,
                variant.value,
                AssetKind.ENHANCED,
                ENHANCEMENT_OPERATION,
                image.id,
                run,
                "the clean cutout could not be made",
            )
        return made

    def _variant(
        self,
        request: AssetRequest,
        original: MediaAssetDto,
        clean: _Parent,
        variant: VariantName,
        run: _Run,
        ctx: JobContext,
    ) -> Result[_Parent | None, PrepareError]:
        enhancement = run.profile.variants.of(variant)
        if enhancement is None:
            return Ok(None)
        canvas = run.profile.cutout.canvas
        config = _enhancement_config(enhancement, canvas)
        item_id = f"{request.request_id}:{variant.value}"
        path = self._library.local_path(clean.asset.id)
        if isinstance(path, Err):
            return path
        run.engines["enhancement"] = f"{ENHANCEMENT_OPERATION}/{ENHANCEMENT_VERSION}"

        def judge(
            facts: ImageFacts, _report: EnhanceReport | None, _hit: MediaAssetDto | None
        ) -> list[QcIssue]:
            return check_file(facts, item_id, expected_format="PNG") + check_transparency(
                facts, item_id, clipped_is_error=True
            )

        derived = self._deriver.derive(
            Derivation(
                source=clean.asset,
                operation=ENHANCEMENT_OPERATION,
                version=ENHANCEMENT_VERSION,
                config=config,
                filename="enhanced.png",
                display_name=f"{original.display_name} ({variant.value})",
            ),
            lambda out: self._enhancer.enhance(path.value, out, enhancement, canvas),
            lambda report: {
                "variant": variant.value,
                "clean_cutout_asset_id": clean.asset.id,
                "original_asset_id": original.id,
                "enhancements": list(enhancement.applied()),
                "subject_scale": round(report.scale, 6),
                "visible_box": list(report.visible_box),
            },
            judge,
            ctx,
        )
        return self._outcome(
            request,
            original,
            clean.asset,
            variant.value,
            AssetKind.ENHANCED,
            ENHANCEMENT_OPERATION,
            config,
            clean.asset.id,
            derived,
            run,
            fallback=clean.asset.id,
            stage="enhancement",
            enhancements=enhancement.applied(),
        )

    def _export(
        self,
        request: AssetRequest,
        original: MediaAssetDto,
        parent: _Parent,
        settings: ExportSettings,
        run: _Run,
        ctx: JobContext,
    ) -> Result[None, PrepareError]:
        variant = f"{parent.variant}.{settings.format.value}"
        item_id = f"{request.request_id}:{variant}"
        config = settings.to_config()
        if _already_delivered(parent.asset, settings):
            run.items.append(
                _item(
                    request,
                    original,
                    parent.asset,
                    variant,
                    AssetKind.EXPORT,
                    EXPORT_OPERATION,
                    config,
                    parent.asset.id,
                    None,
                    parent.facts,
                    reused=True,
                    detail="already a PNG of that size: delivered as is, not recompressed",
                )
            )
            run.stage("export", StageState.OK)
            return Ok(None)
        path = self._library.local_path(parent.asset.id)
        if isinstance(path, Err):
            return path
        run.engines["export"] = f"{EXPORT_OPERATION}/{EXPORT_VERSION}"
        had_alpha = parent.facts.has_alpha and parent.facts.opaque_ratio < 1.0

        def judge(
            facts: ImageFacts, _report: ExportReport | None, _hit: MediaAssetDto | None
        ) -> list[QcIssue]:
            issues = check_file(facts, item_id, expected_format=settings.format.pillow_name)
            if had_alpha and settings.format.keeps_alpha and not facts.has_alpha:
                issues.append(
                    _error("transparency_lost", "the export lost the transparency", item_id)
                )
            too_wide = settings.max_width and facts.width > settings.max_width
            if too_wide or (settings.max_height and facts.height > settings.max_height):
                issues.append(
                    _error("export_too_large", "the export exceeds the size asked", item_id)
                )
            return issues

        derived = self._deriver.derive(
            Derivation(
                source=parent.asset,
                operation=EXPORT_OPERATION,
                version=EXPORT_VERSION,
                config=config,
                filename=f"export.{_extension(settings.format)}",
                display_name=f"{original.display_name} ({variant})",
            ),
            lambda out: self._exporter.export(path.value, out, settings),
            lambda report: {
                "variant": variant,
                "original_asset_id": original.id,
                "format": settings.format.value,
                "fitted": not report.kept_size,
            },
            judge,
            ctx,
        )
        made = self._outcome(
            request,
            original,
            parent.asset,
            variant,
            AssetKind.EXPORT,
            EXPORT_OPERATION,
            config,
            parent.asset.id,
            derived,
            run,
            fallback=parent.asset.id,
            stage="export",
        )
        return made if isinstance(made, Err) else Ok(None)

    # --- bookkeeping -------------------------------------------------------------------------

    def _outcome[R](
        self,
        request: AssetRequest,
        original: MediaAssetDto,
        parent: MediaAssetDto,
        variant: str,
        kind: AssetKind,
        operation: str,
        config: dict[str, JsonValue],
        clean_id: str | None,
        derived: Result[Derived[R], DeriveError],
        run: _Run,
        *,
        fallback: str,
        stage: str,
        enhancements: tuple[str, ...] = (),
    ) -> Result[_Parent | None, PrepareError]:
        item_id = f"{request.request_id}:{variant}"
        if isinstance(derived, Err):
            error = derived.error
            if not isinstance(error, AdjustmentFailed):
                return Err(error)
            run.checks.append(_error(error.code.rsplit(".", 1)[-1], error.user_message, item_id))
            self._failed(
                request, original.id, variant, kind, operation, fallback, run, error.user_message
            )
            run.stage(stage, StageState.FAILED)
            return Ok(None)
        done = derived.value
        run.checks.extend(done.issues)
        if done.result is None:
            reasons = "; ".join(i.message for i in done.issues if i.severity is Severity.ERROR)
            self._failed(request, original.id, variant, kind, operation, fallback, run, reasons)
            run.stage(stage, StageState.FAILED)
            return Ok(None)
        asset = done.result.asset
        clean = asset.id if kind is AssetKind.CLEAN_CUTOUT else clean_id
        run.items.append(
            _item(
                request,
                original,
                asset,
                variant,
                kind,
                operation,
                config,
                parent.id,
                clean,
                done.facts,
                reused=not done.result.created,
                enhancements=enhancements,
            )
        )
        run.stage(stage, StageState.OK)
        return Ok(_Parent(variant=variant, asset=asset, facts=done.facts))

    def _failed(
        self,
        request: AssetRequest,
        source_asset_id: str,
        variant: str,
        kind: AssetKind,
        operation: str,
        fallback: str | None,
        run: _Run,
        detail: str,
    ) -> None:
        run.items.append(
            PreparedAsset(
                item_id=f"{request.request_id}:{variant}",
                request_id=request.request_id,
                purpose=request.purpose,
                variant=variant,
                kind=kind,
                status=ItemStatus.FAILED,
                asset_id=None,
                source_asset_id=source_asset_id,
                derived_from_asset_id=None,
                clean_cutout_asset_id=None,
                operation=operation,
                configuration_hash="",
                width=None,
                height=None,
                format=None,
                has_alpha=None,
                fallback_asset_id=fallback,
                detail=detail,
            )
        )

    def _fail_all(
        self, request: AssetRequest, run: _Run, fallback: str | None, detail: str
    ) -> None:
        run.checks.append(_error("source_unusable", detail, f"{request.request_id}:clean"))
        for variant in request.wanted():
            clean = variant is VariantName.CLEAN
            self._failed(
                request,
                request.source_asset_id,
                variant.value,
                AssetKind.CLEAN_CUTOUT if clean else AssetKind.ENHANCED,
                ADJUSTMENT_OPERATION if clean else ENHANCEMENT_OPERATION,
                fallback,
                run,
                detail,
            )

    def _style(
        self, command: PrepareVisualAssetsCommand
    ) -> tuple[ResolvedStyle | None, StyleRef | None, InputRecord]:
        style, stored = command.style, command.style_ref
        if style is None and stored is not None:
            if self._styles is None:
                return (
                    None,
                    None,
                    InputRecord(
                        kind="visual_style",
                        how=InputHow.NOT_AVAILABLE,
                        identity=stored.fingerprint,
                        detail="the visual style engine is not installed: profile values used",
                    ),
                )
            loaded = self._styles.load_ref(stored)
            if isinstance(loaded, Err):
                return (
                    None,
                    None,
                    InputRecord(
                        kind="visual_style",
                        how=InputHow.NOT_AVAILABLE,
                        identity=stored.fingerprint,
                        detail=f"the style could not be read ({loaded.error}): profile values used",
                    ),
                )
            style = loaded.value
        if style is None:
            return (
                None,
                None,
                InputRecord(
                    kind="visual_style",
                    how=InputHow.NOT_AVAILABLE,
                    detail="no visual style given: the profile's explicit values are used",
                ),
            )
        ref = style_ref(style, stored)
        how = InputHow.REUSED if command.style is None else InputHow.SUPPLIED
        return (
            style,
            ref,
            InputRecord(
                kind="visual_style",
                how=how,
                asset_id=ref.document_id,
                identity=style.fingerprint,
                detail=f"{style.identity_id} v{style.identity_version}, profile {style.profile}",
            ),
        )

    def _store(
        self,
        command: PrepareVisualAssetsCommand,
        anchor: MediaAssetDto,
        spec: DocumentSpec,
        run: _Run,
        ref: StyleRef | None,
        configuration: str,
    ) -> Result[PreparedVisualAssets, PrepareError]:
        version, supersedes = self._history(anchor.id, command.project_id)
        items = tuple(run.items)
        document = VisualAssetSet(
            plan_id=f"vas:{anchor.id}:{command.project_id or '-'}:{version}",
            plan_version=version,
            supersedes=supersedes,
            project_id=command.project_id,
            edit=command.edit,
            style=ref,
            created_at=self._clock.now(),
            state=state_of(items),
            items=items,
            frames=tuple(run.frames),
            checks=tuple(run.checks),
            inputs=tuple(run.inputs),
            stages=tuple(
                StageReport(stage=s, state=run.stages.get(s, StageState.SKIPPED))
                for s in ("style", "frame_grab", "cutout", "enhancement", "export")
            ),
            provenance=Provenance(
                schema_version=SCHEMA_VERSION,
                planner_version=PLANNER_VERSION,
                profile=run.profile.name,
                profile_version=run.profile.version,
                configuration_fingerprint=configuration,
                engines=tuple(sorted(run.engines.items())),
            ),
            warnings=tuple(run.warnings),
            limitations=_LIMITATIONS,
        )
        stored = self._documents.store(
            anchor,
            set_to_json(document),
            filename="visual_asset_set.json",
            spec=spec,
            display_name=f"{anchor.display_name} (visual assets v{version})",
            metadata={
                "project_id": command.project_id,
                "plan_version": version,
                "state": document.state.value,
                "items": len(items),
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info("Visual asset set stored", asset_id=stored.value.id, state=document.state.value)
        return Ok(PreparedVisualAssets(document=document, asset=stored.value, created=True))

    def _history(self, anchor: str, project_id: str | None) -> tuple[int, str | None]:
        earlier = [
            a
            for a in self._library.list_derived(anchor)
            if a.metadata.get("processing_type") == SET_OPERATION
            and a.metadata.get("project_id") == project_id
        ]
        if not earlier:
            return 1, None
        newest = max(earlier, key=lambda a: (a.created_at, a.id))
        return len(earlier) + 1, newest.id


# --- helpers ---------------------------------------------------------------------------------


def _request_problems(requests: tuple[AssetRequest, ...]) -> list[str]:
    if not requests:
        return ["no picture was requested"]
    problems: list[str] = []
    ids = [r.request_id for r in requests]
    if any(not i.strip() for i in ids) or any(not r.source_asset_id.strip() for r in requests):
        problems.append("every request needs a request_id and a source_asset_id")
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append(f"duplicate request ids: {', '.join(duplicates)}")
    return problems


def _enhancement_config(enhancement: Enhancement, canvas: Canvas) -> dict[str, JsonValue]:
    return {
        "enhancement": enhancement.to_config(),
        "canvas": {"width": canvas.width, "height": canvas.height},
    }


def _already_delivered(asset: MediaAssetDto, settings: ExportSettings) -> bool:
    """A PNG that is already within the size limits needs no second copy."""
    if settings.format is not ImageFormat.PNG or asset.mime_type != "image/png":
        return False
    width, height = asset.width or 0, asset.height or 0
    fits_w = settings.max_width == 0 or width <= settings.max_width
    fits_h = settings.max_height == 0 or height <= settings.max_height
    return fits_w and fits_h


def _cutout_facts(report: RenderReport | None, hit: MediaAssetDto | None) -> CutoutFacts:
    if report is not None:
        return CutoutFacts(
            mask_box=report.mask.bbox, source_size=report.source_size, scale=report.scale
        )
    metadata: Mapping[str, JsonValue] = hit.metadata if hit is not None else {}
    return CutoutFacts(
        mask_box=_box(metadata.get("mask_bbox")),
        source_size=_size(metadata.get("source_size")),
        scale=_number(metadata.get("subject_scale")),
    )


def _box(value: object) -> tuple[int, int, int, int] | None:
    if isinstance(value, list) and len(value) == 4 and all(isinstance(v, int) for v in value):
        return int(value[0]), int(value[1]), int(value[2]), int(value[3])
    return None


def _size(value: object) -> tuple[int, int] | None:
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value):
        return int(value[0]), int(value[1])
    return None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _error(code: str, message: str, item_id: str) -> QcIssue:
    return QcIssue(
        code=code,
        severity=Severity.ERROR,
        category=QcCategory.TECHNICAL,
        message=message,
        item_id=item_id,
    )


def _item(
    request: AssetRequest,
    original: MediaAssetDto,
    asset: MediaAssetDto,
    variant: str,
    kind: AssetKind,
    operation: str,
    config: dict[str, JsonValue],
    parent_id: str,
    clean_id: str | None,
    facts: ImageFacts,
    *,
    reused: bool,
    enhancements: tuple[str, ...] = (),
    detail: str = "",
) -> PreparedAsset:
    return PreparedAsset(
        item_id=f"{request.request_id}:{variant}",
        request_id=request.request_id,
        purpose=request.purpose,
        variant=variant,
        kind=kind,
        status=ItemStatus.OK,
        asset_id=asset.id,
        source_asset_id=original.id,
        derived_from_asset_id=parent_id,
        clean_cutout_asset_id=clean_id,
        operation=operation,
        configuration_hash=digest(config),
        width=facts.width or asset.width,
        height=facts.height or asset.height,
        format=facts.format.lower() or None,
        has_alpha=facts.has_alpha,
        enhancements=enhancements,
        reused=reused,
        detail=detail,
    )


def _extension(image_format: ImageFormat) -> str:
    return "jpg" if image_format is ImageFormat.JPEG else image_format.value


def _json(key: Mapping[str, object]) -> Mapping[str, JsonValue]:
    return cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str)))
