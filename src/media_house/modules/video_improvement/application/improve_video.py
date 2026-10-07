"""Use case: video asset -> a natural, consistent, colour-managed derived video asset.

    facts -> source profile -> measure -> plan -> predict -> render -> verify -> store

* The source is never modified; the result is a NEW derived asset.
* Identical requests are answered from the library (the processing fingerprint covers the
  source, both profiles, every setting, the LUT, every engine and the encoder that runs).
* If the footage needs nothing, nothing is rendered: the result is the source asset itself
  (``changed=False``). Good footage is left alone.
* The output is measured. A stage whose result is worse than the source is left out and the
  video is rendered again without it; a broken invariant (size, frame rate, duration, frame
  count, audio) discards the output.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import (
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.modules.video_improvement.application.ports import (
    ColorBaker,
    RenderRequest,
    Samples,
    SceneAnalyzer,
    VideoProbe,
    VideoRenderer,
)
from media_house.modules.video_improvement.application.prior_inspection import known_video_facts
from media_house.modules.video_improvement.domain.color import OUTPUT_COLOR, WORKING_SPACE
from media_house.modules.video_improvement.domain.errors import (
    UnsupportedSource,
    VerificationFailed,
    VideoImprovementError,
)
from media_house.modules.video_improvement.domain.measurements import SceneMeasurements
from media_house.modules.video_improvement.domain.planning import (
    ColorPlan,
    ProcessingPlan,
    plan_processing,
    refine_plan,
)
from media_house.modules.video_improvement.domain.profiles import (
    ResolvedConfiguration,
    apply_overrides,
    resolve_processing_profile,
)
from media_house.modules.video_improvement.domain.provenance import (
    OperationRecord,
    ProcessingProvenance,
)
from media_house.modules.video_improvement.domain.source import (
    ResolvedSource,
    VideoFacts,
    resolve_source_profile,
)
from media_house.modules.video_improvement.domain.values import (
    PROCESSING_VERSION,
    VIDEO_OPERATION,
)
from media_house.modules.video_improvement.domain.verification import (
    Check,
    hard_failures,
    regressed_stages,
    verify_output,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type ImproveError = MediaError | VideoImprovementError
_PERCENT = 100
#: Share of the progress bar the render itself covers (it is nearly all of the time).
_RENDER_START, _RENDER_END = 10, 88
#: Three stages exist, so at most this many renders can be needed before nothing is left to drop.
_MAX_RENDERS = 4


@dataclass(frozen=True, slots=True)
class ImproveVideoCommand:
    source_asset_id: str
    #: Name of a source profile (a camera / capture). ``None`` = decide from the metadata.
    source_profile: str | None = None
    #: Name of a processing profile (the look). ``None`` = the source profile's default.
    processing_profile: str | None = None
    #: Dotted-path overrides, e.g. ``{"color.saturation_max": 0.38}`` or
    #: ``{"source.input_color.transfer": "slog3"}``.
    overrides: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VideoImprovementResult:
    #: The improved video, or the SOURCE asset itself when nothing needed doing (``changed`` false).
    asset: MediaAssetDto
    changed: bool
    #: ``False`` when the identical result already existed (or nothing was rendered).
    created: bool
    provenance: ProcessingProvenance
    #: Decisions and checks of this run; ``None``/empty when the result was reused.
    plan: ProcessingPlan | None = None
    checks: tuple[Check, ...] = ()


def _report_render(ctx: JobContext, done: float) -> None:
    percent = _RENDER_START + int(done * (_RENDER_END - _RENDER_START))
    ctx.progress.report(percent, _PERCENT, f"Rendering {int(done * 100)}%")


class ImproveVideo:
    """One video asset in, one improved video asset out; reuse when nothing changed."""

    def __init__(
        self,
        library: MediaLibrary,
        probe: VideoProbe,
        analyzer: SceneAnalyzer,
        baker: ColorBaker,
        renderer: VideoRenderer,
        paths: AppPaths,
        inspections: InspectionCatalog | None = None,
    ) -> None:
        self._library = library
        self._probe = probe
        self._analyzer = analyzer
        self._baker = baker
        self._renderer = renderer
        self._paths = paths
        self._inspections = inspections

    def execute(
        self,
        command: ImproveVideoCommand,
        ctx: JobContext,
    ) -> Result[VideoImprovementResult, ImproveError]:
        started = time.perf_counter()
        source_result = self._library.get(command.source_asset_id)
        if isinstance(source_result, Err):
            return source_result
        source = source_result.value
        if source.media_type is not MediaType.VIDEO:
            return Err(
                ValidationError(
                    f"Asset {source.id} is {source.media_type.value}, not video",
                    field="source_asset_id",
                    user_message="Only video files can be improved.",
                )
            )
        source_path = self._library.local_path(source.id)
        if isinstance(source_path, Err):
            return source_path
        try:
            return self._execute(command, source, source_path.value, ctx, started)
        except VideoImprovementError as exc:
            return Err(exc)

    # ------------------------------------------------------------------------------------------
    def _execute(
        self,
        command: ImproveVideoCommand,
        source: MediaAssetDto,
        source_path: Path,
        ctx: JobContext,
        started: float,
    ) -> Result[VideoImprovementResult, ImproveError]:
        ctx.progress.report(1, _PERCENT, "Reading the source")
        facts = known_video_facts(self._inspections, source.id)
        if facts is None:
            facts = self._probe.probe_video(source_path, ctx.cancellation)
        if facts.color.is_hdr:
            raise UnsupportedSource("HDR video (PQ/HLG) is not graded and would only be degraded")

        resolved = resolve_source_profile(command.source_profile, facts)
        base = resolve_processing_profile(
            command.processing_profile or resolved.profile.default_processing
        )
        config = apply_overrides(resolved.profile, base, command.overrides)
        look_sha = (
            self._baker.look_identity(config.processing.look.lut_path)
            if config.processing.look.lut_path
            else ""
        )
        encoder = self._renderer.encoder_for(config.processing, facts, ctx.cancellation)
        engines = {
            "analyzer": self._analyzer.identity,
            "baker": self._baker.identity,
            "renderer": self._renderer.identity,
            "encoder": encoder,
        }
        fingerprint_config: dict[str, JsonValue] = {
            "source": dict(config.source.to_config()),
            "processing": config.processing.to_config(),
            "look_sha256": look_sha,
            "engines": dict(engines),
        }
        fingerprint = self._library.fingerprint(
            source.id, VIDEO_OPERATION, fingerprint_config, PROCESSING_VERSION
        )
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self._library.find_derived_asset(source.id, fingerprint.value)
        if existing is not None:
            stored = ProcessingProvenance.from_metadata(existing.metadata)
            if stored is not None:
                _log.info("Video improvement reused", asset_id=existing.id, cache_hit=True)
                return Ok(VideoImprovementResult(existing, True, False, stored))
            _log.warning("Stored improvement unusable, recomputing", asset_id=existing.id)

        with self._paths.temporary_directory(prefix="video-") as tmp:
            ctx.raise_if_cancelled()
            ctx.progress.report(3, _PERCENT, "Measuring the footage")
            samples = self._analyzer.sample(
                source_path, facts, config.processing.execution.sample_count, tmp, ctx.cancellation
            )
            before = self._analyzer.measure(samples, config.source.input_color)

            ctx.progress.report(8, _PERCENT, "Planning")
            plan, predicted = self._plan(before, facts, config, samples, look_sha)
            if not plan.stages_applied:
                _log.info("Video needs no improvement", asset_id=source.id)
                provenance = self._provenance(
                    source, resolved, config, plan, facts, before, before, engines, look_sha
                )
                return Ok(VideoImprovementResult(source, False, False, provenance, plan))

            reference = self._color_reference(plan, config, before, samples)
            output = self._render_verified(
                source_path, tmp, facts, config, plan, predicted, before, reference, samples, ctx
            )
            plan, checks, after, destination = output

            ctx.progress.report(96, _PERCENT, "Saving the improved video")
            if not plan.stages_applied:
                provenance = self._provenance(
                    source, resolved, config, plan, facts, before, before, engines, look_sha
                )
                return Ok(VideoImprovementResult(source, False, False, provenance, plan, checks))
            provenance = self._provenance(
                source, resolved, config, plan, facts, before, after, engines, look_sha
            )
            registered = self._library.register_derived(
                source.id,
                destination,
                operation=VIDEO_OPERATION,
                config=fingerprint_config,
                version=PROCESSING_VERSION,
                display_name=f"{source.display_name} (improved)"[:255],
                metadata=provenance.to_metadata()
                | {
                    "processing_type": VIDEO_OPERATION,
                    "source_asset_id": source.id,
                    "source_profile": resolved.profile.name,
                    "processing_profile": config.processing.name,
                    "stages": [s.value for s in plan.stages_applied],
                },
            )
        if isinstance(registered, Err):
            return registered
        _log.info(
            "Video improved",
            asset_id=registered.value.asset.id,
            source_profile=resolved.profile.name,
            origin=resolved.origin.value,
            processing_profile=config.processing.name,
            stages=[s.value for s in plan.stages_applied],
            encoder=engines["encoder"],
            facts=facts.source.value,
            seconds=round(time.perf_counter() - started, 1),
        )
        return Ok(
            VideoImprovementResult(
                registered.value.asset,
                True,
                registered.value.created,
                provenance,
                plan,
                checks,
            )
        )

    # ------------------------------------------------------------------------------------------
    def _plan(
        self,
        before: SceneMeasurements,
        facts: VideoFacts,
        config: ResolvedConfiguration,
        samples: Samples,
        look_sha: str,
    ) -> tuple[ProcessingPlan, SceneMeasurements | None]:
        plan = plan_processing(
            before, facts, config.source, config.processing, look_sha256=look_sha
        )
        if plan.color is None:
            return plan, None
        plan = refine_plan(plan, self._analyzer.predict(samples, plan.color), config.processing)
        predicted = self._analyzer.predict(samples, plan.color) if plan.color else None
        return plan, predicted

    def _color_reference(
        self,
        plan: ProcessingPlan,
        config: ResolvedConfiguration,
        before: SceneMeasurements,
        samples: Samples,
    ) -> SceneMeasurements:
        """What clipping is judged against: the source, or for log/flat footage (whose raw signal
        never clips) the same footage under the plain rendering without any adaptive correction."""
        if plan.color is None or not plan.color.input_color.scene_referred:
            return before
        rendering = config.source.rendering
        neutral = ColorPlan(
            plan.color.input_color, contrast=rendering.contrast, knee=rendering.shoulder_knee
        )
        return self._analyzer.predict(samples, neutral)

    def _render_verified(
        self,
        source_path: Path,
        tmp: Path,
        facts: VideoFacts,
        config: ResolvedConfiguration,
        plan: ProcessingPlan,
        predicted: SceneMeasurements | None,
        before: SceneMeasurements,
        reference: SceneMeasurements,
        samples: Samples,
        ctx: JobContext,
    ) -> tuple[ProcessingPlan, tuple[Check, ...], SceneMeasurements, Path]:
        """Render, measure the real output and leave out any stage that made things worse."""
        destination = tmp / f"improved{config.processing.output.suffix}"
        for attempt in range(1, _MAX_RENDERS + 1):
            ctx.raise_if_cancelled()
            ctx.progress.report(10, _PERCENT, "Rendering")
            lut_file = None
            if plan.color is not None:
                lut_file = tmp / "grade.cube"
                self._baker.bake(plan.color, config.processing.execution.lut_size, lut_file)
            self._renderer.render(
                RenderRequest(source_path, destination, facts, plan, config.processing, lut_file),
                ctx.cancellation,
                lambda done: _report_render(ctx, done),
            )

            ctx.progress.report(90, _PERCENT, "Verifying the result")
            output_facts = self._probe.probe_video(destination, ctx.cancellation)
            work = tmp / f"verify{attempt}"
            work.mkdir()
            output_samples = self._analyzer.sample(
                destination, output_facts, config.processing.execution.sample_count, work,
                ctx.cancellation,
            )  # fmt: skip
            color = OUTPUT_COLOR if plan.color is not None else config.source.input_color
            after = self._analyzer.measure(output_samples, color)
            checks = verify_output(
                source=facts,
                output=output_facts,
                source_frames=facts.frame_count,
                output_frames=output_facts.frame_count,
                before=before,
                predicted=predicted,
                after=after,
                plan=plan,
                profile=config.processing,
                color_reference=reference,
            )
            if failures := hard_failures(checks):
                raise VerificationFailed("; ".join(f"{c.name}: {c.detail}" for c in failures))
            regressed = regressed_stages(checks)
            if not regressed:
                return plan, checks, after, destination
            for stage in regressed:
                reason = "; ".join(
                    f"{c.name}: {c.detail}" for c in checks if c.stage is stage and not c.passed
                )
                _log.warning("Stage made the video worse, leaving it out", stage=stage.value)
                plan = plan.bypass(stage, f"measured worse than the source ({reason})")
            if not plan.stages_applied:
                return plan, checks, before, destination
            if plan.color is None:
                predicted = None
        raise VerificationFailed("the result kept failing its quality checks")

    @staticmethod
    def _provenance(
        source: MediaAssetDto,
        resolved: ResolvedSource,
        config: ResolvedConfiguration,
        plan: ProcessingPlan,
        facts: VideoFacts,
        before: SceneMeasurements,
        after: SceneMeasurements,
        engines: Mapping[str, str],
        look_sha: str,
    ) -> ProcessingProvenance:
        grading = plan.color is not None
        return ProcessingProvenance(
            source_asset_id=source.id,
            source_profile=resolved.profile.name,
            source_profile_version=resolved.profile.version,
            source_profile_origin=resolved.origin.value,
            source_profile_evidence=resolved.evidence,
            processing_profile=config.processing.name,
            input_color_space=str(config.source.input_color),
            working_color_space=WORKING_SPACE if grading else "none",
            output_color_space=str(OUTPUT_COLOR) if grading else str(config.source.input_color),
            operations=tuple(
                OperationRecord(o.stage, o.name, o.status, o.reason, o.parameters, o.measured)
                for o in plan.operations
            ),
            engines=dict(engines),
            processing_version=PROCESSING_VERSION,
            facts_source=facts.source.value,
            before=before.summary(),
            after=after.summary(),
            warnings=plan.warnings,
            look_sha256=look_sha,
        )
