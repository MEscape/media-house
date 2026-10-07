"""Media Inspection -> Video Improvement: stored facts are reused, nothing is assumed.

Each module also runs alone: without an inspection the source is probed by Video Improvement
itself, and the result is the same either way.
"""

from pathlib import Path

import pytest

from media_house.modules.media_inspection.application.inspect_media import (
    InspectMedia,
    InspectMediaCommand,
)
from media_house.modules.media_inspection.domain.values import Rational
from media_house.modules.media_inspection.infrastructure.ffprobe_prober import FfprobeProber
from media_house.modules.video_improvement.application.calibration import CalibrateProfile
from media_house.modules.video_improvement.domain.values import ProcessingStage
from media_house.modules.video_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.modules.video_improvement.infrastructure.scene_analyzer import NumpySceneAnalyzer
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from tests.integration.video_improvement.conftest import Env, build_env
from tests.support import video_media as vm

pytestmark = pytest.mark.integration

CPU = {"execution.hardware": "cpu"}
HERO9 = {"firmware": "HD9.01.01.60.00"}


def inspector(env: Env) -> InspectMedia:
    return InspectMedia(env.library, FfprobeProber(env.runner), env.paths, env.clock)


def inspect(env: Env, asset_id: str) -> None:
    result = inspector(env).execute(InspectMediaCommand(asset_id), JobContext.detached())
    assert isinstance(result, Ok), result


def source_path(env: Env, asset_id: str) -> Path:
    path = env.library.local_path(asset_id)
    assert isinstance(path, Ok)
    return path.value


def calibration(env: Env) -> CalibrateProfile:
    tool = FfmpegTool(env.runner)
    return CalibrateProfile(env.library, tool, NumpySceneAnalyzer(tool), env.paths)


def dark(base: vm.Image) -> vm.Image:
    return vm.tint(vm.exposure(base, -1.5), 1.12, 0.88)


