"""An instruct language model from Hugging Face, run in this process (``LanguageModel`` adapter).

Local-first and offline: the model comes from a directory the user names, from this module's model
folder (``scripts/fetch_models.py`` puts it there) or from the Hugging Face snapshot cache
with ``local_files_only``. It is downloaded only by that explicit script, never by a run.

Decoding is greedy (``temperature == 0``), so the same prompt gives the same reply on the same
hardware. The reply is primed with an opening brace so the model continues a JSON object; the
checks in ``domain/refine.py`` decide what is kept. Weights load once and stay loaded; inference
is serialised. On a CUDA device the weights run in fp16, on the CPU in bfloat16 (slow).
"""

import importlib.metadata
import importlib.util
import threading
from pathlib import Path
from typing import Any

from media_house.core.infrastructure.models import local_model_path
from media_house.modules.content_intelligence.application.ports import EngineIdentity, LlmReply
from media_house.modules.content_intelligence.domain.config import LlmSettings
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import ExternalSystemError, OperationCancelledError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

_PRIME = "{"
#: Weights are loaded in 16 bit; activations and the key/value cache need headroom on top.
_HEADROOM_FACTOR = 1.25
_HEADROOM_BYTES = 1_000_000_000


def fits_in_memory(weight_bytes: int, free_bytes: int) -> bool:
    """Whether a model of ``weight_bytes`` can run in ``free_bytes`` of device memory.

    A model that does not fit would not fail: the driver would page it through system memory and
    generation would crawl at a fraction of a token per second. It is refused up front instead.
    """
    return weight_bytes * _HEADROOM_FACTOR + _HEADROOM_BYTES <= free_bytes


class TransformersLanguageModel:
    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = model_dir
        self._lock = threading.Lock()
        self._loaded: tuple[str, Any, Any, str] | None = None  # (name, tokenizer, model, device)

    # --- port --------------------------------------------------------------------------------
    def identity(self, settings: LlmSettings) -> EngineIdentity:
        try:
            version = importlib.metadata.version("transformers")
        except importlib.metadata.PackageNotFoundError:
            version = "missing"
        return EngineIdentity("transformers-causal-lm", f"{version}/greedy-prime1", settings.model)

    def unavailable(self, settings: LlmSettings) -> str | None:
        if not settings.model:
            return "no language model is configured"
        for library in ("torch", "transformers"):
            if importlib.util.find_spec(library) is None:
                return f"the '{library}' library is not installed"
        try:
            self._resolve(settings.model)
        except ExternalSystemError as exc:
            return exc.user_message
        return None

    def complete(
        self,
        system: str,
        user: str,
        settings: LlmSettings,
        cancellation: CancellationToken,
    ) -> LlmReply:
        cancellation.raise_if_cancelled()
        with self._lock:
            tokenizer, model, device = self._load(settings.model)
            return self._generate(tokenizer, model, device, system, user, settings, cancellation)

    # --- model location and loading ----------------------------------------------------------
    def _resolve(self, name: str) -> Path:
        found = local_model_path(name, self._model_dir)
        if found is None:
            raise ExternalSystemError(
                f"Language model {name!r} is not available locally",
                user_message=(
                    f"The language model '{name}' is not downloaded. Run "
                    "'python scripts/fetch_models.py' or point to a model folder."
                ),
            )
        return found

    def _load(self, name: str) -> tuple[Any, Any, str]:
        if self._loaded is not None and self._loaded[0] == name:
            return self._loaded[1], self._loaded[2], self._loaded[3]
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        path = self._resolve(name)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.bfloat16
        if device == "cuda":
            weights = sum(f.stat().st_size for f in path.glob("*.safetensors"))
            free = int(torch.cuda.mem_get_info()[0])
            if weights and not fits_in_memory(weights, free):
                raise ExternalSystemError(
                    f"Language model {name!r} needs ~{weights / 1e9:.1f} GB, only "
                    f"{free / 1e9:.1f} GB of GPU memory is free",
                    user_message=(
                        f"The language model '{name}' does not fit into the free GPU memory "
                        f"({free / 1e9:.1f} GB). Use a smaller model or free GPU memory."
                    ),
                )
        else:
            _log.warning("No GPU: the language model runs on the CPU and will be slow", model=name)
        try:
            tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
            model = AutoModelForCausalLM.from_pretrained(
                str(path), local_files_only=True, dtype=dtype
            ).eval()
            model.to(device)
        except Exception as exc:
            raise ExternalSystemError(
                f"Could not load language model {name!r}: {exc}",
                user_message="The language model could not be loaded (not enough memory?).",
            ) from exc
        self._loaded = (name, tokenizer, model, device)
        _log.info("Language model loaded", model=name, device=device)
        return tokenizer, model, device

    # --- inference ---------------------------------------------------------------------------
    def _generate(
        self,
        tokenizer: Any,
        model: Any,
        device: str,
        system: str,
        user: str,
        settings: LlmSettings,
        cancellation: CancellationToken,
    ) -> LlmReply:
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList

        class Cancelled(StoppingCriteria):
            def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> Any:
                return torch.full(
                    (input_ids.shape[0],),
                    cancellation.is_cancelled,
                    dtype=torch.bool,
                    device=input_ids.device,
                )

        text = tokenizer.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            tokenize=False,
            add_generation_prompt=True,
        )
        encoded = tokenizer(text + _PRIME, return_tensors="pt", add_special_tokens=False)
        limit = int(getattr(model.config, "max_position_embeddings", 4096))
        prompt_tokens = int(encoded["input_ids"].shape[1])
        if prompt_tokens + settings.max_output_tokens > limit:
            raise ExternalSystemError(
                f"Prompt of {prompt_tokens} tokens exceeds the model context of {limit}",
                user_message="The text is too long for this language model.",
            )
        encoded = {k: v.to(device) for k, v in encoded.items() if k != "token_type_ids"}
        sampling = settings.temperature > 0
        if sampling:
            torch.manual_seed(settings.seed)
        try:
            with torch.inference_mode():
                output = model.generate(
                    **encoded,
                    max_new_tokens=settings.max_output_tokens,
                    do_sample=sampling,
                    temperature=settings.temperature if sampling else None,
                    top_p=None,
                    top_k=None,
                    stopping_criteria=StoppingCriteriaList([Cancelled()]),
                    pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                )
        except RuntimeError as exc:
            if device == "cuda":
                torch.cuda.empty_cache()
            raise ExternalSystemError(
                f"Language model inference failed: {exc}",
                user_message="The language model ran out of memory or failed.",
            ) from exc
        cancellation.raise_if_cancelled()
        new_tokens = output[0, prompt_tokens:]
        reply = tokenizer.decode(new_tokens, skip_special_tokens=True)
        if cancellation.is_cancelled:
            raise OperationCancelledError("Operation was cancelled")
        return LlmReply(_PRIME + reply, prompt_tokens, int(new_tokens.shape[0]))
