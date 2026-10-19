"""The two model adapters, tested without any real model or any network beyond loopback.

* The embedding adapter runs a tiny randomly initialised BERT built in the test: it proves the
  plumbing (loading, pooling, batching, caching, device choice, offline behaviour), not the
  semantic quality of any real model.
* The language-model adapter talks to a stub HTTP server on 127.0.0.1.
"""

import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from media_house.modules.content_intelligence.application.ports import EngineIdentity, LlmReply
from media_house.modules.content_intelligence.domain.config import EmbeddingSettings, LlmSettings
from media_house.modules.content_intelligence.infrastructure.loopback_llm import LoopbackChatModel
from media_house.modules.content_intelligence.infrastructure.numpy_similarity import DenseSimilarity
from media_house.modules.content_intelligence.infrastructure.transformers_embedder import (
    TransformersSemanticEngine,
)
from media_house.modules.content_intelligence.infrastructure.transformers_llm import (
    TransformersLanguageModel,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError, OperationCancelledError

pytestmark = pytest.mark.integration

SENTENCES = [
    "batteries store energy in chemical cells",
    "charging a battery too fast heats the cells",
    "tomatoes need sunlight and water in the garden",
    "the garden soil should be rich in compost",
    "the yearly budget covers salaries and rent",
]


# --- dense similarity ---------------------------------------------------------------------------
class TestDenseSimilarity:
    def vectors(self) -> DenseSimilarity:
        rng = np.random.default_rng(3)
        base = rng.normal(size=(40, 8)).astype(np.float32)
        base[1] = base[0] + 0.01  # a near duplicate of sentence 0
        return DenseSimilarity(base, "test")

    def test_neighbours_are_bounded_ordered_and_never_the_sentence_itself(self) -> None:
        sim = self.vectors()

        near = sim.neighbours(3, 0.0)

        assert all(len(v) <= 3 for v in near.values())
        assert all(i not in [j for j, _ in v] for i, v in near.items())
        assert near[0][0][0] == 1 and near[0][0][1] > 0.99
        assert all(
            [s for _, s in v] == sorted((s for _, s in v), reverse=True) for v in near.values()
        )

    def test_neighbours_are_identical_between_runs_and_respect_the_floor(self) -> None:
        a, b = self.vectors(), self.vectors()

        assert a.neighbours(5, 0.2) == b.neighbours(5, 0.2)
        assert all(s >= 0.2 for v in a.neighbours(5, 0.2).values() for _, s in v)

    def test_many_sentences_are_processed_in_blocks(self) -> None:
        rng = np.random.default_rng(1)
        sim = DenseSimilarity(rng.normal(size=(1300, 16)).astype(np.float32), "big")

        near = sim.neighbours(4, -1.0)

        assert len(near) == 1300 and all(len(v) == 4 for v in near.values())

    def test_group_and_window_similarity(self) -> None:
        sim = self.vectors()

        assert sim.group_similarity([0], [1]) > 0.99
        assert sim.group_similarity([], [1]) == 0.0
        assert 0.0 <= sim.window_similarity(10, 3) <= 1.0
        assert sim.similarity(0, 0) == pytest.approx(1.0)


# --- embedding adapter --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def tiny_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    directory = tmp_path_factory.mktemp("tiny-bert")
    words = sorted({w for s in SENTENCES for w in s.split()})
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", *words]
    (directory / "vocab.txt").write_text("\n".join(vocab), encoding="utf-8")
    tokenizer = transformers.BertTokenizerFast(vocab_file=str(directory / "vocab.txt"))
    config = transformers.BertConfig(
        vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1, num_attention_heads=2,
        intermediate_size=32, max_position_embeddings=64,
    )  # fmt: skip
    torch.manual_seed(0)
    transformers.BertModel(config).save_pretrained(directory)
    tokenizer.save_pretrained(directory)
    return directory


def build_index(
    engine: TransformersSemanticEngine,
    texts: list[str],
    settings: EmbeddingSettings,
    token: CancellationToken,
) -> DenseSimilarity:
    sim = engine.index(engine.embed(texts, settings, token), settings)
    assert isinstance(sim, DenseSimilarity)
    return sim


class TestTransformersEngine:
    def settings(self, model: Path | str, **changes: object) -> EmbeddingSettings:
        return EmbeddingSettings(model=str(model), device="cpu", **changes)  # type: ignore[arg-type]

    def test_a_local_model_directory_makes_the_engine_available(self, tiny_model: Path) -> None:
        assert TransformersSemanticEngine().unavailable(self.settings(tiny_model)) is None

    def test_sentences_become_comparable_vectors_on_the_cpu(self, tiny_model: Path) -> None:
        engine = TransformersSemanticEngine()

        sim = build_index(engine, SENTENCES, self.settings(tiny_model), CancellationToken())

        assert sim.kind == "embedding"
        assert engine.device == "cpu"
        assert all(0.0 <= sim.similarity(0, j) <= 1.0 for j in range(len(SENTENCES)))
        assert sim.similarity(2, 2) == pytest.approx(1.0)

    def test_the_result_does_not_depend_on_batching_or_the_engine_instance(
        self, tiny_model: Path
    ) -> None:
        one = build_index(
            TransformersSemanticEngine(),
            SENTENCES,
            self.settings(tiny_model, batch_size=1),
            CancellationToken(),
        )
        two = build_index(
            TransformersSemanticEngine(),
            SENTENCES,
            self.settings(tiny_model, batch_size=64),
            CancellationToken(),
        )

        assert [one.similarity(0, j) for j in range(5)] == pytest.approx(
            [two.similarity(0, j) for j in range(5)], abs=1e-5
        )

    def test_texts_already_embedded_are_not_embedded_again(
        self, tiny_model: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = TransformersSemanticEngine()
        embedded: list[int] = []
        original = engine._run

        def spy(tokenizer, model, device, batch):  # type: ignore[no-untyped-def]
            embedded.append(len(batch))
            return original(tokenizer, model, device, batch)

        monkeypatch.setattr(engine, "_run", spy)
        settings = self.settings(tiny_model)

        build_index(engine, SENTENCES, settings, CancellationToken())
        first = sum(embedded)
        build_index(
            engine,
            [*SENTENCES, "a brand new sentence about batteries"],
            settings,
            CancellationToken(),
        )

        assert first == len(SENTENCES) and sum(embedded) - first == 1

    def test_a_missing_model_is_unavailable_offline_with_a_reason(self, tmp_path: Path) -> None:
        engine = TransformersSemanticEngine(tmp_path)

        started = time.perf_counter()
        reason = engine.unavailable(EmbeddingSettings(model="nobody/not-a-real-model"))

        assert reason is not None and "not downloaded" in reason
        assert time.perf_counter() - started < 20  # local files only: no network attempt

    def test_no_model_name_is_a_clear_reason(self) -> None:
        assert "no embedding model" in (
            TransformersSemanticEngine().unavailable(EmbeddingSettings()) or ""
        )

    def test_cancellation_is_honoured_between_batches(self, tiny_model: Path) -> None:
        token = CancellationToken()
        token.cancel()

        with pytest.raises(OperationCancelledError):
            build_index(TransformersSemanticEngine(), SENTENCES, self.settings(tiny_model), token)

    def test_a_failing_gpu_falls_back_to_the_cpu(self, tiny_model: Path) -> None:
        torch = pytest.importorskip("torch")
        if not torch.cuda.is_available():
            pytest.skip("no CUDA device to fail on")
        from transformers import AutoModel, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(str(tiny_model))
        real = AutoModel.from_pretrained(str(tiny_model)).eval()

        class FlakyGpu:
            """Behaves like the model but raises an out-of-memory error while on the GPU."""

            on = "cpu"

            def to(self, device: str) -> "FlakyGpu":
                self.on = device
                real.to(device)
                return self

            def __call__(self, **kwargs):  # type: ignore[no-untyped-def]
                if self.on == "cuda":
                    raise RuntimeError("CUDA out of memory (simulated)")
                return real(**kwargs)

        engine = TransformersSemanticEngine()
        flaky = FlakyGpu().to("cuda")

        vectors, device = engine._run(tokenizer, flaky, "cuda", SENTENCES)

        assert device == "cpu" and vectors.shape[0] == len(SENTENCES)


# --- language-model adapter ---------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    seen: list[dict[str, object]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append({"path": self.path, "body": body})
        if self.mode == "error":
            self.send_response(500)
            self.end_headers()
            return
        payload = (
            b"not json"
            if self.mode == "garbage"
            else json.dumps(
                {
                    "choices": [{"message": {"content": '{"segments": []}'}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 4},
                }
            ).encode()
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def server() -> Iterator[tuple[int, type[_Handler]]]:
    handler = type("Handler", (_Handler,), {"mode": "ok", "seen": []})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], handler
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


class TestLoopbackModel:
    def settings(self, port: int) -> LlmSettings:
        return LlmSettings(
            endpoint=f"http://127.0.0.1:{port}/v1", model="tiny", timeout_seconds=5.0
        )

    def test_a_reply_and_its_token_counts_come_back(
        self, server: tuple[int, type[_Handler]]
    ) -> None:
        port, handler = server

        reply = LoopbackChatModel().complete(
            "system text", "user text", self.settings(port), CancellationToken()
        )

        assert reply.text == '{"segments": []}' and (
            reply.prompt_tokens,
            reply.completion_tokens,
        ) == (11, 4)
        sent = handler.seen[0]
        assert sent["path"] == "/v1/chat/completions"
        body = sent["body"]
        assert isinstance(body, dict)
        assert body["model"] == "tiny" and body["temperature"] == 0.0 and body["seed"] == 7
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        assert "authorization" not in json.dumps(sent).casefold()

    def test_the_server_being_reachable_is_checked_cheaply(
        self, server: tuple[int, type[_Handler]]
    ) -> None:
        assert LoopbackChatModel().unavailable(self.settings(server[0])) is None

    def test_nothing_listening_is_a_reason_not_an_exception(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        assert "nothing is listening" in (
            LoopbackChatModel().unavailable(self.settings(port)) or ""
        )

    @pytest.mark.parametrize("mode", ["error", "garbage"])
    def test_server_problems_are_typed_errors(
        self, server: tuple[int, type[_Handler]], mode: str
    ) -> None:
        port, handler = server
        handler.mode = mode

        with pytest.raises(ExternalSystemError):
            LoopbackChatModel().complete("s", "u", self.settings(port), CancellationToken())

    def test_a_remote_endpoint_is_refused_unless_explicitly_allowed(self) -> None:
        remote = LlmSettings(endpoint="https://api.example.com/v1", model="x")
        model = LoopbackChatModel()

        assert "must run on this computer" in (model.unavailable(remote) or "")
        with pytest.raises(ExternalSystemError):
            model.complete("s", "u", remote, CancellationToken())
        allowed = LlmSettings(
            endpoint="https://api.example.com/v1", model="x", allow_remote_endpoint=True
        )
        assert "must run on this computer" not in (model.unavailable(allowed) or "")

    def test_only_http_urls_are_accepted(self) -> None:
        assert (
            LoopbackChatModel().unavailable(LlmSettings(endpoint="file:///etc/passwd", model="x"))
            is not None
        )

    def test_cancellation_is_checked_before_sending(
        self, server: tuple[int, type[_Handler]]
    ) -> None:
        token = CancellationToken()
        token.cancel()

        with pytest.raises(OperationCancelledError):
            LoopbackChatModel().complete("s", "u", self.settings(server[0]), token)
        assert server[1].seen == []


# --- in-process language-model adapter ----------------------------------------------------------
@pytest.fixture(scope="module")
def tiny_chat_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A randomly initialised 2-layer causal LM with a word-level tokenizer and a chat template."""
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    tokenizers = pytest.importorskip("tokenizers")
    directory = tmp_path_factory.mktemp("tiny-chat")
    words = sorted({w for s in SENTENCES for w in s.split()} | {"{", "}", '"', ":", ",", "[", "]"})
    vocab = {"<pad>": 0, "<unk>": 1, "<eos>": 2, **{w: i + 3 for i, w in enumerate(words)}}
    core = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocab, unk_token="<unk>"))
    core.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer = transformers.PreTrainedTokenizerFast(
        tokenizer_object=core, pad_token="<pad>", unk_token="<unk>", eos_token="<eos>"
    )
    tokenizer.chat_template = (
        "{% for m in messages %}{{ m['content'] }} {% endfor %}{% if add_generation_prompt %}"
        "reply {% endif %}"
    )
    config = transformers.LlamaConfig(
        vocab_size=len(vocab), hidden_size=32, intermediate_size=64, num_hidden_layers=2,
        num_attention_heads=2, num_key_value_heads=2, max_position_embeddings=256,
        pad_token_id=0, eos_token_id=2,
    )  # fmt: skip
    torch.manual_seed(0)
    transformers.LlamaForCausalLM(config).save_pretrained(directory)
    tokenizer.save_pretrained(directory)
    return directory


class TestTransformersLanguageModel:
    def settings(self, model: Path | str, **changes: object) -> LlmSettings:
        return LlmSettings(model=str(model), max_output_tokens=12, **changes)  # type: ignore[arg-type]

    def test_a_local_model_directory_makes_it_available(self, tiny_chat_model: Path) -> None:
        assert TransformersLanguageModel().unavailable(self.settings(tiny_chat_model)) is None

    def test_a_missing_model_is_unavailable_offline_with_the_fetch_hint(
        self, tmp_path: Path
    ) -> None:
        reason = TransformersLanguageModel(tmp_path).unavailable(
            LlmSettings(model="nobody/not-a-model")
        )

        assert reason is not None and "fetch_models" in reason

    def test_no_model_name_is_a_clear_reason(self) -> None:
        assert "no language model" in (TransformersLanguageModel().unavailable(LlmSettings()) or "")

    def test_the_reply_continues_the_primed_json_and_is_deterministic(
        self, tiny_chat_model: Path
    ) -> None:
        model = TransformersLanguageModel()
        settings = self.settings(tiny_chat_model)

        first = model.complete("sys", "batteries store energy", settings, CancellationToken())
        second = TransformersLanguageModel().complete(
            "sys", "batteries store energy", settings, CancellationToken()
        )

        assert first.text.startswith("{") and first.text == second.text
        assert first.prompt_tokens and 0 < (first.completion_tokens or 0) <= 12

    def test_an_oversized_prompt_is_refused_before_generation(self, tiny_chat_model: Path) -> None:
        with pytest.raises(ExternalSystemError, match="exceeds the model context"):
            TransformersLanguageModel().complete(
                "sys",
                " ".join(["battery"] * 400),
                self.settings(tiny_chat_model),
                CancellationToken(),
            )

    def test_cancellation_is_honoured(self, tiny_chat_model: Path) -> None:
        token = CancellationToken()
        token.cancel()

        with pytest.raises(OperationCancelledError):
            TransformersLanguageModel().complete("s", "u", self.settings(tiny_chat_model), token)


class TestMemoryGuard:
    def test_a_model_is_refused_when_it_would_spill_out_of_gpu_memory(self) -> None:
        from media_house.modules.content_intelligence.infrastructure.transformers_llm import (
            fits_in_memory,
        )

        gb = 1_000_000_000

        assert fits_in_memory(3 * gb, 7 * gb)  # 1.5 B parameters in fp16 on an 8 GB card
        assert not fits_in_memory(6 * gb, 7 * gb)  # 3 B parameters: would page through RAM
        assert not fits_in_memory(6 * gb, 6 * gb)


class TestRouting:
    def test_an_endpoint_selects_the_served_model_and_none_the_local_one(self) -> None:
        from media_house.modules.content_intelligence.infrastructure.routed_llm import (
            RoutedLanguageModel,
        )

        class Named:
            def __init__(self, name: str) -> None:
                self.name = name

            def identity(self, settings: LlmSettings) -> EngineIdentity:
                return EngineIdentity(self.name, "1", settings.model)

            def unavailable(self, settings: LlmSettings) -> str | None:
                return self.name

            def complete(self, *args: object) -> LlmReply:
                return LlmReply(self.name)

        routed = RoutedLanguageModel(Named("local"), Named("served"))

        assert routed.identity(LlmSettings(model="m")).name == "local"
        assert (
            routed.identity(LlmSettings(model="m", endpoint="http://127.0.0.1:1/v1")).name
            == "served"
        )
