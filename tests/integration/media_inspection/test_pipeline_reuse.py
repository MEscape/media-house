"""Media Inspection -> Audio Improvement -> Audio Intelligence.

Compatible facts are reused instead of probed again; every module still works when nothing was
inspected; and reusing facts never changes what a module produces.
"""

from pathlib import Path

import pytest

from media_house.modules.audio_improvement.application.improve_audio import (
    ImproveAudio,
    ImproveAudioCommand,
    ImprovementResult,
)
from media_house.modules.audio_improvement.infrastructure.ffmpeg_stages import (
    FfmpegCompressor,
    FfmpegDeclipper,
    FfmpegDeEsser,
    FfmpegEqualizer,
    FfmpegMastering,
    FfmpegNoiseReducer,
)
from media_house.modules.audio_improvement.infrastructure.ffmpeg_tool import FfmpegTool
from media_house.modules.audio_improvement.infrastructure.ffmpeg_transcoder import FfmpegTranscoder
from media_house.modules.audio_improvement.infrastructure.quality_analyzer import (
    SignalQualityAnalyzer,
)
from media_house.modules.audio_improvement.infrastructure.spectral_dereverb import SpectralDereverb
from media_house.modules.audio_intelligence.application.ports import PreparedAudio
from media_house.modules.audio_intelligence.application.transcribe_audio import TranscribeAudio
from media_house.modules.audio_intelligence.domain.values import PreparationConfig
from media_house.modules.audio_intelligence.infrastructure.ffmpeg_audio import FfmpegAudioPreparer
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Ok
from tests.integration.media_inspection.conftest import Env, build_env
from tests.support.analysis_fakes import raw_segments
from tests.support.audio_fakes import FakeEngine
from tests.support.inspection_media import Samples

pytestmark = pytest.mark.integration


def improver(env: Env, catalog: InspectionCatalog | None) -> ImproveAudio:
    tool = FfmpegTool(env.runner)
    return ImproveAudio(
        env.library,
        FfmpegTranscoder(tool),
        SignalQualityAnalyzer(tool),
        [
            FfmpegDeclipper(tool),
            FfmpegNoiseReducer(tool),
            SpectralDereverb(),
            FfmpegEqualizer(tool),
            FfmpegCompressor(tool),
            FfmpegDeEsser(tool),
            FfmpegMastering(tool),
        ],
        env.paths,
        catalog,
    )


def improve(env: Env, asset_id: str, catalog: InspectionCatalog | None) -> ImprovementResult:
    result = improver(env, catalog).execute(ImproveAudioCommand(asset_id), JobContext.detached())
    assert isinstance(result, Ok), result
    return result.value


def transcriber(env: Env, catalog: InspectionCatalog | None) -> TranscribeAudio:
    engine = FakeEngine(lambda config: raw_segments([[("Hello", 0.0, 0.5)]], language="en"))
    return TranscribeAudio(
        env.library, FfmpegAudioPreparer(env.runner), engine, env.paths, env.clock, catalog
    )


def prepare(
    env: Env, asset_id: str, catalog: InspectionCatalog | None
) -> tuple[MediaAssetDto, PreparedAudio]:
    source = env.library.get(asset_id)
    assert isinstance(source, Ok)
    prepared = transcriber(env, catalog).prepare_audio(
        source.value, PreparationConfig(), JobContext.detached()
    )
    assert isinstance(prepared, Ok), prepared
    asset, facts, _path = prepared.value
    return asset, facts


def source_path(env: Env, asset_id: str) -> Path:
    path = env.library.local_path(asset_id)
    assert isinstance(path, Ok)
    return path.value


