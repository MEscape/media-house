"""ImproveAudio end to end: real library, real FFmpeg, real engines, synthetic recordings."""

from pathlib import Path

import numpy as np
import pytest
from scipy import signal

from media_house.modules.audio_improvement.application.improve_audio import (
    ImproveAudioCommand,
    ImprovementResult,
)
from media_house.modules.audio_improvement.domain.errors import UnreadableSource
from media_house.modules.audio_improvement.domain.values import ProcessingStage as Stage
from media_house.modules.audio_improvement.domain.values import StageStatus
from media_house.modules.audio_improvement.infrastructure.ffmpeg_transcoder import read_wav
from media_house.modules.media_library.application.contracts import MediaType
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, ValidationError
from tests.integration.audio_improvement.conftest import Env
from tests.support.improvement_fakes import (
    RATE,
    ScriptedEngine,
    clipped,
    reverberate,
    rms_db,
    save,
    voice,
    with_hum,
    with_noise,
    with_sibilance,
)
from tests.support.media_factories import ffmpeg

pytestmark = pytest.mark.integration


def import_voice(env: Env, x: np.ndarray, name: str = "take.wav") -> str:
    return env.import_media(save(env.tmp / name, x))


def status(result: ImprovementResult, stage: Stage) -> StageStatus:
    record = result.provenance.record(stage)
    assert record is not None, f"no record for {stage}"
    return record.status


def applied(result: ImprovementResult) -> set[Stage]:
    return {r.stage for r in result.provenance.stages if r.status is StageStatus.APPLIED}


def assert_delivered(result: ImprovementResult) -> None:
    failed = [c for c in result.checks if not c.passed]
    assert not failed, failed
    assert result.analysis_after.integrated_lufs == pytest.approx(
        result.profile.mastering.target_lufs, abs=result.profile.mastering.tolerance_lu
    )
    assert result.analysis_after.true_peak_dbtp is not None
    assert (
        result.analysis_after.true_peak_dbtp
        <= result.profile.mastering.true_peak_ceiling_dbtp + 0.1
    )


# --- what gets processed -----------------------------------------------------------------------
def test_clean_speech_is_only_mastered_not_processed(env: Env) -> None:
    source_id = import_voice(env, voice())

    result = env.improve(source_id)

    assert applied(result) == {Stage.MASTERING}
    for stage in (
        Stage.NOISE_REDUCTION,
        Stage.DEREVERBERATION,
        Stage.EQUALIZATION,
        Stage.DE_ESSING,
    ):
        assert status(result, stage) is StageStatus.SKIPPED
    assert_delivered(result)
    assert env.total_runs == 1  # clean audio costs one gain/limiter pass


def test_noisy_speech_is_denoised_and_mastered(env: Env) -> None:
    result = env.improve(import_voice(env, with_noise(voice(), -40.0)))

    assert Stage.NOISE_REDUCTION in applied(result)
    assert result.provenance.noise_reduced
    assert result.analysis_before.snr_db is not None and result.analysis_after.snr_db is not None
    assert result.analysis_after.snr_db > result.analysis_before.snr_db + 8.0
    assert_delivered(result)


def test_mains_hum_is_notched_out(env: Env) -> None:
    result = env.improve(import_voice(env, with_noise(with_hum(voice()), -70.0)))

    assert result.analysis_before.hum_hz == 50.0
    assert result.provenance.noise_reduced
    after = result.analysis_after.hum_prominence_db
    assert after is None or after < (result.analysis_before.hum_prominence_db or 0.0) - 10.0
    assert_delivered(result)


def test_a_reverberant_room_is_dried(env: Env) -> None:
    result = env.improve(import_voice(env, reverberate(voice(16.0), rt60=1.0)))

    assert result.provenance.dereverberated
    before, after = result.analysis_before.reverb_rt60, result.analysis_after.reverb_rt60
    assert before is not None and before > 0.5
    assert after is None or after < before
    assert_delivered(result)


