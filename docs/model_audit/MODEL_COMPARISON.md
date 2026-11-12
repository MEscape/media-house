# Model comparison (measured)

Same machine, inputs and settings for every candidate (see BASELINE_BENCHMARKS). CPU only. Model
repos are pinned by revision; licences checked on the model cards. Scripts: `benchmarks/model_audit/`.

## Decision table

| Module | Current | Candidate | Quality change | Speed change | Memory change | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| ASR | large-v3 int8 (Systran@edaa852, MIT) | large-v3-turbo int8 (dropbox-dash@0a363e9, MIT) | no loss seen: numbers 8/8 both; turbo fixed "Gesamtwahnwert"; 2.7 % of words differ | −35 % CPU s per audio s (6.11 → 3.97); load 39 → 30 s | peak RAM 4.36 → 2.82 GB | **Opt-in now** (`transcription_model: large-v3-turbo`); default after a verbatim reference confirms it |
| ASR | large-v3 | primeline/whisper-large-v3-turbo-german@9e7012d (Apache-2.0, converted to CT2) | worse: numbers 6/8 (drops "Euro"), spells out small numbers | −33 % CPU | −1.5 GB | **Reject** |
| ASR | large-v3 | nvidia/parakeet-tdt-0.6b-v3 | not measured: needs `onnx-asr`/NeMo (not installed) | – | – | Not tested (no new dependency) |
| B-roll text tower | CLIP B/32 text (openai@3d74acf9) | multilingual CLIP text (sentence-transformers mclip-v1@58edf8ca, Apache-2.0) | MRR all 0.323 → 0.538; owner-only 0.334 → 0.318 | ≈ same | ≈ same | **Keep current**; evidence too thin (n = 8) |
| B-roll text tower | CLIP B/32 | mean of CLIP + mclip (post hoc) | owner R@5 0.25 → 1.0; all MRR 0.53; sign test p ≈ 0.19 | 2 text towers loaded | +0.8 GB | **Candidate**: adopt once 40–60 owner-labelled passages confirm it |
| B-roll both towers | CLIP B/32 | SigLIP 2 base (google@75de2d55, Apache-2.0) | no gain (MRR all 0.411) | image embedding ~6× slower; full re-embedding | +1.1 GB | **Reject now** (licence is its only advantage) |
| OCR → caption rule | rule: >2 distinct strings in the bottom band = caption | word-like strings, misread merge (difflib < 0.6), ≤ 2 lines at once | false caption spans on raw footage 63.3/h → **0/h**; false seconds 1419/h → 0/h; synthetic recall 1.00 → 0.99/1.00/1.00/0.95 | re-derivation only (~1 min for 25 assets) | – | **Shipped** (algorithm beats any model swap here) |
| OCR model | PP-OCRv6 small | PP-OCRv6 medium | not measured (needs a new model/ONNX export) | – | – | Keep small: the failure was the rule, not the reader |

## 1. ASR detail

Sample 254.8 s, 4 takes, beam 5, `language="de"`, the pipeline's WhisperX call, 8 threads.

| model | script-WER proxy | numbers k/n | words differing from large-v3 | RTF | CPU s / audio s | load s | peak RAM |
| --- | --- | --- | --- | --- | --- | --- | --- |
| large-v3 | 0.957 | 8/8 spoken (8/13 table) | – | 1.05 | 6.11 | 38.8 | 4.36 GB |
| large-v3-turbo | 1.046 | 8/8 | 2.7 % | 0.68 | 3.97 | 30.1 | 2.82 GB |
| primeline turbo-de | 1.184 | 6/8 | 5.0 % | 0.77 | 4.07 | 35.0 | 2.82 GB |

The script proxy is ≈ 1.0 for all models (the owner paraphrases); it is reported for completeness and
was **not** used to rank. Wall-clock RTF is ±30 % (other jobs ran); CPU s per audio s is the reliable
speed figure.

## 2. B-roll detail (rank of the first relevant shot; lower is better)

| query | random | CLIP (current) | mclip | SigLIP 2 | CLIP+mclip |
| --- | --- | --- | --- | --- | --- |
| Q1 owner "4 Uhr" gag | 32 | 13 | 20 | 17 | 5 |
| Q2 owner "Schlange vor Lidl" | 24 | 11 | 1 | 21 | 2 |
| Q3 owner website page | 12 | 1 | 50 | 1 | 1 |
| Q4 owner "Blick aufs Konto" | 48 | 6 | 5 | 20 | 3 |
| Q5 pre-order link | 4 | 1 | 1 | 1 | 1 |
| Q6 "Schlange stellen" | 8 | 11 | 1 | 9 | 6 |
| Q7 fireworks "am Himmel" | 2.5 | 11 | 1 | 1 | 1 |
| Q8 "online bestellen" | 48 | 15 | 30 | 58 | 23 |

German text is the current tower's weakness (Q6, Q7); the multilingual tower loses on-screen text (Q3,
English CLIP reads the URL in the picture). Fusion takes the best of both, but 8 queries (4 the
owner's) cannot carry a replacement decision.

## 3. OCR caption rule detail

Negatives: 24 raw recordings (0.474 h; raw camera, screen and archive files cannot contain burned-in
captions). Positives: synthetic German caption clips (200 per style; no client text).

| rule | false spans/h | false s/h | recall line / 2 lines / words / rolling | static misread → caption |
| --- | --- | --- | --- | --- |
| legacy | 63.3 (30 spans) | 1419 | 1.00 / 1.00 / 1.00 / 1.00 | 100 % |
| word-like + misread merge | 35.9 | 425 | 0.99 / 1.00 / 1.00 / 0.95 | 8.5 % |
| + time-ordered | 16.9 | 415 | same | 8.5 % |
| **shipped: word-like + merge + ≤ 2 lines at once** | **0.0** | **0.0** | **0.99 / 1.00 / 1.00 / 0.95** | **8.5 %** |

Sources of legacy false positives: camera takes 6 spans / 453 s (the "9AX" misreads), screen
recordings 17 / 181 s, archive 7 / 38 s (web/app UI with many strings in the bottom band).
