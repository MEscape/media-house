# Model research (Phase 3): Hugging Face candidates for the weak, model-bound modules

Date: 2026-11-11. Research only: nothing was downloaded, installed or benchmarked. Sources are the
Hugging Face model cards and the public API (`https://huggingface.co/api/models?search=…&sort=downloads`,
`/api/models/<id>?expand[]=safetensors…`), read anonymously. Benchmark numbers below are the
**vendors' own figures** on public datasets; they are not evidence of improvement on our footage.
Download counts are the HF "last 30 days" figure on the audit date.

Read [PIPELINE_AUDIT.md](PIPELINE_AUDIT.md) first; section numbers "audit §x" refer to it.

## 0. Constraints applied

* **Local inference only.** Every candidate below runs on this machine. Media and transcripts never
  leave it (NDA). HF is used only to download weights later (free tier; a token is needed only for
  gated repos such as pyannote).
* **No paid APIs.** The existing opt-in Groq path is out of scope here.
* **Commercial use required** (the channel is monetised, sponsored videos). Excluded on licence:
  `nyralabs/CrisperWhisper*` (CC-BY-NC / "nyra-health-non-commercial-research"),
  `MahmoudAshraf/mms-300m-1130-forced-aligner` (CC-BY-NC-4.0), `jinaai/jina-clip-v2` (CC-BY-NC-4.0),
  `briaai/RMBG-2.0` (non-commercial), YOLO-World (AGPL/GPL), madmom's pretrained models
  (CC-BY-NC-SA), `Flurin17/whisper-large-v3-turbo-swiss-german` (CC-BY-NC). CC-BY-4.0 models
  (NVIDIA) are allowed but need attribution in the provenance/LICENSES record.
* **Hardware:** Ryzen 7 5850U, 8C/16T, 30.8 GiB RAM, no CUDA GPU (audit §1). Every candidate is
  judged by CPU feasibility and RAM. ONNX Runtime CPU and CTranslate2 int8 are the realistic fast
  paths; DirectML/OpenVINO are not installed.
* **Integration:** the replacement must fit an existing port/contract (hexagonal rules); engine
  identity must change the cache key.

## Decision overview

| Module / function | Weak today? | Model-bound? | Recommendation |
| --- | --- | --- | --- |
| audio_intelligence ASR (German) | Yes: slow on CPU, disfluencies dropped (audit §6.3) | Yes | **Benchmark 3 candidates** (§1) |
| audio_intelligence forced alignment | Yes: word ends stretched over pauses, patched by `snap.py` (audit §6.5) | Yes | **Benchmark 2-3 candidates**; keep `snap.py` either way (§2) |
| broll_intelligence text→picture | Yes: English CLIP for German passages (audit §6.4) | Yes (plus pool hygiene) | **Benchmark 3 candidates** (§3) |
| video_intelligence OCR / burned-in captions | Yes: "9AX" false caption (audit §6.1) | **Mostly no** | **Keep PP-OCRv6 small**; fix rule; optional 1 benchmark (§4) |
| video_intelligence person/face detection (feeds reframing) | Partly (audit §6.2) | Partly | Fix crop handling first; **benchmark 2 detectors** (§5) |
| content_intelligence / story LLM | Weak when used (1.5B mostly rejected); off in pipeline | Yes, if enabled | Benchmark only if the owner wants local LLM wording (§6) |
| music_and_sound_design downbeats | Weak (README confidence 0.04-0.45), no render complaint | Partly | Optional benchmark, low priority (§7) |
| VAD, diarization, text embeddings, matting, shots, motion, quality, colour, DSP, Layer 3-6 rules | No reported weakness | – | **KEEP** (§8) |

---

## 1. ASR (German) — `audio_intelligence`

Current: WhisperX 3.8.6 → faster-whisper `Systran/faster-whisper-large-v3` (1.55 B params, MIT),
CTranslate2 int8 on CPU, beam 5, pyannote VAD, ≈1.1-1.6× real time plus 50-60 s load
(baseline §4). German output is fluent (baseline §6) but true WER is unknown (no reference), and
immediate repeats / broken-off starts are rarely transcribed (overhaul README).