def test_clipped_speech_is_repaired_without_promising_the_impossible(env: Env) -> None:
    result = env.improve(import_voice(env, clipped(voice())))

    assert result.provenance.clipping_repaired
    assert result.analysis_after.clipping_ratio < result.analysis_before.clipping_ratio
    assert_delivered(result)


def test_harsh_sibilance_is_reduced(env: Env) -> None:
    result = env.improve(import_voice(env, with_sibilance(voice())))

    assert result.provenance.de_essed
    before, after = (
        result.analysis_before.sibilance_peak_db,
        result.analysis_after.sibilance_peak_db,
    )
    assert before is not None and after is not None and after < before - 3.0
    assert_delivered(result)


def test_uneven_speech_is_gently_evened(env: Env) -> None:
    uneven = voice()
    uneven[: int(len(uneven) / 2)] *= 3.0
    result = env.improve(import_voice(env, uneven), overrides={"dynamics.dynamics_trigger_db": 4.0})

    assert result.provenance.dynamics_processed
    assert_delivered(result)


def test_very_quiet_audio_is_brought_to_the_target_within_the_gain_limit(env: Env) -> None:
    source_id = import_voice(env, voice(amplitude=0.01))

    capped = env.improve(source_id)
    allowed = env.improve(source_id, overrides={"mastering.max_gain_db": 40.0})

    assert any("loudness_on_target" in w for w in capped.warnings)  # never silently pretended
    assert_delivered(allowed)


def test_different_recordings_of_one_speaker_end_up_equally_loud(env: Env) -> None:
    loud = env.improve(import_voice(env, voice(amplitude=0.8), "loud.wav"))
    soft = env.improve(import_voice(env, voice(amplitude=0.04), "soft.wav"))

    for result in (loud, soft):
        assert_delivered(result)
    assert loud.analysis_after.integrated_lufs == pytest.approx(
        soft.analysis_after.integrated_lufs, abs=1.0
    )


# --- the original is never touched; time is preserved -------------------------------------------
def test_the_original_is_untouched_and_the_result_is_a_derived_asset(env: Env) -> None:
    source_id = import_voice(env, with_noise(voice(), -40.0))
    before = env.library.get(source_id)
    assert isinstance(before, Ok)

    result = env.improve(source_id)

    after = env.library.get(source_id)
    assert isinstance(after, Ok)
    assert after.value.checksum == before.value.checksum
    assert result.asset.id != source_id
    assert result.asset.media_type is MediaType.AUDIO
    assert result.asset.derivation is not None
    assert result.asset.derivation.source_asset_id == source_id
    assert result.asset.derivation.operation == "audio_improvement"
    assert result.asset.metadata["profile"] == "youtube"
    assert [a.id for a in env.library.list_derived(source_id)] == [result.asset.id]


def test_processing_keeps_every_sample_in_place(env: Env) -> None:
    original = with_noise(voice(), -40.0)
    result = env.improve(import_voice(env, original))
    path = env.library.local_path(result.asset.id)
    assert isinstance(path, Ok)

    improved, rate = read_wav(path.value)

    assert rate == RATE
    assert len(improved) == len(original)  # same length, to the sample
    lags = signal.correlation_lags(len(original), len(improved), mode="full")
    lag = lags[np.argmax(signal.correlate(original, improved[:, 0], mode="full", method="fft"))]
    assert lag == 0  # no shift anywhere in the chain
    assert abs(rms_db(improved[:, 0]) - rms_db(original)) < 20.0  # same material, only gained


def test_audio_that_starts_late_in_a_video_stays_in_sync(env: Env) -> None:
    video = env.tmp / "delayed.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc=duration=4:size=160x120:rate=25",
        "-itsoffset", "0.5", "-f", "lavfi", "-i", "sine=frequency=300:duration=3:sample_rate=48000",
        "-c:v", "mpeg4", "-c:a", "aac", str(video),
    )  # fmt: skip
    result = env.improve(env.import_media(video), overrides={"mastering.skip_if_compliant": False})

    pad = result.provenance.leading_pad_seconds
    assert 0.3 < pad < 0.7
    assert result.analysis_after.duration == pytest.approx(pad + 3.0, abs=0.1)
    path = env.library.local_path(result.asset.id)
    assert isinstance(path, Ok)
    samples, rate = read_wav(path.value)
    assert rms_db(samples[: int(0.3 * rate), 0]) < -60.0  # silence where the video has no audio yet
    assert rms_db(samples[int((pad + 0.5) * rate) : int((pad + 1.0) * rate), 0]) > -30.0


