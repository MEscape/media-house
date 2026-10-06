"""Module registration: how Audio Intelligence plugs into the application.

It owns no storage: audio and transcripts live in the Media Library as derived assets. One
``WhisperXEngine`` instance (a lazy singleton) serves every request so loaded models are reused.
No UI yet; the GUI and later pipeline stages call the ``AudioEngine`` contract from a job.
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.analyze_audio import AnalyzeAudio
from media_house.modules.audio_intelligence.application.contracts import (
    AudioAnalyzer,
    AudioEngine,
)
from media_house.modules.audio_intelligence.application.ports import (
    AcousticExtractor,
    AudioPreparer,
    TranscriptionEngine,
)
from media_house.modules.audio_intelligence.application.transcribe_audio import TranscribeAudio
from media_house.modules.audio_intelligence.infrastructure.acoustic_extractor import (
    AcousticExtractor as PraatAcousticExtractor,
)
from media_house.modules.audio_intelligence.infrastructure.ffmpeg_audio import (
    FfmpegAudioPreparer,
)
from media_house.modules.audio_intelligence.infrastructure.whisperx_engine import WhisperXEngine
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.filesystem import AppPaths


class AudioIntelligenceModule:
    name: str = "audio_intelligence"

    def register(self, container: Container) -> None:
        container.register_factory(
            AudioPreparer,
            lambda c: FfmpegAudioPreparer(c.resolve(ProcessRunner)),
        )
        container.register_factory(TranscriptionEngine, lambda _c: WhisperXEngine())
        container.register_factory(
            TranscribeAudio,
            lambda c: TranscribeAudio(
                c.resolve(MediaLibrary),
                c.resolve(AudioPreparer),
                c.resolve(TranscriptionEngine),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(AudioEngine, lambda c: c.resolve(TranscribeAudio))
        container.register_factory(AcousticExtractor, lambda _c: PraatAcousticExtractor())
        container.register_factory(
            AnalyzeAudio,
            lambda c: AnalyzeAudio(
                c.resolve(MediaLibrary),
                c.resolve(TranscribeAudio),
                c.resolve(AcousticExtractor),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(AudioAnalyzer, lambda c: c.resolve(AnalyzeAudio))
