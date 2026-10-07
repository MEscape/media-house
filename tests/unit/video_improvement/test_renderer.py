"""Command construction and hardware choice, with a fake process runner (no FFmpeg needed)."""

from collections.abc import Callable
from pathlib import Path

import pytest

from media_house.core.application.ports import (
    OutputLine,
    OutputStream,
    ProcessResult,
    ProcessSpec,
)
from media_house.modules.video_improvement.application.ports import RenderRequest
from media_house.modules.video_improvement.domain.planning import (
    ColorPlan,
    DenoisePlan,
    PlannedOperation,
    ProcessingPlan,
    SharpenPlan,
)
from media_house.modules.video_improvement.domain.profiles import (
    apply_overrides,
    resolve_processing_profile,
)
from media_house.modules.video_improvement.domain.settings import ProcessingProfile
from media_house.modules.video_improvement.domain.source import get_source_profile
from media_house.modules.video_improvement.domain.values import (
    JsonValue,
    ProcessingStage,
    StageStatus,
)
from media_house.modules.video_improvement.infrastructure.ffmpeg_renderer import (
    FfmpegRenderer,
    denoise_filter,
    pixel_format,
    sharpen_filter,
)
from media_house.modules.video_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ProcessFailedError
from tests.support.video_fakes import REC709, facts

ENCODERS = (
    " V....D libx264              libx264 H.264\n"
    " V....D h264_nvenc           NVIDIA NVENC H.264\n"
    " V....D hevc_nvenc           NVIDIA NVENC hevc\n"
    " V....D libx265              libx265\n"
)


class FakeRunner:
    """Records every command; ``fail_when`` decides which ones fail."""

    def __init__(
        self,
        encoders: str = ENCODERS,
        fail_when: Callable[[ProcessSpec], bool] = lambda spec: False,
    ) -> None:
        self.calls: list[ProcessSpec] = []
        self.progress_lines: list[str] = []
        self._encoders = encoders
        self._fail_when = fail_when

    def run(
        self,
        spec: ProcessSpec,
        *,
        cancellation: CancellationToken | None = None,
        on_output: Callable[[OutputLine], None] | None = None,
    ) -> ProcessResult:
        self.calls.append(spec)
        args = list(spec.args)
        if "-version" in args:
            return ProcessResult(0, "ffmpeg version 7.1.1 Copyright\n", "", 0.0)
        if "-encoders" in args:
            return ProcessResult(0, self._encoders, "", 0.0)
        if on_output is not None and "-progress" in args:
            for text in self.progress_lines:
                on_output(OutputLine(OutputStream.STDOUT, text))
        if self._fail_when(spec):
            if spec.check:
                raise ProcessFailedError(spec.executable, 1, "boom")
            return ProcessResult(1, "", "boom", 0.0)
        return ProcessResult(0, "", "", 0.0)

    def trials(self) -> list[ProcessSpec]:
        return [c for c in self.calls if "color=c=black:s=256x256:r=25:d=0.2" in c.args]

    def renders(self) -> list[list[str]]:
        return [list(c.args) for c in self.calls if "-map" in c.args]


def renderer(runner: FakeRunner) -> FfmpegRenderer:
    return FfmpegRenderer(FfmpegTool(runner))


def profile(**overrides: JsonValue) -> ProcessingProfile:  # keyword names are dotted paths
    return apply_overrides(
        get_source_profile("rec709"),
        resolve_processing_profile("natural"),
        overrides,
    ).processing


def encoder(r: FfmpegRenderer, p: ProcessingProfile, **video: object) -> str:
    return r.encoder_for(p, facts(**video), CancellationToken())


def operation(stage: ProcessingStage) -> PlannedOperation:
    return PlannedOperation(stage, stage.value, StageStatus.APPLIED, "test")


def request(
    tmp_path: Path,
    *,
    stages: tuple[ProcessingStage, ...] = (ProcessingStage.COLOR,),
    p: ProcessingProfile | None = None,
    **video: object,
) -> RenderRequest:
    plan = ProcessingPlan(
        tuple(operation(s) for s in stages),
        ColorPlan(REC709, exposure_stops=0.5) if ProcessingStage.COLOR in stages else None,
        DenoisePlan(0.5) if ProcessingStage.DENOISE in stages else None,
        SharpenPlan(0.4) if ProcessingStage.SHARPEN in stages else None,
    )
    lut = tmp_path / "grade.cube" if ProcessingStage.COLOR in stages else None
    return RenderRequest(
        tmp_path / "in.mp4",
        tmp_path / "out.mp4",
        facts(**video),
        plan,
        p or profile(),
        lut,
    )


def option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