class TestInspectionThenImprovement:
    def test_the_source_is_not_probed_again_and_the_result_is_identical(
        self, tmp_path: Path, samples: Samples
    ) -> None:
        alone = build_env(tmp_path / "alone", samples)
        chained = build_env(tmp_path / "chained", samples)
        alone_id = alone.import_media(samples.late_audio())
        chained_id = chained.import_media(samples.late_audio())
        chained.inspect(chained_id)
        chained.runner.reset()
        alone.runner.reset()

        reusing = improve(chained, chained_id, chained.use_case)
        independent = improve(alone, alone_id, None)

        path = source_path(chained, chained_id)
        assert chained.runner.count_on("ffprobe", path) == 0
        assert alone.runner.count_on("ffprobe", source_path(alone, alone_id)) >= 1
        assert reusing.asset.checksum == independent.asset.checksum  # same audio, byte for byte
        assert reusing.provenance.leading_pad_seconds == pytest.approx(
            independent.provenance.leading_pad_seconds
        )
        assert reusing.provenance.leading_pad_seconds == pytest.approx(0.5, abs=0.05)

    def test_without_any_inspection_the_module_works_alone(self, env: Env) -> None:
        asset_id = env.import_media(env.samples.audio_only())

        result = improve(env, asset_id, env.use_case)  # a catalog exists, nothing stored in it

        assert result.created
        assert env.runner.count_on("ffprobe", source_path(env, asset_id)) >= 1  # it probed itself

    def test_a_video_source_uses_the_inspected_audio_offset(self, env: Env) -> None:
        asset_id = env.import_media(env.samples.late_audio())
        env.inspect(asset_id)
        env.runner.reset()

        result = improve(env, asset_id, env.use_case)

        assert env.runner.count_on("ffprobe", source_path(env, asset_id)) == 0
        assert result.provenance.leading_pad_seconds == pytest.approx(0.5, abs=0.05)


class TestInspectionThenIntelligence:
    def test_the_source_is_not_probed_again_and_the_facts_are_the_same(
        self, tmp_path: Path, samples: Samples
    ) -> None:
        alone = build_env(tmp_path / "alone", samples)
        chained = build_env(tmp_path / "chained", samples)
        alone_id = alone.import_media(samples.late_audio())
        chained_id = chained.import_media(samples.late_audio())
        chained.inspect(chained_id)
        chained.runner.reset()
        alone.runner.reset()

        _, reusing = prepare(chained, chained_id, chained.use_case)
        _, independent = prepare(alone, alone_id, None)

        assert chained.runner.count_on("ffprobe", source_path(chained, chained_id)) == 0
        assert alone.runner.count_on("ffprobe", source_path(alone, alone_id)) >= 1
        assert reusing == independent
        assert reusing.audio_offset == pytest.approx(0.5, abs=0.05)

    def test_without_any_inspection_the_module_works_alone(self, env: Env) -> None:
        asset_id = env.import_media(env.samples.synced())

        _, facts = prepare(env, asset_id, env.use_case)

        assert facts.sample_rate > 0
        assert env.runner.count_on("ffprobe", source_path(env, asset_id)) >= 1

    def test_a_missing_audio_track_is_still_reported_by_the_module_itself(self, env: Env) -> None:
        asset_id = env.import_media(env.samples.silent_video())
        env.inspect(asset_id)
        source = env.library.get(asset_id)
        assert isinstance(source, Ok)

        prepared = transcriber(env, env.use_case).prepare_audio(
            source.value, PreparationConfig(), JobContext.detached()
        )

        assert not isinstance(prepared, Ok)  # no audio: the inspection could not supply facts


class TestFullChain:
    def test_inspect_then_improve_then_analyse_the_improved_audio(self, env: Env) -> None:
        source_id = env.import_media(env.samples.late_audio())
        env.inspect(source_id)
        improved = improve(env, source_id, env.use_case)

        # the improved audio is a new asset: inspect it, then prepare it for analysis
        improved_id = improved.asset.id
        first = prepare(env, improved_id, env.use_case)  # not inspected yet: probes itself
        env.inspect(improved_id)
        env.runner.reset()
        again = prepare(env, improved_id, env.use_case)

        assert first[1].audio_offset == 0.0  # the pad made the audio start at the origin
        assert again[0].id == first[0].id  # the prepared audio is reused, not extracted twice
        assert env.runner.count_on("ffmpeg", source_path(env, improved_id)) == 0

    def test_every_stage_leaves_the_original_untouched(self, env: Env) -> None:
        path = env.samples.late_audio()
        before = path.read_bytes()
        source_id = env.import_media(path)
        env.inspect(source_id)

        improve(env, source_id, env.use_case)
        prepare(env, source_id, env.use_case)

        assert path.read_bytes() == before
        stored = source_path(env, source_id)
        assert stored.read_bytes() == before
