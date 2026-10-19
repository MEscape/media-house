"""The real composition root wires Content Intelligence to the other modules' public contracts."""

from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.audio_intelligence.application.contracts import AudioAnalyzer, AudioEngine
from media_house.modules.content_intelligence.application.analyze_content import (
    AnalyzeContent,
    AnalyzeContentCommand,
)
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalyzer,
    ContentCatalog,
    validate_analysis,
)
from media_house.modules.content_intelligence.application.ports import LanguageModel, SemanticEngine
from media_house.modules.content_intelligence.domain.config import ContentConfig, Profile
from media_house.modules.video_intelligence.application.contracts import VideoAnalyzer
from media_house.shared.concurrency import JobContext
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from tests.support.ci_texts import THREE_TOPICS, lines_to_transcript

pytestmark = pytest.mark.integration


def test_the_application_resolves_the_analyzer_and_runs_a_standalone_analysis(
    tmp_path: Path,
) -> None:
    application = Application.start(
        StartupOptions(env={}, home=tmp_path, environment=Environment.TEST)
    )
    try:
        container = application.container
        analyzer = container.resolve(ContentAnalyzer)

        assert analyzer is container.resolve(AnalyzeContent)
        assert container.resolve(ContentCatalog) is not None
        # The upstream owners it asks are the real ones, resolved by contract.
        for contract in (AudioEngine, AudioAnalyzer, VideoAnalyzer, SemanticEngine, LanguageModel):
            assert container.resolve(contract) is not None

        result = analyzer.execute(
            AnalyzeContentCommand(
                transcript=lines_to_transcript(THREE_TOPICS),
                config=ContentConfig(profile=Profile.FAST),
            ),
            JobContext.detached(),
        )

        assert isinstance(result, Ok)
        validate_analysis(result.value.analysis)
        assert len(result.value.analysis.topics) >= 2
    finally:
        application.shutdown()
