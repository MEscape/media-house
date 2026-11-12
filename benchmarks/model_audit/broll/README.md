# B-roll text-to-picture retrieval benchmark

Measures the picture stage of `broll_intelligence` (German passage -> text tower -> cosine against
shot image embeddings) for three encoders, on CPU, with identical queries and an identical shot pool.
Context: `docs/model_audit/MODEL_RESEARCH.md` §3, `docs/model_audit/PIPELINE_AUDIT.md`.

No source module is changed. All data (pool, transcript sentences, labels, frames, model copies,
embeddings, results) is written to `C:/Users/L14/.media_house_scratch/benchmarks/broll/`
(`BROLL_BENCH_OUT`), outside the repo, because the footage and speech are under NDA.

## Run

```
uv run python benchmarks/model_audit/broll/prepare.py          # pool, queries, 5 frames per shot
uv run python benchmarks/model_audit/broll/embed.py clip_current
uv run python benchmarks/model_audit/broll/embed.py mclip
uv run python benchmarks/model_audit/broll/embed.py clip_frames
uv run python benchmarks/model_audit/broll/embed.py siglip2
uv run python benchmarks/model_audit/broll/score.py             # -> OUT/results.md, results.json
```

`prepare.py` starts the app on the library home (`BROLL_BENCH_HOME`, default the `baseline` home) and
reads stored data only: Media Library (video originals), `ShotIndexCatalog` (shots + stored CLIP shot
vectors), `AudioCatalog` (words -> sentences with `production_project` `sentence_lines`). Nothing is
analysed; the pipeline is not run. Each `embed.py` call is its own process so load time and peak
working set belong to one model.

## Data

* **Pool:** every shot of every video original in the library that has a stored CLIP embedding
  (95 of 98 shots, 25 of 28 video assets; the 3 `mesc_*` intro/outro clips have no analysis).
  Stills are not in the pool (not video).
* **Owner labels:** the 6 `cover` entries of the owner's hand brief (`_qa/v8/run.json`): spoken
  phrase -> footage file + in/out seconds. A query is the transcript sentence that says the phrase
  (with `until`: every sentence up to the one that says the end phrase), cut at 200 characters as
  `retrieval.query_text` does. Relevant = shots of the labelled file overlapping the labelled range.
  Labels whose phrases sit in the same sentence share one query: 6 labels -> **4 queries**.
* **Extra labels (not the owner's):** `OUT/labels_extra.json`, 5 entries -> **4 queries**, each with
  its provenance (a file name that names what the sentence says, e.g. a stock clip named after
  fireworks in the night sky <-> a sentence about effects in the sky). No range: any shot of the file.
  They are reported separately ("owner" vs "all").
* No auto-brief output (`auto_brief.json`) exists in the scratch homes, so no auto-brief labels were
  added (producing one means running part of the pipeline).

## Systems

| id | text | shots | notes |
| --- | --- | --- | --- |
| A | `openai/clip-vit-base-patch32` via the pipeline's own `ClipTextEncoder(ClipRuntime())` | stored vectors | identity equals the stored `embedding_model` (checked) |
| A' | same | CLIP image tower on the 5 extracted frames, mean, normalised | control for the frame extraction (stored vs re-embedded cosine median 0.993, min 0.927) |
| B | `sentence-transformers/clip-ViT-B-32-multilingual-v1` | stored vectors | sentence-transformers is not installed: Transformer -> masked mean pooling -> Dense 768->512 (no bias) re-implemented with transformers + safetensors |
| B' | same | A' frames | |
| C | `google/siglip2-base-patch16-224` | SigLIP 2 image tower on the same 5 frames | lowercased text, `padding="max_length", max_length=64` (model docs) |
| AB | mean of the A and B query vectors (= mean of the two cosines) | stored vectors | added after seeing A and B: post hoc, untuned 0.5/0.5 |

Stored shot vector = normalised mean of the per-frame CLIP embeddings inside the shot
(`video_intelligence/domain/meaning.py` `shot_embedding`); A'/B'/C use 5 frames per shot at the
centres of 5 equal parts (ffmpeg, 640 px wide PNG).

| model | revision | licence |
| --- | --- | --- |
| openai/clip-vit-base-patch32 | 3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268 | MIT code; model card scopes use to research ("deployed use cases ... out of scope") |
| sentence-transformers/clip-ViT-B-32-multilingual-v1 | 58edf8cada9e398793dca955574a48cbb7f18be2 | Apache-2.0 (text model only; the image side stays OpenAI CLIP) |
| google/siglip2-base-patch16-224 | 75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2 | Apache-2.0 |

The two new models were downloaded with `curl` into `OUT/models/` (the antivirus TLS proxy on this
machine breaks Python's certificate check); CLIP comes from the existing HF cache.

## Metrics

Recall@k = share of queries with at least one relevant shot in the top k (hit rate). MRR = mean of
1 / rank of the first relevant shot. "file" columns count any shot of a labelled file as relevant.
"random E[rank]" = (N + 1) / (R + 1), the expected first-relevant rank of a random order.
Timings are wall-clock on a shared machine (other jobs were running): treat them as rough.

## Results (2026-11-11)

Owner queries (n = 4):

| system | R@1 | R@5 | R@10 | MRR |
| --- | --- | --- | --- | --- |
| A current | 0.25 | 0.25 | 0.50 | 0.334 |
| B mclip text | 0.25 | 0.50 | 0.50 | 0.318 |
| C SigLIP 2 | 0.25 | 0.25 | 0.25 | 0.289 |
| AB fusion | 0.25 | 1.00 | 1.00 | 0.508 |

All queries (n = 8):

| system | R@1 | R@5 | R@10 | MRR |
| --- | --- | --- | --- | --- |
| A current | 0.25 | 0.25 | 0.38 | 0.323 |
| B mclip text | 0.50 | 0.62 | 0.62 | 0.538 |
| C SigLIP 2 | 0.38 | 0.38 | 0.50 | 0.411 |
| AB fusion | 0.38 | 0.75 | 0.88 | 0.530 |

Per-query ranks (owner Q1-Q4, extra Q5-Q8; random E[rank] in brackets):
A 13, 11, 1, 6 | 1, 11, 11, 15 — B 20, 1, 50, 5 | 1, 1, 1, 30 — C 17, 21, 1, 20 | 1, 9, 1, 58 —
AB 5, 2, 1, 3 | 1, 6, 1, 23 (random 32, 24, 12, 48 | 4, 8, 2.5, 48).

Cost (CPU, 8 threads): load ~20-25 s each (includes the transformers import); text ~0.22-0.27 s per
query for all three (contended machine); image tower per frame CLIP B/32 0.11 s, SigLIP 2 0.69 s
(~6x); peak working set CLIP full model 0.92 GB, mclip text 0.84 GB, SigLIP 2 incl. 490 decoded
frames 2.05 GB (CLIP image run with the same frames 1.61 GB).

Too few labels for a decision on numbers alone: one query flipping moves R@1 by 0.25 (owner) or
0.125 (all). See the hand-off report for the reading and the recommendation.
