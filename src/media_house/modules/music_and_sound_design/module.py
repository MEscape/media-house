"""Module registration: how Music and Sound Design plugs into the application.

It owns no storage: library sounds are Media Library assets (imported from the owner's folder),
their measurements and the plans are derived JSON documents, a stem is a derived WAV. The
adapters (FFmpeg through the ``ProcessRunner``, numpy/scipy, the pydantic manifest reader) meet
their ports only here. Upstream results are READ through the owners' catalogs.
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.music_and_sound_design.application.contracts import (
    MusicAnalysisCatalog,
    SfxProfileCatalog,
    SoundDesignCatalog,
    SoundDesignPlanner,
    SoundLibraryCatalog,
    SoundLibraryImporter,
    SoundStemRenderer,
)
from media_house.modules.music_and_sound_design.application.import_library import (
    AnalyzeSounds,
    ImportSoundLibrary,
)
from media_house.modules.music_and_sound_design.application.plan_sound_design import (
    PlanSoundDesign,
)
from media_house.modules.music_and_sound_design.application.render_stem import RenderSoundStem
from media_house.modules.music_and_sound_design.application.results import (
    MusicAnalysisResults,
    SfxProfileResults,
    SoundDesignResults,
    SoundLibraryResults,
)
from media_house.modules.music_and_sound_design.infrastructure.ffmpeg_audio import FfmpegAudio
from media_house.modules.music_and_sound_design.infrastructure.manifest import JsonManifestReader
from media_house.modules.music_and_sound_design.infrastructure.measurer import (
    FfmpegSoundMeasurer,
)
from media_house.modules.music_and_sound_design.infrastructure.stem_renderer import (
    FfmpegStemRenderer,
)
from media_house.modules.pacing_intelligence.application.contracts import PacingCatalog
from media_house.modules.story_architecture.application.contracts import StoryCatalog
from media_house.shared.filesystem import AppPaths


class MusicAndSoundDesignModule:
    name: str = "music_and_sound_design"

    def register(self, container: Container) -> None:
        container.register_factory(FfmpegAudio, lambda c: FfmpegAudio(c.resolve(ProcessRunner)))
        container.register_factory(
            MusicAnalysisResults, lambda c: MusicAnalysisResults(c.resolve(MediaLibrary))
        )
        container.register_factory(MusicAnalysisCatalog, lambda c: c.resolve(MusicAnalysisResults))
        container.register_factory(
            SfxProfileResults, lambda c: SfxProfileResults(c.resolve(MediaLibrary))
        )
        container.register_factory(SfxProfileCatalog, lambda c: c.resolve(SfxProfileResults))
        container.register_factory(
            SoundLibraryResults,
            lambda c: SoundLibraryResults(
                c.resolve(MediaLibrary),
                c.resolve(MusicAnalysisResults),
                c.resolve(SfxProfileResults),
            ),
        )
        container.register_factory(SoundLibraryCatalog, lambda c: c.resolve(SoundLibraryResults))
        container.register_factory(
            AnalyzeSounds,
            lambda c: AnalyzeSounds(
                c.resolve(MediaLibrary),
                FfmpegSoundMeasurer(c.resolve(FfmpegAudio), c.resolve(AppPaths)),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(
            ImportSoundLibrary,
            lambda c: ImportSoundLibrary(
                c.resolve(MediaLibrary), JsonManifestReader(), c.resolve(AnalyzeSounds)
            ),
        )
        container.register_factory(SoundLibraryImporter, lambda c: c.resolve(ImportSoundLibrary))
        container.register_factory(
            PlanSoundDesign,
            lambda c: PlanSoundDesign(
                c.resolve(MediaLibrary),
                c.resolve(SoundLibraryResults),
                c.resolve(AudioCatalog),
                c.resolve(StoryCatalog),
                c.resolve(PacingCatalog),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(SoundDesignPlanner, lambda c: c.resolve(PlanSoundDesign))
        container.register_factory(
            SoundDesignResults, lambda c: SoundDesignResults(c.resolve(MediaLibrary))
        )
        container.register_factory(SoundDesignCatalog, lambda c: c.resolve(SoundDesignResults))
        container.register_factory(
            RenderSoundStem,
            lambda c: RenderSoundStem(
                c.resolve(MediaLibrary),
                FfmpegStemRenderer(c.resolve(FfmpegAudio)),
                c.resolve(AppPaths),
            ),
        )
        container.register_factory(SoundStemRenderer, lambda c: c.resolve(RenderSoundStem))
