"""Video Improvement against real FFmpeg: what it fixes, what it leaves alone, what it preserves."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from media_house.modules.video_improvement.application.contracts import (
    ImproveVideoCommand,
    read_provenance,
)
from media_house.modules.video_improvement.domain.color import ColorSpec, Primaries, Transfer
from media_house.modules.video_improvement.domain.errors import InvalidLut, UnsupportedSource
from media_house.modules.video_improvement.domain.measurements import SceneMeasurements
from media_house.modules.video_improvement.domain.planning import ColorPlan
from media_house.modules.video_improvement.domain.source import VideoFacts
from media_house.modules.video_improvement.domain.values import (
    JsonValue,
    ProcessingStage,
    StageStatus,
)
from media_house.modules.video_improvement.infrastructure import color_science as cs
from media_house.modules.video_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.modules.video_improvement.infrastructure.scene_analyzer import NumpySceneAnalyzer
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, Ok, OperationCancelledError, ValidationError
from tests.integration.video_improvement.conftest import Env
from tests.support import video_media as vm
from tests.support.media_factories import ffmpeg, make_wav

pytestmark = pytest.mark.integration

FAST: dict[str, JsonValue] = {"execution.sample_count": 8}
HERO9 = {"firmware": "HD9.01.01.60.00"}

Frame = Callable[[int], vm.Image]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stored_path(env: Env, asset_id: str) -> Path:
    path = env.library.local_path(asset_id)
    assert isinstance(path, Ok)
    return path.value


def facts_of(env: Env, asset_id: str) -> VideoFacts:
    return FfmpegTool(env.runner).probe_video(stored_path(env, asset_id), CancellationToken())


def measured(env: Env, asset_id: str, tmp_path: Path) -> SceneMeasurements:
    tool = FfmpegTool(env.runner)
    facts = tool.probe_video(stored_path(env, asset_id), CancellationToken())
    analyzer = NumpySceneAnalyzer(tool)
    work = tmp_path / f"m-{asset_id}"
    work.mkdir()
    samples = analyzer.sample(stored_path(env, asset_id), facts, 12, work, CancellationToken())
    return analyzer.measure(samples, facts.color)


def renders(env: Env) -> int:
    """How many times a video was encoded."""
    return sum(1 for _, args in env.runner.calls if "-map" in args and "-c:v" in args)


class TestLeavesGoodFootageAlone:
    def test_well_exposed_neutral_footage_is_not_touched(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip("studio", lambda i: base_scene)
        env.runner.reset()

        result = env.improve(source, processing_profile="studio")

        assert not result.changed
        assert result.asset.id == source  # no new asset: the source IS the result
        assert result.plan is not None
        assert result.plan.stages_applied == ()
        assert renders(env) == 0
        assert all(o.status is StageStatus.SKIPPED for o in result.provenance.operations)

    def test_the_decision_is_explained_by_measured_numbers(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        result = env.improve(env.clip("good", lambda i: base_scene))

        exposure = result.provenance.operation("exposure")
        assert exposure is not None
        assert exposure.measured["linear_p50"] == pytest.approx(0.18, abs=0.04)
        assert result.provenance.before == result.provenance.after


class TestCorrectsWhatIsWrong:
    def test_underexposed_footage_with_a_cast_is_brightened_and_balanced(
        self, env: Env, base_scene: vm.Image, tmp_path: Path
    ) -> None:
        frame: Frame = lambda i: vm.tint(vm.exposure(base_scene, -1.5), 1.15, 0.85)  # noqa: E731
        source = env.clip("dark", frame)

        result = env.improve(source)

        assert result.changed
        assert result.asset.id != source
        assert result.asset.derivation is not None
        assert result.asset.derivation.source_asset_id == source
        before = measured(env, source, tmp_path)
        after = measured(env, result.asset.id, tmp_path)
        assert after.linear_p50 > before.linear_p50 * 1.8  # about +1 stop
        assert None not in (before.cast_red, before.cast_blue, after.cast_red, after.cast_blue)
        assert abs(cast(float, after.cast_red) - 1.0) < abs(cast(float, before.cast_red) - 1.0)
        assert abs(cast(float, after.cast_blue) - 1.0) < abs(cast(float, before.cast_blue) - 1.0)
        assert result.provenance.applied(ProcessingStage.COLOR)
        assert not result.provenance.applied(ProcessingStage.DENOISE)  # clean: not touched

    def test_oversaturated_footage_is_brought_into_the_profiles_band(
        self, env: Env, base_scene: vm.Image, tmp_path: Path
    ) -> None:
        source = env.clip("vivid", lambda i: vm.saturate(base_scene, 2.2))

        result = env.improve(source)

        before = measured(env, source, tmp_path)
        after = measured(env, result.asset.id, tmp_path)
        assert after.mean_saturation < before.mean_saturation
        operation = result.provenance.operation("saturation")
        assert operation is not None and operation.status is StageStatus.APPLIED

    def test_noisy_footage_is_denoised_and_keeps_its_detail(
        self, env: Env, base_scene: vm.Image, tmp_path: Path
    ) -> None:
        source = env.clip("noisy", lambda i: vm.noisy(base_scene, 0.03, i), frames=24)

        result = env.improve(source)

        before = measured(env, source, tmp_path)
        after = measured(env, result.asset.id, tmp_path)
        assert result.provenance.applied(ProcessingStage.DENOISE)
        assert after.noise_sigma < before.noise_sigma * 0.75
        assert after.sharpness > before.sharpness * 0.5

    def test_soft_footage_is_sharpened_when_the_profile_says_it_is_soft(
        self, env: Env, base_scene: vm.Image, tmp_path: Path
    ) -> None:
        source = env.clip("soft", lambda i: vm.blur(base_scene, 2.5))

        result = env.improve(source)

        before = measured(env, source, tmp_path)
        after = measured(env, result.asset.id, tmp_path)
        assert result.provenance.applied(ProcessingStage.SHARPEN)
        assert after.sharpness > before.sharpness

    def test_flat_footage_gets_contrast_without_clipping(
        self, env: Env, base_scene: vm.Image, tmp_path: Path
    ) -> None:
        source = env.clip("flat", lambda i: vm.flatten(base_scene, 0.25, 0.7))

        result = env.improve(source)

        before = measured(env, source, tmp_path)
        after = measured(env, result.asset.id, tmp_path)
        assert after.tonal_range > before.tonal_range
        assert after.highlight_clip <= before.highlight_clip + 0.01

    def test_the_same_footage_is_treated_alike_in_every_clip(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        first = env.clip("take1", lambda i: vm.exposure(base_scene, -1.5))
        second = env.clip("take2", lambda i: vm.exposure(base_scene, -1.5) * 0.999)

        a, b = env.improve(first), env.improve(second)

        stops = [r.provenance.operation("exposure").parameters["stops"] for r in (a, b)]  # type: ignore[union-attr]
        assert stops[0] == pytest.approx(stops[1], abs=0.05)


class TestScenarios:
    def test_the_outdoor_profile_rolls_highlights_off_earlier_than_the_studio_profile(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        sunny = env.clip("sun", lambda i: vm.exposure(base_scene, 0.7))

        outdoor = env.improve(sunny, processing_profile="outdoor")
        studio = env.improve(sunny, processing_profile="studio")

        assert outdoor.plan is not None and studio.plan is not None
        outdoor_roll = outdoor.plan.operation("highlight_rolloff")
        assert outdoor_roll is not None
        assert outdoor_roll.parameters.get("knee") == 0.72
        studio_roll = studio.plan.operation("highlight_rolloff")
        assert studio_roll is not None
        assert studio_roll.parameters.get("knee") in (None, 0.8)


class TestFlatAndLogFootage:
    def test_gopro_flat_footage_becomes_a_normal_rec709_picture(
        self, env: Env, base_scene: vm.Image, tmp_path: Path
    ) -> None:
        flat = vm.encode_log(base_scene, vm.PROTUNE_FLAT)
        source = env.clip("flat_gopro", lambda i: flat, tags="")

        result = env.improve(source, source_profile="gopro_flat")

        assert result.provenance.input_color_space == "gopro_protune/bt709"
        assert result.provenance.working_color_space == "linear_rec709"
        assert result.provenance.output_color_space == "bt709/bt709"
        assert result.provenance.source_profile_origin == "explicit"
        after = measured(env, result.asset.id, tmp_path)
        original = vm.scene()
        original_median = float(
            (original @ [0.2126, 0.7152, 0.0722])[original.shape[0] // 4 :].mean()
        )
        assert after.luma_p50 == pytest.approx(0.47, abs=0.12)  # a normal display image
        assert after.tonal_range > 0.5  # no longer flat
        assert original_median > 0

    def test_the_output_is_tagged_so_that_players_and_editors_read_it_correctly(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        flat = vm.encode_log(base_scene, vm.PROTUNE_FLAT)
        source = env.clip("flat2", lambda i: flat, tags="")

        result = env.improve(source, source_profile="gopro_flat")

        output = facts_of(env, result.asset.id)
        assert output.color == ColorSpec(Transfer.BT709, Primaries.BT709)
        assert (output.color_range, output.color_matrix) == ("tv", "bt709")

    def test_an_unknown_log_curve_can_be_declared_by_the_caller(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip(
            "untagged_log", lambda i: vm.encode_log(base_scene, vm.PROTUNE_FLAT), tags=""
        )

        result = env.improve(
            source,
            source_profile="generic",
            overrides={
                "source.input_color.transfer": "gopro_protune",
                "source.input_color.primaries": "bt709",
            },
        )

        assert result.provenance.applied(ProcessingStage.COLOR)
        assert result.provenance.input_color_space == "gopro_protune/bt709"


class TestOneArchitectureForEveryCamera:
    """A studio clip, a GoPro clip and a cinema-camera log clip take the same code path."""

    def test_a_cinema_camera_log_clip_is_handled_by_a_profile_not_by_new_code(
        self, env: Env, base_scene: vm.Image, tmp_path: Path
    ) -> None:
        slog = ColorSpec(Transfer.SLOG3, Primaries.SGAMUT3_CINE)
        source = env.clip("slog", lambda i: vm.encode_log(base_scene, slog), tags="")

        result = env.improve(source, source_profile="sony_slog3")

        assert result.provenance.input_color_space == "slog3/sgamut3_cine"
        assert result.provenance.output_color_space == "bt709/bt709"
        after = measured(env, result.asset.id, tmp_path)
        assert 0.3 < after.luma_p50 < 0.65  # a normal display image, not a grey log picture
        assert after.tonal_range > 0.45

    def test_the_three_cameras_differ_only_in_the_data_that_describes_them(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        studio = env.clip("studio_dark", lambda i: vm.exposure(base_scene, -1.5))
        gopro = env.clip("gopro_dark", lambda i: vm.exposure(base_scene, -1.5), metadata=HERO9)
        log = env.clip(
            "log_dark",
            lambda i: vm.encode_log(vm.exposure(base_scene, -1.5), vm.PROTUNE_FLAT),
            tags="",
        )

        results = [
            env.improve(studio, source_profile="studio_camera"),
            env.improve(gopro),  # identified from its metadata
            env.improve(log, source_profile="gopro_flat"),
        ]

        assert [r.provenance.source_profile for r in results] == [
            "studio_camera",
            "gopro_hero_9",
            "gopro_flat",
        ]
        assert [r.provenance.processing_profile for r in results] == [
            "studio",
            "outdoor",
            "outdoor",
        ]
        for result in results:
            assert result.changed
            assert result.provenance.output_color_space == "bt709/bt709"
            assert all(c.passed for c in result.checks if c.kind.value == "hard")


class TestUnknownAndUnsupported:
    def test_unknown_colour_is_not_guessed_but_denoising_still_runs(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        small = base_scene[::2, ::2]  # 320x180: untagged SD-sized video has no colour convention
        source = env.clip("sd", lambda i: vm.noisy(small, 0.03, i), tags="", frames=24)

        result = env.improve(source)

        assert result.provenance.source_profile == "generic"
        assert result.provenance.input_color_space == "unknown/unknown"
        color = result.provenance.operation("color")
        assert color is not None and color.status is StageStatus.SKIPPED
        assert "not guessed" in color.reason
        assert result.provenance.applied(ProcessingStage.DENOISE)
        assert result.provenance.working_color_space == "none"

    def test_hdr_video_is_refused_not_degraded(self, env: Env, base_scene: vm.Image) -> None:
        source = env.clip(
            "hdr",
            lambda i: base_scene,
            tags="setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=smpte2084:range=tv",
        )

        result = env.use_case.execute(ImproveVideoCommand(source), JobContext.detached())

        assert isinstance(result, Err)
        assert isinstance(result.error, UnsupportedSource)
        assert "HDR" in result.error.user_message

    def test_audio_only_media_is_refused(self, env: Env) -> None:
        voice = make_wav(env.tmp / "voice.wav")
        imported = env.library.import_file(voice)
        assert isinstance(imported, Ok)

        result = env.use_case.execute(
            ImproveVideoCommand(imported.value.asset.id), JobContext.detached()
        )

        assert isinstance(result, Err)
        assert isinstance(result.error, ValidationError)

    def test_an_unknown_asset_is_an_error_result(self, env: Env) -> None:
        assert isinstance(
            env.use_case.execute(ImproveVideoCommand("missing"), JobContext.detached()), Err
        )

    def test_an_unknown_profile_name_is_an_error_result(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip("c", lambda i: base_scene)

        result = env.use_case.execute(
            ImproveVideoCommand(source, source_profile="nikon"), JobContext.detached()
        )

        assert isinstance(result, Err)
        assert "gopro_hero_9" in result.error.user_message


class TestPreservation:
    def test_size_rate_duration_frames_audio_and_timecode_survive(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip(
            "tc",
            lambda i: vm.exposure(base_scene, -1.5),
            extra=["-timecode", "01:02:03;04"],
        )
        before = facts_of(env, source)

        result = env.improve(source)

        after = facts_of(env, result.asset.id)
        assert (after.width, after.height) == (before.width, before.height)
        assert after.frame_rate == before.frame_rate  # exactly 30000/1001
        assert after.frame_count == before.frame_count
        assert after.duration == pytest.approx(before.duration, abs=0.05)
        assert after.audio_stream_count == before.audio_stream_count == 1
        assert after.timecode == before.timecode == "01:02:03;04"
        assert all(c.passed for c in result.checks if c.kind.value == "hard")

    def test_the_audio_is_copied_not_re_encoded(self, env: Env, base_scene: vm.Image) -> None:
        source = env.clip("a", lambda i: vm.exposure(base_scene, -1.5))
        original = vm.probe(stored_path(env, source))

        result = env.improve(source)

        improved = vm.probe(stored_path(env, result.asset.id))
        for key in ("codec_name=aac", "sample_rate=44100", "channels=1"):
            assert key in original
            assert key in improved
        assert "codec_name=h264" in improved

    def test_variable_frame_rate_timestamps_are_kept(self, env: Env, base_scene: vm.Image) -> None:
        source = env.clip(
            "vfr",
            lambda i: vm.exposure(base_scene, -1.5),
            frames=20,
            audio=False,
            fps="25",
            extra=[],
        )
        before = facts_of(env, source)

        result = env.improve(source)

        after = facts_of(env, result.asset.id)
        assert after.frame_count == before.frame_count
        assert after.duration == pytest.approx(before.duration, abs=0.05)

    def test_a_rotated_video_stays_rotated_with_its_stored_size(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        upright = env.clip("up", lambda i: vm.exposure(base_scene, -1.5), audio=False)
        rotated_path = env.tmp / "rotated.mp4"
        ffmpeg(
            "-display_rotation",
            "90",
            "-i",
            str(stored_path(env, upright)),
            "-c",
            "copy",
            str(rotated_path),
        )
        imported = env.library.import_file(rotated_path)
        assert isinstance(imported, Ok)
        source = imported.value.asset.id
        before = facts_of(env, source)
        assert before.rotation == 270

        result = env.improve(source)

        after = facts_of(env, result.asset.id)
        assert after.rotation == before.rotation
        assert (after.width, after.height) == (before.width, before.height)

    def test_a_clip_cut_without_re_encoding_keeps_its_visible_frames(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        # a stream copy that starts between key frames carries hidden pre-roll packets that no
        # decoder shows; they are not frames and must not count as lost
        full = env.clip(
            "full",
            lambda i: vm.exposure(base_scene, -1.5),
            frames=40,
            audio=False,
            extra=["-g", "10"],
        )
        cut_path = env.tmp / "cut.mp4"
        ffmpeg(
            "-ss",
            "0.55",
            "-t",
            "0.9",
            "-i",
            str(stored_path(env, full)),
            "-c",
            "copy",
            str(cut_path),
        )
        imported = env.library.import_file(cut_path)
        assert isinstance(imported, Ok)

        result = env.improve(imported.value.asset.id)

        before = facts_of(env, imported.value.asset.id)
        after = facts_of(env, result.asset.id)
        assert after.frame_count == before.frame_count
        assert all(c.passed for c in result.checks if c.kind.value == "hard")

    def test_progress_is_reported_through_the_render(self, env: Env, base_scene: vm.Image) -> None:
        source = env.clip("progress", lambda i: vm.exposure(base_scene, -1.5))
        seen: list[tuple[int, str]] = []

        class Recorder:
            def report(self, current: int, total: int | None = None, message: str = "") -> None:
                assert total == 100
                seen.append((current, message))

        ctx = JobContext("progress", JobContext.detached().cancellation, Recorder())
        result = env.use_case.execute(ImproveVideoCommand(source, overrides=FAST), ctx)

        assert isinstance(result, Ok)
        percents = [p for p, _ in seen]
        assert percents == sorted(percents)  # never goes backwards
        assert percents[0] <= 3 and percents[-1] >= 96
        assert any(m.startswith("Rendering") for _, m in seen)

    def test_the_original_is_never_modified(self, env: Env, base_scene: vm.Image) -> None:
        source = env.clip("orig", lambda i: vm.exposure(base_scene, -1.5))
        before = digest(stored_path(env, source))

        env.improve(source)

        assert digest(stored_path(env, source)) == before


class TestReuseAndIdentity:
    @pytest.fixture
    def dark(self, env: Env, base_scene: vm.Image) -> str:
        return env.clip("dark", lambda i: vm.exposure(base_scene, -1.5))

    def test_an_identical_request_is_answered_from_the_library(self, env: Env, dark: str) -> None:
        first = env.improve(dark)
        env.runner.reset()

        second = env.improve(dark)

        assert first.created
        assert not second.created
        assert second.asset.id == first.asset.id
        assert renders(env) == 0
        assert second.provenance == first.provenance

    def test_a_restarted_application_finds_it(self, env: Env, dark: str) -> None:
        first = env.improve(dark)
        env.runner.reset()

        again = env.improve(dark, use_case=env.build())

        assert again.asset.id == first.asset.id
        assert renders(env) == 0

    def test_a_different_setting_is_a_different_result(self, env: Env, dark: str) -> None:
        default = env.improve(dark)

        other = env.improve(dark, overrides={"color.exposure_strength": 0.4})

        assert other.created
        assert other.asset.id != default.asset.id

    def test_a_different_profile_is_a_different_result(self, env: Env, dark: str) -> None:
        natural = env.improve(dark, processing_profile="natural")
        social = env.improve(dark, processing_profile="social")

        assert social.asset.id != natural.asset.id

    def test_the_provenance_is_readable_without_this_module(self, env: Env, dark: str) -> None:
        result = env.improve(dark)

        stored = env.library.get(result.asset.id)
        assert isinstance(stored, Ok)
        record = read_provenance(stored.value.metadata)
        assert record == result.provenance
        assert record is not None
        assert record.processing_version == 1
        assert record.engines["encoder"] in {"libx264", "h264_nvenc"}
        assert record.source_asset_id == dark

    def test_a_damaged_record_is_recomputed(self, env: Env, dark: str) -> None:
        first = env.improve(dark)
        env.library.update_metadata(first.asset.id, {"video_processing": "damaged"})

        again = env.improve(dark)

        assert again.provenance.source_asset_id == dark


class TestLookLut:
    def write_look(self, path: Path, *, invert: bool) -> Path:
        table = cs.bake(
            cs.ColorTransform(ColorPlan(ColorSpec(Transfer.BT709, Primaries.BT709))), 17
        )
        cs.write_cube(path, 1.0 - table if invert else table, "look")
        return path

    def test_a_configured_look_is_applied_traceably(
        self, env: Env, base_scene: vm.Image, tmp_path: Path
    ) -> None:
        look = self.write_look(env.tmp / "look.cube", invert=True)
        source = env.clip("lookclip", lambda i: base_scene)

        result = env.improve(source, overrides={"look.lut_path": str(look), "look.strength": 1.0})

        assert result.changed
        assert result.provenance.look_sha256 == cs.read_cube(look).sha256
        operation = result.provenance.operation("look")
        assert (
            operation is not None
            and operation.parameters["sha256"] == result.provenance.look_sha256
        )
        before = measured(env, source, tmp_path)
        after = measured(env, result.asset.id, tmp_path)
        assert after.luma_p50 == pytest.approx(1.0 - before.luma_p50, abs=0.06)

    def test_changing_the_lut_content_changes_the_result_identity(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        look = env.tmp / "look.cube"
        source = env.clip("lookclip2", lambda i: base_scene)
        self.write_look(look, invert=True)
        first = env.improve(source, overrides={"look.lut_path": str(look)})

        self.write_look(look, invert=False)
        second = env.improve(source, overrides={"look.lut_path": str(look)})

        assert first.provenance.look_sha256 != second.provenance.look_sha256
        assert first.asset.id != second.asset.id

    def test_a_broken_lut_is_an_error_result(self, env: Env, base_scene: vm.Image) -> None:
        bad = env.tmp / "bad.cube"
        bad.write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="ascii")
        source = env.clip("lookclip3", lambda i: base_scene)

        result = env.use_case.execute(
            ImproveVideoCommand(source, overrides={"look.lut_path": str(bad)}),
            JobContext.detached(),
        )

        assert isinstance(result, Err)
        assert isinstance(result.error, InvalidLut)


class TestQualityGuard:
    def test_a_stage_that_makes_the_output_worse_is_left_out_and_the_video_rendered_again(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip("guard", lambda i: vm.exposure(base_scene, -1.5))
        env.runner.reset()

        # an extreme user contrast crushes the blacks; the guard forbids any such increase
        result = env.improve(
            source,
            overrides={"color.contrast": 2.0, "color.guard_max_crush_increase": 0.0},
        )

        bypassed = [o for o in result.provenance.operations if o.status is StageStatus.BYPASSED]
        assert bypassed
        assert all(o.stage is ProcessingStage.COLOR for o in bypassed)
        assert all("measured worse" in o.reason for o in bypassed)
        assert not result.provenance.applied(ProcessingStage.COLOR)
        assert not result.changed  # nothing else was needed: the source is returned
        assert renders(env) >= 1


class TestHardware:
    def test_cpu_mode_uses_the_software_encoder(self, env: Env, base_scene: vm.Image) -> None:
        source = env.clip("cpu", lambda i: vm.exposure(base_scene, -1.5))

        result = env.improve(source, overrides={"execution.hardware": "cpu"})

        assert result.provenance.engines["encoder"] == "libx264"
        assert "codec_name=h264" in vm.probe(stored_path(env, result.asset.id))

    def test_gpu_and_cpu_results_both_pass_the_same_verification(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip("both", lambda i: vm.exposure(base_scene, -1.5))

        gpu = env.improve(source)
        cpu = env.improve(source, overrides={"execution.hardware": "cpu"})

        for result in (gpu, cpu):
            assert all(c.passed for c in result.checks if c.kind.value == "hard")
        if gpu.provenance.engines["encoder"] == "h264_nvenc":
            assert gpu.asset.id != cpu.asset.id  # a different encoder is a different file

    def test_a_cancelled_job_does_nothing(self, env: Env, base_scene: vm.Image) -> None:
        source = env.clip("cancel", lambda i: vm.exposure(base_scene, -1.5))
        ctx = JobContext.detached()
        ctx.cancellation.cancel()
        env.runner.reset()

        with pytest.raises(OperationCancelledError):
            env.use_case.execute(ImproveVideoCommand(source), ctx)

        assert renders(env) == 0
