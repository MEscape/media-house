"""Module registration: how Audio-Visual Alignment plugs into the application.

It owns no storage: alignments are JSON documents derived from the video asset in the Media
Library. Audio, video and content analyses and the inspection come from the public contracts of
their owners (never probed, decoded or recomputed here). No UI yet; pipelines and the GUI call the
``AudioVisualAligner`` contract from a job. The multilingual text encoder is Content
Intelligence's own (through its contract); it only runs when its model is already on disk, and
matching falls back to exact words without it.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import (
    AudioAnalyzer,
    AudioCatalog,
)
from media_house.modules.audio_visual_alignment.application.align import AlignAudioVisual
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentCatalog,
    AudioVisualAligner,
)
from media_house.modules.audio_visual_alignment.application.resolver import UpstreamResolver
from media_house.modules.audio_visual_alignment.application.results import AlignmentResults
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalyzer,
    ContentCatalog,
    TextEncoder,
)
from media_house.modules.media_inspection.application.contracts import (
    InspectionCatalog,
    MediaInspector,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.video_intelligence.application.contracts import (
    VideoAnalyzer,
    VideoCatalog,
)
from media_house.shared.filesystem import AppPaths


class AudioVisualAlignmentModule:
    name: str = "audio_visual_alignment"

    def register(self, container: Container) -> None:
        container.register_factory(
            UpstreamResolver,
            lambda c: UpstreamResolver(
                c.resolve(AudioCatalog),
                c.resolve(AudioAnalyzer),
                c.resolve(VideoCatalog),
                c.resolve(VideoAnalyzer),
                c.resolve(ContentCatalog),
                c.resolve(ContentAnalyzer),
                c.resolve(InspectionCatalog),
                c.resolve(MediaInspector),
            ),
        )
        container.register_factory(
            AlignAudioVisual,
            lambda c: AlignAudioVisual(
                c.resolve(MediaLibrary),
                c.resolve(UpstreamResolver),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(TextEncoder),
            ),
        )
        container.register_factory(AudioVisualAligner, lambda c: c.resolve(AlignAudioVisual))
        container.register_factory(
            AlignmentResults, lambda c: AlignmentResults(c.resolve(MediaLibrary))
        )
        container.register_factory(AlignmentCatalog, lambda c: c.resolve(AlignmentResults))
