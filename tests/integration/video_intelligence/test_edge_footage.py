"""Footage and files that make naive analysis lie: rotation, odd shapes, VFR, damage, bad input."""

import subprocess
from pathlib import Path

import pytest

from media_house.modules.video_intelligence.application.analyze_video import AnalyzeVideoCommand
from media_house.modules.video_intelligence.application.contracts import (
    CameraMovement,
    InvalidProfile,
    VideoIntelligenceError,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, ValidationError
from tests.integration.video_intelligence.conftest import VideoEnv
from tests.support import vi_footage as vf
from tests.support.media_factories import make_wav

pytestmark = pytest.mark.integration


def ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],  # noqa: S607
        check=True,
    )


class TestGeometry:
    def test_a_portrait_clip_is_analysed_in_its_own_shape(
        self, venv: VideoEnv, tmp_path: Path
    ) -> None:
        picture = vf.texture(1, 180, 320)  # 180 wide, 320 high
        asset = venv.import_clip(vf.write(tmp_path / "portrait.mp4", lambda _i: picture, 40))

        analysis = venv.analyze(asset, "fast").analysis

        assert (analysis.source.width, analysis.source.height) == (180, 320)
        assert analysis.shots[0].camera.movement is CameraMovement.STATIC

    def test_rotation_metadata_is_applied_so_everything_is_in_display_orientation(
        self, venv: VideoEnv, tmp_path: Path
    ) -> None:
        plain = vf.write(tmp_path / "plain.mp4", vf.pan(2, 2.0), 50)
        rotated = tmp_path / "rotated.mp4"
        ffmpeg("-display_rotation:v:0", "90", "-i", str(plain), "-c", "copy", str(rotated))
        asset = venv.import_clip(rotated)

        analysis = venv.analyze(asset, "fast").analysis

        assert analysis.source.rotation in (90, 270)
        assert (analysis.source.width, analysis.source.height) == (180, 320)  # turned
        camera = analysis.shots[0].camera
        # a sideways pan in the stored picture is a vertical move in the displayed one
        assert camera.movement is CameraMovement.TILT

    def test_non_square_pixels_are_corrected_to_the_display_aspect(
        self, venv: VideoEnv, tmp_path: Path
    ) -> None:
        plain = vf.write(tmp_path / "plain.mp4", vf.still(1), 40)
        anamorphic = tmp_path / "anamorphic.mp4"
        ffmpeg("-i", str(plain), "-vf", "setsar=2/1", "-c:v", "libx264", str(anamorphic))
        asset = venv.import_clip(anamorphic)

        analysis = venv.analyze(asset, "triage").analysis

        assert analysis.source.width == 2 * analysis.source.height * 320 // 360 * 360 // 320 or (
            analysis.source.width > analysis.source.height * 2
        )


class TestTiming:
    def test_variable_frame_rate_keeps_exact_per_frame_timestamps(
        self, venv: VideoEnv, tmp_path: Path
    ) -> None:
        constant = vf.write(tmp_path / "constant.mp4", vf.still(1), 60)
        variable = tmp_path / "variable.mp4"
        # the first second at 30 fps, the rest at 15 fps
        ffmpeg(
            "-i", str(constant),
            "-vf", "setpts='if(lt(N,30),N/30,1+(N-30)/15)/TB'",
            "-fps_mode", "passthrough", "-video_track_timescale", "30000",
            "-c:v", "libx264", str(variable),
        )  # fmt: skip
        asset = venv.import_clip(variable)

        analysis = venv.analyze(asset, "triage").analysis

        end = analysis.shots[-1].range.end
        assert end.frame == 60
        assert end.timebase.denominator == 30000
        assert analysis.source.variable_frame_rate is True
        # 30 frames in the first second, 30 frames over the following two seconds
        assert end.seconds == pytest.approx(3.0, abs=0.1)
        assert end.pts != end.frame * 1000  # not the constant-rate guess

    def test_ntsc_frame_rates_keep_the_exact_time_base(
        self, venv: VideoEnv, tmp_path: Path
    ) -> None:
        asset = venv.import_clip(vf.write(tmp_path / "ntsc.mp4", vf.still(1), 60, fps="30000/1001"))

        analysis = venv.analyze(asset, "triage").analysis

        shot = analysis.shots[0]
        assert shot.range.end.frame == 60
        assert shot.range.end.seconds == pytest.approx(60 * 1001 / 30000, rel=0.001)


class TestDamageAndWrongInput:
    def test_a_truncated_file_gives_a_structured_outcome_and_never_a_crash(
        self, venv: VideoEnv, tmp_path: Path
    ) -> None:
        whole = tmp_path / "whole.mkv"
        ffmpeg(
            "-i", str(vf.write(tmp_path / "src.mp4", vf.pan(2, 1.0), 90)), "-c", "copy", str(whole)
        )
        data = whole.read_bytes()
        cut = tmp_path / "cut.mkv"
        cut.write_bytes(data[: len(data) * 2 // 3])
        imported = venv.library.import_file(cut)
        if isinstance(imported, Err):  # the library may refuse it outright: also a clear outcome
            assert imported.error.user_message
            return

        result = venv.build().execute(
            AnalyzeVideoCommand(imported.value.asset.id), JobContext.detached()
        )

        if isinstance(result, Err):
            assert isinstance(result.error, VideoIntelligenceError | ValidationError)
            assert result.error.user_message
        else:
            analysis = result.value.analysis
            assert analysis.shots[-1].range.end.frame < 90  # only what could be decoded
            assert any("damaged or truncated" in w or "decoder" in w for w in analysis.warnings)

    def test_audio_is_refused_with_a_clear_message(self, venv: VideoEnv, tmp_path: Path) -> None:
        wav = venv.library.import_file(make_wav(tmp_path / "tone.wav"))
        assert isinstance(wav, Ok)

        result = venv.build().execute(
            AnalyzeVideoCommand(wav.value.asset.id), JobContext.detached()
        )

        assert isinstance(result, Err)
        assert isinstance(result.error, ValidationError)
        assert "Only video files" in result.error.user_message

    def test_an_unknown_asset_is_a_not_found_result(self, venv: VideoEnv) -> None:
        result = venv.build().execute(AnalyzeVideoCommand("no-such-asset"), JobContext.detached())

        assert isinstance(result, Err)

    def test_an_unknown_profile_is_refused_before_any_work(
        self, venv: VideoEnv, tmp_path: Path
    ) -> None:
        asset = venv.import_clip(vf.write(tmp_path / "c.mp4", vf.still(1), 20))
        calls = len(venv.runner.calls)

        result = venv.build().execute(AnalyzeVideoCommand(asset, "turbo"), JobContext.detached())

        assert isinstance(result, Err) and isinstance(result.error, InvalidProfile)
        assert "triage" in result.error.user_message
        assert venv.decodes() == 0 and len(venv.runner.calls) == calls

    def test_a_very_short_clip_is_analysed_with_explicit_unknowns(
        self, venv: VideoEnv, tmp_path: Path
    ) -> None:
        asset = venv.import_clip(vf.write(tmp_path / "tiny.mp4", vf.still(1), 3))

        analysis = venv.analyze(asset, "standard").analysis

        shot = analysis.shots[0]
        assert shot.range.end.frame == 3
        assert shot.handles.state.value == "unknown" and shot.handles.reasons
        assert shot.camera.state.value == "unknown" and shot.camera.reasons
