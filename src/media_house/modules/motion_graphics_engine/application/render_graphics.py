"""Render graphic requests (the executor contract caption_engine and transition_engine call).

Per request: check -> layout -> collisions -> animation -> style drift check -> composition ->
library cache lookup (identical composition + style + renderer = reuse, nothing rendered) ->
render -> render QC (ffprobe + decoded alpha) -> derived asset of the anchor with provenance.
"""

from dataclasses import dataclass, field, replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaQuery,
)
from media_house.modules.motion_graphics_engine.application.ports import (
    ComposedScene,
    FontAsset,
    FontMetricsReader,
    GraphicsBackend,
    ImageAsset,
    RenderProbe,
    Scene,
    ScenePart,
)
from media_house.modules.motion_graphics_engine.application.style import (
    binding,
    from_style_issue,
    style_facts,
    usage,
)
from media_house.modules.motion_graphics_engine.domain.animation import CaptionMotion
from media_house.modules.motion_graphics_engine.domain.engine import (
    BuildSettings,
    Built,
    build,
    changing_frames,
    digest,
    planned,
    resample,
    resolve_collisions,
)
from media_house.modules.motion_graphics_engine.domain.errors import (
    InvalidGraphicRequest,
    InvalidGraphicsConfig,
    NothingToRender,
    RendererNotAvailable,
    StyleMismatch,
)
from media_house.modules.motion_graphics_engine.domain.model import (
    AnimationEvent,
    FaceMetrics,
    FrameSpan,
    GraphicRequest,
    PlannedGraphic,
    RenderRecord,
    StyleFacts,
)
from media_house.modules.motion_graphics_engine.domain.profile import (
    ActiveWord,
    MotionGraphicsConfig,
    MotionGraphicsProfile,
)
from media_house.modules.motion_graphics_engine.domain.qc import (
    RenderExpectation,
    blocking,
    issue,
    render_qc,
)
from media_house.modules.motion_graphics_engine.domain.values import (
    RENDER_OPERATION,
    RENDER_TAG,
    RENDER_VERSION,
    EventKind,
    GraphicKind,
    ItemStatus,
    QcIssue,
    RenderQuality,
    Severity,
    StyleBinding,
)
from media_house.modules.visual_style_engine.application.contracts import (
    FontFiles,
    InvalidStyleDocument,
    ResolvedStyle,
    StyleRef,
    StyleRefMismatch,
    StyleValidator,
    VisualStyleCatalog,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderGraphicsCommand:
    """Render ``requests`` for the edit whose anchor (first source asset) is ``anchor_asset_id``.

    ``style``: a ``ResolvedStyle`` or the ``StyleRef`` of a stored one (read through
    ``VisualStyleCatalog``). ``fps``: the edit's frame rate. Graphics are drawn at the
    style's output size.
    """

    anchor_asset_id: str
    requests: tuple[GraphicRequest, ...]
    style: ResolvedStyle | StyleRef
    fps: Rational
    quality: RenderQuality = RenderQuality.FINAL
    config: MotionGraphicsConfig = field(default_factory=MotionGraphicsConfig)
    story_format: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderedGraphic:
    request_id: str
    status: ItemStatus
    #: The rendered MOV (PNG for a preview) in the library; ``None`` when not rendered.
    asset_id: str | None
    #: ``False`` when the identical render already existed (nothing rendered).
    created: bool
    frames: FrameSpan | None
    events: tuple[AnimationEvent, ...]
    qc: tuple[QcIssue, ...]
    render: RenderRecord | None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderedGraphics:
    items: tuple[RenderedGraphic, ...]
    style: StyleBinding
    renderer: tuple[str, str]

    def item(self, request_id: str) -> RenderedGraphic:
        return next(i for i in self.items if i.request_id == request_id)


type RenderError = (
    MediaError
    | RendererNotAvailable
    | NothingToRender
    | InvalidGraphicsConfig
    | InvalidGraphicRequest
    | StyleMismatch
)


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleInput:
    style: ResolvedStyle
    ref: StyleRef | None


class GraphicsProducer:
    """The shared core of ``RenderGraphics`` and ``PlanGraphics``."""

    def __init__(
        self,
        library: MediaLibrary,
        styles: VisualStyleCatalog,
        validator: StyleValidator,
        fonts: FontFiles,
        metrics: FontMetricsReader,
        backend: GraphicsBackend,
        probe: RenderProbe,
        paths: AppPaths,
    ) -> None:
        self._library = library
        self._styles = styles
        self._validator = validator
        self._fonts = fonts
        self._metrics = metrics
        self._backend = backend
        self._probe = probe
        self._paths = paths
        self._metric_cache: dict[str, FaceMetrics] = {}

    @property
    def backend(self) -> GraphicsBackend:
        return self._backend

    def style(self, style: ResolvedStyle | StyleRef) -> Result[StyleInput, StyleMismatch]:
        if isinstance(style, ResolvedStyle):
            return Ok(StyleInput(style=style, ref=None))
        loaded = self._styles.load_ref(style)
        if isinstance(loaded, Err):
            error = loaded.error
            detail = (
                str(error)
                if isinstance(error, InvalidStyleDocument | StyleRefMismatch)
                else error.user_message
            )
            return Err(StyleMismatch(detail))
        return Ok(StyleInput(style=loaded.value, ref=style))

    # --- inputs -----------------------------------------------------------------------------

    def prepare(self, request: GraphicRequest, style: ResolvedStyle) -> GraphicRequest:
        """Content the style formats (a price text from exact cents)."""
        cents = request.content.price_cents
        if cents is not None and cents >= 0 and not request.content.price_text:
            return replace(
                request, content=replace(request.content, price_text=style.format_price(cents))
            )
        return request

    def font_inputs(
        self, style: ResolvedStyle
    ) -> tuple[dict[str, FaceMetrics], list[FontAsset], list[QcIssue]]:
        metrics: dict[str, FaceMetrics] = {}
        assets: list[FontAsset] = []
        found: list[QcIssue] = []
        for face in style.faces:
            located = self._fonts.locate(face)
            if isinstance(located, Err):
                found.append(
                    issue("font.unavailable", Severity.WARNING, located.error.user_message, None)
                )
                continue
            path = located.value.path
            if face.key not in self._metric_cache:
                self._metric_cache[face.key] = self._metrics.metrics(face.key, path)
            metrics[face.key] = self._metric_cache[face.key]
            assets.append(
                FontAsset(
                    key=face.key,
                    family=face.family,
                    weight=face.weight,
                    path=path,
                    sha256=located.value.sha256,
                )
            )
        return metrics, assets, found

    def image_inputs(
        self, requests: tuple[GraphicRequest, ...], facts: StyleFacts
    ) -> dict[str, MediaAssetDto]:
        wanted = {a for r in requests for a in r.content.image_asset_ids}
        wanted |= {a for _, a in facts.brand_assets if a}
        return {a.id: a for a in self._library.get_many(sorted(wanted)) if a.width and a.height}

    # --- produce ----------------------------------------------------------------------------

    def produce(
        self,
        anchor: MediaAssetDto,
        requests: tuple[GraphicRequest, ...],
        source: StyleInput,
        fps: Rational,
        quality: RenderQuality,
        profile: MotionGraphicsProfile,
        ctx: JobContext,
        *,
        render: bool = True,
    ) -> list[PlannedGraphic]:
        style = source.style
        facts = style_facts(style)
        requests = tuple(self.prepare(r, style) for r in requests)
        metrics, fonts, font_issues = self.font_inputs(style)
        images = self.image_inputs(requests, facts)
        settings = BuildSettings(
            fps=fps,
            captions=CaptionMotion(
                active_word=profile.captions.active_word == ActiveWord.HIGHLIGHT.value,
                word_reveal=profile.captions.word_reveal,
            ),
            rank_label=profile.templates.rank_label,
        )
        sizes = {a.id: (a.width or 0, a.height or 0) for a in images.values()}
        built = [build(r, facts, metrics, sizes, settings) for r in requests]
        built = resolve_collisions(built, facts, metrics, sizes, settings)
        out: dict[int, PlannedGraphic] = {}
        batchable: list[tuple[int, Built, tuple[QcIssue, ...]]] = []
        unavailable = self._backend.unavailable()
        for i, b in enumerate(built):
            ctx.raise_if_cancelled()
            ctx.progress.report(i, len(built), f"graphic {b.request.request_id}")
            b = replace(b, issues=(*b.issues, *font_issues)) if not b.usable and font_issues else b
            if not b.usable:
                out[i] = planned(b, None)
                continue
            composition, animation, _ = b.parts()
            drift = tuple(
                from_style_issue(x)
                for x in self._validator.validate(style, usage(style, composition, animation))
            )
            if blocking(drift):
                out[i] = planned(b, None, drift, detail="breaks a locked style rule")
                continue
            if not render:
                out[i] = planned(b, None, drift)
                continue
            if unavailable is not None:
                out[i] = planned(
                    b,
                    None,
                    (
                        *drift,
                        issue(
                            "render.not_available",
                            Severity.WARNING,
                            unavailable,
                            b.request.request_id,
                        ),
                    ),
                    ItemStatus.NOT_AVAILABLE,
                    unavailable,
                )
                continue
            if _batchable(b, quality, profile):
                batchable.append((i, b, drift))
                continue
            record, render_issues = self._render(
                anchor, b, facts, fonts, images, quality, profile, ctx
            )
            status = ItemStatus.OK if record is not None else ItemStatus.FALLBACK
            out[i] = planned(b, record, (*drift, *render_issues), status)
        for group in _batches(batchable, profile.captions.batch_seconds):
            ctx.raise_if_cancelled()
            if len(group) == 1:
                i, b, drift = group[0]
                record, render_issues = self._render(
                    anchor, b, facts, fonts, images, quality, profile, ctx
                )
                status = ItemStatus.OK if record is not None else ItemStatus.FALLBACK
                out[i] = planned(b, record, (*drift, *render_issues), status)
                continue
            records, batch_issues = self._render_batch(
                anchor, [b for _, b, _ in group], facts, fonts, images, profile, ctx
            )
            for i, b, drift in group:
                record = records.get(b.request.request_id)
                own = tuple(q for q in batch_issues if q.item_id in (None, b.request.request_id))
                status = ItemStatus.OK if record is not None else ItemStatus.FALLBACK
                out[i] = planned(b, record, (*drift, *own), status)
        ctx.progress.report(len(built), len(built), "graphics done")
        return [out[i] for i in sorted(out)]

    def _render_batch(
        self,
        anchor: MediaAssetDto,
        group: list[Built],
        facts: StyleFacts,
        fonts: list[FontAsset],
        images: dict[str, MediaAssetDto],
        profile: MotionGraphicsProfile,
        ctx: JobContext,
    ) -> tuple[dict[str, RenderRecord], tuple[QcIssue, ...]]:
        """Several caption groups as ONE file: each shown only in its own frames (transparent
        between them); every item keeps its own frames and learns where it starts in the file."""
        spans = [b.parts()[2] for b in group]
        first = min(f.start_frame for f in spans)
        last = max(f.end_frame for f in spans)
        rid = f"batch:{group[0].request.request_id}..{group[-1].request.request_id}"
        parts = tuple(
            ScenePart(
                composition=b.parts()[0],
                animation=b.parts()[1],
                start_frame=f.start_frame - first,
                frames=f.frames,
            )
            for b, f in zip(group, spans, strict=True)
        )
        if any(e.image_asset_id for p in parts for e in p.composition.elements):
            return {}, (issue("batch.images", Severity.ERROR, "captions draw no images", rid),)
        emoji = {
            e.text.emoji_face
            for p in parts
            for e in p.composition.elements
            if e.text and e.text.emoji_face
        }
        if facts.emoji_face not in emoji:
            fonts = [f for f in fonts if f.key != facts.emoji_face]
        fps = spans[0].fps
        scene = Scene(
            composition=parts[0].composition,
            animation=parts[0].animation,
            fps=fps,
            frames=last - first,
            easings=facts.easings,
            fonts=tuple(fonts),
            images=(),
            parts=parts,
        )
        composed = self._backend.compose(scene)
        name, version = self._backend.identity()
        config: dict[str, JsonValue] = {
            "composition": composed.digest,
            "fps": str(fps),
            "frames": last - first,
            "quality": RenderQuality.FINAL.value,
            "renderer": [name, version],
            "style": facts.fingerprint,
            "size": [composed.width, composed.height],
            "still_frame": None,
            "software_gl": profile.render.software_gl,
            "batch": [p.start_frame for p in parts],
        }
        fingerprint = self._library.fingerprint(anchor.id, RENDER_OPERATION, config, RENDER_VERSION)
        if isinstance(fingerprint, Err):
            return {}, (
                issue("library.fingerprint", Severity.ERROR, fingerprint.error.user_message, rid),
            )
        found = self._library.find_derived_asset(anchor.id, fingerprint.value)
        record = (
            None
            if found is None
            else _record_from(found, created=False, fingerprint=fingerprint.value)
        )
        problems: tuple[QcIssue, ...] = ()
        if record is None:
            record, problems = self._render_file(
                anchor, rid, composed, fps, last - first, config, fingerprint.value, facts,
                profile, ctx,
                samples=tuple(
                    p.start_frame + min(p.frames - 1, _hold_frame(b))
                    for p, b in zip(parts, group, strict=True)
                ),
            )  # fmt: skip
        if record is None:
            return {}, problems
        _log.info("Caption batch", groups=len(group), frames=last - first, asset_id=record.asset_id)
        return {
            b.request.request_id: replace(record, asset_start_frame=p.start_frame)
            for b, p in zip(group, parts, strict=True)
        }, problems

    def _render_file(
        self,
        anchor: MediaAssetDto,
        rid: str,
        composed: ComposedScene,
        fps: Rational,
        frames: int,
        config: dict[str, JsonValue],
        fingerprint: str,
        facts: StyleFacts,
        profile: MotionGraphicsProfile,
        ctx: JobContext,
        *,
        samples: tuple[int, ...],
    ) -> tuple[RenderRecord | None, tuple[QcIssue, ...]]:
        """Render, probe, check and register one batch file (the single-graphic path is
        ``_render``)."""
        name, version = self._backend.identity()
        want = RenderExpectation(
            width=composed.width, height=composed.height, fps=fps, frames=frames,
            may_be_opaque=False, still=False,
        )  # fmt: skip
        with self._paths.temporary_directory(prefix="motion-graphic-") as tmp:
            output = tmp / f"{_safe(rid)}.mov"
            rendered = self._backend.render(
                composed, output=output, fps=fps, settings=profile.render,
                cancellation=ctx.cancellation,
            )  # fmt: skip
            if isinstance(rendered, Err):
                return None, (
                    issue("render.failed", Severity.ERROR, rendered.error.user_message, rid),
                )
            probed = self._probe.probe(output, samples, ctx.cancellation)
            if isinstance(probed, Err):
                return None, (
                    issue("render.probe_failed", Severity.ERROR, probed.error.user_message, rid),
                )
            probe = probed.value
            problems = render_qc(probe, want, rid)
            if blocking(problems):
                return None, problems
            metadata: dict[str, JsonValue] = {
                "request_id": rid,
                "kind": "caption_batch",
                "renderer": name,
                "renderer_version": version,
                "composition_digest": composed.digest,
                "style_fingerprint": facts.fingerprint,
                "quality": RenderQuality.FINAL.value,
                "frames": probe.frames,
                "fps": str(fps),
                "pix_fmt": probe.pix_fmt,
                "codec": f"{probe.codec} {probe.profile}".strip(),
                "alpha_min": min(s.alpha_min for s in probe.alpha),
                "alpha_max": max(s.alpha_max for s in probe.alpha),
                "opaque_fraction": round(max(s.opaque for s in probe.alpha), 4),
                "batch_start_frames": config["batch"],
            }
            registered = self._library.register_derived(
                anchor.id, output, operation=RENDER_OPERATION, config=config,
                version=RENDER_VERSION, display_name=f"{rid} (caption batch)", metadata=metadata,
                role=AssetRole.DERIVED,
            )  # fmt: skip
        if isinstance(registered, Err):
            return None, (
                issue("library.register", Severity.ERROR, registered.error.user_message, rid),
            )
        self._library.add_tags(registered.value.asset.id, (RENDER_TAG,))
        record = _record_from(
            registered.value.asset, created=registered.value.created, fingerprint=fingerprint
        )
        return record, problems

    def _render(
        self,
        anchor: MediaAssetDto,
        b: Built,
        facts: StyleFacts,
        fonts: list[FontAsset],
        images: dict[str, MediaAssetDto],
        quality: RenderQuality,
        profile: MotionGraphicsProfile,
        ctx: JobContext,
    ) -> tuple[RenderRecord | None, tuple[QcIssue, ...]]:
        composition, animation, frames = b.parts()
        rid = b.request.request_id
        used = {e.image_asset_id for e in composition.elements if e.image_asset_id}
        image_assets: list[ImageAsset] = []
        for asset_id in sorted(used):
            path = self._library.local_path(asset_id)
            if isinstance(path, Err) or asset_id not in images:
                return None, (
                    issue(
                        "image.unreadable", Severity.ERROR, f"image {asset_id} is not readable", rid
                    ),
                )
            image_assets.append(
                ImageAsset(asset_id=asset_id, path=path.value, checksum=images[asset_id].checksum)
            )
        emoji = {e.text.emoji_face for e in composition.elements if e.text and e.text.emoji_face}
        if facts.emoji_face not in emoji:  # the emoji face is copied only where it is drawn
            fonts = [f for f in fonts if f.key != facts.emoji_face]
        still = quality is RenderQuality.PREVIEW
        scale = profile.render.preview_scale if still else 1.0
        still_frame = _hold_frame(b) if still else None
        samples = () if still else changing_frames(animation, frames.frames, frames.fps)
        if len(samples) * 4 > frames.frames * 3:  # under a quarter repeats: draw every frame
            samples = ()
        scene = Scene(
            composition=composition,
            animation=resample(animation, samples, frames.frames, frames.fps)
            if samples
            else animation,
            fps=frames.fps,
            frames=frames.frames,
            easings=facts.easings,
            fonts=tuple(fonts),
            images=tuple(image_assets),
            still_frame=still_frame,
            scale=scale,
            samples=samples,
        )
        composed = self._backend.compose(scene)
        name, version = self._backend.identity()
        config: dict[str, JsonValue] = {
            "composition": composed.digest,
            "fps": str(frames.fps),
            "frames": frames.frames,
            "quality": quality.value,
            "renderer": [name, version],
            "style": facts.fingerprint,
            "size": [composed.width, composed.height],
            "still_frame": still_frame,
            "software_gl": profile.render.software_gl,
        }
        fingerprint = self._library.fingerprint(anchor.id, RENDER_OPERATION, config, RENDER_VERSION)
        if isinstance(fingerprint, Err):
            return None, (
                issue("library.fingerprint", Severity.ERROR, fingerprint.error.user_message, rid),
            )
        existing = self._library.find_derived_asset(anchor.id, fingerprint.value)
        if existing is not None:
            record = _record_from(existing, created=False, fingerprint=fingerprint.value)
            if record is not None:
                _log.info("Graphic reused", request_id=rid, asset_id=existing.id)
                return record, ()
        content_tag = _content_tag(config)
        shared = self._shared(anchor, b, content_tag, config, fingerprint.value, quality)
        if shared is not None:
            return shared, ()
        want = RenderExpectation(
            width=composed.width,
            height=composed.height,
            fps=frames.fps,
            frames=1 if still else frames.frames,
            may_be_opaque=b.request.kind
            in (
                GraphicKind.END_SCREEN,
                GraphicKind.MEMBERSHIP_PLUG,
                GraphicKind.TRANSITION_OVERLAY,
            ),
            still=still,
        )
        with self._paths.temporary_directory(prefix="motion-graphic-") as tmp:
            output = tmp / f"{_safe(rid)}.{'png' if still else 'mov'}"
            rendered = self._backend.render(
                composed,
                output=output,
                fps=frames.fps,
                settings=profile.render,
                cancellation=ctx.cancellation,
            )
            if isinstance(rendered, Err):
                return None, (
                    issue(
                        "render.failed",
                        Severity.ERROR,
                        rendered.error.user_message,
                        rid,
                        fix="see the render log",
                    ),
                )
            samples = (0,) if still else _samples(b, profile.render.alpha_samples)
            facts_result = self._probe.probe(output, samples, ctx.cancellation)
            if isinstance(facts_result, Err):
                return None, (
                    issue(
                        "render.probe_failed", Severity.ERROR, facts_result.error.user_message, rid
                    ),
                )
            probe = facts_result.value
            problems = render_qc(probe, want, rid)
            if blocking(problems):
                return None, problems
            alpha_min = min(s.alpha_min for s in probe.alpha)
            alpha_max = max(s.alpha_max for s in probe.alpha)
            opaque = round(max(s.opaque for s in probe.alpha), 4)
            metadata: dict[str, JsonValue] = {
                "request_id": rid,
                "kind": b.request.kind.value,
                "renderer": name,
                "renderer_version": version,
                "composition_digest": composed.digest,
                "style_fingerprint": facts.fingerprint,
                "quality": quality.value,
                "frames": probe.frames,
                "fps": str(frames.fps),
                "pix_fmt": probe.pix_fmt,
                "codec": f"{probe.codec} {probe.profile}".strip(),
                "alpha_min": alpha_min,
                "alpha_max": alpha_max,
                "opaque_fraction": opaque,
                "item_fingerprint": b.fingerprint,
                "drawn_frames": len(samples) or probe.frames,
            }
            registered = self._library.register_derived(
                anchor.id,
                output,
                operation=RENDER_OPERATION,
                config=config,
                version=RENDER_VERSION,
                display_name=f"{rid} ({b.request.kind.value})",
                metadata=metadata,
                role=AssetRole.PREVIEW if still else AssetRole.DERIVED,
            )
        if isinstance(registered, Err):
            return None, (
                issue("library.register", Severity.ERROR, registered.error.user_message, rid),
            )
        asset = registered.value.asset
        self._library.add_tags(asset.id, (RENDER_TAG, content_tag))
        record = _record_from(
            asset, created=registered.value.created, fingerprint=fingerprint.value
        )
        if record is None:
            return None, (
                issue("library.metadata", Severity.ERROR, "render metadata missing", rid),
            )
        return record, problems

    def _shared(
        self,
        anchor: MediaAssetDto,
        b: Built,
        tag: str,
        config: dict[str, JsonValue],
        fingerprint: str,
        quality: RenderQuality,
    ) -> RenderRecord | None:
        """The identical render made for ANOTHER clip (same content tag): registered for this
        anchor from the stored file (the library stores identical bytes once); nothing drawn.
        The end screen of every clip is rendered once."""
        roles = frozenset(
            {AssetRole.PREVIEW if quality is RenderQuality.PREVIEW else AssetRole.DERIVED}
        )
        found = self._library.search(MediaQuery(tags=frozenset({tag}), roles=roles, page_size=1))
        if not found.items:
            return None
        source = found.items[0]
        path = self._library.local_path(source.id)
        if isinstance(path, Err):
            return None
        rid = b.request.request_id
        metadata = {**source.metadata, "request_id": rid, "shared_from": source.id}
        registered = self._library.register_derived(
            anchor.id, path.value, operation=RENDER_OPERATION, config=config,
            version=RENDER_VERSION, display_name=f"{rid} ({b.request.kind.value})",
            metadata=metadata, role=source.role,
        )  # fmt: skip
        if isinstance(registered, Err):
            return None
        self._library.add_tags(registered.value.asset.id, (RENDER_TAG, tag))
        _log.info("Graphic shared", request_id=rid, source=source.id)
        return _record_from(registered.value.asset, created=False, fingerprint=fingerprint)


def _f(r: Rational) -> Fraction:
    return Fraction(r.numerator, r.denominator)


def _content_tag(config: dict[str, JsonValue]) -> str:
    """Library tag of a render's PIXELS: the composition digest (HTML, fonts, images, GSAP),
    fps, frames, size, quality and renderer; not the anchor and not the style document (each
    clip resolves its own, the drawn result is the composition). The same graphic of another
    clip finds it."""
    return f"mgc:{digest({k: v for k, v in config.items() if k != 'style'})[:40]}"


def _batchable(b: Built, quality: RenderQuality, profile: MotionGraphicsProfile) -> bool:
    return (
        b.request.kind is GraphicKind.CAPTION
        and quality is RenderQuality.FINAL
        and profile.captions.batch_seconds > 0
        and b.frames is not None
        and b.frames.timeline_start is not None
    )


def _batches(
    items: list[tuple[int, Built, tuple[QcIssue, ...]]], seconds: float
) -> list[list[tuple[int, Built, tuple[QcIssue, ...]]]]:
    """Consecutive caption groups whose first-to-last span stays within ``seconds``."""
    out: list[list[tuple[int, Built, tuple[QcIssue, ...]]]] = []
    for item in sorted(items, key=lambda t: t[1].parts()[2].start_frame):
        span = item[1].parts()[2]
        limit = Fraction(str(seconds)) * Fraction(span.fps.numerator, span.fps.denominator)
        if out and span.end_frame - out[-1][0][1].parts()[2].start_frame <= limit:
            out[-1].append(item)
        else:
            out.append([item])
    return out


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)[:80] or "graphic"


