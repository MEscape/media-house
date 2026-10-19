"""Sentence embeddings with a Hugging Face encoder (mean pooling), as a ``SemanticEngine``.

Local-first and offline: the model comes from a directory the user names, from this module's
model directory (``scripts/fetch_models.py`` puts it there), or from the Hugging Face
snapshot cache. A run never downloads. ``torch`` and ``transformers`` are imported lazily, so a
missing library or model makes the engine ``unavailable`` with a reason instead of breaking the app.

The model loads once and is reused. The GPU is used when present (``device`` auto/cuda) and the
engine falls back to the CPU on its own when the GPU fails (for example out of memory). Texts
already embedded are reused from a bounded in-process cache, so re-analysing an edited transcript
embeds only the sentences that changed. The use case also stores the vectors in the Media Library.
"""

import hashlib
import importlib.metadata
import importlib.util
import threading
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from media_house.core.infrastructure.models import local_model_path
from media_house.modules.content_intelligence.application.ports import EngineIdentity
from media_house.modules.content_intelligence.domain.config import EmbeddingSettings
from media_house.modules.content_intelligence.domain.similarity import SimilarityProvider
from media_house.modules.content_intelligence.infrastructure.numpy_similarity import (
    DenseSimilarity,
    Matrix,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_MAX_TOKENS = 256
_CACHE_LIMIT = 50_000


class TransformersSemanticEngine:
    """One instance serves every request; inference is serialised by a lock."""

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = model_dir
        self._lock = threading.Lock()
        self._loaded: tuple[str, Any, Any, str] | None = (
            None  # (model name, tokenizer, model, device)
        )
        self._cache: OrderedDict[str, Matrix] = OrderedDict()
        self._device = "none"

    # --- port --------------------------------------------------------------------------------
    @property
    def device(self) -> str:
        return self._device

    def identity(self, settings: EmbeddingSettings) -> EngineIdentity:
        try:
            version = importlib.metadata.version("transformers")
        except importlib.metadata.PackageNotFoundError:
            version = "missing"
        return EngineIdentity(
            "transformers-mean-pool", f"{version}/l{_MAX_TOKENS}", settings.model or ""
        )

    def unavailable(self, settings: EmbeddingSettings) -> str | None:
        if not settings.model:
            return "no embedding model is configured"
        for library in ("torch", "transformers"):
            if importlib.util.find_spec(library) is None:
                return f"the '{library}' library is not installed"
        try:
            self._resolve(settings)
        except ExternalSystemError as exc:
            return exc.user_message
        return None

    def embed(
        self,
        texts: Sequence[str],
        settings: EmbeddingSettings,
        cancellation: CancellationToken,
    ) -> list[list[float]]:
        with self._lock:
            vectors = self._embed(list(texts), settings, cancellation)
        return [[float(x) for x in row] for row in vectors.tolist()]

    def index(
        self, vectors: Sequence[Sequence[float]], settings: EmbeddingSettings
    ) -> SimilarityProvider:
        matrix = np.asarray(vectors, dtype=np.float32).reshape(len(vectors), -1)
        return DenseSimilarity(matrix, f"embedding:{self.identity(settings).model}")

    # --- model location ----------------------------------------------------------------------
    def _resolve(self, settings: EmbeddingSettings) -> Path:
        name = settings.model or ""
        found = local_model_path(name, self._model_dir)
        if found is None:
            raise ExternalSystemError(
                f"Embedding model {name!r} is not available locally",
                user_message=(
                    f"The embedding model '{name}' is not downloaded. Run "
                    "'python scripts/fetch_models.py' or point to a model folder."
                ),
            )
        return found

    def _load(self, settings: EmbeddingSettings) -> tuple[Any, Any, str]:
        name = settings.model or ""
        if self._loaded is not None and self._loaded[0] == name:
            return self._loaded[1], self._loaded[2], self._loaded[3]
        import torch
        from transformers import AutoModel, AutoTokenizer

        path = self._resolve(settings)
        use_cuda = settings.device in {"auto", "cuda"} and torch.cuda.is_available()
        device = "cuda" if use_cuda else "cpu"
        try:
            tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
            model = AutoModel.from_pretrained(str(path), local_files_only=True).eval()
            model.to(device)
        except Exception as exc:
            raise ExternalSystemError(
                f"Could not load embedding model {name!r}: {exc}",
                user_message="The embedding model could not be loaded.",
            ) from exc
        self._loaded = (name, tokenizer, model, device)
        _log.info("Embedding model loaded", model=name, device=device)
        return tokenizer, model, device

    # --- inference ---------------------------------------------------------------------------
    def _embed(
        self, texts: list[str], settings: EmbeddingSettings, cancellation: CancellationToken
    ) -> Matrix:
        keys = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self._cache]
        if missing:
            tokenizer, model, device = self._load(settings)
            order = sorted(missing, key=lambda i: len(texts[i]))
            for start in range(0, len(order), settings.batch_size):
                cancellation.raise_if_cancelled()
                batch = order[start : start + settings.batch_size]
                vectors, device = self._run(tokenizer, model, device, [texts[i] for i in batch])
                for i, vector in zip(batch, vectors, strict=True):
                    self._cache[keys[i]] = vector
            self._device = device
            while len(self._cache) > _CACHE_LIMIT:
                self._cache.popitem(last=False)
        elif self._loaded is not None:
            self._device = self._loaded[3]
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        for k in keys:
            self._cache.move_to_end(k)
        return np.stack([self._cache[k] for k in keys]).astype(np.float32)

    def _run(self, tokenizer: Any, model: Any, device: str, batch: list[str]) -> tuple[Matrix, str]:
        import torch

        def forward(current: str) -> Matrix:
            encoded = tokenizer(
                batch, padding=True, truncation=True, max_length=_MAX_TOKENS, return_tensors="pt"
            )
            encoded = {k: v.to(current) for k, v in encoded.items()}
            with torch.inference_mode():
                hidden = model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
            return np.asarray(pooled.float().cpu().numpy(), dtype=np.float32)

        try:
            return forward(device), device
        except RuntimeError as exc:
            if device == "cpu":
                raise ExternalSystemError(
                    f"Embedding inference failed: {exc}", user_message="The embedding model failed."
                ) from exc
            _log.warning(
                "Embedding on the GPU failed, falling back to the CPU", reason=str(exc)[:80]
            )
            torch.cuda.empty_cache()
            model.to("cpu")
            if self._loaded is not None:
                self._loaded = (self._loaded[0], self._loaded[1], model, "cpu")
            try:
                return forward("cpu"), "cpu"
            except RuntimeError as again:
                raise ExternalSystemError(
                    f"Embedding inference failed: {again}",
                    user_message="The embedding model failed.",
                ) from again
