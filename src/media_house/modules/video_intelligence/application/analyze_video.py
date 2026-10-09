"""Use case: one video asset version -> its ``VideoAnalysis``, reusing everything already known.

Each analyzer keeps its raw signals as its own derived Media Library asset, found by its own
processing fingerprint. The finished result is one more derived asset. So:

* a second identical run finds the result and computes nothing;
* a richer profile computes only the analyzers it does not yet have, from ONE decode;
* a changed threshold re-derives from the stored signals and decodes nothing;
* the original and the improved version of a clip are different assets, so they are measured and
  cached separately, and the result says which one it was measured on;
* an analyzer whose library or model is missing is ``not_available`` (and so is whatever depends
  on it); the rest of the result is complete.

The module measures and describes. It never modifies media and never recommends an action.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.modules.video_intelligence.application.ports import (
    DecodedVideo,
    DecodeRequest,
    FrameSource,
    MeasureRequest,
    SignalAnalyzer,
    Signals,
)
from media_house.modules.video_intelligence.application.resolver import UpstreamResolver
from media_house.modules.video_intelligence.domain.analyzers import ANALYZERS, run_order
from media_house.modules.video_intelligence.domain.derive import DERIVATION_VERSIONS, derive
from media_house.modules.video_intelligence.domain.errors import (
    InvalidAnalysis,
    VideoIntelligenceError,
)
from media_house.modules.video_intelligence.domain.profiles import (
    DEFAULT_PROFILE,
    RUBRIC_VERSION,
    ProcessingProfile,
    RuntimeConfig,
    get_profile,
)
from media_house.modules.video_intelligence.domain.result import (
    AnalyzerReport,
    Provenance,
    VideoAnalysis,
)
from media_house.modules.video_intelligence.domain.sampling import (
    decode_stride,
    plan_rgb,
    plan_samples,
    rgb_stride,
)
from media_house.modules.video_intelligence.domain.serialization import (
    SIGNAL_TYPES,
    analysis_from_json,
    analysis_to_json,
    document_from_json,
    document_to_json,
    signals_kind,
)
from media_house.modules.video_intelligence.domain.signals import ShotSignals
from media_house.modules.video_intelligence.domain.source import ResolvedInputs
from media_house.modules.video_intelligence.domain.validation import validate_analysis
from media_house.modules.video_intelligence.domain.values import (
    PROCESSING_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    AnalyzerId,
    AnalyzerState,
    CacheOutcome,
    DeviceKind,
)
from media_house.modules.video_intelligence.domain.vocabulary import VOCABULARY_VERSION
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import (
    Err,
    MediaHouseError,
    Ok,
    OperationCancelledError,
    Result,
    ValidationError,
)
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type AnalyzeVideoError = MediaError | VideoIntelligenceError

_STEPS = 6
#: A decoded frame count this far (and 1 %) from the declared one is reported as a warning.
_FRAME_COUNT_SLACK = 2


@dataclass(frozen=True, slots=True)
class AnalyzeVideoCommand:
    source_asset_id: str
    #: A profile name (``triage``, ``fast``, ``standard``, ``deep``) or a profile built in code,
    #: for example a named profile with changed thresholds (``dataclasses.replace``).
    profile: str | ProcessingProfile = DEFAULT_PROFILE
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


@dataclass(frozen=True, slots=True)
class VideoAnalysisResult:
    """The analysis plus where it lives in the Media Library."""

    analysis: VideoAnalysis
    #: The stored result document (a derived asset of the analysed video).
    asset: MediaAssetDto
    #: The stored signals of each analyzer that ran or was reused.
    signal_assets: Mapping[AnalyzerId, MediaAssetDto]
    #: ``False`` when the identical result already existed (nothing was computed).
    created: bool


@dataclass(slots=True)
class _Stored:
    signals: Signals
    asset: MediaAssetDto
    spec: DocumentSpec
    reused: bool


class AnalyzeVideo:
    """Resolve upstream facts, reuse stored signals, decode once for the rest, derive, store."""

    def __init__(
        self,
        library: MediaLibrary,
        resolver: UpstreamResolver,
        frames: FrameSource,
        analyzers: Mapping[AnalyzerId, SignalAnalyzer],
        paths: AppPaths,
    ) -> None:
        self._library = library
        self._resolver = resolver
        self._frames = frames
        self._analyzers = analyzers
        self._paths = paths
        self._documents = DerivedDocuments(library, paths)

    def execute(
        self,
        command: AnalyzeVideoCommand,
        ctx: JobContext,
    ) -> Result[VideoAnalysisResult, AnalyzeVideoError]:
        started = time.perf_counter()
        source_result = self._library.get(command.source_asset_id)
        if isinstance(source_result, Err):
            return source_result
        asset = source_result.value
        if asset.media_type is not MediaType.VIDEO:
            return Err(
                ValidationError(
                    f"Asset {asset.id} is {asset.media_type.value}, not video",
                    field="source_asset_id",
                    user_message="Only video files can be analysed.",
                )
            )
        try:
            profile = (
                get_profile(command.profile)
                if isinstance(command.profile, str)
                else command.profile
            )
            ctx.progress.report(1, _STEPS, "Reading the technical facts")
            inputs = self._resolver.resolve(asset, ctx)
            return self._analyse(asset, profile, command.runtime, inputs, ctx, started)
        except VideoIntelligenceError as exc:
            _log.warning("Video analysis refused", reason=exc.code, asset_id=asset.id)
            return Err(exc)

    # --- availability ------------------------------------------------------------------------
    def _availability(
        self, order: tuple[AnalyzerId, ...], runtime: RuntimeConfig
    ) -> dict[AnalyzerId, str]:
        """Why each analyzer that cannot run is unavailable (missing engine, library, model, or a
        required dependency that is unavailable)."""
        reasons: dict[AnalyzerId, str] = {}
        for analyzer in order:
            engine = self._analyzers.get(analyzer)
            if engine is None:
                reasons[analyzer] = "no engine is installed for this analyzer"
                continue
            why = engine.unavailable(runtime.allow_model_download)
            if why is None:
                blocked = next((d for d in ANALYZERS[analyzer].depends_on if d in reasons), None)
                if blocked is not None:
                    why = f"depends on {blocked.value}, which is not available"
            if why is not None:
                reasons[analyzer] = why
        return reasons

    # --- the pipeline ------------------------------------------------------------------------
    def _analyse(
        self,
        asset: MediaAssetDto,
        profile: ProcessingProfile,
        runtime: RuntimeConfig,
        inputs: ResolvedInputs,
        ctx: JobContext,
        started: float,
    ) -> Result[VideoAnalysisResult, AnalyzeVideoError]:
        order = run_order(profile.analyzers)
        unavailable = self._availability(order, runtime)
        if AnalyzerId.SHOTS in unavailable:
            raise VideoIntelligenceError(
                f"the shots analyzer is required and unavailable: {unavailable[AnalyzerId.SHOTS]}"
            )
        runnable = tuple(a for a in order if a not in unavailable)
        engines = self._all_engines(runnable)
        _log.info(
            "Video analysis started",
            asset_id=asset.id,
            profile=f"{profile.name}@{profile.version}",
            analyzers=[a.value for a in runnable],
            unavailable={a.value: why for a, why in unavailable.items()},
            device=runtime.device.value,
            measured_on=inputs.history.measured_on.value,
        )
        fps = inputs.source.frame_rate.value if inputs.source.frame_rate else None
        strides = (decode_stride(fps, profile.measurement), rgb_stride(fps, profile.measurement))
        specs: dict[AnalyzerId, DocumentSpec] = {}
        fingerprints: dict[AnalyzerId, str] = {}
        for analyzer in runnable:
            specs[analyzer] = self._signal_spec(analyzer, profile, inputs, strides, fingerprints)
            fingerprint = self._documents.fingerprint(asset.id, specs[analyzer])
            if isinstance(fingerprint, Err):
                return fingerprint
            fingerprints[analyzer] = fingerprint.value

        result_spec = DocumentSpec(
            RESULT_OPERATION,
            PROCESSING_VERSION,
            _result_config(profile, inputs, fingerprints, engines, tuple(unavailable)),
        )
        result_fingerprint = self._documents.fingerprint(asset.id, result_spec)
        if isinstance(result_fingerprint, Err):
            return result_fingerprint
        cached = self._documents.load(asset.id, result_fingerprint.value, analysis_from_json)
        if cached is not None:
            analysis, stored_asset = cached
            _log.info("Video analysis reused", asset_id=stored_asset.id, cache_hit=True)
            return Ok(
                VideoAnalysisResult(analysis, stored_asset, self._children(stored_asset), False)
            )

        ctx.raise_if_cancelled()
        ctx.progress.report(2, _STEPS, "Looking for stored measurements")
        stored: dict[AnalyzerId, _Stored] = {}
        for analyzer in runnable:
            found = self._load_signals(asset.id, analyzer, fingerprints[analyzer])
            if found is not None:
                _log.info("Signals reused", analyzer=analyzer.value, cache_hit=True)
                stored[analyzer] = _Stored(found[0], found[1], specs[analyzer], reused=True)

        failures: dict[AnalyzerId, str] = {}
        decode_warnings: tuple[str, ...] = ()
        todo = [a for a in runnable if a not in stored]
        if todo:
            outcome = self._measure_missing(
                asset, profile, runtime, inputs, todo, stored, specs, strides, ctx
            )
            if isinstance(outcome, Err):
                return outcome
            failures, decode_warnings = outcome.value

        shots = stored.get(AnalyzerId.SHOTS)
        if shots is None or not isinstance(shots.signals, ShotSignals):
            return Err(InvalidAnalysis(("the shot signals are missing",)))

        ctx.raise_if_cancelled()
        ctx.progress.report(5, _STEPS, "Describing the footage")
        derived = derive(
            shots.signals,
            {a: s.signals for a, s in stored.items()},
            profile,
            inputs.source,
            inputs.history,
        )
        used_device = self._device_used(stored, runtime)
        warnings = [
            *inputs.warnings,
            *decode_warnings,
            *derived.warnings,
            *_frame_count_warnings(shots.signals, inputs),
        ]
        if runtime.device is DeviceKind.GPU and used_device != "gpu":
            warnings.append("gpu requested but no engine could use one; the CPU was used")
        analysis = VideoAnalysis(
            asset_id=asset.id,
            asset_checksum=asset.checksum,
            source=inputs.source,
            history=inputs.history,
            provenance=Provenance(
                schema_version=SCHEMA_VERSION,
                processing_version=PROCESSING_VERSION,
                profile_name=profile.name,
                profile_version=profile.version,
                analyzer_versions={a.value: ANALYZERS[a].version for a in order},
                derivation_versions=dict(DERIVATION_VERSIONS),
                engines=engines,
                device_requested=runtime.device.value,
                device_used=used_device,
                rubric_version=RUBRIC_VERSION,
                vocabulary_version=VOCABULARY_VERSION,
            ),
            inputs_used=inputs.used,
            analyzers=tuple(_report(a, stored.get(a), failures, unavailable) for a in order),
            shots=derived.shots,
            curves=derived.curves,
            warnings=tuple(warnings),
            scenes=derived.scenes,
            tracks=derived.tracks,
            identities=derived.identities,
            retakes=derived.retakes,
            overlays=derived.overlays,
            events=derived.events,
            embeddings=derived.embeddings,
        )
        findings = validate_analysis(analysis)
        if findings:
            _log.error("Analysis rejected its own result", findings=len(findings))
            return Err(InvalidAnalysis(findings))

        ctx.progress.report(6, _STEPS, "Saving the analysis")
        children = {a.value: s.asset.id for a, s in stored.items()}
        # A result missing an analyzer that FAILED is stored under its own key (it names what
        # failed), so a later healthy run never mistakes it for the complete result and retries.
        stored_spec = (
            result_spec
            if not failures
            else DocumentSpec(
                RESULT_OPERATION,
                PROCESSING_VERSION,
                _result_config(profile, inputs, fingerprints, engines, (*unavailable, *failures)),
            )
        )
        saved = self._documents.store(
            asset,
            analysis_to_json(analysis),
            filename="video_analysis.json",
            spec=stored_spec,
            display_name=f"{asset.display_name} (video intelligence)",
            metadata={
                "profile": profile.name,
                "profile_version": profile.version,
                "measured_on": analysis.measured_on.value,
                "signal_asset_ids": dict(children),
                "shot_count": len(analysis.shots),
                "warning_count": len(analysis.warnings),
                "schema_version": SCHEMA_VERSION,
            },
        )
        if isinstance(saved, Err):
            return saved
        _log.info(
            "Video analysis created",
            asset_id=saved.value.id,
            cache_hit=False,
            profile=profile.name,
            shots=len(analysis.shots),
            analyzers_reused=sum(1 for s in stored.values() if s.reused),
            analyzers_computed=sum(1 for s in stored.values() if not s.reused),
            analyzers_failed=len(failures),
            analyzers_unavailable=len(unavailable),
            seconds=round(time.perf_counter() - started, 2),
        )
        return Ok(
            VideoAnalysisResult(
                analysis, saved.value, {a: s.asset for a, s in stored.items()}, created=True
            )
        )

    def _device_used(self, stored: Mapping[AnalyzerId, _Stored], runtime: RuntimeConfig) -> str:
        fresh = [a for a, s in stored.items() if not s.reused]
        used = {self._analyzers[a].device(runtime.device) for a in fresh}
        return "gpu" if "gpu" in used else "cpu"

    # --- measuring what is not stored --------------------------------------------------------
    def _measure_missing(
        self,
        asset: MediaAssetDto,
        profile: ProcessingProfile,
        runtime: RuntimeConfig,
        inputs: ResolvedInputs,
        todo: list[AnalyzerId],
        stored: dict[AnalyzerId, _Stored],
        specs: Mapping[AnalyzerId, DocumentSpec],
        strides: tuple[int, int],
        ctx: JobContext,
    ) -> Result[tuple[dict[AnalyzerId, str], tuple[str, ...]], AnalyzeVideoError]:
        """Decode once, run every missing analyzer on that decode, store each result at once.

        A failing optional analyzer is recorded and the rest continue; the shots analyzer is
        required by everything else, so its failure fails the run.
        """
        path = self._library.local_path(asset.id)
        if isinstance(path, Err):
            return path
        streams = {ANALYZERS[a].stream for a in todo}
        request = DecodeRequest(
            inputs.source,
            profile.measurement,
            strides[0],
            strides[1],
            dense="dense" in streams,
            samples="gray" in streams,
            rgb="rgb" in streams,
        )
        failures: dict[AnalyzerId, str] = {}
        with self._paths.temporary_directory(prefix="video-intelligence-") as work:
            ctx.progress.report(3, _STEPS, "Decoding the video")
            video = self._frames.decode(path.value, request, work, ctx.cancellation)
            ctx.progress.report(4, _STEPS, "Measuring")
            for analyzer in todo:
                ctx.raise_if_cancelled()
                measured = self._run_analyzer(analyzer, video, request, runtime, stored, ctx)
                if isinstance(measured, Err):
                    if analyzer is AnalyzerId.SHOTS:
                        raise measured.error  # required by everything: an infrastructure failure
                    failures[analyzer] = str(measured.error)
                    _log.warning(
                        "Analyzer failed, continuing without it",
                        analyzer=analyzer.value,
                        reason=str(measured.error),
                    )
                    continue
                saved = self._save_signals(asset, analyzer, measured.value, specs[analyzer])
                if isinstance(saved, Err):
                    return saved
                stored[analyzer] = _Stored(measured.value, saved.value, specs[analyzer], False)
        return Ok((failures, video.warnings))

    def _run_analyzer(
        self,
        analyzer: AnalyzerId,
        video: DecodedVideo,
        request: DecodeRequest,
        runtime: RuntimeConfig,
        stored: Mapping[AnalyzerId, _Stored],
        ctx: JobContext,
    ) -> Result[Signals, MediaHouseError]:
        timeline_entry = stored.get(AnalyzerId.SHOTS)
        timeline = (
            timeline_entry.signals
            if timeline_entry and isinstance(timeline_entry.signals, ShotSignals)
            else None
        )
        plan: tuple[int, ...] = ()
        colour_plan: tuple[int, ...] = ()
        if timeline is not None and analyzer is not AnalyzerId.SHOTS:
            if ANALYZERS[analyzer].stream == "gray":
                plan = plan_samples(timeline, request.stride, request.settings)
            else:
                colour_plan = plan_rgb(timeline, request.rgb_stride, request.settings)
        inputs = {d: stored[d].signals for d in ANALYZERS[analyzer].inputs if d in stored}
        started = time.perf_counter()
        try:
            signals = self._analyzers[analyzer].measure(
                video,
                MeasureRequest(
                    settings=request.settings,
                    stride=request.stride,
                    plan=plan,
                    rgb_stride=request.rgb_stride,
                    rgb_plan=colour_plan,
                    timeline=timeline,
                    source=request.source,
                    dependencies=inputs,
                    device=runtime.device,
                    allow_downloads=runtime.allow_model_download,
                ),
                ctx.cancellation,
            )
        except OperationCancelledError:
            raise
        except MediaHouseError as exc:
            return Err(exc)
        _log.info(
            "Signals measured",
            analyzer=analyzer.value,
            cache_hit=False,
            seconds=round(time.perf_counter() - started, 2),
        )
        return Ok(signals)

    def _load_signals(
        self, asset_id: str, analyzer: AnalyzerId, fingerprint: str
    ) -> tuple[Signals, MediaAssetDto] | None:
        def parse(text: str) -> Signals:
            signals: Signals = document_from_json(
                signals_kind(analyzer), SIGNAL_TYPES[analyzer], text
            )
            return signals

        return self._documents.load(asset_id, fingerprint, parse)

    def _save_signals(
        self,
        asset: MediaAssetDto,
        analyzer: AnalyzerId,
        signals: Signals,
        spec: DocumentSpec,
    ) -> Result[MediaAssetDto, MediaError]:
        return self._documents.store(
            asset,
            document_to_json(signals_kind(analyzer), signals),
            filename=f"signals_{analyzer.value}.json",
            spec=spec,
            display_name=f"{asset.display_name} (video signals: {analyzer.value})",
            metadata={
                "analyzer": analyzer.value,
                "analyzer_version": ANALYZERS[analyzer].version,
                "schema_version": SCHEMA_VERSION,
            },
        )

    # --- identity ----------------------------------------------------------------------------
    def _engines_of(self, analyzer: AnalyzerId) -> dict[str, str]:
        """What decides ``analyzer``'s values: the decoder and its own engine, nothing else."""
        decoder = {f"frames.{k}": v for k, v in self._frames.identity().items()}
        own = {f"{analyzer.value}.{k}": v for k, v in self._analyzers[analyzer].identity().items()}
        return {**decoder, **own}

    def _all_engines(self, order: tuple[AnalyzerId, ...]) -> dict[str, str]:
        engines: dict[str, str] = {}
        for analyzer in order:
            engines.update(self._engines_of(analyzer))
        return dict(sorted(engines.items()))

    def _signal_spec(
        self,
        analyzer: AnalyzerId,
        profile: ProcessingProfile,
        inputs: ResolvedInputs,
        strides: tuple[int, int],
        fingerprints: Mapping[AnalyzerId, str],
    ) -> DocumentSpec:
        """The cache identity of one analyzer's signals.

        Source content is part of the library's fingerprint already; the rest decides the
        values: settings, decode geometry, tool and model versions, and the fingerprint of every
        analyzer whose signals this one reads (so a changed dependency changes this key too).
        Lineage-aware reuse (skipping re-analysis because an upstream transformation is known
        not to affect this analyzer) would add the identity of that transformation here; it is
        deliberately not implemented.
        """
        spec = ANALYZERS[analyzer]
        source = inputs.source
        stride: int | None = {"dense": None, "gray": strides[0], "rgb": strides[1]}[spec.stream]
        config: dict[str, JsonValue] = {
            "analyzer": analyzer.value,
            "settings": profile.measurement.config_for(analyzer),
            # the sample decodes (and so their strides) are irrelevant to the per-frame signals
            "stride": stride,
            "source": {"width": source.width, "height": source.height},
            "engines": _as_json(self._engines_of(analyzer)),
            "inputs": {d.value: fingerprints.get(d) for d in spec.inputs},
            "schema_version": SCHEMA_VERSION,
        }
        return DocumentSpec(f"{RESULT_OPERATION}.{analyzer.value}", spec.version, config)

    def _children(self, result_asset: MediaAssetDto) -> dict[AnalyzerId, MediaAssetDto]:
        ids = result_asset.metadata.get("signal_asset_ids")
        children: dict[AnalyzerId, MediaAssetDto] = {}
        if isinstance(ids, dict):
            for name, asset_id in ids.items():
                found = self._library.get(asset_id) if isinstance(asset_id, str) else None
                if isinstance(found, Ok):
                    children[AnalyzerId(name)] = found.value
        return children


