"""Module registration: how Audio Intelligence plugs into the application.

It owns no storage: audio and transcripts live in the Media Library as derived assets. One
``WhisperXEngine`` instance (a lazy singleton) serves every request so loaded models are reused.
No UI yet; the GUI and later pipeline stages call the ``AudioEngine`` contract from a job.

``remote_transcription`` (opt-in, default ``None``) replaces WhisperX with a hosted
OpenAI-compatible recogniser (Groq). That UPLOADS THE AUDIO; its API key comes from the
application's secrets (``MEDIA_HOUSE_SECRET_GROQ_API_KEY``).
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.analyze_audio import AnalyzeAudio
from media_house.modules.audio_intelligence.application.contracts import (
    AudioAnalyzer,
    AudioCatalog,
    AudioEngine,
)
from media_house.modules.audio_intelligence.application.ports import (
    AcousticExtractor,
    AudioPreparer,
    SpeakerDiarizer,
    TranscriptionEngine,
)
from media_house.modules.audio_intelligence.application.results import AudioResults
from media_house.modules.audio_intelligence.application.transcribe_audio import TranscribeAudio
from media_house.modules.audio_intelligence.infrastructure.acoustic_extractor import (
    AcousticExtractor as PraatAcousticExtractor,
)
from media_house.modules.audio_intelligence.infrastructure.ffmpeg_audio import (
    FfmpegAudioPreparer,
)
from media_house.modules.audio_intelligence.infrastructure.groq_transcription import (
    GroqTranscriptionEngine,
    RemoteTranscriptionSettings,
)
from media_house.modules.audio_intelligence.infrastructure.pyannote_diarizer import (
    PyannoteDiarizer,
)
from media_house.modules.audio_intelligence.infrastructure.whisperx_engine import WhisperXEngine
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.configuration import AppSettings
from media_house.shared.filesystem import AppPaths


class AudioIntelligenceModule:
    name: str = "audio_intelligence"

    def __init__(self, remote_transcription: RemoteTranscriptionSettings | None = None) -> None:
        self._remote = remote_transcription

    def register(self, container: Container) -> None:
        container.register_factory(
            AudioPreparer,
            lambda c: FfmpegAudioPreparer(c.resolve(ProcessRunner)),
        )
        container.register_factory(TranscriptionEngine, self._transcription_engine)
        container.register_factory(
            TranscribeAudio,
            lambda c: TranscribeAudio(
                c.resolve(MediaLibrary),
                c.resolve(AudioPreparer),
                c.resolve(TranscriptionEngine),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(InspectionCatalog),
            ),
        )
        container.register_factory(AudioEngine, lambda c: c.resolve(TranscribeAudio))
        container.register_factory(AcousticExtractor, lambda _c: PraatAcousticExtractor())
        container.register_factory(
            SpeakerDiarizer,
            lambda c: PyannoteDiarizer(c.resolve(AppPaths).install("audio_intelligence", "models")),
        )
        container.register_factory(
            AnalyzeAudio,
            lambda c: AnalyzeAudio(
                c.resolve(MediaLibrary),
                c.resolve(TranscribeAudio),
                c.resolve(AcousticExtractor),
                c.resolve(AppPaths),
                c.resolve(Clock),
                diarizer=c.resolve(SpeakerDiarizer),
            ),
        )
        container.register_factory(AudioAnalyzer, lambda c: c.resolve(AnalyzeAudio))
        container.register_factory(AudioResults, lambda c: AudioResults(c.resolve(MediaLibrary)))
        container.register_factory(AudioCatalog, lambda c: c.resolve(AudioResults))

    def _transcription_engine(self, c: Container) -> TranscriptionEngine:
        if self._remote is None:
            return WhisperXEngine()
        return GroqTranscriptionEngine(
            c.resolve(ProcessRunner),
            self._remote,
            lambda name: _secret(c.resolve(AppSettings), name),
        )


def _secret(settings: AppSettings, name: str) -> str | None:
    found = settings.secrets.get(name)
    return None if found is None else found.get_secret_value()
