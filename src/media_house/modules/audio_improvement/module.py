"""Module registration: how Audio Improvement plugs into the application.

It owns no storage: improved and mixed audio are Media Library assets derived from their sources.
The stage engines are registered as a collection; a better denoiser or dereverberator is one new
class plus one line here (registered for the same stage, replacing its predecessor).
"""

from media_house.core.application.ports import ProcessRunner
from media_house.core.modules import Container
from media_house.modules.audio_improvement.application.contracts import AudioImprover, AudioMixing
from media_house.modules.audio_improvement.application.improve_audio import ImproveAudio
from media_house.modules.audio_improvement.application.mix_audio import MixAudio
from media_house.modules.audio_improvement.application.ports import (
    AudioMixer,
    AudioTranscoder,
    QualityAnalyzer,
    StageProcessor,
)
from media_house.modules.audio_improvement.infrastructure.ffmpeg_mixer import FfmpegMixer
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
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.filesystem import AppPaths


class AudioImprovementModule:
    name: str = "audio_improvement"

    def register(self, container: Container) -> None:
        container.register_factory(FfmpegTool, lambda c: FfmpegTool(c.resolve(ProcessRunner)))
        container.register_factory(
            AudioTranscoder, lambda c: FfmpegTranscoder(c.resolve(FfmpegTool))
        )
        container.register_factory(
            QualityAnalyzer, lambda c: SignalQualityAnalyzer(c.resolve(FfmpegTool))
        )
        container.register_factory(AudioMixer, lambda c: FfmpegMixer(c.resolve(FfmpegTool)))
        container.register_factory(
            FfmpegMastering, lambda c: FfmpegMastering(c.resolve(FfmpegTool))
        )
        container.register_factory(
            ImproveAudio,
            lambda c: ImproveAudio(
                c.resolve(MediaLibrary),
                c.resolve(AudioTranscoder),
                c.resolve(QualityAnalyzer),
                _engines(c.resolve(FfmpegTool)),
                c.resolve(AppPaths),
                c.resolve(InspectionCatalog),
            ),
        )
        container.register_factory(AudioImprover, lambda c: c.resolve(ImproveAudio))
        container.register_factory(
            MixAudio,
            lambda c: MixAudio(
                c.resolve(MediaLibrary),
                c.resolve(AudioTranscoder),
                c.resolve(QualityAnalyzer),
                c.resolve(AudioMixer),
                c.resolve(FfmpegMastering),
                c.resolve(AppPaths),
                c.resolve(InspectionCatalog),
            ),
        )
        container.register_factory(AudioMixing, lambda c: c.resolve(MixAudio))


def _engines(tool: FfmpegTool) -> list[StageProcessor]:
    """One engine per stage: the default processing chain."""
    return [
        FfmpegDeclipper(tool),
        FfmpegNoiseReducer(tool),
        SpectralDereverb(),
        FfmpegEqualizer(tool),
        FfmpegCompressor(tool),
        FfmpegDeEsser(tool),
        FfmpegMastering(tool),
    ]