# --- safety nets -------------------------------------------------------------------------------
def test_a_stage_that_does_nothing_useful_is_reverted(env: Env) -> None:
    useless = ScriptedEngine(Stage.NOISE_REDUCTION)  # copies its input: no improvement
    env.use_case = env.build([useless])

    result = env.improve(import_voice(env, with_noise(voice(), -40.0)))

    record = result.provenance.record(Stage.NOISE_REDUCTION)
    assert record is not None
    assert record.status is StageStatus.BYPASSED
    assert "reverted" in record.reason
    assert not result.provenance.noise_reduced  # the audio does not carry that processing
    assert_delivered(result)


def test_a_stage_that_shifts_the_timing_is_reverted(env: Env) -> None:
    def delay(source: Path, destination: Path, params: object) -> None:
        samples, rate = read_wav(source)
        padded = np.concatenate([np.zeros((int(0.5 * rate), samples.shape[1])), samples])
        from scipy.io import wavfile

        wavfile.write(destination, rate, padded.astype(np.float32))

    env.use_case = env.build([ScriptedEngine(Stage.DE_ESSING, delay)])

    result = env.improve(import_voice(env, with_sibilance(voice())))

    record = result.provenance.record(Stage.DE_ESSING)
    assert record is not None
    assert record.status is StageStatus.BYPASSED
    assert "duration" in record.reason
    assert result.analysis_after.duration == pytest.approx(
        result.analysis_before.duration, abs=0.01
    )


def test_expected_failures_are_results(env: Env) -> None:
    silent_video = env.tmp / "silent.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=64x64:rate=10",
        "-c:v",
        "mpeg4",
        str(silent_video),
    )
    picture = env.tmp / "pic.png"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=16x16", "-frames:v", "1", str(picture))
    audio_id = import_voice(env, voice(3.0))
    ctx = JobContext.detached()

    no_audio = env.use_case.execute(ImproveAudioCommand(env.import_media(silent_video)), ctx)
    image = env.use_case.execute(ImproveAudioCommand(env.import_media(picture)), ctx)
    missing = env.use_case.execute(ImproveAudioCommand("missing"), ctx)
    unknown = env.use_case.execute(ImproveAudioCommand(audio_id, "no-such-profile"), ctx)
    invalid = env.use_case.execute(
        ImproveAudioCommand(audio_id, overrides={"mastering.target_lufs": 5.0}), ctx
    )

    assert isinstance(no_audio, Err) and isinstance(no_audio.error, UnreadableSource)
    for failure in (image, missing, unknown, invalid):
        assert isinstance(failure, Err)
    assert isinstance(image.error, ValidationError)  # type: ignore[union-attr]
    assert isinstance(unknown.error, ValidationError)  # type: ignore[union-attr]
    assert isinstance(invalid.error, ValidationError)  # type: ignore[union-attr]
    assert env.total_runs == 0


# --- reuse and determinism ---------------------------------------------------------------------
def test_identical_requests_reuse_the_result_also_after_a_restart(env: Env) -> None:
    source_id = import_voice(env, with_noise(voice(), -40.0))
    first = env.improve(source_id)
    runs = env.total_runs

    again = env.improve(source_id)
    restarted = env.build()
    after_restart = env.improve(source_id, use_case=restarted)

    assert (first.created, again.created, after_restart.created) == (True, False, False)
    assert again.asset.id == after_restart.asset.id == first.asset.id
    assert env.total_runs == 0 and runs > 0  # the restarted service ran no engine at all
    assert after_restart.provenance == first.provenance
    assert after_restart.analysis_after == first.analysis_after
    assert after_restart.checks == first.checks
    assert len(env.library.list_derived(source_id)) == 1


