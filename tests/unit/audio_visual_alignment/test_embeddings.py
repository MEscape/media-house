"""The optional multilingual embedding step: when it runs, what it computes, how it fails."""

import math
from dataclasses import dataclass

import pytest

from media_house.modules.audio_visual_alignment.application.align import (
    AlignAudioVisual,
    _cache_key,
    _cosine,
    _visual_text,
)
from media_house.modules.audio_visual_alignment.application.resolver import Resolved
from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.content_intelligence.application.encoding import SemanticTextEncoder
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import ExternalSystemError
from tests.support.avl_facts import audio, content, shot, video
from tests.support.ci_engines import FakeSemanticEngine


@dataclass
class FakeEmbedder:
    why: str | None = None
    fail: bool = False
    name: str = "fake/1/model"
    calls: int = 0

    def identity(self) -> str:
        return self.name

    def unavailable(self) -> str | None:
        return self.why

    def embed(self, texts: list[str], cancellation: CancellationToken) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise ExternalSystemError("boom", user_message="the model crashed")
        return [[1.0, 0.0] if "battery" in t else [0.0, 1.0] for t in texts]


def resolved(with_content: bool = True, shots: int = 6) -> Resolved:
    facts = [
        shot(f"s{i}", i * 10, i * 10 + 10, objects=("battery",) if i == 2 else ("chair",))
        for i in range(shots)
    ]
    return Resolved(
        audio=audio([]),
        video=video(facts),
        content=content([("p1", 20.0, 30.0, "the battery pack")]) if with_content else None,
        clock=None,
        records=(),
        identities=(("video", "v"), ("audio", "a"), ("content", "c"), ("inspection", None)),
        warnings=(),
    )


def use_case(embedder: FakeEmbedder | None) -> AlignAudioVisual:
    return AlignAudioVisual(None, None, None, None, embedder)  # type: ignore[arg-type]


ctx = JobContext.detached()


class TestWhenItRuns:
    def test_it_runs_with_content_and_an_available_engine(self) -> None:
        engine = FakeEmbedder()

        assert use_case(engine)._embedder_identity(AlignmentProfile(), resolved()) == "fake/1/model"

    @pytest.mark.parametrize(
        ("embedder", "content", "profile"),
        [
            (None, True, AlignmentProfile()),
            (FakeEmbedder(why="no model"), True, AlignmentProfile()),
            (FakeEmbedder(), False, AlignmentProfile()),
            (FakeEmbedder(), True, AlignmentProfile(stages=("speech",))),
        ],
    )
    def test_otherwise_matching_is_by_words_alone(
        self, embedder: FakeEmbedder | None, content: bool, profile: AlignmentProfile
    ) -> None:
        assert use_case(embedder)._embedder_identity(profile, resolved(content)) is None
        if embedder is not None:
            assert embedder.calls == 0  # an unavailable or unneeded engine is never loaded


class TestWhatItComputes:
    def test_every_passage_is_compared_with_every_shot_that_has_something_to_read(self) -> None:
        engine = FakeEmbedder()

        found, warnings = use_case(engine)._similarities(AlignmentProfile(), resolved(), "x", ctx)

        assert warnings == ()
        assert found is not None
        assert set(found) == {("p1", f"s{i}") for i in range(6)}
        assert found[("p1", "s2")] == pytest.approx(1.0)  # 'battery' with 'battery'
        assert found[("p1", "s0")] == pytest.approx(0.0)
        assert engine.calls == 1  # one batch, not one call per pair

    def test_too_few_shots_are_not_compared_at_all(self) -> None:
        engine = FakeEmbedder()

        found, _ = use_case(engine)._similarities(AlignmentProfile(), resolved(shots=3), "x", ctx)

        assert found == {} and engine.calls == 0

    def test_a_shot_with_nothing_to_read_is_left_out(self) -> None:
        bare = shot("s9", 90, 100)

        assert _visual_text(bare) == ""
        assert (
            _visual_text(shot("s", 0, 1, text=("A",), objects=("b",), description="c")) == "A. b. c"
        )

    def test_a_failing_engine_costs_the_vectors_not_the_run(self) -> None:
        engine = FakeEmbedder(fail=True)

        found, warnings = use_case(engine)._similarities(AlignmentProfile(), resolved(), "x", ctx)

        assert found is None  # None = the engine failed; {} would mean nothing applies
        assert warnings and "the model crashed" in warnings[0] and "words only" in warnings[0]

    def test_cosine_similarity(self) -> None:
        assert _cosine([1, 0], [1, 0]) == 1.0
        assert _cosine([1, 0], [0, 1]) == 0.0
        assert _cosine([0, 0], [1, 1]) == 0.0
        assert _cosine([1, 1], [-1, -1]) == -1.0
        assert math.isclose(_cosine([3, 4], [4, 3]), 0.96, abs_tol=1e-5)


class TestIdentity:
    def test_the_model_is_part_of_the_cache_key_and_absence_is_too(self) -> None:
        profile, r = AlignmentProfile(), resolved()

        none = _cache_key(profile, r, None, None)
        a = _cache_key(profile, r, None, "model-a")
        b = _cache_key(profile, r, None, "model-b")

        assert len({repr(none), repr(a), repr(b)}) == 3

    def test_every_input_identity_is_in_the_key(self) -> None:
        profile = AlignmentProfile()
        base = _cache_key(profile, resolved(), None, None)

        changed = resolved()
        changed = Resolved(
            audio=changed.audio,
            video=changed.video,
            content=changed.content,
            clock=None,
            records=(),
            identities=(("video", "v2"), ("audio", "a"), ("content", "c"), ("inspection", None)),
            warnings=(),
        )

        assert _cache_key(profile, changed, None, None) != base
        assert _cache_key(profile, resolved(), 0.5, None) != base

    def test_how_an_input_arrived_is_not_in_the_key(self) -> None:
        profile, r = AlignmentProfile(), resolved()
        other = Resolved(
            audio=r.audio,
            video=r.video,
            content=r.content,
            clock=None,
            records=(),  # a different provenance record list
            identities=r.identities,
            warnings=("anything",),
        )

        assert _cache_key(profile, r, None, None) == _cache_key(profile, other, None, None)


class TestContentSideEncoder:
    def test_the_contract_adapter_delegates_to_the_one_engine(self) -> None:
        engine = FakeSemanticEngine()
        encoder = SemanticTextEncoder(engine)

        vectors = encoder.embed(["a b", "b c"], CancellationToken())

        assert len(vectors) == 2 and engine.calls == 1
        assert encoder.identity().startswith("fake-embedder/1/")
        assert encoder.unavailable() is None

    def test_an_unavailable_engine_says_why(self) -> None:
        encoder = SemanticTextEncoder(FakeSemanticEngine(available="model missing"))

        assert encoder.unavailable() == "model missing"