class TestInspectionThenImprovement:
    def test_the_source_is_not_probed_again_and_the_result_is_identical(
        self, tmp_path: Path, base_scene: vm.Image
    ) -> None:
        alone = build_env(tmp_path / "alone")
        chained = build_env(tmp_path / "chained")
        alone_id = alone.clip("dark", lambda i: dark(base_scene))
        chained_id = chained.clip("dark", lambda i: dark(base_scene))
        inspect(chained, chained_id)
        chained.runner.reset()
        alone.runner.reset()

        reusing = chained.improve(
            chained_id, overrides=CPU, use_case=chained.build(inspector(chained))
        )
        independent = alone.improve(alone_id, overrides=CPU)

        assert chained.runner.count_on("ffprobe", source_path(chained, chained_id)) == 0
        assert alone.runner.count_on("ffprobe", source_path(alone, alone_id)) >= 1
        assert reusing.provenance.facts_source == "inspection"
        assert reusing.provenance.inspection_used
        assert independent.provenance.facts_source == "probe"
        assert not independent.provenance.inspection_used
        assert reusing.asset.checksum == independent.asset.checksum  # the same video, byte for byte
        assert reusing.provenance.operations == independent.provenance.operations
        assert reusing.provenance.before == independent.provenance.before

    def test_without_any_inspection_the_module_works_alone(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip("alone", lambda i: dark(base_scene))

        result = env.improve(source, use_case=env.build(inspector(env)))  # empty catalog

        assert result.changed
        assert result.provenance.facts_source == "probe"
        assert env.runner.count_on("ffprobe", source_path(env, source)) >= 1

    def test_the_cache_does_not_care_how_the_facts_arrived(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip("cached", lambda i: dark(base_scene))
        first = env.improve(source, overrides=CPU)
        inspect(env, source)
        env.runner.reset()

        again = env.improve(source, overrides=CPU, use_case=env.build(inspector(env)))

        assert not again.created
        assert again.asset.id == first.asset.id

    def test_inspection_after_improvement_describes_the_result_consistently(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip("chain", lambda i: dark(base_scene), extra=["-timecode", "10:00:00:00"])
        inspect(env, source)
        result = env.improve(source, use_case=env.build(inspector(env)))

        inspect(env, result.asset.id)

        before = inspector(env).find(source)
        after = inspector(env).find(result.asset.id)
        assert before is not None and after is not None
        video_before, video_after = before.inspection.primary_video, after.inspection.primary_video
        assert video_before is not None and video_after is not None
        assert (
            video_after.frame_rate.value == video_before.frame_rate.value == Rational(30000, 1001)
        )
        assert video_after.geometry.width == video_before.geometry.width
        assert video_after.timing is not None and video_before.timing is not None
        assert video_after.timing.packet_count == video_before.timing.packet_count
        assert str(after.inspection.observed.timecode.start) == "10:00:00:00"  # type: ignore[union-attr]
        assert (video_after.color.transfer.value, video_after.color.primaries.value) == (
            "bt709",
            "bt709",
        )
        assert after.inspection.status.usable
        assert len(after.inspection.audio_streams) == len(before.inspection.audio_streams)


class TestProfileDetection:
    def hero9(self, env: Env, base: vm.Image) -> str:
        return env.clip("hero9", lambda i: dark(base), metadata=HERO9)

    def test_camera_metadata_in_an_inspection_selects_the_profile(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = self.hero9(env, base_scene)
        inspect(env, source)

        result = env.improve(source, use_case=env.build(inspector(env)))

        assert result.provenance.source_profile == "gopro_hero_9"
        assert result.provenance.source_profile_origin == "inspection"
        assert "HD9.01.01.60.00" in result.provenance.source_profile_evidence
        assert result.provenance.processing_profile == "outdoor"  # the camera's default scenario

    def test_the_same_metadata_is_found_in_the_file_when_nothing_was_inspected(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = self.hero9(env, base_scene)

        result = env.improve(source)

        assert result.provenance.source_profile == "gopro_hero_9"
        assert result.provenance.source_profile_origin == "embedded_metadata"

    def test_an_explicit_profile_beats_the_cameras_metadata(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = self.hero9(env, base_scene)
        inspect(env, source)

        result = env.improve(
            source, source_profile="studio_camera", use_case=env.build(inspector(env))
        )

        assert result.provenance.source_profile == "studio_camera"
        assert result.provenance.source_profile_origin == "explicit"
        assert result.provenance.processing_profile == "studio"

    def test_an_explicit_processing_profile_beats_the_default_of_the_camera(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = self.hero9(env, base_scene)

        result = env.improve(source, processing_profile="social")

        assert result.provenance.source_profile == "gopro_hero_9"
        assert result.provenance.processing_profile == "social"

    def test_a_clip_without_camera_metadata_uses_its_declared_colour(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        source = env.clip("plain", lambda i: dark(base_scene))

        result = env.improve(source)

        assert result.provenance.source_profile == "rec709"
        assert result.provenance.source_profile_evidence == "declared bt709 colour tags"


class TestCalibration:
    def test_reference_footage_becomes_overrides_that_leave_similar_footage_alone(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        references = [
            env.clip("ref1", lambda i: base_scene),
            env.clip("ref2", lambda i: vm.exposure(base_scene, 0.15)),
        ]
        calibrate = calibration(env)

        result = calibrate.execute(references, JobContext.detached())

        assert isinstance(result, Ok)
        overrides = result.value.overrides
        assert set(result.value.measurements) == set(references)
        assert "color.target_median" in overrides
        # explicit, storable configuration: the same input always gives the same output
        similar = env.clip("like_refs", lambda i: vm.exposure(base_scene, 0.07))
        outcome = env.improve(similar, overrides=overrides)
        assert not outcome.changed

    def test_calibration_needs_references(self, env: Env) -> None:
        calibrate = calibration(env)

        result = calibrate.execute([], JobContext.detached())

        assert not isinstance(result, Ok)

    def test_calibrating_does_not_apply_any_processing(
        self, env: Env, base_scene: vm.Image
    ) -> None:
        reference = env.clip("ref", lambda i: base_scene)
        env.runner.reset()

        calibration(env).execute([reference], JobContext.detached())

        assert not any("-c:v" in args and "-map" in args for _, args in env.runner.calls)


def test_stage_names_are_stable_for_other_modules() -> None:
    assert [s.value for s in ProcessingStage] == ["denoise", "color", "sharpen"]
