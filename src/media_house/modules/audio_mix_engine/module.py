"""Module registration: how the audio mix engine plugs into the application.

No storage of its own: specifications and results are documents of the recording's anchor asset
in the Media Library; stems and the mix are derived assets of it. The processing backend is one
port; the default drives FFmpeg through the ``ProcessRunner``.
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.audio_mix_engine.application.attach_mix import AttachMix
from media_house.modules.audio_mix_engine.application.contracts import (
    AudioMixCreator,
    AudioMixer,
    MixAttacher,
    MixCatalog,
)
from media_house.modules.audio_mix_engine.application.create_spec import CreateMixSpecification
from media_house.modules.audio_mix_engine.application.ports import AudioBackend
from media_house.modules.audio_mix_engine.application.render_mix import RenderAudioMix
from media_house.modules.audio_mix_engine.application.sources import SoundFiles
from media_house.modules.audio_mix_engine.application.store import MixStore
from media_house.modules.audio_mix_engine.infrastructure.ffmpeg_backend import FfmpegAudioBackend
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.filesystem import AppPaths


class AudioMixEngineModule:
    name: str = "audio_mix_engine"

    def __init__(self, backend: AudioBackend | None = None) -> None:
        self._backend = backend

    def register(self, container: Container) -> None:
        if self._backend is not None:
            container.register_instance(AudioBackend, self._backend)
        else:
            container.register_factory(
                AudioBackend, lambda c: FfmpegAudioBackend(c.resolve(ProcessRunner))
            )
        container.register_factory(SoundFiles, lambda c: SoundFiles(c.resolve(MediaLibrary)))
        container.register_factory(
            MixStore, lambda c: MixStore(c.resolve(MediaLibrary), c.resolve(AppPaths))
        )
        container.register_factory(
            CreateMixSpecification,
            lambda c: CreateMixSpecification(
                c.resolve(MediaLibrary),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(MixStore),
                c.resolve(Clock),
            ),
        )
        container.register_factory(
            RenderAudioMix,
            lambda c: RenderAudioMix(
                c.resolve(MediaLibrary),
                c.resolve(CreateMixSpecification),
                c.resolve(AudioBackend),
                c.resolve(SoundFiles),
                c.resolve(MixStore),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(AudioMixCreator, lambda c: c.resolve(CreateMixSpecification))
        container.register_factory(AudioMixer, lambda c: c.resolve(RenderAudioMix))
        container.register_factory(
            AttachMix,
            lambda c: AttachMix(
                c.resolve(MediaLibrary), c.resolve(AudioBackend), c.resolve(AppPaths)
            ),
        )
        container.register_factory(MixAttacher, lambda c: c.resolve(AttachMix))
        container.register_factory(MixCatalog, lambda c: c.resolve(MixStore))
