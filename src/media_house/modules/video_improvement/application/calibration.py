"""Calibration: turn representative footage into explicit, versioned configuration values.

A user provides reference clips (a studio recording they like, typical outdoor footage, ...).
The result is a plain ``overrides`` mapping (dotted paths, exactly what ``ImproveVideoCommand``
accepts) plus the measurements it came from. Nothing about runtime processing depends on this:
the mapping is data that the caller stores and passes in, so the same configuration always
gives the same output. No model, service or prompt is involved.
"""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import (
    JsonValue,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.modules.video_improvement.application.ports import SceneAnalyzer, VideoProbe
from media_house.modules.video_improvement.application.prior_inspection import known_video_facts
from media_house.modules.video_improvement.domain.errors import VideoImprovementError
from media_house.modules.video_improvement.domain.measurements import SceneMeasurements
from media_house.modules.video_improvement.domain.profiles import (
    apply_overrides,
    resolve_processing_profile,
)
from media_house.modules.video_improvement.domain.source import resolve_source_profile
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths

#: Clean references define "clean": their noise, with some tolerance, is where denoising starts.
_NOISE_TOLERANCE = 1.5
_FULL_STRENGTH_FACTOR = 4.0
_SOFT_FRACTION = 0.8
_BAND_BELOW, _BAND_ABOVE = 0.05, 0.06
_RANGE_FRACTION = 0.9
_MIN_SOFT_BELOW = 0.005


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    #: Dotted-path overrides to pass to ``ImproveVideoCommand.overrides``.
    overrides: Mapping[str, JsonValue]
    #: What each reference measured (by asset id), for review.
    measurements: Mapping[str, SceneMeasurements]


def derive_overrides(references: Sequence[SceneMeasurements]) -> dict[str, JsonValue]:
    """The settings under which these references would be left alone (pure and deterministic)."""
    median = statistics.median
    saturation = median(m.mean_saturation for m in references)
    noise = max(m.noise_sigma for m in references) * _NOISE_TOLERANCE
    trigger = min(max(noise, 0.001), 0.04)
    return {
        "color.target_median": round(
            min(0.5, max(0.02, median(m.linear_p50 for m in references))), 4
        ),
        "color.saturation_min": round(max(0.02, saturation - _BAND_BELOW), 4),
        "color.saturation_max": round(min(1.0, saturation + _BAND_ABOVE), 4),
        "color.min_tonal_range": round(
            min(1.0, max(0.2, median(m.tonal_range for m in references) * _RANGE_FRACTION)), 4
        ),
        "denoise.trigger_sigma": round(trigger, 5),
        "denoise.full_strength_sigma": round(min(0.2, trigger * _FULL_STRENGTH_FACTOR), 5),
        "sharpen.soft_below": round(
            max(_MIN_SOFT_BELOW, median(m.sharpness for m in references) * _SOFT_FRACTION), 5
        ),
    }


class CalibrateProfile:
    """Measure reference clips and derive the overrides that describe their look."""

    def __init__(
        self,
        library: MediaLibrary,
        probe: VideoProbe,
        analyzer: SceneAnalyzer,
        paths: AppPaths,
        inspections: InspectionCatalog | None = None,
    ) -> None:
        self._library = library
        self._probe = probe
        self._analyzer = analyzer
        self._paths = paths
        self._inspections = inspections

    def execute(
        self,
        reference_asset_ids: Sequence[str],
        ctx: JobContext,
        *,
        source_profile: str | None = None,
    ) -> Result[CalibrationResult, MediaError | VideoImprovementError]:
        if not reference_asset_ids:
            return Err(
                ValidationError(
                    "No reference footage given",
                    field="reference_asset_ids",
                    user_message="Choose at least one reference clip.",
                )
            )
        measured: dict[str, SceneMeasurements] = {}
        for asset_id in reference_asset_ids:
            ctx.raise_if_cancelled()
            asset = self._library.get(asset_id)
            if isinstance(asset, Err):
                return asset
            if asset.value.media_type is not MediaType.VIDEO:
                return Err(
                    ValidationError(
                        f"Asset {asset_id} is not video",
                        field="reference_asset_ids",
                        user_message="Only video clips can be used as references.",
                    )
                )
            path = self._library.local_path(asset_id)
            if isinstance(path, Err):
                return path
            try:
                facts = known_video_facts(self._inspections, asset_id) or self._probe.probe_video(
                    path.value, ctx.cancellation
                )
                resolved = resolve_source_profile(source_profile, facts)
                config = apply_overrides(
                    resolved.profile,
                    resolve_processing_profile(resolved.profile.default_processing),
                    {},
                )
                with self._paths.temporary_directory(prefix="calibrate-") as tmp:
                    samples = self._analyzer.sample(
                        path.value, facts, config.processing.execution.sample_count, tmp,
                        ctx.cancellation,
                    )  # fmt: skip
                    measured[asset_id] = self._analyzer.measure(
                        samples, resolved.profile.input_color
                    )
            except VideoImprovementError as exc:
                return Err(exc)
        return Ok(CalibrationResult(derive_overrides(list(measured.values())), measured))
