"""Design (or reuse) the A/B/C thumbnails of one brief as derived library pictures.

1. Style: the video's stored channel style (``VisualStyleCatalog``); without one the current
   identity is resolved in memory (nothing is stored, the look is the same).
2. Cut-outs: the reaction and product pictures go through visual asset generation's clean cut-out
   (cached there: a picture is segmented once, whichever thumbnail asks).
3. Per variant: the library fingerprint (brief + input pictures + style + font + renderer) is the
   cache; a hit is returned with its stored checks, never re-rendered. A new picture is rendered,
   checked, and registered as a derived asset of the video (or of the reaction without one).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from media_house.core.application.ports import Clock
from media_house.core.domain import Rational
from media_house.modules.media_library.application.contracts import (
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.modules.thumbnail_design.application.ports import (
    Colours,
    Scene,
    ThumbnailRenderer,
)
from media_house.modules.thumbnail_design.domain.brief import ThumbnailBrief
from media_house.modules.thumbnail_design.domain.errors import (
    InputUnusable,
    StyleUnavailable,
    ThumbnailFailed,
)
from media_house.modules.thumbnail_design.domain.layout import lay_out, unplaced_products
from media_house.modules.thumbnail_design.domain.qc import QcIssue, blocking, judge
from media_house.modules.thumbnail_design.domain.variants import Variant, variants_of
from media_house.modules.visual_asset_generation.application.contracts import (
    AdjustImageCommand,
    AdjustmentFailed,
    AdjustmentSettings,
    Canvas,
    FrameGrabber,
    GrabFrameCommand,
    ImageAdjuster,
)
from media_house.modules.visual_style_engine.application.contracts import (
    CURRENT_IDENTITY_VERSION,
    IDENTITIES,
    FontFiles,
    ResolvedStyle,
    StyleConfig,
    VisualStyleCatalog,
    resolve_style,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, ExternalSystemError, Ok, Result
from media_house.shared.filesystem import AppPaths

OPERATION: Final = "thumbnail_design"
VERSION: Final = 1
#: Cut-out canvases: big enough for a 1920 master (the reaction is drawn ~1330 px wide).
_REACTION_CANVAS: Final = Canvas(width=2048, height=2048, padding=16)
_PRODUCT_CANVAS: Final = Canvas(width=1280, height=1280, padding=16)
#: A video plate gives the frame at this share of its duration (past any intro darkness).
_PLATE_AT: Final = 0.4
_OUTPUT: Final = "1920x1080"

type DesignError = MediaError | ThumbnailFailed | ExternalSystemError


@dataclass(frozen=True, slots=True, kw_only=True)
class DesignThumbnailsCommand:
    brief: ThumbnailBrief
    #: The video the thumbnail belongs to (its style, and the source of the derived pictures).
    anchor_asset_id: str | None = None
    #: A style already in hand (the pipeline's); ``None`` = the stored one, else the identity.
    style: ResolvedStyle | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DesignedVariant:
    name: str
    label: str
    #: ``None`` when an ERROR check refused the picture (nothing was registered).
    asset: MediaAssetDto | None
    created: bool
    issues: tuple[QcIssue, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DesignedThumbnails:
    variants: tuple[DesignedVariant, ...]
    style_fingerprint: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class _Inputs:
    colours: Colours
    font: Path
    font_sha: str
    style_fingerprint: str
    files: dict[str, Path]


class DesignThumbnails:
    def __init__(
        self,
        *,
        library: MediaLibrary,
        paths: AppPaths,
        clock: Clock,
        adjuster: ImageAdjuster,
        frames: FrameGrabber,
        styles: VisualStyleCatalog,
        fonts: FontFiles,
        renderer: ThumbnailRenderer,
    ) -> None:
        self._library = library
        self._paths = paths
        self._clock = clock
        self._adjuster = adjuster
        self._frames = frames
        self._styles = styles
        self._fonts = fonts
        self._renderer = renderer

    def execute(
        self, command: DesignThumbnailsCommand, ctx: JobContext
    ) -> Result[DesignedThumbnails, DesignError]:
        brief = command.brief
        source = self._library.get(command.anchor_asset_id or brief.reaction_asset_id)
        if isinstance(source, Err):
            return source
        if command.style is not None:
            style = command.style
        else:
            stored = self._style(source.value.id)
            if isinstance(stored, Err):
                return stored
            style = stored.value
        try:
            inputs = self._inputs(brief, style, ctx)
        except (ThumbnailFailed, ExternalSystemError) as exc:  # a refused input, a missing model
            return Err(exc)
        if isinstance(inputs, Err):
            return inputs
        designed = []
        variants = variants_of(brief)
        for done, variant in enumerate(variants):
            ctx.raise_if_cancelled()
            ctx.progress.report(done, len(variants), f"Thumbnail {variant.label}")
            result = self._variant(source.value, variant, inputs.value, ctx)
            if isinstance(result, Err):
                return result
            designed.append(result.value)
        ctx.progress.report(len(variants), len(variants), "Thumbnails ready")
        skipped = unplaced_products(brief)
        return Ok(
            DesignedThumbnails(
                variants=tuple(designed),
                style_fingerprint=inputs.value.style_fingerprint,
                warnings=(f"{len(skipped)} product(s) not shown",) if skipped else (),
            )
        )

    # --- inputs --------------------------------------------------------------------------------

    def _style(self, anchor: str) -> Result[ResolvedStyle, DesignError]:
        stored = self._styles.latest(anchor, output=_OUTPUT)
        if isinstance(stored, Err):
            return stored
        if stored.value is not None:
            return Ok(stored.value[0])
        return Ok(
            resolve_style(
                IDENTITIES[CURRENT_IDENTITY_VERSION],
                StyleConfig(),
                created_at=self._clock.now(),
                width=1920,
                height=1080,
                anchor_asset_id=anchor,
            )
        )

    def _inputs(
        self, brief: ThumbnailBrief, style: ResolvedStyle, ctx: JobContext
    ) -> Result[_Inputs, DesignError]:
        face = style.type_style("headline").face
        font = self._fonts.locate(face)
        if isinstance(font, Err):
            raise StyleUnavailable(font.error.user_message)
        try:
            colours = Colours(
                blue=style.hex("thumbnail_blue"),
                blue_light=style.hex("thumbnail_blue_light"),
                highlight=style.hex("highlight"),
                white=style.hex("white"),
                black=style.hex("black"),
                silver_light=style.hex("silver_light"),
                silver_mid=style.hex("silver_mid"),
                silver_dark=style.hex("silver_dark"),
                strike=style.hex("brand_red_neon"),
            )
        except KeyError as exc:
            raise StyleUnavailable(f"the identity has no {exc}") from exc
        files: dict[str, Path] = {}
        wanted: list[tuple[str, str, Canvas | None]] = [
            ("reaction", brief.reaction_asset_id, _REACTION_CANVAS),
            *((f"product:{p}", p, _PRODUCT_CANVAS) for p in brief.products),
        ]
        if brief.device is not None:
            wanted.append(("device_product", brief.device.product_asset_id, _PRODUCT_CANVAS))
            if brief.device.screen_asset_id:
                wanted.append(("device_screen", brief.device.screen_asset_id, None))
        if brief.logo_asset_id:
            wanted.append(("logo", brief.logo_asset_id, None))
        if brief.plate_asset_id:
            wanted.append(("plate", brief.plate_asset_id, None))
        for role, asset_id, canvas in wanted:
            ctx.raise_if_cancelled()
            path = (
                self._cutout(role, asset_id, canvas, ctx)
                if canvas is not None
                else self._picture(role, asset_id, ctx)
            )
            if isinstance(path, Err):
                return path
            files[role] = path.value
        return Ok(
            _Inputs(
                colours=colours,
                font=font.value.path,
                font_sha=font.value.sha256,
                style_fingerprint=style.fingerprint,
                files=files,
            )
        )

    def _image(self, role: str, asset_id: str) -> Result[MediaAssetDto, DesignError]:
        asset = self._library.get(asset_id)
        if isinstance(asset, Err):
            return asset
        if asset.value.media_type is not MediaType.IMAGE:
            return Err(InputUnusable(role.split(":")[0], "it is not a picture"))
        return asset

    def _cutout(
        self, role: str, asset_id: str, canvas: Canvas, ctx: JobContext
    ) -> Result[Path, DesignError]:
        asset = self._image(role, asset_id)
        if isinstance(asset, Err):
            return asset
        cut = self._adjuster.execute(
            AdjustImageCommand(asset_id, settings=AdjustmentSettings(canvas=canvas)), ctx
        )
        if isinstance(cut, Err):
            return _refused(role, cut.error)
        return self._library.local_path(cut.value.asset.id)

    def _picture(self, role: str, asset_id: str, ctx: JobContext) -> Result[Path, DesignError]:
        asset = self._library.get(asset_id)
        if isinstance(asset, Err):
            return asset
        if asset.value.media_type is MediaType.VIDEO and role == "plate":
            seconds = (asset.value.duration_seconds or 0.0) * _PLATE_AT
            grab = self._frames.execute(
                GrabFrameCommand(
                    video_asset_id=asset_id,
                    source_time=Rational(round(seconds * 1000), 1000),
                ),
                ctx,
            )
            if isinstance(grab, Err):
                return _refused(role, grab.error)
            return self._library.local_path(grab.value.asset.id)
        if asset.value.media_type is not MediaType.IMAGE:
            return Err(InputUnusable(role, "it is not a picture"))
        return self._library.local_path(asset_id)

    # --- one variant ---------------------------------------------------------------------------

    def _config(self, variant: Variant, inputs: _Inputs) -> dict[str, JsonValue]:
        return {
            "brief": variant.brief.to_config(),
            "style": inputs.style_fingerprint,
            "font": inputs.font_sha,
            "renderer": dict(self._renderer.identity()),
        }

    def _variant(
        self, source: MediaAssetDto, variant: Variant, inputs: _Inputs, ctx: JobContext
    ) -> Result[DesignedVariant, DesignError]:
        config = self._config(variant, inputs)
        fingerprint = self._library.fingerprint(source.id, OPERATION, config, VERSION)
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self._library.find_derived_asset(source.id, fingerprint.value)
        if existing is not None:
            return Ok(
                DesignedVariant(
                    name=variant.name,
                    label=variant.label,
                    asset=existing,
                    created=False,
                    issues=_stored_issues(existing.metadata),
                )
            )
        brief = variant.brief
        layout = lay_out(brief)
        files = inputs.files
        scene = Scene(
            layout=layout,
            font=inputs.font,
            colours=inputs.colours,
            headline=brief.headline,
            reaction=files["reaction"],
            flip_reaction=brief.flip_reaction,
            products={p.asset_id: files[f"product:{p.asset_id}"] for p in layout.products},
            logo=files.get("logo"),
            device=brief.device,
            device_product=files.get("device_product"),
            device_screen=files.get("device_screen"),
            badge=brief.badge,
            plate=files.get("plate"),
        )
        with self._paths.temporary_directory(prefix=f"{OPERATION}-") as tmp:
            output = tmp / f"thumbnail_{variant.name}.png"
            try:
                report = self._renderer.render(scene, output, ctx.cancellation)
            except ThumbnailFailed as exc:
                return Err(exc)
            issues = judge(
                width=report.width,
                height=report.height,
                boxes=report.boxes,
                headline_px=report.headline_px,
                skipped_products=len(brief.products) - len(layout.products),
            )
            if blocking(issues):
                return Ok(
                    DesignedVariant(
                        name=variant.name,
                        label=variant.label,
                        asset=None,
                        created=False,
                        issues=issues,
                    )
                )
            ctx.raise_if_cancelled()
            registered = self._library.register_derived(
                source.id,
                output,
                operation=OPERATION,
                config=config,
                version=VERSION,
                display_name=f"Thumbnail {variant.label} · {source.display_name}"[:255],
                metadata={
                    "processing_type": OPERATION,
                    "source_asset_id": source.id,
                    "variant": variant.name,
                    "qc": [i.to_json() for i in issues],
                },
            )
        if isinstance(registered, Err):
            return registered
        return Ok(
            DesignedVariant(
                name=variant.name,
                label=variant.label,
                asset=registered.value.asset,
                created=registered.value.created,
                issues=issues,
            )
        )


def _refused(
    role: str, error: MediaError | AdjustmentFailed | ExternalSystemError
) -> Err[DesignError]:
    """A picture visual asset generation refused is an unusable INPUT; anything else passes."""
    if isinstance(error, AdjustmentFailed):
        return Err(InputUnusable(role.split(":")[0], error.user_message))
    return Err(error)


def _stored_issues(metadata: Mapping[str, JsonValue]) -> tuple[QcIssue, ...]:
    stored = metadata.get("qc")
    if not isinstance(stored, list):
        return ()
    return tuple(QcIssue.from_json(i) for i in stored if isinstance(i, dict))
