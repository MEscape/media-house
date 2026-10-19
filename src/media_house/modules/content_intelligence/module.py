"""Module registration: how Content Intelligence plugs into the application.

It owns no storage: analyses and cached model replies are JSON documents derived from their source
asset in the Media Library. The transcript, acoustic cues and visual observations come from the
public contracts of Audio and Video Intelligence (never probed or recomputed here). No UI yet;
pipelines and the GUI call the ``ContentAnalyzer`` contract from a job.

Both model engines are lazy singletons. Models are looked up in the application's cache directory
(``content_intelligence/models``, filled by ``scripts/fetch_models.py``) and the Hugging
Face cache; a run never downloads. The language model runs in this process unless a command names
an HTTP endpoint, which must be on this machine unless the command says otherwise.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_intelligence.application.contracts import AudioAnalyzer, AudioEngine
from media_house.modules.content_intelligence.application.analyze_content import AnalyzeContent
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalyzer,
    ContentCatalog,
    TextEncoder,
)
from media_house.modules.content_intelligence.application.encoding import SemanticTextEncoder
from media_house.modules.content_intelligence.application.ports import (
    LanguageModel,
    SemanticEngine,
)
from media_house.modules.content_intelligence.application.results import ContentResults
from media_house.modules.content_intelligence.infrastructure.loopback_llm import LoopbackChatModel
from media_house.modules.content_intelligence.infrastructure.routed_llm import RoutedLanguageModel
from media_house.modules.content_intelligence.infrastructure.transformers_embedder import (
    TransformersSemanticEngine,
)
from media_house.modules.content_intelligence.infrastructure.transformers_llm import (
    TransformersLanguageModel,
)
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.video_intelligence.application.contracts import VideoAnalyzer
from media_house.shared.filesystem import AppPaths


class ContentIntelligenceModule:
    name: str = "content_intelligence"

    def register(self, container: Container) -> None:
        container.register_factory(
            SemanticEngine,
            lambda c: TransformersSemanticEngine(
                c.resolve(AppPaths).cache_dir / "content_intelligence" / "models"
            ),
        )
        container.register_factory(
            LanguageModel,
            lambda c: RoutedLanguageModel(
                TransformersLanguageModel(
                    c.resolve(AppPaths).cache_dir / "content_intelligence" / "models"
                ),
                LoopbackChatModel(),
            ),
        )
        container.register_factory(
            AnalyzeContent,
            lambda c: AnalyzeContent(
                c.resolve(MediaLibrary),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(AudioEngine),
                c.resolve(AudioAnalyzer),
                c.resolve(VideoAnalyzer),
                c.resolve(SemanticEngine),
                c.resolve(LanguageModel),
            ),
        )
        container.register_factory(ContentAnalyzer, lambda c: c.resolve(AnalyzeContent))
        container.register_factory(
            ContentResults, lambda c: ContentResults(c.resolve(MediaLibrary))
        )
        container.register_factory(ContentCatalog, lambda c: c.resolve(ContentResults))
        container.register_factory(
            TextEncoder, lambda c: SemanticTextEncoder(c.resolve(SemanticEngine))
        )