Port to plug into: the transcription engine behind `TranscribeAudioCommand`
(`infrastructure/whisperx_engine.py`; the Groq engine `infrastructure/groq_transcription.py`,
wired in `module.py:92-99`, shows how an alternative engine with its own identity is added).

| Candidate | Task fit | Size | Licence | CPU feasibility / quantisation | German evidence (vendor) | Maintenance | Known limits | Plug-in effort |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `large-v3-turbo` via faster-whisper alias → `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (or `deepdml/faster-whisper-large-v3-turbo-ct2`, MIT, 406k dl) | Same task, same pipeline | 809 M (32 encoder, 4 decoder layers vs 32) | MIT | CT2 int8 on CPU; decoder cost falls sharply, encoder unchanged | primeline table: Tuda-De 8.30, CV19 3.85, MLS 3.20 vs large-v3 7.88 / 3.48 / 2.83 (WER %, slightly worse than large-v3) | OpenAI weights, stable; alias already in `faster_whisper/utils.py:29-30` | Same disfluency cleaning as Whisper; slightly lower accuracy | **Config only**: `TranscriptionConfig.model="large-v3-turbo"` |
| `primeline/whisper-large-v3-turbo-german` | German fine-tune of turbo | 809 M | Apache-2.0 | Needs CT2 format: community conversions `jimmymeister/whisper-large-v3-turbo-german-ct2`, `cstr/whisper-large-v3-turbo-german-int8_float32` (Apache, low downloads, unverified), or a local `ct2-transformers-converter` run (offline, no data leaves) | primeline table: Tuda-De 6.44, CV19 3.20, MLS 2.07, "All" 2.63 vs large-v3 3.28 | Single maintainer, last update 2024-12 | Fine-tunes can lose language-ID robustness and punctuation style; WhisperX still needs its own aligner | **Config** (model path) once a CT2 copy exists |
| `nvidia/parakeet-tdt-0.6b-v3` (ONNX: `istupakov/parakeet-tdt-0.6b-v3-onnx`, 114k dl) | Multilingual (25 EU langs) transducer, **word-level timestamps built in** | 627 M | CC-BY-4.0 (attribution) | Transducer (no autoregressive LM decoding) → expected CPU-friendly; ONNX int8 ports exist; NeMo itself is heavy on Windows, ONNX path avoids it | FLEURS de 5.04, CoVoST2 de 4.84 (card) | NVIDIA, updated 2026-08 | Card says optimised for NVIDIA GPUs; output punctuation/case from model; verbatim behaviour on disfluencies unknown | **New adapter** implementing the transcription port (like Groq); could also skip the wav2vec2 aligner |
| `Qwen/Qwen3-ASR-0.6B` / `-1.7B` (+ `Qwen3-ForcedAligner-0.6B`) | 30 languages incl. German, LID, long audio | 0.94 B / 2.35 B (safetensors total) | Apache-2.0 | `qwen-asr` package or transformers from source; GGUF ports (`handy-computer/Qwen3-ASR-0.6B-gguf`, 304k dl) | Aggregate FLEURS-12 WER 4.90 (1.7B) / 7.57 (0.6B) vs Whisper-large-v3 5.27; no German-only figure | Qwen, 2026-01 | LLM-style decoder (autoregressive) → CPU latency unknown; dependency conflict risk with the `transformers<5` pin | New adapter; heavier integration |
| `nvidia/canary-1b-v2` | 25 EU langs ASR/AST, word timestamps | 978 M | CC-BY-4.0 | NeMo; ONNX `istupakov/canary-1b-v2-onnx` (3.8k dl) | FLEURS de 4.40, CoVoST2 de 5.53 | NVIDIA | Encoder-decoder, slower than parakeet; timestamps need the auxiliary CTC model | New adapter (watch list) |
| `ibm-granite/granite-speech-4.1-2b` | EN/FR/DE/ES/PT/JA ASR, German noun capitalisation | ~2 B | Apache-2.0 | transformers; 2 B on CPU is heavy | CV-DE 3.66 WER (card) | IBM, 2026-04 | Word timestamps only in the `-plus` variant | Watch list |
| Excluded | `nyralabs/CrisperWhisper2.0_*` (best verbatim German in primeline's table: Tuda-De 5.15) — **non-commercial**; `mistralai/Voxtral-Mini-4B-Realtime-2602` — vLLM only, 4 B | | | | | | | |

**Shortlist to benchmark:** (1) `large-v3` int8 = baseline; (2) `large-v3-turbo` int8 and the
primeline German turbo in CT2 int8 (one slot, same engine — cheapest to try); (3)
`parakeet-tdt-0.6b-v3` ONNX int8.

**Metric / data (local only):** 10-15 min of the owner's German footage across conditions (GoPro
talking head, room, outdoor noise, numbers/dates/prices, at least 20 retakes/false starts). The
owner hand-corrects a **verbatim** reference (fillers and restarts kept) stored outside the repo
(e.g. `~/.media_house_scratch/bench/asr/`). Measure: WER and CER (normalised: case, punctuation),
**numeric-entity accuracy** (dates, prices, units, model numbers), **disfluency recall** (share of
repeats/false starts present in the hypothesis — this is what the retake detectors need), real-time
factor and model load time on this CPU, peak RAM. Decide on WER + numeric accuracy first,
disfluency recall second, RTF third.

## 2. Forced alignment (word timing) — `audio_intelligence`

Current: torchaudio `VOXPOPULI_ASR_BASE_10K_DE` (wav2vec2 base, the WhisperX default for `de`,
`whisperx/alignment.py:35`), then `snap.py` trims edges to measured energy. Override hook:
`whisperx_engine.py:219-223` (HF wav2vec2 CTC ids accepted by WhisperX).

| Candidate | Fit | Size | Licence | CPU | Evidence (vendor) | Plug-in |
| --- | --- | --- | --- | --- | --- | --- |
| `jonatasgrosman/wav2vec2-large-xlsr-53-german` | CTC aligner, German | ~315 M | Apache-2.0 | fine (larger than base, still CTC) | 117k dl; widely used WhisperX override for German; no timing benchmark on the card | **Config only** via the existing override |
| `Qwen/Qwen3-ForcedAligner-0.6B` (`-hf` transformers-native variant; ONNX community ports) | Non-autoregressive aligner, 11 languages incl. German, word or character units | 0.92 B total | Apache-2.0 | unknown; ≤5 min per call (needs chunking) | Card, "Forced Alignment Benchmarks (AAS ms ↓)", German MFA-labelled: NFA 122.7, WhisperX 165.1, Qwen3-ForcedAligner 46.5; second German set 220.3 / 993.4 / 62.4 | New adapter; transformers from source or ONNX port → dependency risk |
| parakeet-tdt word timestamps (if §1 picks parakeet) | built-in timestamps | – | CC-BY-4.0 | – | card claims "accurate word-level timestamps", no German timing figure | Comes with the ASR adapter |
| Excluded | `MahmoudAshraf/mms-300m-1130-forced-aligner` (CC-BY-NC) | | | | | |

Keep `snap.py` in every variant: it uses measured activity, which no aligner replaces.

**Shortlist:** (1) VoxPopuli base (baseline), (2) XLSR-53 German (zero-code), (3) Qwen3-ForcedAligner-0.6B.

**Metric / data:** ~200 hand-marked word boundaries (Praat/Audacity labels) from the same local
clips, over-sampling sentence-final words before pauses. Measure mean absolute boundary error, share
within 50/100 ms, count of words longer than 1 s (`long_word` warnings), and pause recall for pauses
≥ 0.3 s — measured both before and after `snap.py`.

## 3. B-roll text→picture search — `broll_intelligence` via `video_intelligence`

Current: German passage text → `openai/clip-vit-base-patch32` text tower, cosine against stored shot
embeddings (`retrieval.py:53-56,125-170`). Note: the OpenAI CLIP card scopes the model to research
use ("deployed use cases … out of scope"); the code licence is MIT. The owner should be aware.

Plug-in: `VisualTextEncoder` (`video_intelligence/application/contracts.py`) and the shot index;
B-roll only searches when the encoder identity matches the footage `embedding_model`
(`broll_intelligence/application/pool.py:136-146`).

| Candidate | Fit | Size | Licence | CPU | Evidence | Plug-in |
| --- | --- | --- | --- | --- | --- | --- |
| `sentence-transformers/clip-ViT-B-32-multilingual-v1` | Multilingual **text** encoder distilled into the OpenAI ViT-B/32 image space (50+ languages; card example is German) | 135 M (text only) | Apache-2.0 | light; text only | Card: text model "aligned to the img_model and maps 50+ languages to the same vector space"; 105k dl; last update 2024 | **Keeps every stored image embedding**: only the text side changes. Needs an identity rule that this text encoder is compatible with `openai/clip-vit-base-patch32` image embeddings |
| `google/siglip2-base-patch16-224` | Multilingual image-text encoder (SigLIP 2 paper title: "Multilingual Vision-Language Encoders") | 375 M (both towers) | Apache-2.0 | heavier than B/32 per frame; feasible at 3 fps sampling | 1.39M dl; 2025 | New embedding identity → **re-embed all shots**; also helps continuity and image selection |
| `laion/CLIP-ViT-B-32-xlm-roberta-base-laion5B-s13B-b90k` | Multilingual CLIP (XLM-R text tower) | ~366 M | MIT | similar to B/32 | 99k dl; 2022 | Needs `open_clip` (new dependency) and re-embedding |
| Excluded / not now | `jinaai/jina-clip-v2` (CC-BY-NC); Apple MobileCLIP (apple-amlr licence); `Qwen/Qwen3-VL-Embedding-2B` (2 B on CPU) | | | | | |

No-model part to fix regardless (audit §6.4): stills and non-analysed assets make every run
`PARTIAL`; analyse stills (one frame) or exclude them from the "partial" verdict.

**Shortlist:** (1) current CLIP B/32 with German text (baseline), (2) multilingual-v1 text tower
(cheapest), (3) SigLIP 2 base.

**Metric / data:** 40-60 German passages from local projects; the owner marks relevant shots in the
local pool (currently 98 shots / 25 assets). Measure Recall@5, MRR, nDCG@10, plus the share of
passages where the evidence gate accepts a shot; report German query vs an owner-written English
paraphrase to isolate the language effect. Record per-shot embedding time for (3).

## 4. OCR / burned-in captions — `video_intelligence`

**Keep the model.** RapidOCR 3.10 ships `PP-OCRv6_det_small` + `PP-OCRv6_rec_small`; the v6 small
recogniser "supports 50 languages" (5.2 M params, Apache-2.0, card), and the code notes it reads
umlauts/ß/€ (`profiles.py:84-86`). The "9AX" error is a rule problem (audit §6.1). Fix first:
require ≥ N time-ordered, non-overlapping string changes; merge strings within a small edit
distance; minimum length and letter share; per-string persistence; raise or calibrate
`min_confidence`.

Optional model benchmark after the rule fix:

| Candidate | Fit | Licence | CPU | Evidence | Plug-in |
| --- | --- | --- | --- | --- | --- |
| `PaddlePaddle/PP-OCRv6_medium_rec` (+ `_det`) | same family, bigger | Apache-2.0 | slower; ONNX export needed if RapidOCR does not ship it | card: avg recognition accuracy 83.2 (medium) vs 81.3 (small) | same adapter, model path |
| `microsoft/Florence-2-base` (`<OCR_WITH_REGION>`) as a **verifier** on candidate caption spans only | 232 M | MIT | seconds per frame on CPU; only for flagged spans | 12.5M dl | second opinion inside the text analyzer |
| Not recommended | Qwen3-VL-2B (2.1 B; strong OCR in 32 languages per card) — too slow per frame on CPU; `latin_PP-OCRv5_mobile_rec` — older family than the shipped v6 | | | | |

**Metric / data:** ~300 frames sampled from local takes (including the "9AX" take), labelled
"burned-in caption: yes/no" plus the true strings. Measure false-positive BURNED_IN_CAPTION spans per
hour of footage (primary), CER on true text, analyzer seconds per minute.

## 5. Person / face detection for reframing — `video_intelligence`

Fix first (no model): run face detection on every sampled frame instead of only frames where
SSDLite found a person (`mediapipe_engines.py:~91-99`); use the stored face track as the pivot of
compiler camera moves instead of `(0.5, 0.42)`; the Resolve crop issues (audit §6.2).

Then benchmark the detector that gates people:

| Candidate | Size | Licence | CPU | Evidence | Plug-in |
| --- | --- | --- | --- | --- | --- |
| `torchvision ssdlite320_mobilenet_v3_large` (current) | ~3.4 M | BSD-3 | fast | baseline | – |
| `PekingU/rtdetr_v2_r18vd` | 20 M | Apache-2.0 | moderate; transformers-native (fits the `<5` pin) | 165k dl | new `EntityDetector` adapter |
| `ustc-community/dfine-small-coco` (or `-nano-coco`, 3.8 M) | 10 M | Apache-2.0 | moderate; transformers-native | 5k dl | same |
| Open-vocabulary for "product" (no COCO class): `google/owlv2-base-patch16-ensemble` (155 M, Apache), `IDEA-Research/grounding-dino-tiny` (172 M, Apache) | | | slow per frame on CPU; only on sparse frames | | separate analyzer; not part of this shortlist |

Faces: keep MediaPipe (Apache-2.0, fast). Re-test its short-range detector on wide GoPro shots
before considering another face model; OpenCV's YuNet (opencv-contrib is already installed, model
from the OpenCV zoo) would be the zero-dependency alternative.

**Shortlist:** (1) SSDLite (baseline), (2) RT-DETRv2-r18, (3) D-FINE-small.

**Metric / data:** ~300 local frames (talking head close/wide, low light, back to camera, fisheye
edges) with person and face boxes. Measure person recall/precision at IoU 0.5, **face recall with
and without the person gate**, seconds per sampled frame; then on 10 reframed clips: share of frames
where the face stays inside the crop and crop jitter (px/frame).

## 6. Local LLM for story/content wording — `content_intelligence`, `story_architecture`

Only relevant if the owner enables `--story-llm` locally; the pipeline currently runs content
analysis without any model (audit §4.2). The existing OpenAI-compatible adapter already talks to a
local Ollama/llama.cpp server (`loopback_llm.py`), so a better model is a **configuration change**,
and the strict checks in `domain/refine.py` stay the safety net.

| Candidate | Size | Licence | CPU | Notes |
| --- | --- | --- | --- | --- |
| `Qwen/Qwen2.5-1.5B-Instruct` (current) | 1.5 B | Apache-2.0 | ok | "most of their output is discarded" (README:236-237) |
| `Qwen/Qwen3-4B-Instruct-2507` (GGUF `unsloth/…-GGUF`, 782k dl) | 4.0 B | Apache-2.0 | Q4 GGUF via Ollama; seconds per short answer, to be measured | strong multilingual instruct |
| `HuggingFaceTB/SmolLM3-3B` (GGUF available) | 3.1 B | Apache-2.0 | similar | open training data, multilingual incl. German |
| `microsoft/Phi-4-mini-instruct` | 3.8 B | MIT | similar | weaker German expected (not verified) |

**Metric / data:** run content refine + story wording on the transcripts of 5 local recordings;
measure the acceptance rate of `refine.py` checks (accepted / proposed), owner rating of accepted
wording (1-5), seconds per call, RAM.

## 7. Music downbeats — `music_and_sound_design` (optional, low priority)

Current NumPy Ellis DP beat tracker: 24/35 library tracks within 2 % BPM, downbeat confidence
0.04-0.45 (README:105-112). No render complaint is attributed to it yet.

Candidate: **Beat This** (CPJKU, MIT; ONNX port `musetric/beat-this-onnx`, MIT, 6k dl; the CPJKU HF
card returned 401 anonymously, so verify the official weights' location and licence before use).
Excluded: madmom models (CC-BY-NC-SA). Plug-in: the music signal analyser behind
`ImportSoundLibraryCommand`; bump `ANALYZER_VERSION`.

**Metric / data:** owner taps beats/downbeats on 10 library tracks; F-measure (±70 ms) for beats and
downbeats, BPM accuracy on all 35 tracks.

## 8. KEEP (no model change justified by evidence)

| Function | Current | Why keep |
| --- | --- | --- |
| VAD inside WhisperX | pyannote segmentation (bundled) | no reported failure; `silero` is available via `vad_method` if a benchmark ever shows missed speech |
| Diarization | pyannote community-1, off | single presenter; never needed so far. If enabled, note torchcodec cannot load (audit §7) |
| Text embeddings | `paraphrase-multilingual-MiniLM-L12-v2` | already multilingual; first wire it into the pipeline's content stage. Upgrade candidates for later: `intfloat/multilingual-e5-small` (118 M, MIT), `Qwen/Qwen3-Embedding-0.6B` (Apache) |
| Cut-outs | rembg `birefnet-general-lite` (BiRefNet, MIT) | chosen deliberately over ISNet; non-commercial RMBG-2.0 excluded |
| Shot detection, motion, quality, saliency | NumPy | golden-set test exists; no reported failure (TransNetV2 ports on HF have unclear licences and tiny usage) |
| Audio DSP, loudness, mixing | FFmpeg / SciPy | not model problems |
| Colour | video_improvement LUT pipeline, Resolve CDL | the observed issue is the un-converted full-range tag (audit §7), a code/config problem |
| Layer 3-6 planners and checks | rules | calibration on real footage, not models |
| Sound-event tagging (`MIT/ast-finetuned-audioset-10-10-0.4593` BSD-3, PANNs, `laion/clap-htsat-unfused` Apache) | not built | a new capability, not a fix; revisit only if laughter/breath events are wanted |
| VLM descriptions (deep profile) | SmolVLM-256M | deep profile is not used in runs; `HuggingFaceTB/SmolVLM2-500M-Video-Instruct` (Apache, English) or `Qwen/Qwen3-VL-2B-Instruct` (Apache) only if descriptions become a requirement |
| Image generation | unavailable by design | not a weakness; CPU generation would be very slow |

## 9. Benchmark shortlist (all local footage, references kept outside the repo)

| # | Module | Compare | Primary metric | Data the owner must provide |
| --- | --- | --- | --- | --- |
| 1 | ASR | large-v3 int8 · large-v3-turbo / primeline turbo-german CT2 int8 · parakeet-tdt-0.6b-v3 ONNX | WER + numeric-entity accuracy; then disfluency recall, RTF | 10-15 min verbatim German reference |
| 2 | Alignment | VoxPopuli base · XLSR-53 German · Qwen3-ForcedAligner-0.6B (each ± `snap.py`) | mean boundary error, % ≤ 100 ms, words > 1 s | ~200 hand-marked boundaries |
| 3 | B-roll | CLIP B/32 · clip-ViT-B-32-multilingual-v1 · SigLIP 2 base | Recall@5, MRR | 40-60 German passages with relevant shots marked |
| 4 | Person/face | SSDLite · RT-DETRv2-r18 · D-FINE-s (faces with/without the gate) | face recall, crop containment | ~300 labelled frames, 10 reframed clips |
| 5 (after rule fix) | OCR | PP-OCRv6 small (rule fixed) · v6 medium | false caption spans per hour | ~300 labelled frames incl. the "9AX" take |
| 6 (optional) | LLM | Qwen2.5-1.5B · Qwen3-4B-Instruct-2507 · SmolLM3-3B (local Ollama) | refine acceptance rate | 5 transcripts |
| 7 (optional) | Beats | Ellis DP · Beat This | downbeat F-measure | 10 tapped tracks |

Rules for every benchmark: record model repo + revision hash, quantisation, library versions and
CPU thread count; run on this machine with the same inputs; report RTF/latency and peak RAM next to
quality; never upload footage, audio or transcripts; keep results and references outside the repo
(NDA). A candidate replaces the current model only if it wins on the primary metric without a
material loss in another, and its engine identity changes the cache key.
