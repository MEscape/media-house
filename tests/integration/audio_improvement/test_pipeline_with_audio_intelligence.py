"""Audio Improvement -> Audio Intelligence: compatible work is reused, nothing is assumed.

Each module also runs alone: Intelligence normalises loudness itself when its input does not
PROVE (by measurement) that this was already done.
"""

from pathlib import Path

import pytest

from media_house.modules.audio_improvement.application.contracts import (
    ProcessingStage,
    read_provenance,
)
from media_house.modules.audio_intelligence.application.analyze_audio import (
    AnalyzeAudio,
    AnalyzeAudioCommand,
)
from media_house.modules.audio_intelligence.application.ports import (
    KnownSourceTiming,
    PreparedAudio,
)
from media_house.modules.audio_intelligence.application.transcribe_audio import TranscribeAudio
from media_house.modules.audio_intelligence.domain.analysis.config import AudioIntelligenceConfig
from media_house.modules.audio_intelligence.domain.values import (
    PreparationConfig,
    TranscriptionConfig,
)
from media_house.modules.audio_intelligence.infrastructure.acoustic_extractor import (
    AcousticExtractor,
)
from media_house.modules.audio_intelligence.infrastructure.ffmpeg_audio import FfmpegAudioPreparer
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Ok
from tests.integration.audio_improvement.conftest import Env
from tests.support.analysis_fakes import raw_segments
from tests.support.audio_fakes import FakeEngine
from tests.support.improvement_fakes import save, voice

pytestmark = pytest.mark.integration

NORMALIZE = PreparationConfig(loudness_normalization=True)


class RecordingPreparer:
    """The real FFmpeg preparer that remembers whether each run was asked to normalise."""

    def __init__(self, inner: FfmpegAudioPreparer) -> None:
        self._inner = inner
        self.normalized: list[bool] = []

    def prepare(
        self,
        source: Path,
        destination: Path,
        config: PreparationConfig,
        cancellation: CancellationToken,
        known: KnownSourceTiming | None = None,
    ) -> PreparedAudio:
        self.normalized.append(config.loudness_normalization)
        return self._inner.prepare(source, destination, config, cancellation, known)


def script(config: TranscriptionConfig) -> object:
    return raw_segments([[("Hello", 0.0, 0.5), ("world", 1.6, 2.1)]], language="en")


class Pipeline:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.preparer = RecordingPreparer(FfmpegAudioPreparer(env.runner))
        self.engine = FakeEngine(script)  # type: ignore[arg-type]
        self.transcriber = TranscribeAudio(
            env.library, self.preparer, self.engine, env.paths, env.clock
        )

    def asset(self, asset_id: str) -> MediaAssetDto:
        found = self.env.library.get(asset_id)
        assert isinstance(found, Ok)
        return found.value

    def prepare(self, asset_id: str) -> MediaAssetDto:
        prepared = self.transcriber.prepare_audio(
            self.asset(asset_id), NORMALIZE, JobContext.detached()
        )
        assert isinstance(prepared, Ok), prepared
        return prepared.value[0]


@pytest.fixture
def pipeline(env: Env) -> Pipeline:
    return Pipeline(env)


def source_asset(env: Env) -> str:
    return env.import_media(save(env.tmp / "take.wav", voice(12.0)))


# --- reuse, decided by measurement -------------------------------------------------------------
def test_standalone_intelligence_normalises_arbitrary_input_itself(pipeline: Pipeline) -> None:
    original = source_asset(pipeline.env)

    audio = pipeline.prepare(original)

    assert pipeline.preparer.normalized == [True]
    assert audio.metadata["reused_processing"] == []


def test_improved_audio_on_the_same_target_is_not_normalised_a_second_time(
    pipeline: Pipeline,
) -> None:
    env = pipeline.env
    improved = env.improve(source_asset(env), "podcast")  # -16 LUFS: Intelligence's own target
    provenance = read_provenance(pipeline.asset(improved.asset.id).metadata)
    assert provenance is not None and provenance.applied(ProcessingStage.MASTERING)

    audio = pipeline.prepare(improved.asset.id)

    assert pipeline.preparer.normalized == [False]  # the duplicate pass never ran
    assert audio.metadata["reused_processing"] == ["loudness_normalization"]


def test_improved_audio_on_another_target_is_still_normalised(pipeline: Pipeline) -> None:
    env = pipeline.env
    improved = env.improve(source_asset(env), "youtube")  # -14 LUFS: 2 LU off the -16 target

    audio = pipeline.prepare(improved.asset.id)

    assert pipeline.preparer.normalized == [True]  # nothing is assumed, only measured facts count
    assert audio.metadata["reused_processing"] == []


def test_the_prepared_audio_of_improved_audio_is_itself_reused(pipeline: Pipeline) -> None:
    env = pipeline.env
    improved = env.improve(source_asset(env), "podcast")

    first = pipeline.prepare(improved.asset.id)
    second = pipeline.prepare(improved.asset.id)

    assert first.id == second.id
    assert pipeline.preparer.normalized == [False]  # extracted once


# --- the whole chain ---------------------------------------------------------------------------
def analyze(pipeline: Pipeline, asset_id: str) -> object:
    use_case = AnalyzeAudio(
        pipeline.env.library,
        pipeline.transcriber,
        AcousticExtractor(),
        pipeline.env.paths,
        pipeline.env.clock,
    )
    result = use_case.execute(
        AnalyzeAudioCommand(
            asset_id,
            AudioIntelligenceConfig(transcription=TranscriptionConfig(preparation=NORMALIZE)),
        ),
        JobContext.detached(),
    )
    assert isinstance(result, Ok), result
    return result.value


def test_improvement_then_intelligence_gives_a_synchronised_timeline(pipeline: Pipeline) -> None:
    env = pipeline.env
    original = source_asset(env)
    improved = env.improve(original, "podcast")

    chained = analyze(pipeline, improved.asset.id)

    timeline = chained.timeline  # type: ignore[attr-defined]
    assert timeline.metadata.source_asset_id == improved.asset.id
    assert [w.raw_word for w in timeline.words] == ["Hello", "world"]
    hello = timeline.word_at(0.25)
    assert hello is not None and hello.pitch is not None
    reference = analyze(pipeline, original).timeline.word_at(0.25)  # type: ignore[attr-defined]
    assert reference is not None and reference.pitch is not None
    # prosody survives improvement: the same word has the same pitch as in the original
    assert hello.pitch.median_hz == pytest.approx(reference.pitch.median_hz, abs=3.0)
    assert pipeline.preparer.normalized == [False, True]  # improved: reused; original: normalised
    assert chained.audio_asset.metadata["reused_processing"] == ["loudness_normalization"]  # type: ignore[attr-defined]


def test_both_modules_work_independently_on_the_same_original(pipeline: Pipeline) -> None:
    env = pipeline.env
    original = source_asset(env)

    alone = analyze(pipeline, original)  # Intelligence without any improvement
    improved = env.improve(original, "podcast")  # Improvement without any intelligence

    assert [w.raw_word for w in alone.timeline.words] == ["Hello", "world"]  # type: ignore[attr-defined]
    assert pipeline.preparer.normalized == [True]  # it normalised by itself
    assert improved.asset.derivation is not None
    assert improved.asset.derivation.source_asset_id == original
    assert not improved.provenance.noise_reduced  # and improving needed no intelligence