def _result_config(
    profile: ProcessingProfile,
    inputs: ResolvedInputs,
    fingerprints: Mapping[AnalyzerId, str],
    engines: Mapping[str, str],
    unavailable_or_failed: tuple[AnalyzerId, ...],
) -> dict[str, JsonValue]:
    history = inputs.history
    missing: list[JsonValue] = [*sorted({a.value for a in unavailable_or_failed})]
    return {
        **profile.interpretation_config(),
        "signals": {a.value: f for a, f in fingerprints.items()},
        "derivations": dict(DERIVATION_VERSIONS),
        "history": {
            "measured_on": history.measured_on.value,
            "stabilized": history.stabilized.value,
            "operations": list(history.operations),
        },
        "source": {
            "color_transfer": inputs.source.color_transfer,
            "width": inputs.source.width,
            "height": inputs.source.height,
        },
        "engines": _as_json(engines),
        "missing": missing,
        "vocabulary_version": VOCABULARY_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


def _as_json(values: Mapping[str, str]) -> dict[str, JsonValue]:
    converted: dict[str, JsonValue] = {}
    converted.update(values)
    return converted


def _report(
    analyzer: AnalyzerId,
    entry: _Stored | None,
    failures: Mapping[AnalyzerId, str],
    unavailable: Mapping[AnalyzerId, str],
) -> AnalyzerReport:
    spec = ANALYZERS[analyzer]
    common = {
        "analyzer": analyzer,
        "version": spec.version,
        "cost": spec.cost,
        "depends_on": spec.depends_on,
    }
    if entry is not None:
        return AnalyzerReport(
            state=AnalyzerState.OK,
            cache=CacheOutcome.REUSED if entry.reused else CacheOutcome.COMPUTED,
            asset_id=entry.asset.id,
            **common,  # type: ignore[arg-type]
        )
    if analyzer in unavailable:
        return AnalyzerReport(
            state=AnalyzerState.NOT_AVAILABLE,
            cache=CacheOutcome.NONE,
            reason=unavailable[analyzer],
            **common,  # type: ignore[arg-type]
        )
    return AnalyzerReport(
        state=AnalyzerState.FAILED,
        cache=CacheOutcome.NONE,
        reason=failures.get(analyzer, "the analyzer produced no signals"),
        **common,  # type: ignore[arg-type]
    )


def _frame_count_warnings(signals: ShotSignals, inputs: ResolvedInputs) -> list[str]:
    declared = inputs.source.declared_frame_count
    if declared is None:
        return []
    difference = abs(signals.frame_count - declared)
    if difference > max(_FRAME_COUNT_SLACK, declared // 100):
        return [
            f"decoded {signals.frame_count} frames but the file declares {declared}; "
            "the file may be damaged or truncated"
        ]
    return []
