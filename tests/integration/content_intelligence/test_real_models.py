"""The real Hugging Face models, when they are on this machine.

Fetch them with ``scripts/fetch_models.py``.

* Embedding tests run whenever the sentence-embedding model is present (seconds on a GPU).
* Language-model tests load a multi-GB model (minutes on a slow disk), so they run only when
  ``MEDIA_HOUSE_REAL_LLM_TESTS=1`` is set.

All tests skip, never fail, when a model is absent. They assert behaviour the stand-in tests
cannot: that real embeddings find topic boundaries and paraphrases that word overlap misses, and
that a real instruct model's replies survive the grounding checks.
"""

import os

import pytest

from media_house.modules.content_intelligence.application.ports import LanguageModel
from media_house.modules.content_intelligence.domain.config import (
    ContentConfig,
    EmbeddingSettings,
    LlmSettings,
    Profile,
)
from media_house.modules.content_intelligence.domain.engine import prepare
from media_house.modules.content_intelligence.domain.model import ContentAnalysis, Relationship
from media_house.modules.content_intelligence.domain.refine import (
    SYSTEM_PROMPT,
    build_prompt,
    merge,
    parse_response,
    plan_chunks,
)
from media_house.modules.content_intelligence.domain.validation import validate_analysis
from media_house.modules.content_intelligence.domain.values import (
    Origin,
    RelationKind,
    SegmentLevel,
)
from media_house.modules.content_intelligence.infrastructure.numpy_similarity import DenseSimilarity
from media_house.modules.content_intelligence.infrastructure.transformers_embedder import (
    TransformersSemanticEngine,
)
from media_house.modules.content_intelligence.infrastructure.transformers_llm import (
    TransformersLanguageModel,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.filesystem import AppPaths
from tests.support.ci_texts import (
    BATTERIES,
    GERMAN,
    THREE_TOPICS,
    analysis_of,
    lines_to_transcript,
)

pytestmark = pytest.mark.integration

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
LLM_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
MODELS = AppPaths.for_user().cache_dir / "content_intelligence" / "models"

CARS = (
    "My old car needs a service soon.",
    "The vehicle's motor burns too much lubricant.",
    "A mechanic should replace the worn pistons and gaskets.",
    "Automobile repairs can become very expensive.",
    "Fixing the engine of this truck takes several days.",
    "The garage will also inspect the brakes and tires.",
)
PASTA = (
    "Tonight we are cooking spaghetti for dinner.",
    "Boil the noodles in salted water until tender.",
    "Meanwhile fry garlic and onions in olive oil.",
    "Add chopped tomatoes and basil to the sauce.",
    "Sprinkle grated parmesan over the plate before serving.",
    "Everyone at the table enjoyed the meal.",
)
SPACE = (
    "Astronomers photographed a distant galaxy last week.",
    "The telescope captured light from billions of years ago.",
    "Stars in that cluster are born inside dense gas clouds.",
    "Scientists measure the cosmos expansion with supernovae.",
    "Planets orbiting other suns might hold liquid oceans.",
    "Observatories across the globe share the new images.",
)


def need(model: str) -> None:
    if not (MODELS / model.replace("/", "--")).is_dir():
        pytest.skip(f"{model} is not downloaded (python scripts/fetch_models.py)")


# --- embeddings ---------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def embedder() -> tuple[TransformersSemanticEngine, EmbeddingSettings]:
    need(EMBEDDING_MODEL)
    return TransformersSemanticEngine(MODELS), EmbeddingSettings(model=EMBEDDING_MODEL)


def index(
    embedder: tuple[TransformersSemanticEngine, EmbeddingSettings], texts: list[str]
) -> DenseSimilarity:
    engine, settings = embedder
    sim = engine.index(engine.embed(texts, settings, CancellationToken()), settings)
    assert isinstance(sim, DenseSimilarity)
    return sim


class TestRealEmbeddings:
    def test_the_engine_is_available_offline(
        self, embedder: tuple[TransformersSemanticEngine, EmbeddingSettings]
    ) -> None:
        engine, settings = embedder

        assert engine.unavailable(settings) is None

    def test_paraphrases_and_translations_are_close_unrelated_text_is_far(
        self, embedder: tuple[TransformersSemanticEngine, EmbeddingSettings]
    ) -> None:
        texts = [
            "Heat damages the battery over time.",
            "Over time, high temperatures harm the battery.",
            "Hitze schädigt die Batterie mit der Zeit.",
            "Tomatoes need a lot of sunlight and water.",
        ]
        sim = index(embedder, texts)

        assert sim.similarity(0, 1) > 0.8  # paraphrase
        assert sim.similarity(0, 2) > 0.8  # German translation
        assert sim.similarity(0, 3) < 0.35  # unrelated

    def test_topics_without_shared_words_are_still_separated(
        self, embedder: tuple[TransformersSemanticEngine, EmbeddingSettings]
    ) -> None:
        lines = [*CARS, *PASTA, *SPACE]
        source = lines_to_transcript(lines)
        config = ContentConfig(profile=Profile.FAST)
        texts = prepare(source, config).texts

        by_words = analysis_of(source, config)
        by_meaning = analysis_of(source, config, similarity=index(embedder, texts))

        starts = [int(s.unit_ids[0][1:]) for s in by_meaning.segments[1:]]
        assert starts == [6, 12]  # exactly the two subject changes
        assert len(by_words.segments) == 1  # word overlap sees no boundary at all

    def test_a_restatement_in_other_words_is_found_as_overlap(
        self, embedder: tuple[TransformersSemanticEngine, EmbeddingSettings]
    ) -> None:
        lines = [
            *BATTERIES[3:7],
            "Charging a battery too quickly makes its cells hot.",
            *CARS,
            "Quick charging warms the cells of the battery.",
            *PASTA,
        ]
        source = lines_to_transcript(lines)
        config = ContentConfig(profile=Profile.STANDARD)
        texts = prepare(source, config).texts

        plain = analysis_of(source, config)
        meaning = analysis_of(source, config, similarity=index(embedder, texts))

        def overlaps(a: ContentAnalysis) -> list[Relationship]:
            return [
                r
                for r in a.relationships
                if r.kind in {RelationKind.REPEATS, RelationKind.OVERLAPS}
            ]

        assert len(overlaps(meaning)) > len(overlaps(plain))
        validate_analysis(meaning)

    def test_german_text_is_segmented_by_meaning(
        self, embedder: tuple[TransformersSemanticEngine, EmbeddingSettings]
    ) -> None:
        source = lines_to_transcript(GERMAN, language="de")
        config = ContentConfig(profile=Profile.FAST)
        sim = index(embedder, prepare(source, config).texts)

        a = analysis_of(source, config, similarity=sim)

        topics = [t for t in a.topics if t.level is SegmentLevel.TOPIC]
        assert len(topics) >= 2
        validate_analysis(a)


# --- language model -----------------------------------------------------------------------------
llm_tests = pytest.mark.skipif(
    os.environ.get("MEDIA_HOUSE_REAL_LLM_TESTS") != "1",
    reason="set MEDIA_HOUSE_REAL_LLM_TESTS=1 to run the (slow) real language-model tests",
)


@pytest.fixture(scope="module")
def llm() -> tuple[LanguageModel, LlmSettings]:
    need(LLM_MODEL)
    return TransformersLanguageModel(MODELS), LlmSettings(model=LLM_MODEL, max_output_tokens=1200)


@llm_tests
class TestRealLanguageModel:
    def test_replies_survive_the_grounding_checks_and_improve_the_result(
        self, llm: tuple[LanguageModel, LlmSettings]
    ) -> None:
        model, settings = llm
        assert model.unavailable(settings) is None
        a = analysis_of(lines_to_transcript(THREE_TOPICS))
        chunks = plan_chunks(a, list(range(len(a.segments))), 6000, 1)

        refinements = []
        for chunk in chunks:
            reply = model.complete(
                SYSTEM_PROMPT, build_prompt(a, chunk), settings, CancellationToken()
            )
            refinements.append(
                parse_response(a, chunk, reply.text, len(a.key_items), len(a.relationships))
            )
        merged = merge(a, refinements)

        validate_analysis(merged)
        refined = [
            s for s in merged.segments if s.summary and s.summary.origin is Origin.PARAPHRASE
        ]
        assert refined, [r.rejected for r in refinements]
        assert all(s.summary and s.summary.unit_ids for s in refined)