class TestEncoderChoice:
    def test_the_gpu_is_used_when_it_is_present_and_proven(self) -> None:
        runner = FakeRunner()

        assert encoder(renderer(runner), profile()) == "h264_nvenc"

    def test_cpu_mode_never_looks_at_the_gpu(self) -> None:
        runner = FakeRunner()

        assert encoder(renderer(runner), profile(**{"execution.hardware": "cpu"})) == "libx264"
        assert not runner.trials()

    def test_a_gpu_encoder_missing_from_the_build_means_the_cpu(self) -> None:
        runner = FakeRunner(encoders=" V....D libx264              libx264\n")

        assert encoder(renderer(runner), profile()) == "libx264"
        assert not runner.trials()

    def test_a_gpu_that_fails_the_trial_encode_means_the_cpu_and_is_asked_only_once(self) -> None:
        runner = FakeRunner(fail_when=lambda spec: "h264_nvenc" in spec.args)
        r = renderer(runner)

        assert [encoder(r, profile()) for _ in range(3)] == ["libx264"] * 3
        assert len(runner.trials()) == 1

    @pytest.mark.parametrize(
        "video",
        [
            {"bit_depth": 10, "pixel_format": "yuv420p10le"},  # NVENC H.264 is 8-bit only
            {"pixel_format": "yuv422p"},  # NVENC encodes 4:2:0 only
            {"pixel_format": "yuv444p"},
        ],
    )
    def test_formats_the_gpu_cannot_encode_go_to_the_cpu(self, video: dict[str, object]) -> None:
        runner = FakeRunner()

        assert encoder(renderer(runner), profile(), **video) == "libx264"
        assert not runner.trials()

    def test_hevc_on_the_gpu_keeps_ten_bit(self) -> None:
        p = profile(**{"output.codec": "h265"})

        assert (
            encoder(renderer(FakeRunner()), p, bit_depth=10, pixel_format="yuv420p10le")
            == "hevc_nvenc"
        )

    def test_prores_is_always_software(self) -> None:
        assert encoder(renderer(FakeRunner()), profile(**{"output.codec": "prores"})) == "prores_ks"

    def test_the_encoder_is_part_of_the_renderer_identity_story(self) -> None:
        assert renderer(FakeRunner()).identity.startswith("ffmpeg-renderer-")
        assert "7.1.1" in renderer(FakeRunner()).identity


class TestPixelFormat:
    @pytest.mark.parametrize(
        ("source", "depth", "expected"),
        [
            ("yuv420p", 8, "yuv420p"),
            ("yuv420p10le", 10, "yuv420p10le"),
            ("yuv422p10le", 10, "yuv422p10le"),
            ("yuv444p", 8, "yuv444p"),
            (None, None, "yuv420p"),
        ],
    )
    def test_the_source_layout_and_depth_are_kept(
        self, source: str | None, depth: int | None, expected: str
    ) -> None:
        assert pixel_format(profile(), facts(pixel_format=source, bit_depth=depth)) == expected

    def test_a_configured_depth_wins(self) -> None:
        assert pixel_format(profile(**{"output.bit_depth": 10}), facts()) == "yuv420p10le"

    def test_prores_is_ten_bit_422(self) -> None:
        assert pixel_format(profile(**{"output.codec": "prores"}), facts()) == "yuv422p10le"