def test_a_different_profile_or_setting_is_a_different_result(env: Env) -> None:
    source_id = import_voice(env, voice())

    youtube = env.improve(source_id)
    podcast = env.improve(source_id, "podcast")
    tuned = env.improve(source_id, overrides={"mastering.target_lufs": -18.0})

    assert len({youtube.asset.id, podcast.asset.id, tuned.asset.id}) == 3
    assert podcast.analysis_after.integrated_lufs == pytest.approx(-16.0, abs=0.6)
    assert tuned.analysis_after.integrated_lufs == pytest.approx(-18.0, abs=0.6)


def test_a_better_engine_for_a_stage_retires_old_results(env: Env) -> None:
    source_id = import_voice(env, with_noise(voice(), -40.0))
    first = env.improve(source_id)

    env.use_case = env.build([ScriptedEngine(Stage.NOISE_REDUCTION)])
    second = env.improve(source_id)

    assert second.created
    assert second.asset.id != first.asset.id


def test_every_engine_is_deterministic(env: Env) -> None:
    from media_house.modules.audio_improvement.infrastructure.ffmpeg_stages import (
        FfmpegNoiseReducer,
    )
    from media_house.modules.audio_improvement.infrastructure.spectral_dereverb import (
        SpectralDereverb,
    )
    from media_house.shared.concurrency import CancellationToken

    source = save(env.tmp / "in.wav", reverberate(with_noise(voice(), -45.0), rt60=0.8))
    token = CancellationToken()
    noise = {"broadband": True, "strength": 0.6, "noise_floor_dbfs": -45.0}
    room = {"strength": 0.5, "rt60": 0.8}

    for engine, params in ((FfmpegNoiseReducer(env.tool), noise), (SpectralDereverb(), room)):
        first, second = env.tmp / "a.wav", env.tmp / "b.wav"
        engine.process(source, first, params, token)
        engine.process(source, second, params, token)
        assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize(
    ("engine_name", "params"),
    [
        ("FfmpegNoiseReducer", {"broadband": True, "strength": 0.6, "noise_floor_dbfs": -40.0}),
        ("FfmpegNoiseReducer", {"broadband": False, "hum_hz": 50.0}),
        ("FfmpegDeclipper", {}),
        ("FfmpegEqualizer", {"highpass_hz": 80.0, "mud_center_hz": 300.0, "mud_gain_db": -3.0}),
        (
            "FfmpegCompressor",
            {"ratio": 2.0, "attack_ms": 15.0, "release_ms": 150.0, "threshold_dbfs": -30.0},
        ),
        ("FfmpegDeEsser", {"intensity": 0.5, "max_reduction": 0.5}),
        ("FfmpegMastering", {"gain_db": 6.0, "ceiling_dbtp": -1.0, "release_ms": 60.0}),
        ("SpectralDereverb", {"strength": 0.5, "rt60": 1.0}),
    ],
)
def test_no_engine_shifts_or_trims_the_audio(
    env: Env, engine_name: str, params: dict[str, object]
) -> None:
    from media_house.modules.audio_improvement.infrastructure import (
        ffmpeg_stages,
        spectral_dereverb,
    )
    from media_house.shared.concurrency import CancellationToken

    module = spectral_dereverb if engine_name == "SpectralDereverb" else ffmpeg_stages
    engine = (
        getattr(module, engine_name)()
        if engine_name == "SpectralDereverb"
        else getattr(module, engine_name)(env.tool)
    )
    source = save(env.tmp / "in.wav", with_noise(voice(), -40.0))
    destination = env.tmp / "out.wav"

    engine.process(source, destination, params, CancellationToken())

    before, _ = read_wav(source)
    after, _ = read_wav(destination)
    assert len(after) == len(before)  # to the sample: nothing trimmed, nothing padded
    lags = signal.correlation_lags(len(before), len(after))
    lag = int(lags[np.argmax(signal.correlate(before[:, 0], after[:, 0], method="fft"))])
    assert abs(lag) <= 8  # filter phase only (< 0.2 ms), never a latency
