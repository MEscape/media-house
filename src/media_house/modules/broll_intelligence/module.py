"""Module registration: how B-roll Intelligence plugs into the application.

It owns no storage and no UI: analyses are JSON documents derived from their first recording in the
Media Library. Every input comes from the public contract of the module that owns it (story,
content, audio, video, alignment, pacing, relationships, the library and the video shot index); the
only numerical dependency is the NumPy vector adapter. The editing stage reads the result through
the ``BrollCatalog`` contract. Pipelines and the GUI call ``BrollAnalyzer`` from a job.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.asset_relationships.application.contracts import RelationshipCatalog
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentCatalog
from media_house.modules.broll_intelligence.application.analyze_broll import AnalyzeBroll
from media_house.modules.broll_intelligence.application.contracts import (
    BrollAnalyzer,
    BrollCatalog,
)
from media_house.modules.broll_intelligence.application.pool import LibraryPool
from media_house.modules.broll_intelligence.application.ports import VectorIndexer
from media_house.modules.broll_intelligence.application.results import BrollResults
from media_house.modules.broll_intelligence.application.retrieval import Retriever
from media_house.modules.broll_intelligence.infrastructure.numpy_vectors import NumpyVectorIndexer
from media_house.modules.content_intelligence.application.contracts import (
    ContentCatalog,
    TextEncoder,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.pacing_intelligence.application.contracts import PacingCatalog
from media_house.modules.story_architecture.application.contracts import StoryCatalog
from media_house.modules.video_intelligence.application.contracts import (
    ShotIndexCatalog,
    VideoCatalog,
    VisualTextEncoder,
)
from media_house.shared.filesystem import AppPaths


def _available(encoder: VisualTextEncoder | TextEncoder) -> str | None:
    """The encoder's identity when it can run now (so a missing model changes the cache key)."""
    return encoder.identity() if encoder.unavailable() is None else None


class BrollIntelligenceModule:
    name: str = "broll_intelligence"

    def register(self, container: Container) -> None:
        container.register_factory(VectorIndexer, lambda _c: NumpyVectorIndexer())
        container.register_factory(
            LibraryPool,
            lambda c: LibraryPool(
                c.resolve(MediaLibrary), c.resolve(ShotIndexCatalog), c.resolve(RelationshipCatalog)
            ),
        )
        container.register_factory(
            Retriever,
            lambda c: Retriever(
                c.resolve(VisualTextEncoder), c.resolve(TextEncoder), c.resolve(VectorIndexer)
            ),
        )
        container.register_factory(
            AnalyzeBroll,
            lambda c: AnalyzeBroll(
                c.resolve(MediaLibrary),
                c.resolve(StoryCatalog),
                c.resolve(ContentCatalog),
                c.resolve(AudioCatalog),
                c.resolve(VideoCatalog),
                c.resolve(AlignmentCatalog),
                c.resolve(PacingCatalog),
                c.resolve(LibraryPool),
                c.resolve(Retriever),
                lambda: _available(c.resolve(VisualTextEncoder)),
                lambda: _available(c.resolve(TextEncoder)),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(BrollAnalyzer, lambda c: c.resolve(AnalyzeBroll))
        container.register_factory(BrollResults, lambda c: BrollResults(c.resolve(MediaLibrary)))
        container.register_factory(BrollCatalog, lambda c: c.resolve(BrollResults))
