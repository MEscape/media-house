"""Use case: audio/video asset -> improved audio asset (the source is never touched).

    analyze -> decide -> enhance (each stage re-checked) -> master -> verify -> register

* Analysis-driven: every stage needs measured evidence; clean audio is left alone.
* Guarded: after each stage the output is re-measured; a stage that made things worse is
  reverted (BYPASSED) and the previous signal continues.
* Deterministic and cached: the resolved profile, every engine identity and the processing
  version make the fingerprint, so an identical request is answered from the Media Library.
* Provenance: the result carries what was done (and measured) so later modules, such as Audio
  Intelligence, can skip work that is already done instead of assuming it.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from media_house.modules.audio_improvement.application.mastering import Mastering
from media_house.modules.audio_improvement.application.ports import (
    AudioTranscoder,
    QualityAnalyzer,
    StageProcessor,
)
from media_house.modules.audio_improvement.application.prior_inspection import (
    known_audio_facts,
)
from media_house.modules.audio_improvement.domain.errors import (
    AudioImprovementError,
    InvalidProfile,
)
from media_house.modules.audio_improvement.domain.measurements import QualityMeasurements
from media_house.modules.audio_improvement.domain.planning import PlannedStage, plan_processing
from media_house.modules.audio_improvement.domain.profiles import DEFAULT_PROFILE, resolve_profile
from media_house.modules.audio_improvement.domain.provenance import (
    ProcessingProvenance,
    StageRecord,
)
from media_house.modules.audio_improvement.domain.settings import AudioProfile
from media_house.modules.audio_improvement.domain.values import (
    ENHANCEMENT_ORDER,
    IMPROVEMENT_OPERATION,
    METADATA_KEY,
    PROCESSING_VERSION,
    JsonValue,
    ProcessingStage,
    StageStatus,
)
from media_house.modules.audio_improvement.domain.verification import (
    Check,
    stage_regression,
    verify_output,
)
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    ConfigurationError,
    Err,
    InvariantViolation,
    Ok,
    Result,
    ValidationError,
)
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type ImproveError = MediaError | AudioImprovementError

_DISPLAY_NAME_MAX = 255
_FIXED_STEPS = 4  # prepare, measure, master, save


@dataclass(frozen=True, slots=True)
class ImproveAudioCommand:
    source_asset_id: str
    #: A built-in profile name (see ``profile_names``) or ``custom``.
    profile: str = DEFAULT_PROFILE
    #: Dotted-path overrides of single settings, e.g. ``{"mastering.target_lufs": -15.0}``.
    overrides: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImprovementResult:
    """The improved asset with everything known about how it came to be."""

    source_asset: MediaAssetDto
    #: The improved audio: a derived asset of the source, the source stays untouched.
    asset: MediaAssetDto
    profile: AudioProfile
    provenance: ProcessingProvenance
    analysis_before: QualityMeasurements
    analysis_after: QualityMeasurements
    checks: tuple[Check, ...]
    warnings: tuple[str, ...]
    #: ``False`` when the identical result already existed (nothing was processed).
    created: bool


class ImproveAudio:
    """One audio asset in, one improved audio asset out; reuse when nothing changed."""

    def __init__(
        self,
        library: MediaLibrary,
        transcoder: AudioTranscoder,
        analyzer: QualityAnalyzer,
        engines: Sequence[StageProcessor],
        paths: AppPaths,
        inspections: InspectionCatalog | None = None,
    ) -> None:
        self._library = library
        self._transcoder = transcoder
        self._analyzer = analyzer
        self._engines = {engine.stage: engine for engine in engines}
        self._paths = paths
        self._inspections = inspections
        if ProcessingStage.MASTERING not in self._engines:
            raise ConfigurationError("Audio improvement needs a mastering engine")

    def execute(
        self,
        command: ImproveAudioCommand,
        ctx: JobContext,
    ) -> Result[ImprovementResult, ImproveError]:
        started = time.perf_counter()
        source_result = self._library.get(command.source_asset_id)
        if isinstance(source_result, Err):
            return source_result
        source = source_result.value
        if source.media_type not in {MediaType.AUDIO, MediaType.VIDEO}:
            return Err(
                ValidationError(
                    f"Asset {source.id} is {source.media_type.value}, not audio or video",
                    field="source_asset_id",
                    user_message="Only audio and video files can be improved.",
                )
            )
        try:
            profile = resolve_profile(command.profile, command.overrides)
        except InvalidProfile as exc:
            return Err(ValidationError(str(exc), field="profile", user_message=exc.user_message))

        config = self._fingerprint_config(profile)
        fingerprint = self._library.fingerprint(
            source.id, IMPROVEMENT_OPERATION, config, PROCESSING_VERSION
        )
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self._library.find_derived_asset(source.id, fingerprint.value)
        if existing is not None:
            reused = _restore(source, existing, profile)
            if reused is not None:
                _log.info("Audio improvement reused", asset_id=existing.id, cache_hit=True)
                return Ok(reused)
            _log.warning("Stored improvement unusable, recomputing", asset_id=existing.id)

        source_path = self._library.local_path(source.id)
        if isinstance(source_path, Err):
            return source_path
        try:
            return self._improve(source, source_path.value, profile, config, started, ctx)
        except AudioImprovementError as exc:
            return Err(exc)

    # ------------------------------------------------------------------------------------------
    def _improve(
        self,
        source: MediaAssetDto,
        source_path: Path,
        profile: AudioProfile,
        config: dict[str, JsonValue],
        started: float,
        ctx: JobContext,
    ) -> Result[ImprovementResult, ImproveError]:
        with self._paths.temporary_directory(prefix="improve-") as tmp:
            ctx.raise_if_cancelled()
            ctx.progress.report(1, _FIXED_STEPS, "Preparing audio")
            working = tmp / "working.wav"
            known = known_audio_facts(self._inspections, source.id)
            decoded = self._transcoder.decode(source_path, working, ctx.cancellation, known)

            ctx.progress.report(2, _FIXED_STEPS, "Measuring quality")
            before = self._analyzer.analyze(working, ctx.cancellation)
            plan = plan_processing(before, profile)

            ctx.progress.report(3, _FIXED_STEPS, f"Enhancing ({len(plan.planned)} stages)")
            current, measured = working, before
            records: list[StageRecord] = []
            for planned in plan.stages:
                ctx.raise_if_cancelled()
                if not planned.apply:
                    records.append(StageRecord(planned.stage, StageStatus.SKIPPED, planned.reason))
                    continue
                current, measured, record = self._run_stage(
                    planned, current, measured, profile, tmp, ctx
                )
                records.append(record)

            ctx.progress.report(4, _FIXED_STEPS, "Mastering")
            mastered = Mastering(self._engines[ProcessingStage.MASTERING], self._analyzer).master(
                current, tmp, measured, profile.mastering, ctx.cancellation
            )
            records.append(mastered.record)

            delivery = tmp / "improved.wav"
            self._transcoder.encode(mastered.path, delivery, ctx.cancellation)
            after = self._analyzer.analyze(delivery, ctx.cancellation)
            checks = verify_output(before, after, profile)
            warnings = (
                *plan.warnings,
                *(f"{c.name}: {c.detail}" for c in checks if not c.passed),
            )
            provenance = ProcessingProvenance(
                profile=profile.name,
                processing_version=PROCESSING_VERSION,
                sample_rate=after.sample_rate,
                channels=after.channels,
                stages=tuple(records),
                output_integrated_lufs=after.integrated_lufs,
                output_true_peak_dbtp=after.true_peak_dbtp,
                leading_pad_seconds=decoded.leading_pad_seconds,
            )
            registered = self._library.register_derived(
                source.id,
                delivery,
                operation=IMPROVEMENT_OPERATION,
                config=config,
                version=PROCESSING_VERSION,
                display_name=f"{source.display_name} (improved, {profile.name})"[
                    :_DISPLAY_NAME_MAX
                ],
                metadata=_metadata(source, profile, provenance, before, after, checks, warnings),
            )
        if isinstance(registered, Err):
            return registered
        _log.info(
            "Audio improved",
            asset_id=registered.value.asset.id,
            profile=profile.name,
            applied=[r.stage.value for r in records if r.status is StageStatus.APPLIED],
            bypassed=[r.stage.value for r in records if r.status is StageStatus.BYPASSED],
            lufs=after.integrated_lufs,
            processing_seconds=round(time.perf_counter() - started, 1),
        )
        return Ok(
            ImprovementResult(
                source,
                registered.value.asset,
                profile,
                provenance,
                before,
                after,
                checks,
                warnings,
                created=registered.value.created,
            )
        )

    def _run_stage(
        self,
        planned: PlannedStage,
        current: Path,
        measured: QualityMeasurements,
        profile: AudioProfile,
        workdir: Path,
        ctx: JobContext,
    ) -> tuple[Path, QualityMeasurements, StageRecord]:
        """Run one planned stage and keep its output only if the re-measurement approves."""
        engine = self._engines.get(planned.stage)
        if engine is None:
            raise ConfigurationError(f"No engine registered for stage {planned.stage.value!r}")
        output = workdir / f"{planned.stage.value}.wav"
        engine.process(current, output, planned.params, ctx.cancellation)
        after = self._analyzer.analyze(output, ctx.cancellation)
        regression = stage_regression(planned.stage, measured, after, profile, planned.params)
        identity = engine.identity
        if regression is not None:
            _log.warning("Stage reverted", stage=planned.stage.value, reason=regression)
            record = StageRecord(
                planned.stage,
                StageStatus.BYPASSED,
                f"{planned.reason}; reverted: {regression}",
                identity.name,
                identity.version,
                planned.params,
            )
            return current, measured, record
        record = StageRecord(
            planned.stage,
            StageStatus.APPLIED,
            planned.reason,
            identity.name,
            identity.version,
            planned.params,
        )
        return output, after, record

    def _fingerprint_config(self, profile: AudioProfile) -> dict[str, JsonValue]:
        """Everything that shapes the result: profile, every engine, the processing version."""
        engines: dict[str, JsonValue] = {
            stage.value: str(engine.identity) for stage, engine in sorted(self._engines.items())
        }
        engines["transcoder"] = str(self._transcoder.identity)
        engines["analyzer"] = str(self._analyzer.identity)
        return {
            "profile": profile.to_config(),
            "engines": engines,
            "stage_order": [stage.value for stage in ENHANCEMENT_ORDER],
        }


def _metadata(
    source: MediaAssetDto,
    profile: AudioProfile,
    provenance: ProcessingProvenance,
    before: QualityMeasurements,
    after: QualityMeasurements,
    checks: Sequence[Check],
    warnings: Sequence[str],
) -> dict[str, JsonValue]:
    return {
        "processing_type": IMPROVEMENT_OPERATION,
        "source_asset_id": source.id,
        "profile": profile.name,
        METADATA_KEY: provenance.to_json_value(),
        "analysis_before": before.to_json_value(),
        "analysis_after": after.to_json_value(),
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
        "warnings": list(warnings),
    }


def _restore(
    source: MediaAssetDto,
    asset: MediaAssetDto,
    profile: AudioProfile,
) -> ImprovementResult | None:
    """The stored result rebuilt from the asset's metadata, or ``None`` if it is unusable."""
    meta = asset.metadata
    try:
        provenance = ProcessingProvenance.from_metadata(meta)
        before_raw, after_raw = meta["analysis_before"], meta["analysis_after"]
        raw_checks, raw_warnings = meta["checks"], meta["warnings"]
        if (
            provenance is None
            or not isinstance(before_raw, dict)
            or not isinstance(after_raw, dict)
        ):
            return None
        if not isinstance(raw_checks, list) or not isinstance(raw_warnings, list):
            return None
        checks = tuple(
            Check(str(c["name"]), bool(c["passed"]), str(c["detail"]))  # type: ignore[index,call-overload]
            for c in raw_checks
        )
        return ImprovementResult(
            source,
            asset,
            profile,
            provenance,
            QualityMeasurements.from_json_value(before_raw),
            QualityMeasurements.from_json_value(after_raw),
            checks,
            tuple(str(w) for w in raw_warnings),
            created=False,
        )
    except (KeyError, TypeError, ValueError, InvariantViolation):
        return None
