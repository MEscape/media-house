"""Use case: produce (or reuse) the standardised transparent variant of a library image."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from media_house.modules.image_adjustment.application.ports import ImageRenderer, RenderReport
from media_house.modules.image_adjustment.domain.errors import AdjustmentFailed
from media_house.modules.image_adjustment.domain.values import (
    ADJUSTMENT_OPERATION,
    ADJUSTMENT_VERSION,
    AdjustmentSettings,
)
from media_house.modules.media_library.application.contracts import (
    DerivedAssetResultDto,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type AdjustError = MediaError | AdjustmentFailed


@dataclass(frozen=True, slots=True)
class AdjustImageCommand:
    """Adjust ``source_asset_id`` as described by ``settings``.

    The result joins the source's media groups (unless ``inherit_source_groups`` is off) and
    every group in ``group_ids``, so it shows up next to the original in the library.
    """

    source_asset_id: str
    settings: AdjustmentSettings = field(default_factory=AdjustmentSettings)
    group_ids: Sequence[str] = ()
    inherit_source_groups: bool = True


class AdjustImage:
    """Process once, persist once, reuse everywhere.

    Identity is the library's processing fingerprint (source content + operation + version +
    canonical settings). A hit returns the stored asset without touching the renderer.
    """

    def __init__(self, library: MediaLibrary, renderer: ImageRenderer, paths: AppPaths) -> None:
        self._library = library
        self._renderer = renderer
        self._paths = paths

    def execute(
        self,
        command: AdjustImageCommand,
        ctx: JobContext,
    ) -> Result[DerivedAssetResultDto, AdjustError]:
        source_result = self._library.get(command.source_asset_id)
        if isinstance(source_result, Err):
            return source_result
        source = source_result.value
        if source.media_type is not MediaType.IMAGE:
            return Err(
                ValidationError(
                    f"Asset {source.id} is {source.media_type.value}, not an image",
                    field="source_asset_id",
                    user_message="Only images can be adjusted.",
                ),
            )

        config = command.settings.to_config()
        fingerprint = self._library.fingerprint(
            source.id,
            ADJUSTMENT_OPERATION,
            config,
            ADJUSTMENT_VERSION,
        )
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self._library.find_derived_asset(source.id, fingerprint.value)
        if existing is not None:
            _log.info("Adjusted image reused", asset_id=existing.id)
            return self._place_in_groups(
                DerivedAssetResultDto(existing, created=False), source, command
            )

        ctx.raise_if_cancelled()
        stored = self._render_and_store(command, source, config, ctx)
        if isinstance(stored, Err):
            return stored
        return self._place_in_groups(stored.value, source, command)

    def _render_and_store(
        self,
        command: AdjustImageCommand,
        source: MediaAssetDto,
        config: dict[str, JsonValue],
        ctx: JobContext,
    ) -> Result[DerivedAssetResultDto, AdjustError]:
        source_path = self._library.local_path(source.id)
        if isinstance(source_path, Err):
            return source_path
        with self._paths.temporary_directory(prefix="adjust-") as tmp:
            output = tmp / "adjusted.png"
            try:
                report = self._renderer.render(source_path.value, output, command.settings)
            except AdjustmentFailed as exc:
                _log.warning("Image adjustment rejected", reason=exc.code, asset_id=source.id)
                return Err(exc)
            ctx.raise_if_cancelled()
            registered = self._library.register_derived(
                source.id,
                output,
                operation=ADJUSTMENT_OPERATION,
                config=config,
                version=ADJUSTMENT_VERSION,
                display_name=_display_name(source, command.settings),
                metadata=_metadata(source, command.settings, report),
            )
        if isinstance(registered, Ok):
            _log.info(
                "Adjusted image created",
                asset_id=registered.value.asset.id,
                source_asset_id=source.id,
                created=registered.value.created,
            )
        return registered

    def _place_in_groups(
        self,
        result: DerivedAssetResultDto,
        source: MediaAssetDto,
        command: AdjustImageCommand,
    ) -> Result[DerivedAssetResultDto, AdjustError]:
        wanted = dict.fromkeys(
            (*(source.group_ids if command.inherit_source_groups else ()), *command.group_ids),
        )
        missing = [g for g in wanted if g not in result.asset.group_ids]
        for group_id in missing:
            added = self._library.add_to_group(result.asset.id, group_id)
            if isinstance(added, Err):
                return added
        if not missing:
            return Ok(result)
        refreshed = self._library.get(result.asset.id)
        if isinstance(refreshed, Err):
            return refreshed
        return Ok(DerivedAssetResultDto(refreshed.value, result.created))


def _display_name(source: MediaAssetDto, settings: AdjustmentSettings) -> str:
    parts = ["cutout" if settings.background_removal.enabled else "adjusted"]
    if settings.glow.enabled:
        parts.append(f"glow {settings.glow.color.value}")
    parts.append(f"{settings.canvas.width}x{settings.canvas.height}")
    return f"{source.display_name} ({', '.join(parts)})"[:255]


def _metadata(
    source: MediaAssetDto,
    settings: AdjustmentSettings,
    report: RenderReport,
) -> dict[str, JsonValue]:
    """How this asset was made, flat and human readable (the fingerprint holds the identity)."""
    glow = settings.glow
    metadata: dict[str, JsonValue] = {
        "processing_type": ADJUSTMENT_OPERATION,
        "processing_version": ADJUSTMENT_VERSION,
        "source_asset_id": source.id,
        "background_removal": settings.background_removal.enabled,
        "glow_enabled": glow.enabled,
        "canvas_width": settings.canvas.width,
        "canvas_height": settings.canvas.height,
        "padding": settings.canvas.padding,
        "centering": settings.canvas.centering.value,
        "mask_foreground_ratio": round(report.mask.foreground_ratio, 6),
        "subject_box": list(report.subject_box),
        "subject_scale": round(report.scale, 6),
    }
    if settings.background_removal.enabled:
        metadata["background_removal_model"] = settings.background_removal.model
    if glow.enabled:
        metadata |= {
            "glow_color": glow.color.value,
            "glow_opacity": glow.opacity,
            "glow_radius": glow.radius,
            "glow_spread": glow.spread,
        }
    return metadata
