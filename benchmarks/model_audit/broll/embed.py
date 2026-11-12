# ruff: noqa: T201, TID251 - benchmark CLI
"""Step 2: embed queries and/or shots with ONE model, in its own process.

    uv run python benchmarks/model_audit/broll/embed.py clip_current   # A: pipeline text encoder
    uv run python benchmarks/model_audit/broll/embed.py clip_frames    # control: CLIP image tower
    uv run python benchmarks/model_audit/broll/embed.py mclip          # B: multilingual text tower
    uv run python benchmarks/model_audit/broll/embed.py siglip2        # C: both towers

Writes ``OUT/emb_<name>.npz`` (``q`` query vectors and/or ``s`` shot vectors, unit length) and
``OUT/emb_<name>.json`` (timings, memory, model identity). CPU only.
"""

import os
import sys
import time

os.environ["CUDA_VISIBLE_DEVICES"] = ""

import numpy as np
from common import (
    CLIP,
    FRAMES,
    FRAMES_PER_SHOT,
    MCLIP,
    OUT,
    SIGLIP2,
    memory_mb,
    model_dir,
    read_json,
    write_json,
)

_BATCH = 16  # frames per image batch, as the pipeline (clip_engines._BATCH)


def unit(x: np.ndarray) -> np.ndarray:
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def per_query_seconds(encode, texts: list[str]) -> float:
    encode(texts[:1])  # warm-up
    t = time.perf_counter()
    for text in texts:
        encode([text])
    return (time.perf_counter() - t) / len(texts)


def shot_vectors(encode_images, pool: list[dict]) -> tuple[np.ndarray, float, int]:
    """Mean of the per-frame embeddings of each shot, normalised (as derive.shot_embedding does)."""
    from PIL import Image

    files = [FRAMES / f"{n:04d}_{k}.png" for n in range(len(pool)) for k in range(FRAMES_PER_SHOT)]
    images = [Image.open(f).convert("RGB") for f in files]
    encode_images(images[:2])  # warm-up
    t = time.perf_counter()
    rows = np.concatenate(
        [encode_images(images[i : i + _BATCH]) for i in range(0, len(images), _BATCH)]
    )
    seconds = time.perf_counter() - t
    rows = rows.reshape(len(pool), FRAMES_PER_SHOT, -1)
    return unit(unit(rows).mean(axis=1)), seconds, len(images)


def main(name: str) -> None:
    import torch

    torch.manual_seed(0)
    queries = [q["text"] for q in read_json(OUT / "queries.json")]
    pool = read_json(OUT / "pool.json")["shots"]
    _, base_peak = memory_mb()
    info: dict = {
        "name": name,
        "torch_threads": torch.get_num_threads(),
        "rss_after_imports_mb": base_peak,
    }
    out: dict[str, np.ndarray] = {}
    text = images = None

    t0 = time.perf_counter()
    if name == "clip_current":
        # The pipeline's own text code path: the class the video_intelligence module registers.
        from media_house.modules.video_intelligence.infrastructure.clip_engines import (
            ClipRuntime,
            ClipTextEncoder,
        )
        from media_house.shared.concurrency import CancellationToken

        encoder = ClipTextEncoder(ClipRuntime())
        info["identity"] = encoder.identity()
        info["identity_matches_stored"] = (
            encoder.identity() in read_json(OUT / "pool.json")["embedding_models"]
        )
        token = CancellationToken()
        encoder.embed(["warm"], token)  # forces the lazy model load
        info["load_s"] = time.perf_counter() - t0

        def text(xs: list[str]) -> np.ndarray:
            return np.asarray(encoder.embed(xs, token), dtype=np.float32)

    elif name == "clip_frames":
        from huggingface_hub import snapshot_download
        from transformers import CLIPModel, CLIPProcessor

        snap = snapshot_download(CLIP[0], revision=CLIP[1], local_files_only=True)
        model = CLIPModel.from_pretrained(snap).eval()
        proc = CLIPProcessor.from_pretrained(snap)
        info["identity"] = f"{CLIP[0]}@{CLIP[1]}"
        info["load_s"] = time.perf_counter() - t0

        def images(xs):
            with torch.inference_mode():
                return model.get_image_features(**proc(images=xs, return_tensors="pt")).numpy()

    elif name == "mclip":
        # sentence-transformers is not installed: its 3 modules re-implemented with transformers
        # (Transformer -> mean pooling over the attention mask -> Dense 768->512, no bias).
        from safetensors.torch import load_file
        from transformers import AutoModel, AutoTokenizer

        d = model_dir(MCLIP)
        tok = AutoTokenizer.from_pretrained(d)
        bert = AutoModel.from_pretrained(d).eval()
        dense = torch.nn.Linear(768, 512, bias=False)
        dense.weight.data = load_file(str(d / "2_Dense" / "model.safetensors"))["linear.weight"]
        info["identity"] = f"{MCLIP[0]}@{MCLIP[1]} (text) + stored {CLIP[0]} image vectors"
        info["load_s"] = time.perf_counter() - t0

        def text(xs: list[str]) -> np.ndarray:
            batch = tok(xs, padding=True, truncation=True, max_length=128, return_tensors="pt")
            with torch.inference_mode():
                hidden = bert(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1).float()
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                return dense(pooled).numpy()

    elif name == "siglip2":
        from transformers import AutoModel, AutoProcessor

        d = model_dir(SIGLIP2)
        model = AutoModel.from_pretrained(d).eval()
        proc = AutoProcessor.from_pretrained(d)
        info["identity"] = f"{SIGLIP2[0]}@{SIGLIP2[1]}"
        info["load_s"] = time.perf_counter() - t0

        def text(xs: list[str]) -> np.ndarray:
            # Trained on lowercased text with max_length padding to 64 (model card / HF docs).
            batch = proc(
                text=[x.lower() for x in xs],
                padding="max_length",
                max_length=64,
                truncation=True,
                return_tensors="pt",
            )
            with torch.inference_mode():
                return model.get_text_features(**batch).numpy()

        def images(xs):
            with torch.inference_mode():
                return model.get_image_features(**proc(images=xs, return_tensors="pt")).numpy()

    else:
        raise SystemExit(f"unknown model {name}")

    if text is not None:
        out["q"] = unit(text(queries).astype(np.float32))
        info["per_query_s"] = per_query_seconds(text, queries)
        info["queries"] = len(queries)
    if images is not None:
        out["s"], seconds, frames = shot_vectors(images, pool)
        info["image_s_total"] = seconds
        info["per_frame_s"] = seconds / frames
        info["per_shot_s"] = seconds / len(pool)
        info["frames"] = frames
    _, peak = memory_mb()
    info["peak_rss_mb"] = peak
    info["model_delta_peak_mb"] = peak - base_peak
    np.savez(OUT / f"emb_{name}.npz", **out)
    write_json(OUT / f"emb_{name}.json", info)
    print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in info.items()})


if __name__ == "__main__":
    main(sys.argv[1])
