# Optimization plan (ranked)

Ranked by expected improvement of the FINAL edit, confidence in the evidence, complexity, hardware and
maintenance. The evidence is in MODEL_COMPARISON; what was done is in IMPLEMENTATION_REPORT.

Finding that shaped the ranking: most of the visible quality problems were **not model problems**.
Letterboxed Shorts, missing captions, captions over cards, stale graphics and garbage Resolve projects
were code and rule defects downstream of correct analysis. They come first because they are certain
and cheap.

| # | Change | Expected gain | Evidence | Complexity | Hardware | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Short pipeline defects: aspect-changing reframe, window at the clip's time, reframing lookup by anchor, camera moves inside the window | Short fills 9:16 with the subject framed (was letterboxed / off-frame) | renders before/after | low | none | **done** |
| 2 | Caption fixes: OCR false caption rule (source) + heard-word guard (consumer); no captions under full-screen cards; Short caption profile with emoji | captions appear, never collide with cards | 30 → 0 false spans; renders | low | none | **done** |
| 3 | Automatic brief (format-driven graphics, covers, screen windows, background, intro/outro) | other videos get this quality without a hand brief | 12/25 graphics, 5/6 covers, screen window, all style keys reproduced | medium | none | **done** (copy-written graphics remain, see #7) |
| 4 | No garbage: own project per video/format with `regenerate`, older renders archived on delivery | one project + one review file per format | Resolve run + prune counts | low | none | **done** |
| 5 | ASR `large-v3-turbo` | −35 % CPU, −1.5 GB, no accuracy loss seen | 255 s sample, numbers 8/8 | config only | CPU | **opt-in shipped**; make default after #8 |
| 6 | B-roll multilingual/fused text tower | better German retrieval (MRR 0.32 → 0.53 on 8 queries) | weak (p ≈ 0.19) | medium (identity gate split) | +0.8 GB | **wait for labels (#9)** |
| 7 | Copy-written graphics (callouts, highlight panels, feature lists) from a local LLM | closes the auto-brief gap (13 graphics) | none yet | medium | CPU LLM 3–4B is slow | benchmark Qwen3-4B / SmolLM3-3B via the Ollama adapter first |
| 8 | Verbatim German reference (10–15 min, owner-corrected) | lets #5 become the default and measures disfluency recall | – | owner time | – | needed |
| 9 | 40–60 owner-labelled B-roll passages | decides #6 | – | owner time | – | needed |
| 10 | Alignment model (Qwen3-ForcedAligner / XLSR-53 German) | tighter word boundaries → cleaner cuts | vendor figures only | medium | CPU | benchmark needs ~200 hand-marked boundaries |
| 11 | Person/face detector (RT-DETRv2 / D-FINE) for reframing | fewer lost faces in crops | none observed in renders after #1 | medium | CPU | not justified now |

Not recommended: fine-tuning (no labelled editing data beyond one project; retrieval/rules solved the
observed issues), weight-space methods (no measurable target), GPU-only models (no NVIDIA GPU).

A GPU would cut analysis wall time (1.86 h for 25 recordings, all CPU) roughly 5–10×, but would not
change editorial quality: every observed quality issue this round was a rule or code defect.
