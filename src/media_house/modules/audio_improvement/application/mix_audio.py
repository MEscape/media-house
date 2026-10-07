"""Use case: voice asset + music asset -> mastered mix (both sources stay untouched).

    measure both -> place the music relative to the voice -> duck it under the voice -> master

The voice is expected to be improved already (``ImproveAudio``); mixing never improves it again.
The mix is a derived asset of the VOICE (it defines the timeline and the length) and records
the music asset it used. Mastering runs after the mix, on the whole.

Ducking here follows the voice's level through a sidechain. Speech-aware ducking from Audio
Intelligence signals is a later layer that decides what to duck; it plugs in by producing a
different ``AudioMixer`` configuration, not by this module importing Audio Intelligence.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from media_house.modules.audio_improvement.application.improve_audio import ImproveError
from media_house.modules.audio_improvement.application.mastering import Mastering
from media_house.modules.audio_improvement.application.ports import (
    AudioMixer,
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
from media_house.modules.audio_improvement.domain.profiles import DEFAULT_PROFILE, resolve_profile
from media_house.modules.audio_improvement.domain.provenance import (
    ProcessingProvenance,
    StageRecord,
)
from media_house.modules.audio_improvement.domain.settings import AudioProfile
from media_house.modules.audio_improvement.domain.values import (
    METADATA_KEY,
    MIX_OPERATION,
    PROCESSING_VERSION,
    JsonValue,
    ProcessingStage,
    StageStatus,
)
from media_house.modules.audio_improvement.domain.verification import Check, verify_output
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
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

_DISPLAY_NAME_MAX = 255


@dataclass(frozen=True, slots=True)
class MixAudioCommand:
    voice_asset_id: str
    music_asset_id: str
    profile: str = DEFAULT_PROFILE
    overrides: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MixResult:
    voice_asset: MediaAssetDto
    music_asset: MediaAssetDto
    #: The mastered mix: a derived asset of the voice.
    asset: MediaAssetDto
    profile: AudioProfile
    provenance: ProcessingProvenance
    analysis_after: QualityMeasurements
    #: How far the music was moved to sit at the configured offset below the voice (dB).
    music_gain_db: float
    checks: tuple[Check, ...]
    warnings: tuple[str, ...]
    created: bool


class MixAudio:
    """Voice and music into one mastered track."""

    def __init__(
        self,
        library: MediaLibrary,
        transcoder: AudioTranscoder,
        analyzer: QualityAnalyzer,
        mixer: AudioMixer,
        mastering_engine: StageProcessor,
        paths: AppPaths,
        inspections: InspectionCatalog | None = None,
    ) -> None:
        if mastering_engine.stage is not ProcessingStage.MASTERING:
            raise ConfigurationError("The mix needs a mastering engine")
        self._library = library
        self._transcoder = transcoder
        self._analyzer = analyzer
        self._mixer = mixer
        self._mastering_engine = mastering_engine
        self._paths = paths
        self._inspections = inspections

    def execute(
        self,
        command: MixAudioCommand,
        ctx: JobContext,
    ) -> Result[MixResult, ImproveError]:
        voice = self._library.get(command.voice_asset_id)
        if isinstance(voice, Err):
            return voice
        music = self._library.get(command.music_asset_id)
        if isinstance(music, Err):
            return music
        for asset in (voice.value, music.value):
            if asset.media_type not in {MediaType.AUDIO, MediaType.VIDEO}:
                return Err(
                    ValidationError(
                        f"Asset {asset.id} is {asset.media_type.value}, not audio or video",
                        user_message="Only audio and video files can be mixed.",
                    )
                )
        try:
            profile = resolve_profile(command.profile, command.overrides)
        except InvalidProfile as exc:
            return Err(ValidationError(str(exc), field="profile", user_message=exc.user_message))

        config: dict[str, JsonValue] = {
            "profile": profile.to_config(),
            "music_checksum": music.value.checksum,
            "engines": {
                "mixer": str(self._mixer.identity),
                "mastering": str(self._mastering_engine.identity),
                "transcoder": str(self._transcoder.identity),
                "analyzer": str(self._analyzer.identity),
            },
        }
        fingerprint = self._library.fingerprint(
            voice.value.id, MIX_OPERATION, config, PROCESSING_VERSION
        )
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self._library.find_derived_asset(voice.value.id, fingerprint.value)
        if existing is not None:
            restored = _restore(voice.value, music.value, existing, profile)
            if restored is not None:
                _log.info("Audio mix reused", asset_id=existing.id, cache_hit=True)
                return Ok(restored)

        voice_path = self._library.local_path(voice.value.id)
        if isinstance(voice_path, Err):
            return voice_path
        music_path = self._library.local_path(music.value.id)
        if isinstance(music_path, Err):
            return music_path
        try:
            return self._mix(
                voice.value, voice_path.value, music.value, music_path.value, profile, config, ctx
            )
        except AudioImprovementError as exc:
            return Err(exc)

    def _mix(
        self,
        voice: MediaAssetDto,
        voice_source: Path,
        music: MediaAssetDto,
        music_source: Path,
        profile: AudioProfile,
        config: dict[str, JsonValue],
        ctx: JobContext,
    ) -> Result[MixResult, ImproveError]:
        with self._paths.temporary_directory(prefix="mix-") as tmp:
            voice_wav, music_wav = tmp / "voice.wav", tmp / "music.wav"
            decoded = self._transcoder.decode(
                voice_source,
                voice_wav,
                ctx.cancellation,
                known_audio_facts(self._inspections, voice.id),
            )
            self._transcoder.decode(
                music_source,
                music_wav,
                ctx.cancellation,
                known_audio_facts(self._inspections, music.id),
            )
            voice_m = self._analyzer.analyze(voice_wav, ctx.cancellation)
            music_m = self._analyzer.analyze(music_wav, ctx.cancellation)
            if voice_m.integrated_lufs is None or music_m.integrated_lufs is None:
                raise AudioImprovementError(
                    "Voice or music loudness is not measurable",
                    user_message="The voice or the music is silent and cannot be mixed.",
                )
            music_gain = (
                voice_m.integrated_lufs + profile.mix.music_offset_lu - music_m.integrated_lufs
            )

            ctx.raise_if_cancelled()
            mixed = tmp / "mix.wav"
            self._mixer.mix(voice_wav, music_wav, mixed, profile.mix, music_gain, ctx.cancellation)
            mixed_m = self._analyzer.analyze(mixed, ctx.cancellation)
            mastered = Mastering(self._mastering_engine, self._analyzer).master(
                mixed, tmp, mixed_m, profile.mastering, ctx.cancellation
            )
            delivery = tmp / "mix-master.wav"
            self._transcoder.encode(mastered.path, delivery, ctx.cancellation)
            after = self._analyzer.analyze(delivery, ctx.cancellation)
            checks = verify_output(voice_m, after, profile)
            identity = self._mixer.identity
            mixing = StageRecord(
                ProcessingStage.MIXING,
                StageStatus.APPLIED,
                f"music {profile.mix.music_offset_lu:+.1f} LU relative to the voice, "
                "ducked by the voice",
                identity.name,
                identity.version,
                {
                    "music_gain_db": music_gain,
                    "music_offset_lu": profile.mix.music_offset_lu,
                    "duck_ratio": profile.mix.duck_ratio,
                    "duck_threshold_db": profile.mix.duck_threshold_db,
                },
            )
            provenance = ProcessingProvenance(
                profile=profile.name,
                processing_version=PROCESSING_VERSION,
                sample_rate=after.sample_rate,
                channels=after.channels,
                stages=(mixing, mastered.record),
                output_integrated_lufs=after.integrated_lufs,
                output_true_peak_dbtp=after.true_peak_dbtp,
                leading_pad_seconds=decoded.leading_pad_seconds,
            )
            warnings = tuple(f"{c.name}: {c.detail}" for c in checks if not c.passed)
            registered = self._library.register_derived(
                voice.id,
                delivery,
                operation=MIX_OPERATION,
                config=config,
                version=PROCESSING_VERSION,
                display_name=f"{voice.display_name} (mix with {music.display_name})"[
                    :_DISPLAY_NAME_MAX
                ],
                metadata={
                    "processing_type": MIX_OPERATION,
                    "source_asset_id": voice.id,
                    "music_asset_id": music.id,
                    "profile": profile.name,
                    "music_gain_db": music_gain,
                    METADATA_KEY: provenance.to_json_value(),
                    "analysis_after": after.to_json_value(),
                    "checks": [
                        {"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks
                    ],
                    "warnings": list(warnings),
                },
            )
        if isinstance(registered, Err):
            return registered
        return Ok(
            MixResult(
                voice,
                music,
                registered.value.asset,
                profile,
                provenance,
                after,
                music_gain,
                checks,
                warnings,
                created=registered.value.created,
            )
        )


def _restore(
    voice: MediaAssetDto,
    music: MediaAssetDto,
    asset: MediaAssetDto,
    profile: AudioProfile,
) -> MixResult | None:
    meta = asset.metadata
    try:
        provenance = ProcessingProvenance.from_metadata(meta)
        after_raw, gain = meta["analysis_after"], meta["music_gain_db"]
        raw_checks, raw_warnings = meta["checks"], meta["warnings"]
        if (
            provenance is None
            or not isinstance(after_raw, dict)
            or not isinstance(raw_checks, list)
            or not isinstance(raw_warnings, list)
            or not isinstance(gain, int | float)
        ):
            return None
        checks = tuple(
            Check(str(c["name"]), bool(c["passed"]), str(c["detail"]))  # type: ignore[index,call-overload]
            for c in raw_checks
        )
        return MixResult(
            voice,
            music,
            asset,
            profile,
            provenance,
            QualityMeasurements.from_json_value(after_raw),
            float(gain),
            checks,
            tuple(str(w) for w in raw_warnings),
            created=False,
        )
    except (KeyError, TypeError, ValueError, InvariantViolation):
        return None
