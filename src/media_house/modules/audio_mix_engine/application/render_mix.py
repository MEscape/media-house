"""Render the final mix and its stems, measure them, correct the level, and judge the result.

    resolve sources -> level the dialogue -> render each bus (a stem) -> sum + master -> measure
    -> correct toward the loudness target (bounded) -> validate -> store

Every stem and the mix are derived assets of the recording, found again by the hash of everything
that can change them: changing the dialogue gain re-renders the dialogue stem and the mix, not the
music; a new loudness target re-renders only the mix. Nothing is re-rendered whose hash is
unchanged, and an earlier valid output is never overwritten (a changed hash is a new asset).

A backend failure is the result (state ``failed``), with what was already rendered kept; the
specification's own errors and an unsupported operation stop before any rendering.
"""

from dataclasses import dataclass, field, replace
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.modules.audio_mix_engine.application.create_spec import (
    CreateMixSpecification,
    CreateSpecCommand,
    MixConfig,
    MixError,
)
from media_house.modules.audio_mix_engine.application.ports import (
    AudioBackend,
    MixRequest,
    StemRequest,
)
from media_house.modules.audio_mix_engine.application.sources import SoundFile, SoundFiles
from media_house.modules.audio_mix_engine.application.store import MixStore
from media_house.modules.audio_mix_engine.domain.errors import (
    BackendFailed,
    SpecificationInvalid,
    TimelineNotMixable,
)
from media_house.modules.audio_mix_engine.domain.output import OutputFacts, validate_output
from media_house.modules.audio_mix_engine.domain.plan import (
    dialogue_trims,
    loudness_correction,
    mix_hash,
    stem_hash,
)
from media_house.modules.audio_mix_engine.domain.profile import mix_profile
from media_house.modules.audio_mix_engine.domain.result import (
    CorrectionStep,
    DialogueLevel,
    Measurement,
    MixOutput,
    MixResult,
    OutputIssue,
    ProcessingRecord,
    StemOutput,
)
from media_house.modules.audio_mix_engine.domain.spec import AudioMixSpecification
from media_house.modules.audio_mix_engine.domain.timebase import fr
from media_house.modules.audio_mix_engine.domain.validate import check_capabilities
from media_house.modules.audio_mix_engine.domain.values import (
    ENGINE_VERSION,
    MIX_OPERATION,
    STEM_OPERATION,
    BusRole,
    JobState,
    Severity,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaLibrary
from media_house.modules.timeline_builder.application.contracts import BuiltTimeline
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, InvariantViolation, Ok, OperationCancelledError, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger
from media_house.shared.types import new_id

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RenderMixCommand:
    timeline: BuiltTimeline | None = None
    anchor_asset_id: str | None = None
    timeline_id: str | None = None
    config: MixConfig = field(default_factory=MixConfig)


@dataclass(frozen=True, slots=True)
class MixRenderResult:
    result: MixResult
    result_asset: MediaAssetDto
    spec: AudioMixSpecification
    spec_asset: MediaAssetDto
    mix_asset: MediaAssetDto | None
    stem_assets: tuple[MediaAssetDto, ...]


class RenderAudioMix:
    def __init__(
        self,
        library: MediaLibrary,
        specifications: CreateMixSpecification,
        backend: AudioBackend,
        sources: SoundFiles,
        store: MixStore,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._specs = specifications
        self._backend = backend
        self._sources = sources
        self._store = store
        self._paths = paths
        self._clock = clock

    def execute(
        self, command: RenderMixCommand, ctx: JobContext
    ) -> Result[MixRenderResult, MixError]:
        made = self._specs.execute(
            CreateSpecCommand(
                command.timeline, command.anchor_asset_id, command.timeline_id, command.config
            )
        )
        if isinstance(made, Err):
            return made
        spec, spec_asset = made.value.spec, made.value.asset
        anchor = self._library.get(spec.provenance.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        capabilities = self._backend.capabilities()
        unsupported = check_capabilities(spec, capabilities.operations)
        if unsupported:
            return Err(SpecificationInvalid([i.message for i in unsupported]))
        files = self._sources.resolve(spec.sources)
        problems = _source_problems(spec, files)
        if problems:
            return Err(TimelineNotMixable(problems))

        job = _Job(
            self, spec, spec_asset, anchor.value, files, command.config, capabilities.identity, ctx
        )
        cancelled: OperationCancelledError | None = None
        try:
            job.run()
        except OperationCancelledError as exc:
            job.state = JobState.CANCELLED
            cancelled = exc
        except BackendFailed as exc:
            job.errors.append(str(exc))
            job.state = JobState.FAILED
        result = job.result(self._clock.now().isoformat())
        stored = self._store.save_result(anchor.value, result)
        if isinstance(stored, Err):
            return stored
        if cancelled is not None:
            raise cancelled
        _log.info(
            "Audio mix rendered",
            state=result.state.value,
            stems=len(result.stems),
            reused=result.reused_stems,
        )
        return Ok(
            MixRenderResult(
                result, stored.value, spec, spec_asset, job.mix_asset, tuple(job.stem_assets)
            )
        )


@dataclass
class _Job:
    owner: RenderAudioMix
    spec: AudioMixSpecification
    spec_asset: MediaAssetDto
    anchor: MediaAssetDto
    files: dict[str, SoundFile]
    config: MixConfig
    backend_id: str
    ctx: JobContext
    state: JobState = JobState.QUEUED
    stems: list[StemOutput] = field(default_factory=list)
    stem_paths: dict[str, Path] = field(default_factory=dict)
    stem_assets: list[MediaAssetDto] = field(default_factory=list)
    mix: MixOutput | None = None
    mix_path: Path | None = None
    mix_asset: MediaAssetDto | None = None
    trim: float = 0.0
    corrections: list[CorrectionStep] = field(default_factory=list)
    levels: list[DialogueLevel] = field(default_factory=list)
    issues: list[OutputIssue] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log: list[ProcessingRecord] = field(default_factory=list)
    job_id: str = field(default_factory=new_id)

    # --- the run -------------------------------------------------------------------------
    def run(self) -> None:
        self.log.append(ProcessingRecord("backend", self.backend_id))
        trims = self._level_dialogue()
        buses = [s.bus_id for s in self.spec.stems]
        total = len(buses) + 3
        self.state = JobState.RENDERING_STEMS
        hashes: dict[str, str] = {}
        checksums = {k: f.checksum for k, f in self.files.items()}
        for n, bus_id in enumerate(buses, start=1):
            self.ctx.raise_if_cancelled()
            self.ctx.progress.report(n, total, f"stem {bus_id}")
            hashes[bus_id] = self._stem(bus_id, trims, checksums)
        self.ctx.raise_if_cancelled()
        self.state = JobState.MIXING
        self.ctx.progress.report(len(buses) + 1, total, "mix")
        self._mix(hashes)
        self.ctx.raise_if_cancelled()
        self.state = JobState.VALIDATING
        self.ctx.progress.report(len(buses) + 2, total, "validate")
        self._validate()
        bad = [i for i in self.issues if i.severity in {Severity.ERROR, Severity.CRITICAL}]
        self.state = JobState.PARTIAL if bad else JobState.COMPLETED
        self.ctx.progress.report(total, total, "done")

    def _level_dialogue(self) -> dict[str, float]:
        if not self.config.level_dialogue:
            return {}
        self.state = JobState.LEVELLING
        profile = mix_profile(self.spec.provenance.profile)
        rate = self.spec.timebase.sample_rate
        measured: dict[str, float | None] = {}
        for clip in self.spec.clips_of(BusRole.DIALOGUE.value):
            self.ctx.raise_if_cancelled()
            measured[clip.clip_id] = self.owner._backend.measure_range(
                self._path(clip.source_id), float(fr(clip.source_in)), float(fr(clip.source_out)),
                self.ctx.cancellation,
            )  # fmt: skip
        trims = dialogue_trims(
            measured,
            target_lufs=profile.dialogue_target_lufs,
            levelling=profile.dialogue_levelling,
            max_trim_db=profile.max_dialogue_trim_db,
        )
        self.levels = [DialogueLevel(c, measured[c], trims.get(c, 0.0)) for c in sorted(measured)]
        self.log.append(
            ProcessingRecord(
                "dialogue levelling", f"{len(trims)} of {len(measured)} clips trimmed (rate {rate})"
            )
        )
        return trims

    def _stem(self, bus_id: str, trims: dict[str, float], checksums: dict[str, str | None]) -> str:
        spec, owner = self.spec, self.owner
        bus = spec.bus(bus_id)
        if bus is None:
            raise InvariantViolation(f"stem of a bus the specification does not have: {bus_id}")
        h = stem_hash(spec, bus_id, checksums, trims, self.backend_id)
        key = {"stem_hash": h, "bus": bus_id}
        fingerprint = owner._library.fingerprint(
            self.anchor.id, STEM_OPERATION, key, ENGINE_VERSION
        )
        if isinstance(fingerprint, Err):
            raise BackendFailed("find the stem", str(fingerprint.error))
        asset = owner._library.find_derived_asset(self.anchor.id, fingerprint.value)
        reused = asset is not None
        path = None
        if asset is not None:
            located = owner._library.local_path(asset.id)
            path = None if isinstance(located, Err) else located.value
            reused = path is not None and path.is_file()
        if not reused:
            clips = spec.clips_of(bus_id)
            ids = {c.clip_id for c in clips}
            request = StemRequest(
                bus=bus,
                clips=clips,
                ducks=tuple(d for d in spec.ducking if ids & set(d.clip_ids)),
                automation=tuple(
                    a for a in spec.automation
                    if (a.target_kind == "bus" and a.target_id == bus_id)
                    or (a.target_kind == "clip" and a.target_id in ids)
                ),
                trims={i: t for i, t in trims.items() if i in ids},
                sources={s: self._path(s) for s in {c.source_id for c in clips}},
                source_rates={s.source_id: s.sample_rate for s in spec.sources},
                sample_rate=spec.render.sample_rate,
                timebase_rate=spec.timebase.sample_rate,
                duration_samples=spec.timebase.duration_samples,
                destination=Path(),
            )  # fmt: skip
            with owner._paths.temporary_directory(prefix="audio-mix-") as tmp:
                destination = tmp / f"{bus_id}.wav"
                owner._backend.render_stem(
                    replace(request, destination=destination), self.ctx.cancellation
                )
                registered = owner._library.register_derived(
                    self.anchor.id,
                    destination,
                    operation=STEM_OPERATION,
                    config=key,
                    version=ENGINE_VERSION,
                    display_name=f"{self.anchor.display_name} (stem {bus_id})",
                    metadata={
                        "timeline_id": spec.timeline_id, "bus_id": bus_id,
                        "stage_hash": h, "specification_id": spec.specification_id,
                    },
                )  # fmt: skip
                if isinstance(registered, Err):
                    raise BackendFailed("store the stem", str(registered.error))
                asset = registered.value.asset
            located = owner._library.local_path(asset.id)
            if isinstance(located, Err):
                raise BackendFailed("read the stem back", str(located.error))
            path = located.value
            self.log.append(
                ProcessingRecord(f"stem {bus_id}", f"rendered {len(clips)} clips, hash {h[:12]}")
            )
        else:
            self.log.append(ProcessingRecord(f"stem {bus_id}", f"reused, hash {h[:12]}"))
        if asset is None or path is None:
            raise BackendFailed("find the stem", f"{bus_id} has no file")
        measurement = owner._backend.measure(path, self.ctx.cancellation)
        self.stem_paths[bus_id] = path
        self.stem_assets.append(asset)
        self.stems.append(StemOutput(f"stem:{bus_id}", bus_id, asset.id, h, reused, measurement))
        return h

    def _path(self, source_id: str) -> Path:
        path = self.files[source_id].path
        if path is None:
            raise BackendFailed("find a source", f"{source_id} has no file")
        return path

    def _mix(self, hashes: dict[str, str]) -> None:
        spec, owner = self.spec, self.owner
        h = mix_hash(spec, hashes, self.backend_id)
        key = {"mix_hash": h}
        fingerprint = owner._library.fingerprint(self.anchor.id, MIX_OPERATION, key, ENGINE_VERSION)
        if isinstance(fingerprint, Err):
            raise BackendFailed("find the mix", str(fingerprint.error))
        asset = owner._library.find_derived_asset(self.anchor.id, fingerprint.value)
        located = None if asset is None else owner._library.local_path(asset.id)
        if (
            asset is not None
            and located is not None
            and not isinstance(located, Err)
            and located.value.is_file()
        ):
            trim = asset.metadata.get("master_trim_db")
            self.trim = float(trim) if isinstance(trim, int | float) else 0.0
            self.mix_asset, self.mix_path = asset, located.value
            self.mix = MixOutput(
                asset.id, h, True, owner._backend.measure(located.value, self.ctx.cancellation)
            )
            self.log.append(
                ProcessingRecord("mix", f"reused, hash {h[:12]}, trim {self.trim:g} dB")
            )
            return
        stems = tuple(self.stem_paths[s.bus_id] for s in spec.stems)
        master = spec.master
        with owner._paths.temporary_directory(prefix="audio-mix-") as tmp:
            trim = 0.0
            final: Path | None = None
            measurement: Measurement | None = None
            for iteration in range(self.config.max_corrections + 1):
                self.ctx.raise_if_cancelled()
                destination = tmp / f"mix_{iteration}.{spec.render.container}"
                owner._backend.render_mix(
                    MixRequest(stems, master, trim, spec.render, spec.render_samples, destination),
                    self.ctx.cancellation,
                )
                self.state = JobState.MEASURING
                measurement = owner._backend.measure(destination, self.ctx.cancellation)
                self.corrections.append(
                    CorrectionStep(
                        iteration, trim, measurement.integrated_lufs, measurement.true_peak_dbtp
                    )
                )
                delta = loudness_correction(measurement.integrated_lufs, spec.loudness)
                final = destination
                if delta is None or iteration == self.config.max_corrections:
                    break
                self.state = JobState.CORRECTING
                trim = round(trim + delta, 2)
            if final is None or measurement is None:
                raise BackendFailed("render the mix", "nothing was rendered")
            self.trim = trim
            registered = owner._library.register_derived(
                self.anchor.id,
                final,
                operation=MIX_OPERATION,
                config=key,
                version=ENGINE_VERSION,
                display_name=f"{self.anchor.display_name} (final mix, {spec.loudness.delivery})",
                metadata={
                    "timeline_id": spec.timeline_id, "stage_hash": h, "master_trim_db": trim,
                    "specification_id": spec.specification_id, "delivery": spec.loudness.delivery,
                },
            )  # fmt: skip
            if isinstance(registered, Err):
                raise BackendFailed("store the mix", str(registered.error))
            asset = registered.value.asset
        located = owner._library.local_path(asset.id)
        if isinstance(located, Err):
            raise BackendFailed("read the mix back", str(located.error))
        self.mix_asset, self.mix_path = asset, located.value
        self.mix = MixOutput(asset.id, h, False, measurement)
        self.log.append(
            ProcessingRecord(
                "mix",
                f"rendered, hash {h[:12]}, master trim {trim:g} dB, "
                f"{len(self.corrections)} pass(es)",
            )
        )

    def _validate(self) -> None:
        spec, owner, mix, path = self.spec, self.owner, self.mix, self.mix_path
        if mix is None or path is None:
            raise BackendFailed("validate the mix", "there is no mix")
        cancel = self.ctx.cancellation
        silences = owner._backend.silences(
            path, spec.validation.silence_threshold_db, spec.validation.silence_min_seconds, cancel
        )
        rate = spec.timebase.sample_rate
        ranges = _merge(
            [
                (c.start_sample / rate, c.end_sample / rate)
                for c in spec.clips_of(BusRole.DIALOGUE.value)
            ]
        )
        dialogue = self.stem_paths.get(BusRole.DIALOGUE.value)
        music = self.stem_paths.get(BusRole.MUSIC.value)
        dialogue_lufs = (
            owner._backend.loudness_over(dialogue, ranges, cancel) if dialogue and ranges else None
        )
        music_lufs = (
            owner._backend.loudness_over(music, ranges, cancel) if music and ranges else None
        )
        recombination = None
        if self.config.verify_recombination:
            with owner._paths.temporary_directory(prefix="audio-mix-") as tmp:
                again = tmp / f"again.{spec.render.container}"
                owner._backend.render_mix(
                    MixRequest(
                        tuple(self.stem_paths[s.bus_id] for s in spec.stems), spec.master,
                        self.trim, spec.render, spec.render_samples, again,
                    ),
                    cancel,
                )  # fmt: skip
                recombination = owner._backend.null_test(path, again, cancel)
        facts = OutputFacts(
            mix=mix.measurement,
            stems={s.stem_id: s.measurement for s in self.stems},
            silences=silences,
            dialogue_lufs=dialogue_lufs,
            music_lufs=music_lufs,
            recombination_db=recombination,
        )
        self.issues = list(validate_output(spec, facts))

    def result(self, created_at: str) -> MixResult:
        spec = self.spec
        warnings = tuple(i.message for i in spec.warnings if i.severity is not Severity.INFO)
        return MixResult(
            mix_job_id=self.job_id,
            anchor_asset_id=self.anchor.id,
            timeline_id=spec.timeline_id,
            timeline_version=spec.timeline_version,
            specification_id=spec.specification_id,
            specification_hash=spec.content_hash,
            specification_asset_id=self.spec_asset.id,
            state=self.state,
            backend=self.backend_id,
            profile=spec.provenance.profile,
            delivery=spec.provenance.delivery,
            stems=tuple(sorted(self.stems, key=lambda s: s.stem_id)),
            mix=self.mix,
            master_trim_db=self.trim,
            corrections=tuple(self.corrections),
            dialogue_levels=tuple(self.levels),
            issues=tuple(self.issues),
            warnings=warnings,
            errors=tuple(self.errors),
            processing=tuple(self.log),
            created_at=created_at,
        )


# --- helpers ------------------------------------------------------------


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _source_problems(spec: AudioMixSpecification, files: dict[str, SoundFile]) -> list[str]:
    names = {s.source_id: s.name for s in spec.sources}
    problems: set[str] = set()
    for clip in spec.clips:
        f = files.get(clip.source_id)
        label = names.get(clip.source_id, clip.source_id)
        if f is None or not f.exists:
            problems.add(f"{label} is no longer available")
        elif f.changed:
            problems.add(f"{label} changed since the timeline was built")
    return sorted(problems)