class TestCommand:
    def test_the_chain_runs_denoise_then_colour_then_sharpen_then_tags(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner()
        stages = (ProcessingStage.DENOISE, ProcessingStage.COLOR, ProcessingStage.SHARPEN)

        renderer(runner).render(request(tmp_path, stages=stages), CancellationToken())

        graph = option(runner.renders()[0], "-vf")
        assert graph.index("hqdn3d") < graph.index("lut3d") < graph.index("unsharp")
        assert graph.index("unsharp") < graph.index("setparams")
        assert "interp=tetrahedral" in graph
        assert "format=gbrp16le" in graph  # the colour maths runs in 16-bit, not 8

    def test_the_colour_stage_converts_with_the_sources_own_matrix_and_range(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner()

        renderer(runner).render(
            request(tmp_path, color_range="pc", color_matrix="bt2020nc"), CancellationToken()
        )

        graph = option(runner.renders()[0], "-vf")
        assert "in_range=pc" in graph
        assert "in_color_matrix=bt2020" in graph
        assert "out_color_matrix=bt709:out_range=tv" in graph
        assert "colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv" in graph

    def test_without_the_colour_stage_the_pixels_are_not_converted_to_rgb(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner()

        renderer(runner).render(
            request(tmp_path, stages=(ProcessingStage.DENOISE,)), CancellationToken()
        )

        graph = option(runner.renders()[0], "-vf")
        assert "lut3d" not in graph
        assert "gbrp16le" not in graph
        assert "color_trc=bt709" in graph  # the source's own tags are carried over

    def test_audio_metadata_chapters_and_timestamps_are_preserved(self, tmp_path: Path) -> None:
        runner = FakeRunner()

        renderer(runner).render(request(tmp_path, timecode="01:02:03;04"), CancellationToken())

        args = runner.renders()[0]
        assert option(args, "-c:a") == "copy"
        assert "0:a?" in args
        assert option(args, "-map_metadata") == "0"
        assert option(args, "-map_chapters") == "0"
        assert option(args, "-fps_mode") == "passthrough"
        assert option(args, "-enc_time_base") == "demux"
        assert option(args, "-timecode") == "01:02:03;04"

    def test_rotation_is_kept_as_a_display_matrix_not_baked_into_the_pixels(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner()

        renderer(runner).render(request(tmp_path, rotation=90), CancellationToken())
        renderer(runner).render(request(tmp_path, rotation=0), CancellationToken())

        rotated, upright = runner.renders()
        assert "-noautorotate" in rotated
        assert "-noautorotate" not in upright

    def test_the_gpu_decodes_and_encodes_when_it_is_in_use(self, tmp_path: Path) -> None:
        runner = FakeRunner()

        renderer(runner).render(request(tmp_path), CancellationToken())

        args = runner.renders()[0]
        assert option(args, "-hwaccel") == "cuda"
        assert option(args, "-c:v") == "h264_nvenc"

    def test_the_cpu_path_uses_x264_with_the_configured_quality(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        settings: dict[str, JsonValue] = {
            "execution.hardware": "cpu",
            "output.crf": 14,
            "output.preset": "slow",
        }
        p = profile(**settings)

        renderer(runner).render(request(tmp_path, p=p), CancellationToken())

        args = runner.renders()[0]
        assert "-hwaccel" not in args
        assert option(args, "-c:v") == "libx264"
        assert (option(args, "-crf"), option(args, "-preset")) == ("14", "slow")

    def test_the_lut_is_found_next_to_the_command_not_by_an_absolute_path(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner()

        renderer(runner).render(request(tmp_path), CancellationToken())

        call = next(c for c in runner.calls if "-map" in c.args)
        assert call.cwd == tmp_path
        assert "lut3d=file=grade.cube" in option(list(call.args), "-vf")

    def test_prores_goes_into_a_mov_without_faststart(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        p = profile(**{"output.codec": "prores"})

        renderer(runner).render(request(tmp_path, p=p), CancellationToken())

        args = runner.renders()[0]
        assert option(args, "-c:v") == "prores_ks"
        assert option(args, "-pix_fmt") == "yuv422p10le"
        assert "-movflags" not in args
        assert p.output.suffix == ".mov"

    def test_a_failed_gpu_render_is_repeated_on_the_cpu(self, tmp_path: Path) -> None:
        runner = FakeRunner(
            fail_when=lambda spec: "-map" in spec.args and "h264_nvenc" in spec.args
        )

        renderer(runner).render(request(tmp_path), CancellationToken())

        first, second = runner.renders()
        assert option(first, "-c:v") == "h264_nvenc"
        assert option(second, "-c:v") == "libx264"
        assert "-hwaccel" not in second

    def test_a_failing_cpu_render_is_an_error(self, tmp_path: Path) -> None:
        runner = FakeRunner(fail_when=lambda spec: "-map" in spec.args)
        p = profile(**{"execution.hardware": "cpu"})

        with pytest.raises(ProcessFailedError):
            renderer(runner).render(request(tmp_path, p=p), CancellationToken())


class TestProgress:
    def test_the_finished_fraction_is_reported_while_rendering(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        runner.progress_lines = [
            "frame=10",
            "out_time_us=2500000",  # a quarter of the 10 s clip
            "out_time_us=5000000",
            "out_time_us=N/A",
            "out_time_us=99000000",  # never more than 100%
        ]
        seen: list[float] = []

        renderer(runner).render(request(tmp_path), CancellationToken(), seen.append)

        assert seen == [0.25, 0.5, 1.0]
        assert "-progress" in runner.renders()[0]

    def test_without_a_listener_no_progress_is_requested(self, tmp_path: Path) -> None:
        runner = FakeRunner()

        renderer(runner).render(request(tmp_path), CancellationToken())

        assert "-progress" not in runner.renders()[0]


class TestFilters:
    def test_stronger_denoising_means_stronger_filter_values(self) -> None:
        def values(strength: float) -> list[float]:
            return [float(v) for v in denoise_filter(strength).split("=")[1].split(":")]

        weak, strong = values(0.2), values(0.8)

        assert all(s > w for w, s in zip(weak, strong, strict=True))
        assert len(weak) == 4

    def test_sharpening_touches_luma_only(self) -> None:
        assert sharpen_filter(0.5) == "unsharp=lx=5:ly=5:la=0.500:cx=3:cy=3:ca=0"
