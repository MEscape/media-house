"""Use case: render a stored plan's music and effects into one stem (WAV, float, 48 kHz).

The stem carries the planned levels, fades, crossfades and (optionally) the planned ducking as
gain rides; it is NOT normalised, limited or mastered: that is audio_improvement's job. Hand-off:
``AudioMixing.execute(MixAudioCommand(voice_asset_id=<dialogue>, music_asset_id=<stem asset>))``
mixes it under the voice. With ``include_ducking`` the stem is already ducked, so set
``overrides={"mix.duck_ratio": 1.0}`` there to avoid ducking twice.

The stem is a derived asset of the plan's anchor (operation ``sound_stem_render``); the plan
hash, the options and the renderer identity are its cache key, so an identical request returns
the stored stem and renders nothing. Render QC reads the produced file's facts.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from media_house.modules.media_library.application.contracts import (
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.music_and_sound_design.application.ports import (
    StemOptions,
    StemRenderer,
)
from media_house.modules.music_and_sound_design.application.results import SoundDesignResults
from media_house.modules.music_and_sound_design.domain.errors import (
    SoundDesignError,
    StemRenderFailed,
)
from media_house.modules.music_and_sound_design.domain.model import QcIssue
from media_house.modules.music_and_sound_design.domain.timeline import fraction
from media_house.modules.music_and_sound_design.domain.values import (
    RENDERER_VERSION,
    STEM_RENDER,
    QcCategory,
    Severity,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type RenderStemError = MediaError | SoundDesignError | MediaHouseError

#: The produced file may differ from the plan by at most this (one audio block).
_DURATION_TOLERANCE = 0.05
_PEAK_WARNING_DBFS = 0.0


@dataclass(frozen=True, slots=True)
class RenderSoundStemCommand:
    plan_asset_id: str
    options: StemOptions = field(default_factory=StemOptions)


@dataclass(frozen=True, slots=True)
class StemResult:
    asset: MediaAssetDto
    created: bool
    checks: tuple[QcIssue, ...] = ()


class RenderSoundStem:
    def __init__(self, library: MediaLibrary, renderer: StemRenderer, paths: AppPaths) -> None:
        self._library = library
        self._renderer = renderer
        self._paths = paths
        self._plans = SoundDesignResults(library)

    def execute(
        self, command: RenderSoundStemCommand, ctx: JobContext
    ) -> Result[StemResult, RenderStemError]:
        loaded = self._plans.load(command.plan_asset_id)
        if isinstance(loaded, Err):
            return loaded
        plan = loaded.value
        try:
            engine = self._renderer.identity
        except MediaHouseError as exc:
            return Err(exc)
        config: dict[str, JsonValue] = {
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "include_ducking": command.options.include_ducking,
            "sample_rate": command.options.sample_rate,
            "renderer": RENDERER_VERSION,
            "engine": engine,
        }
        fingerprint = self._library.fingerprint(
            plan.anchor_asset_id, STEM_RENDER, config, RENDERER_VERSION
        )
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self._library.find_derived_asset(plan.anchor_asset_id, fingerprint.value)
        if existing is not None:
            return Ok(StemResult(existing, created=False, checks=_stored_checks(existing)))
        sources = {}
        for asset_id in plan.used_asset_ids():
            path = self._library.local_path(asset_id)
            if isinstance(path, Err):
                return path
            sources[asset_id] = path.value
        ctx.raise_if_cancelled()
        ctx.progress.report(1, 2, "Rendering the music and effects stem")
        with self._paths.temporary_directory(prefix="sound-stem-") as tmp:
            destination = tmp / "sound_stem.wav"
            try:
                report = self._renderer.render(
                    plan, sources, destination, command.options, ctx.cancellation
                )
            except MediaHouseError as exc:
                return Err(exc)
            expected = float(fraction(plan.timeline_duration))
            if (
                report.codec != "pcm_f32le"
                or report.sample_rate != command.options.sample_rate
                or abs(report.duration_seconds - expected) > _DURATION_TOLERANCE
            ):
                return Err(
                    StemRenderFailed(
                        f"produced {report.codec} {report.sample_rate} Hz"
                        f" {report.duration_seconds:.3f} s, expected pcm_f32le"
                        f" {command.options.sample_rate} Hz {expected:.3f} s"
                    )
                )
            checks = _checks(report.peak_dbfs)
            ctx.progress.report(2, 2, "Saving the stem")
            registered = self._library.register_derived(
                plan.anchor_asset_id,
                destination,
                operation=STEM_RENDER,
                config=config,
                version=RENDERER_VERSION,
                display_name=f"Music and effects stem ({plan.profile}, plan {plan.plan_version})",
                metadata={
                    "processing_type": STEM_RENDER,
                    "plan_asset_id": command.plan_asset_id,
                    "plan_id": plan.plan_id,
                    "plan_hash": plan.plan_hash,
                    "profile": plan.profile,
                    "include_ducking": command.options.include_ducking,
                    "mastered": False,
                    "duration_frames": round(
                        Fraction(report.duration_seconds) * fraction(plan.frame_rate)
                    ),
                    "peak_dbfs": report.peak_dbfs,
                    "assets": list(plan.used_asset_ids()),
                    "credits": plan.credit_lines,
                    "engine": engine,
                    "qc": [c.code for c in checks],
                },
            )
        if isinstance(registered, Err):
            return registered
        _log.info(
            "Sound stem rendered",
            asset_id=registered.value.asset.id,
            seconds=report.duration_seconds,
            peak_dbfs=report.peak_dbfs,
        )
        return Ok(StemResult(registered.value.asset, registered.value.created, checks))


def _checks(peak: float | None) -> tuple[QcIssue, ...]:
    if peak is None or peak <= _PEAK_WARNING_DBFS:
        return ()
    return (
        QcIssue(
            code="stem_over_full_scale",
            severity=Severity.WARNING,
            category=QcCategory.TECHNICAL,
            message=f"the stem peaks at {peak:+.1f} dBFS (float WAV keeps it; a fixed-point"
            " export would clip): audio_improvement's limiter must handle it",
        ),
    )


def _stored_checks(asset: MediaAssetDto) -> tuple[QcIssue, ...]:
    peak = asset.metadata.get("peak_dbfs")
    return _checks(float(peak) if isinstance(peak, int | float) else None)


__all__ = [
    "RenderSoundStem",
    "RenderSoundStemCommand",
    "StemResult",
]