def _hold_frame(b: Built) -> int:
    """The middle of the hold (fully entered, not yet leaving): what a still preview shows."""
    _, animation, frames = b.parts()
    m = animation.motion
    rate = Fraction(frames.fps.numerator, frames.fps.denominator)
    peak = max(
        (_f(e.local_at) for e in animation.events if e.kind is EventKind.ENTER_PEAK),
        default=Fraction(0),
    )
    leave = _f(animation.duration) - _f(m.exit_seconds)
    middle = (peak + max(leave, peak)) / 2
    if b.request.kind is GraphicKind.CAPTION and b.request.content.words:
        last = b.request.content.words[-1]
        middle = _f(last.local_start)
    return min(max(round(middle * rate), 0), frames.frames - 1)


def _samples(b: Built, count: int) -> tuple[int, ...]:
    """Alpha check frames spread over the hold (entry frames may be transparent on purpose)."""
    middle = _hold_frame(b)
    last = b.parts()[2].frames - 1
    picks = {middle}
    for k in range(1, count):
        picks.add(min(last, max(0, middle + (k - count // 2) * max(1, last // (count + 1)))))
    return tuple(sorted(picks))


def _record_from(asset: MediaAssetDto, *, created: bool, fingerprint: str) -> RenderRecord | None:
    m = asset.metadata
    try:
        return RenderRecord(
            asset_id=asset.id,
            created=created,
            quality=RenderQuality(str(m["quality"])),
            renderer=str(m["renderer"]),
            renderer_version=str(m["renderer_version"]),
            composition_digest=str(m["composition_digest"]),
            render_fingerprint=fingerprint,
            width=asset.width or 0,
            height=asset.height or 0,
            frames=int(str(m["frames"])),
            pix_fmt=str(m["pix_fmt"]),
            codec=str(m["codec"]),
            alpha_min=int(str(m["alpha_min"])),
            alpha_max=int(str(m["alpha_max"])),
            opaque_fraction=float(str(m["opaque_fraction"])),
        )
    except (KeyError, ValueError):
        return None


class RenderGraphics:
    """``GraphicsRenderer``: render (or reuse) graphics for another module."""

    def __init__(self, library: MediaLibrary, producer: GraphicsProducer) -> None:
        self._library = library
        self._producer = producer

    def render(
        self, command: RenderGraphicsCommand, ctx: JobContext
    ) -> Result[RenderedGraphics, RenderError]:
        if not command.requests:
            return Err(NothingToRender())
        ids = [r.request_id for r in command.requests]
        if len(ids) != len(set(ids)):
            return Err(
                InvalidGraphicRequest(
                    ",".join(sorted({i for i in ids if ids.count(i) > 1})), "duplicate request id"
                )
            )
        unavailable = self._producer.backend.unavailable()
        if unavailable is not None:
            return Err(RendererNotAvailable(unavailable))
        anchor = self._library.get(command.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        source = self._producer.style(command.style)
        if isinstance(source, Err):
            return source
        try:
            profile = command.config.resolve(command.story_format)
        except InvalidGraphicsConfig as exc:
            return Err(exc)
        items = self._producer.produce(
            anchor.value, command.requests, source.value, command.fps, command.quality, profile, ctx
        )
        return Ok(
            RenderedGraphics(
                items=tuple(
                    RenderedGraphic(
                        request_id=g.graphic_id,
                        status=g.status,
                        asset_id=g.render.asset_id if g.render else None,
                        created=g.render.created if g.render else False,
                        frames=g.frames,
                        events=g.animation.events if g.animation else (),
                        qc=g.checks,
                        render=g.render,
                        detail=g.detail,
                    )
                    for g in items
                ),
                style=binding(source.value.style, source.value.ref),
                renderer=self._producer.backend.identity(),
            )
        )
