# Baseline benchmarks (before any model change)

Measured 2026-11-11 on the owner's machine: Ryzen 7 5850U (8 cores), 31 GiB RAM, **no NVIDIA GPU**
(every model runs on CPU; `torch` is a CUDA build that never applies here). Real project: the
Lidl × Nico fireworks video (25 recordings, ~25 min of footage). Client speech, transcripts and
frames stay outside the repo (NDA); this file holds numbers only.

Reproduce: the scripts under `benchmarks/model_audit/` (one folder per module, each with a README);
results are written to `~/.media_house_scratch/benchmarks/<module>/`.

## 1. End-to-end pipeline (from the real run logs in `_qa/*/run*.log`)

| Run | Scope | Wall | Notes |
| --- | --- | --- | --- |
| Full analysis, 25 recordings (`analysis_all.log`) | inspect, audio, video, content, alignment | **6683 s (1.86 h)** | up to 7 tasks at once |
| ↳ audio_analysis (WhisperX large-v3 int8 + alignment) | per recording | mean 349 s, sum 6628 s | CPU-bound |
| ↳ video_analysis (shots, CLIP, SSDLite, faces, OCR) | per recording | mean 412 s, sum 10304 s | CPU-bound |
| ↳ inspect | per recording | mean 162 s | includes proxy/probe work |
| One talking take, all stages (`t1_full.log`) | analysis → layer 4 → timeline | 778 s | graphics render 433 s |
| Long video v6, edit → render (`v8/run5.log`) | edit plan → Resolve render | ~30 min | 763 Resolve operations, 0 failed |
| Short (9:16), edit → delivery (`short/run_auto.log`) | auto brief → delivery | ~12 min | auto brief ~3.5 min, Resolve 206 s |

Model load is paid per process: about 30–75 s per ML model (WhisperX 39 s, CLIP ~25 s).

## 2. Delivered-output quality (measured on the renders)

| Output | Metric | Value |
| --- | --- | --- |
| Long video v6 (16:9, 494.8 s) | integrated loudness / true peak | −15.1 LUFS / −1.5 dBTP (YouTube profile −15 ±1) |
| | music under speech | ~23 LU |
| | dialogue level spread p10–p90 | ~4 LU |
| | Resolve execution | 763 ops, 0 failed, 0 changed by a person |
| Short (9:16, 45.8 s) | loudness / true peak | −14.0 LUFS / −1.5 dBTP (Shorts profile) |
| | Resolve execution | 107 executed, 19 unsupported (no keyframes in scripting), 0 failed |
| | framing | subject inside the 9:16 window on every sampled second (contact sheet) |

Human editorial review is the owner's: v4 "getting somewhere", v5/v6 "getting better", the Short
before this round "not good" (letterboxed, no emoji, no story thread) — see IMPLEMENTATION_REPORT.

## 3. Module baselines

### ASR (audio_intelligence: faster-whisper large-v3 int8, beam 5, WhisperX)
Sample: 254.8 s of German speech from 4 talking takes (number-dense passages).

| Metric | large-v3 (current) |
| --- | --- |
| RTF (wall / audio), 8 threads | 1.05 (±30 %: shared machine) |
| CPU seconds per audio second | 6.11 |
| model load | 38.8 s |
| peak RAM | 4.36 GB |
| spoken numbers/prices correct (German decimal comma) | 8 / 8 |
| known misrecognition in the sample | "Gesamtwahnwert" (for "Gesamtwarenwert"); one garbled total |

There is **no verbatim reference**, so true WER is unmeasured. A script-based proxy WER is ≈1.0 for
every model because the owner paraphrases his script; it cannot rank models (documented, not used).

### B-roll text→picture retrieval (broll_intelligence via CLIP ViT-B/32)
Pool: 95 shots with stored embeddings (25 recordings). Labels: 4 owner queries (the hand brief's
cover pairs) + 4 file-name labels (provenance in the benchmark folder).

| Metric | CLIP B/32 (current) |
| --- | --- |
| R@1 / R@5 / R@10 (owner n=4) | 0.25 / 0.25 / 0.50 |
| R@1 / R@5 / R@10 (all n=8) | 0.25 / 0.25 / 0.38 |
| MRR (owner / all) | 0.334 / 0.323 |
| per query / per shot (5 frames) | 0.26 s / 0.57 s; load ~25 s; 0.92 GB |

Observed weakness: German passages through an English text tower (fireworks query ranked 11
although 38 of 95 shots are fireworks — worse than random).

### OCR → burned-in caption detection (video_intelligence `text_cues.py`)
See MODEL_COMPARISON §3 for the measured false-caption rate (benchmark in `benchmarks/model_audit/ocr/`).
Known real failure: misreads of one static object ("9AX", "AF", "AE") counted as a changing caption,
one span covering 8.7–59.7 s of an uncaptioned take; it disabled every caption of the Short.

### Reframing / framing (reframing_intelligence → resolve_adapter)
Not model-bound in the observed failures: the Short rendered letterboxed because a moving 9:16 window
was turned into a Fusion zoom of 1.0, and the held window was the take's first key, not the clip's.
Fixed in code (IMPLEMENTATION_REPORT §1); no detector benchmark was needed to explain it.
