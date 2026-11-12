# Pipeline audit (Phase 1): modules, models, limits

Date: 2026-11-11. Read-only audit of the working tree on `main` (`26eaba0` plus uncommitted edits by
other sessions). Line numbers are those found at audit time; files were being edited concurrently,
so treat `file:line` as "near this line". Paths are relative to `src/media_house/modules/` unless
they start with `src/`, `scripts/`, `scratch/`, `tests/` or `docs/`.

Method: code and README reading per module; `uv.lock` and `uv pip list` for versions; model cache
listing; a pattern count over the 38 real run logs in `~/.media_house_scratch/baseline/_qa/`
(numbers and quoted strings normalised, no client text read into this document). Nothing was run
except read-only shell commands. No model was downloaded, no test or pipeline run.

The companion document [MODEL_RESEARCH.md](MODEL_RESEARCH.md) covers the Hugging Face candidates.

---

## 1. Hardware and runtime

| Item | Found | Source |
| --- | --- | --- |
| CPU | AMD Ryzen 7 PRO 5850U, 8 cores / 16 threads | `Win32_Processor` |
| RAM | 33.1 GB (30.8 GiB) | `Win32_ComputerSystem` |
| GPU | AMD Radeon integrated (driver 31.0.21924), 1 GB reported; **no NVIDIA GPU**, `nvidia-smi` not found | `Win32_VideoController` |
| torch | `2.8.0+cu128` (CUDA build; CUDA is never available on this machine) | `uv pip list` |
| ONNX Runtime | 1.30.0, `CPUExecutionProvider` only (no DirectML/OpenVINO EP installed) | `uv pip list`, `visual_asset_generation/infrastructure/rembg_segmenter.py:35-52` |
| Run mode | `--cpu-only` (sets device in three places only: WhisperX `executors.py:791`, video `:812`, content embeddings `:843`) | `media_pipeline/application/executors.py` |

Consequences:

* Every model runs on CPU. Model choice must be judged by CPU latency and RAM, not VRAM.
* The in-process LLM ignores `cpu_only` (`content_intelligence/infrastructure/transformers_llm.py:101`
  picks CUDA when available). Harmless here (no CUDA) but wrong on a GPU machine.
* Models load once per process (`OnceLoaded`, `src/media_house/core/modules/container.py:42-71`)
  and are reloaded by every CLI process: about 50-75 s per process (media_pipeline README:286-312;
  baseline §4).

## 2. Installed ML packages (from `uv.lock`, matches the venv)

| Package | Version | Used by |
| --- | --- | --- |
| whisperx | 3.8.6 | audio_intelligence (ASR, alignment, optional diarization) |
| faster-whisper / ctranslate2 | 1.2.1 / 4.8.2 | WhisperX backend |
| torch / torchaudio / torchvision | 2.8.0 / 2.8.0 / 0.23.0 (+cu128) | aligner, SSDLite, CLIP, LLM |
| torchcodec | 0.7.0 | pulled in by pyannote; **fails to load** (FFmpeg shared DLLs missing, see §7) |
| pyannote-audio | 4.0.7 | WhisperX VAD; optional diarization |
| transformers / tokenizers / safetensors | 4.57.6 / 0.22.2 / 0.8.0 | CLIP, SmolVLM, MiniLM, Qwen |
| mediapipe | 1.1.0 | faces, pose, hands |
| rapidocr / onnxruntime | 3.10.0 / 1.30.0 | OCR |
| rembg | 2.0.85 | cut-outs (visual_asset_generation) |
| praat-parselmouth | 0.4.7 | pitch |
| opencv-python-headless / opencv-contrib | 5.0.0.93 | transitive (not imported by the analyzers found) |
| numpy / scipy | 2.5.3 / 1.18.1 | all signal code |

`pyproject.toml`: core deps include `whisperx>=3.8.6`, `torch>=2.8,<2.9`; the optional `vision` extra
holds mediapipe, rapidocr, torchvision, `transformers>=4.57,<5`. The `<5` pin matters for some
candidates in MODEL_RESEARCH.md.

## 3. Model weights on disk and where they are cached

| Model | Location | Size | Used |
| --- | --- | --- | --- |
| `Systran/faster-whisper-large-v3` | `~/.cache/huggingface/hub` | 2.9 GB | yes (ASR) |
| torchaudio `wav2vec2_voxpopuli_base_10k_asr_de.pt` (+ `wav2vec2_fairseq_base_ls960_asr_ls960.pth` for EN) | `~/.cache/torch/hub/checkpoints` | 0.72 GB total | yes (DE/EN alignment) |
| WhisperX pyannote VAD segmentation | bundled in `whisperx/assets/pytorch_model.bin` | – | yes |
| `openai/clip-vit-base-patch32` | HF cache | 1.7 GB (pt + tf + flax copies) | yes (embeddings, B-roll, continuity) |
| `HuggingFaceTB/SmolVLM-256M-Instruct` | HF cache | 3.3 GB (incl. ONNX variants) | deep profile only |
| `ssdlite320_mobilenet_v3_large_coco-a79551df.pth` | `%LOCALAPPDATA%/media-house/Cache/video_intelligence/models` | 14 MB | yes (entities) |
| MediaPipe `face_landmarker.task`, `blaze_face_short_range.tflite` | same | 3.6 MB / 0.2 MB | yes (faces) |
| MediaPipe pose/hand landmarkers | not on disk | – | deep profile only; would be `not_available` |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | `%LOCALAPPDATA%/media-house/Cache/content_intelligence/models` | 477 MB | B-roll description stage, alignment semantic match; **not** content_analysis in pipeline runs (§5.4) |
| `Qwen/Qwen2.5-1.5B-Instruct` | same | 2.9 GB | only with `--story-llm` (local) |
| rembg `birefnet-general-lite` | `~/.rembg/models` | 214 MB | yes (cut-outs) |
| RapidOCR `PP-OCRv6_det_small.onnx`, `PP-OCRv6_rec_small.onnx`, `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | inside the rapidocr wheel (`.venv`) | small | yes (text) |
| `pyannote/speaker-diarization-community-1` | **not on disk** (gated) | – | diarization never ran for real |

Three cache roots exist (HF default, torch hub, app cache `AppPaths`/platformdirs,
`src/media_house/shared/filesystem/paths.py:29-35`) plus `~/.rembg`. `scripts/fetch_models.py`
fetches the content-intelligence models, the speech model and aligner, the gated diarization model
and rembg, but **no video-intelligence model** (CLIP, SmolVLM, MediaPipe, SSDLite). Runs never
download unless `--allow-model-download` (`executors.py:496-498`).

Hidden dependency: CLIP's identity is `repo@<snapshot revision>` (`video_intelligence/infrastructure/clip_engines.py:80-89`).
B-roll and continuity only compare embeddings whose identity matches
(`broll_intelligence/application/pool.py:136-146`, `continuity_intelligence/domain/detect.py:93-118`).
A new HF snapshot of the same model silently disables picture search until every asset is
re-analysed.

---

## 4. Module map

39 module folders exist. All have real code. Only the Layer-2 modules (plus rembg in
`visual_asset_generation`) run ML models; Layers 3-6 are deterministic rule engines that consume
model output through contracts.

### 4.1 Layer 1: library, inspection, relations, proxies

| Module | Responsibility | Current implementation | Input → Output | Contract deps | Current limitations (evidence) | Optimization potential |
| --- | --- | --- | --- | --- | --- | --- |
| media_library | Single asset store; derived-asset cache by fingerprint | SQLite + content-addressed store; SHA-256 checksum (`infrastructure/file_inspector.py:121`), fingerprint hash (`domain/values.py:391`); ffprobe at import (`infrastructure/media_probes.py:59`); ffmpeg thumbnails. No ML | files → `MediaAssetDto`, `DerivedAssetResultDto`, documents | none | Duplicates only byte-identical (README:60); `MEDIA_SUFFIXES` audio/video only, stills enter via scripts (baseline §8) | Not model-bound |
| media_inspection | Technical ground truth (streams, timing, colour, timecode) + findings | ffprobe ×3 (streams, every packet `ffprobe_prober.py:154`, first-frame side data `:194`); depth FULL adds a full decode `-f null` only for decode errors (`:220-235`). `INSPECTION_VERSION=1`. No ML | `InspectMediaCommand` → `MediaInspection` | media_library | FULL reads the whole file (README:142), ~75 s per 0.9 GB (baseline §4); no per-frame/interlace check (README:133-136); no image inspection (README:139) | Not model-bound; the one full decode could also feed audio PCM / scene stats to later stages |
| asset_relationships | Relationship claims with evidence and decisions | Rules only: SHA-256 exact duplicate, `wall_clock_overlap` v1 (>2 s), `production_tag_takes` v1 (README:205-208). No fingerprint, pHash or embedding (README:126, 210-212, 301) | `DetectCommand` → `RelationshipDto` | media_library, media_inspection, proxy_manager | Precision never measured on real footage (README:308); nothing triggers detection automatically (README:310) | Capability gap (audio fingerprint/visual similarity), not a weak model |
| proxy_manager | Verified proxies per (source, profile) | libx264 veryfast / prores_ks proxy (`infrastructure/ffmpeg_encoder.py:22-36`); full-decode validation optional | `EnsureProxyCommand` → `ProxyDto` | media_library, media_inspection | CPU encoders only; parameters "drafts until benchmarked" (README:52); own ffprobe if no inspection (`ffprobe_facts.py:78`) | Not model-bound |

### 4.2 Layer 2: analysis (the model-bearing layer)

| Module | Responsibility | Current implementation | Input → Output | Contract deps | Current limitations (evidence) | Optimization potential |
| --- | --- | --- | --- | --- | --- | --- |
| audio_improvement | Guarded speech enhancement + voice/music mix | FFmpeg `adeclip`, `afftdn`, EQ, `acompressor`, `deesser`, `alimiter` (`infrastructure/ffmpeg_stages.py:78-184`); `sidechaincompress`; `ebur128` loudness; STFT dereverb (`spectral_dereverb.py:42-57`). `PROCESSING_VERSION=1`. No ML | `ImproveAudioCommand` → `ImprovementResult` | media_library, media_inspection | Thresholds from synthetic recordings (README:69-71); no neural denoiser (README:122-124); mono/stereo only (README:136); not in the default stage set | DSP-bound. A neural denoiser would be a new `StageProcessor`; not a reported weakness |
| audio_intelligence | Word transcript, acoustics, events, scores | **ASR** WhisperX 3.8.6 → faster-whisper `large-v3` (`domain/values.py:77`), CPU `int8` (`infrastructure/whisperx_engine.py:46-47,283-286`), batch 16, chunk 30 s, language auto unless `--language` (`values.py:79-94`); no `asr_options` passed, so WhisperX defaults beam 5, best_of 5, temperature fallback, `condition_on_previous_text=False` (`.venv/.../whisperx/asr.py:372-388`). **VAD** WhisperX pyannote (onset 0.500 / offset 0.363, `asr.py:410-412`). **Alignment** torchaudio `VOXPOPULI_ASR_BASE_10K_DE` for German (`whisperx/alignment.py:35`), override hook `whisperx_engine.py:219-223`. **Word-edge snap** onto measured activity (`domain/analysis/snap.py:27-34`, `ANALYSIS_VERSION=2`). **Diarization** `pyannote/speaker-diarization-community-1`, off by default (`domain/diarization.py:30`). **Acoustics**: Praat pitch two-pass (`acoustic_extractor.py:45,198-237`), RMS, BS.1770 momentary meter, own adaptive energy activity (`:359`), `ImpulseDetector` transient events (`:53-66`), 20 ms hop. **Rhythm** loudness-rise autocorrelation 60-180 BPM (`domain/analysis/rhythm.py:35-36`). Remote alternative: Groq `whisper-large-v3-turbo`, opt-in only (`infrastructure/groq_transcription.py`) | `TranscribeAudioCommand` / `AnalyzeAudioCommand` → `AudioIntelligenceTimeline` | media_library, media_inspection, audio_improvement | CPU 1.1-1.6× real time + 50-60 s load (baseline §4); aligner stretched sentence-final words over pauses up to 9.8-12 s (baseline §7.4, overhaul README "Silences survived") — patched by snap.py; large-v3 "rarely transcribes" immediate repeats and broken-off word starts (overhaul README, v4 review); a month misheard or misspoken at word confidence 0.72 (baseline §7.7); beam/VAD not configurable; diarization never run (README:291-292); no sound-event classifier, `LAUGHTER`/`BREATH` are empty slots (README:174-177); rhythm "not music analysis" (README:186-193); one lock serialises inference (`whisperx_engine.py:92`) | **Strongly model-bound**: ASR model (speed, verbatim-ness), aligner (word edges). See MODEL_RESEARCH §1-2 |
| video_improvement | Conservative colour/exposure/denoise/sharpen into a derived asset | 24 sampled frames → plan → one FFmpeg pass (`hqdn3d`, luma `unsharp`, float64-baked 65³ `.cube` LUT via `lut3d` in `gbrp16le`), linear Rec.709 working space, gray-world WB, hand-written transfer functions (`infrastructure/color_science.py`); **no OCIO** (README:44-46). No ML | `VideoImprover` → derived asset | media_library, media_inspection | Thresholds synthetic; GoPro curves never checked on real files; static per clip; no stabilisation; HDR refused (README:202-216); not in the default stage set | Not model-bound (calibration, vidstab) |
| video_intelligence | Measures the picture: shots, quality, motion, entities, faces, text, embeddings | Profiles `profiles.py:671-740` (standard = shots, quality, motion, saliency, geometry, entities, faces, text, embeddings; deep adds body, appearance, VLM descriptions). Sampling: all frames grey 128 px for cuts; 480 px grey at 12 fps (standard); 640 px RGB at 3 fps adaptive; OCR every 3rd RGB frame ≈ 1 fps (`profiles.py:44-92`). **Shots** NumPy frame-diff + luma histogram (`cut_diff` 0.12, `cut_hist` 0.2), flash/fade/dissolve handling (`profiles.py:156-185`). **Motion** FFT phase correlation 3×3. **Quality** exposure percentiles, gradient sharpness, Immerkaer noise. **Entities** torchvision SSDLite320-MobileNetV3 COCO_V1, min score 0.4 (`torchvision_detector.py:65-73`); IoU tracker (`domain/tracking.py`, `profiles.py:312-330`). **Faces** MediaPipe face_landmarker (4 faces, blendshapes) + `blaze_face_short_range`, min score 0.5, run only on frames where SSDLite found a person (`mediapipe_engines.py:61-99`). **OCR** RapidOCR 3.10, PP-OCRv6 small det+rec (multilingual; `Rec.lang_type="ch"` reads umlauts/ß/€ per `profiles.py:84-86`), min score 0.5. **Embeddings** `openai/clip-vit-base-patch32` fp32 batch 16 (`clip_engines.py:61-63`). **VLM** SmolVLM-256M fp32, deep only (`vlm_descriptions.py:37`) | `AnalyzeVideoCommand` → `VideoAnalysis`; `ShotIndexCatalog`; `VisualTextEncoder` | media_library, media_inspection, video_improvement | No "product" class in COCO; tracking only at sample rate; speaking cue = "mouth moves"; analyzers sequential after one decode (README:226-247). Burned-in caption rule is fragile (§6.1). Faces depend on the person detector (hidden coupling). Real timing per asset 192-1017 s; text median 34 s (max 841 s), entities median 44 s (max 749 s), embeddings median 29 s (max 291 s) over 25 analyses (§7) | Mixed. Model-bound: person detector (gates faces → reframing), CLIP text side (English only). Not model-bound: shots, motion, quality (keep), the caption/overlay rules |
| content_intelligence | Transcript structure: segments, topics, roles, key items, scores | Deterministic engine: EN/DE cue lexicon, TF-IDF with 6-letter stemmer, TextTiling (`domain/segmentation.py:1-9`). Optional embeddings MiniLM multilingual (`application/encoding.py:16`); optional LLM Qwen2.5-1.5B in process or any OpenAI-compatible endpoint (`application/generation.py:14`, `infrastructure/loopback_llm.py`); every LLM suggestion is checked and most are rejected (`domain/refine.py:297-514`; README:236-237) | `AnalyzeContentCommand` → content analysis; `TextEncoder`; `TextGenerator` | media_library, audio_intelligence, video_intelligence | **Pipeline runs pass no embedding model and no LLM**: `ContentConfig(embedding=EmbeddingSettings(device=...))` only (`media_pipeline/application/executors.py:842-844`), so topics are word overlap (README:222-241); 1.5B LLM weak at counting/roles (README:236-237); bf16 on CPU | Model-bound only when enabled; the first step is wiring the existing MiniLM, not a new model |
| audio_visual_alignment | Relates heard to seen (speaker, text match, sound-action) | Pearson lag probe of speech activity vs jaw opening (±3 s, 0.5 s steps; `domain/clock.py:132-209`), exact/normalised word match, optional MiniLM semantic outlier (`config.py:65`). `ALIGNMENT_VERSION=2` | `AlignCommand` → `AlignmentDocument` | media_library, media_inspection, audio_intelligence, video_intelligence, content_intelligence | Mouth signal ~3 samples/s, offsets ≥1 s only, never applied (README:61-65); largest face only (README:123); one video + one audio (README:132) | Bound by upstream models (face rate, diarization), not its own |

### 4.3 Layer 3: story and editing (all rule-based)

| Module | Responsibility | Current implementation | Input → Output | Contract deps | Current limitations (evidence) | Optimization potential |
| --- | --- | --- | --- | --- | --- | --- |
| production_project | Script parse + conformance of recorded lines to script sentences | Regex scenes/approval gates/slots (`domain/script.py:37-83`); bag-of-content-words Dice, argmax per line, `match_at=0.3`, `exclude_at=0.5` (`domain/conform.py:164-201`); score²-weighted median ordering (`:203-215`); retakes / false starts / broken-off (`:307-353`); month-mismatch only (`:356`). No README, no version constant | `Script`, `TranscriptLine` → `Conformance` | none | No order-aware alignment, word order and negation ignored, figures not compared (`conform.py:357-359`); wired only through `scratch/run_pipeline.py:851-887` → `excluded_cuts` → edit replan (`executors.py:311-328,1236-1241`) | Not model-bound first (sequence alignment); MiniLM already exists for paraphrase scoring |
| story_architecture | Narrative plan from content analysis | Rules (`domain/engine.py:107-161`); optional LLM rewording, off by default, grounded check (`domain/refine.py:127`). `PLANNER_VERSION=2` | `PlanStoryCommand` → `StoryPlan` | content_intelligence, audio_visual_alignment, asset_relationships, media_library | Only as good as upstream heuristic roles; no chronology model; synthetic validation (README:167-182) | Model-bound only through content_intelligence roles/claims |
| editing_intelligence | Keep/cut decisions, cut points, cover placement | Rules: judge/tighten/assemble (`domain/judge.py`, `assemble.py:59-69`), profile pause limits (`domain/profile.py:30-43,257`), EN/DE filler lexicons (`domain/speech.py:25-48`), `CoverWish` placement (`domain/wishes.py:44-59`). `EDITOR_VERSION=12` (README:222 still says 4) | `PlanEditCommand` → `EditPlan` | story, pacing, broll, reframing, continuity, content, audio, alignment, video, inspection, library | No real-footage calibration; meaning from structure only; false start = 2-token prefix match (`judge.py:221`) (README:235-250) | Thresholds: not model-bound. Retake semantics: depends on ASR verbatim-ness upstream |
| pacing_intelligence | Rhythm measurement + recommendations | Rules on words, pauses, events, static fraction (`domain/speech.py`, `picture.py:26-31`) | `AnalyzePacingCommand` → `PacingAnalysis` | audio, video, content, alignment, inspection, story, library | Uncalibrated; beats from energy autocorrelation (README:111-142) | Not model-bound |
| continuity_intelligence | Judges cuts of a proposed edit | Rules: CLIP cosine (same identity only; `domain/detect.py:42-118`), luma/RB steps, subject/eyeline, audio level steps | `AnalyzeContinuityCommand` → `ContinuityAnalysis` | video, audio, alignment, relationships, broll, content, library | Shot medians only; 2-pass loop with the editor; synthetic (README:109-144) | Identity/re-ID would be model-bound; not a reported weakness |
| broll_intelligence | Footage search for passages; evidence vs illustration | Words (EN/DE stemmer) → CLIP text tower vs stored shot embeddings (`application/retrieval.py:125-170`) → MiniLM on top-12 descriptions (`:176-210`) → near-dup 0.85 → 13 heuristic scores | `AnalyzeBrollCommand` → `BrollAnalysis` | story, pacing, content, video, audio, alignment, relationships, library | German passages go raw to English CLIP (`retrieval.py:53-56`); "found nothing for German passages" and "never sees stills" (overhaul README); `PARTIAL` when any pool asset lacks analysis or a model is unavailable (§6.4) | **Model-bound** (text-image encoder language) + pool hygiene |
| reframing_intelligence | Per-shot crop plan for one target aspect | Subject from VI tracks; face fallback; spectral-residual saliency only in `fireworks_sky` (README:140); widest window, triangular smoothing, speed/zoom limits, dead zone (`domain/planner.py:501-564`, `camera.py:41,161`) | `AnalyzeReframingCommand` → `ReframingAnalysis` | video, inspection, alignment, story, continuity, library | No subject → non-executable placeholder (`planner.py:796-812`); whole-person boxes, no active speaker (README:174-186); downstream crop handling (§6.2) | Mostly not model-bound (downstream); upstream detector quality is |

### 4.4 Layer 4: creative planning and rendering

| Module | Responsibility | Current implementation | Input → Output | Contract deps | Current limitations (evidence) | Optimization potential |
| --- | --- | --- | --- | --- | --- | --- |
| visual_style_engine | Channel identity as versioned data | Pure data + WCAG contrast (`domain/color.py:30`); own font parser. No footage colour work | `ResolveStyleCommand` → `ResolvedStyle` | media_library | Brand files missing; colours measured on 360p (README:248-266) | Not model-bound |
| motion_graphics_engine | Builds and renders all graphics (also captions, transition overlays) | Rule layout → HTML → HyperFrames 0.9.5 + GSAP 3.15 + headless Chrome, SwiftShader, ProRes 4444 (README:250-275). `PLANNER_VERSION=9` | `PlanGraphicsCommand` → `GraphicsPlan` | visual_style, visual_asset_generation, content, library | 0.3-1 s per 1080p frame on CPU; slowest stage in baseline (433 s / 85 s clip) | Render throughput, not model-bound |
| caption_engine | Caption mode, grouping, timing, placement | Rules + DP segmentation, German function-word lists (`domain/text.py`, `segmentation.py:100-147`); trusts an OCR caption span only if it contains a heard word (`domain/engine.py:444-455`). `PLANNER_VERSION=5` | `PlanCaptionsCommand` → `CaptionPlan` | audio, content, video, reframing, inspection, motion, style, library | OCR text boxes not avoided in layout (`domain/layout.py:101-111`); captions disabled by the creator profile; logs: "captions exist as text cues only" (20×) | Not model-bound; quality follows ASR |
| transition_engine | Transition or hard cut per boundary + audio crossfades | Rule cascade (README:71-134). `PLANNER_VERSION=2` | `PlanTransitionsCommand` → `TransitionPlan` | audio, continuity, inspection, pacing, video, motion, style, library | Main layer only; no renderer for non-overlay transitions (README:184-195) | Not model-bound |
| vfx_engine | Punch-in, retime, blur, looks, composites | Rule planner + FFmpeg float `gbrpf32le` graph → ProRes 422 HQ (README:134-147); `SubjectMatter`/`Inpainter` registered as unavailable (`module.py:18-42`) | `PlanVfxCommand` → `VfxPlan` | audio, alignment, reframing, video, inspection, style, library | Punch-ins turned off by the run (stacking with compiler moves); matting/inpainting `not_available` (README:81-82,165-186) | Model-bound only for the four unavailable effects (not a reported weakness) |
| music_and_sound_design | Music/SFX plan, ducking, M+E stem | NumPy/SciPy signal analysis, "no model, no librosa" (`infrastructure/music_signal.py:1-25`): spectral-flux onsets, autocorrelation tempo with 120 BPM prior, Ellis DP beats, 4/4 downbeats; SFX categories from the manifest (`infrastructure/manifest.py:49-118`). `PLANNER_VERSION=8` | `PlanSoundDesignCommand` → `SoundDesignPlan` | audio, pacing, story, library | 24/35 tracks within 2 % BPM, downbeat confidence 0.04-0.45 (README:105-112); vocal presence `not_available`; not validated by listening | Partly model-bound (downbeats); planning not |
| visual_asset_generation | Cut-outs, variants, frame grabs, image selection; no generation | rembg `birefnet-general-lite` on CPU (`domain/values.py:18-19`, `infrastructure/rembg_segmenter.py:35-52`); float32 matte clean-up; `UnavailableImageGenerator` (`module.py:93`) | `PrepareVisualAssetsCommand`, `GrabFrameCommand`, `SelectImagesCommand` | broll, inspection, style, library | Selection by words only (no image embedding); 8-bit RGBA output; never validated with the real model on real photos (README:199-209) | Image embedding for selection is model-bound; not a reported weakness |
| thumbnail_design | A/B/C thumbnails from the brief | Rules + Pillow/NumPy; reaction score from stored per-shot face cues, frame = shot middle (`domain/reactions.py`) | `DesignThumbnailsCommand` → `DesignedThumbnails` | visual_asset_generation, video, style, library | One layout; `pixels.py` copies another module's private ops (README:73-80); runs skipped without owner headline | Not model-bound |

### 4.5 Layers 5-6, orchestration, UI

| Module | Responsibility | Current implementation | Input → Output | Contract deps | Current limitations (evidence) | Optimization potential |
| --- | --- | --- | --- | --- | --- | --- |
| media_pipeline | Per-asset/per-project task DAG, lanes, reuse | ThreadPool with lanes gpu 1 / cpu 2 / light 4 (`application/runner.py`); graph `domain/plan.py:121-287`; preflight reuse (`runner.py:220`); `REPORT_SCHEMA_VERSION=2` | `RunPipelineCommand` → report | ~28 module contracts | No `--force`; models reload per process; no retries (README:260-312); content stage runs without models (§4.2) | Long-lived model worker would save ~50-75 s/process (not model choice) |
| edit_plan_compiler | Edit plan + Layer-4 plans → `timeline_specification` | Pure domain; reframe keys copied 1:1 (`domain/units.py:612-673`); camera-move cycle with **hard-coded pivot** `_FACE=(0.5, 0.42)` (`domain/punch.py:36,107-110`). `COMPILER_VERSION=7` | plans → timeline spec | caption, inspection, library, motion, sound, reframing, transition, vfx, style | Missing analysis → `framing_unresolved` warning, original framing kept (`units.py:612-650`); reframe + punch conflict (`:598-610`) | Face-tracked pivot would use existing VI tracks (not a new model) |
| timeline_builder | Timeline document from the specification | Pure domain, preflight validation (`domain/preflight.py`). `BUILDER_VERSION=2` | spec → built timeline | edit_plan_compiler, library, proxy_manager | Failed every baseline run (music tail, fixed per overhaul README §4); logs show later failures for other items (§7) | Not model-bound |
| resolve_adapter | Drives DaVinci Resolve (build, read back, render) | Scripting API: `ImportMedia`, `AppendToTimeline`, `SetProperty`, Fusion `Transform` "MH_Zoom" with Bezier keys (`infrastructure/scripting.py:372-912`); optional CDL auto grade, off by default (`domain/grade.py`, `config.py:76`); rec709 only (`translate.py:222`) | built timeline → Resolve project / render | inspection, library, proxy_manager, timeline_builder | Aspect-changing crop **held still at the clip's middle frame** (`domain/translate.py:286-289`, marked `shortcut`); static crop zoom from `crop.w` only (`translate.py:485-503`); moving window without Fusion → gap (`:298-306`) | Not model-bound |
| audio_mix_engine | Bus/stem mix with loudness check | FFmpeg graph, ducking as volume expression (`infrastructure/ffmpeg_graph.py:73`), `ebur128` measure + bounded gain; dialogue −18 LUFS (`domain/profile.py:71`) | built timeline → mix | library, timeline_builder | Speech-aware ducking not wired; sample-peak limiter (README:137-155); one run failed measuring loudness (§7) | Not model-bound |
| quality_control | Timeline + preview render checks | FFmpeg `blackdetect`/`freezedetect`/`silencedetect`/`ebur128`/`astats` (`src/media_house/core/infrastructure/ffmpeg_media_analyzer.py`), NumPy sharpness/borders. No ML, no OCR | timeline + render → `QualityReport` | audio_mix, continuity, compiler, inspection, library, resolve_adapter, review, story, timeline_builder | Thresholds uncalibrated; text in graphics, focus, lip-sync not measured | OCR on rendered graphics would be model-bound (optional) |
| final_polish | Readiness report | Rules (`domain/checks.py:91-431`) | QC + review → report | audio_mix, library, QC, review, revision, timeline_builder | First/last/section-middle frames only | Not model-bound |
| export_validation | Validates the exported file | Full decode, codec/fps/pix_fmt/colour tags, black/frozen, loudness; "A/V sync" = container start offset only (`domain/rules.py:500-507`) | export → verdict | audio_mix, inspection, library, render_manager, review, timeline_builder | Never run on a real Resolve render | Not model-bound |
| render_manager | Render profiles, queue, verification | Backend calls `resolve_adapter` (`infrastructure/resolve_backend.py`) | job → rendered file | inspection, library, resolve_adapter, review, timeline_builder | Two profiles only; in-process queue | Not model-bound |
| resolve_export | Approval → export → validation → delivery | `application/run_export.py`, `approvals.py` | approved version → delivery | audio_mix, export_validation, final_polish, library, QC, render_manager, resolve_adapter, review, timeline_builder | Not run on real Resolve; not reachable from the CLI | Not model-bound |
| review_orchestrator | Review state, approvals, audit | State machine (`domain/workflow.py`) + own SQLite `review.db` | events → state | library, timeline_builder | No id existence checks | Not model-bound |
| revision_engine | Feedback → proposals → execution | Regex feedback parsing (`domain/feedback.py`) | findings/feedback → proposals | audio_mix, library, QC, resolve_adapter, review, timeline_builder | Hand-offs not executed (README:62-72) | An LLM parser would be model-bound (not a reported weakness) |
| control_center | PySide6 UI | Views pipeline/resolve/review/sound/thumbnail; static copy of `STAGES` (`application/catalogue.py:1-12`) | – | library, sound, render_manager, resolve_adapter, resolve_export, review, revision, thumbnail | No README; catalogue drift guarded by a test | – |
| workspace | Minimal workspace aggregate | SQLite repo + Qt view (~830 lines) | – | none | – | – |

---

## 5. Capability coverage (what the requested pipeline steps map to)

| Capability | Where | Model / algorithm | Status |
| --- | --- | --- | --- |
| Ingestion / inspection | media_library, media_inspection | SHA-256, ffprobe ×3, full decode | works; duplicate probing (§8) |
| Frame extraction | no dedicated module; VI decodes (grey 128 px all frames, 480 px @12 fps, RGB 640 px @3 fps); `GrabFrameCommand`; library thumbnails | FFmpeg | works |
| Shot detection | video_intelligence | NumPy diff + histogram | works; F1 golden test exists (`tests/integration/video_intelligence/test_accuracy.py`) |
| Scene (semantic) grouping | none | – | not built (VI README: "scenes need embeddings") |
| Visual understanding | video_intelligence | CLIP ViT-B/32 embeddings + zero-shot vocabulary; SmolVLM-256M (deep profile only) | standard runs have no captions/descriptions |
| Object detection / tracking | video_intelligence | SSDLite320 COCO + IoU tracker at 3 fps | weak detector; no product class |
| Face detection | video_intelligence | MediaPipe face landmarker + BlazeFace short-range, only on person frames | coupling to SSDLite |
| OCR | video_intelligence | RapidOCR 3.10, PP-OCRv6 small, ≈1 fps | model OK, aggregation rules fragile (§6.1) |
| Audio analysis | audio_intelligence, audio_improvement | Praat pitch, RMS, BS.1770, energy VAD, impulse events | works |
| ASR | audio_intelligence | WhisperX 3.8.6 / faster-whisper large-v3 int8 CPU; Groq turbo opt-in remote | slow; slips (§6.3) |
| Forced alignment | audio_intelligence | torchaudio wav2vec2 VoxPopuli base 10k DE + `snap.py` | word-end stretching patched, not solved |
| Diarization | audio_intelligence | pyannote community-1 | configured, off, weights absent |
| Music / beat analysis | music_and_sound_design; audio_intelligence rhythm | NumPy Ellis DP; loudness autocorrelation | weak downbeats (README:105-112) |
| Sound classification | none (impulse detector only; SFX categories from manifest) | – | not built |
| Embeddings / semantic search | VI shot index (CLIP), content `TextEncoder` (MiniLM), broll retrieval | CLIP B/32 (EN), MiniLM-L12 multilingual | German→picture search weak |
| Asset relationships | asset_relationships | rules + SHA-256 | no similarity |
| Story / narrative | story_architecture | rules (+ optional Qwen 1.5B wording) | upstream-heuristic bound |
| Pacing | pacing_intelligence | rules | uncalibrated |
| B-roll | broll_intelligence + editing `CoverWish` | staged retrieval | partial runs, English CLIP |
| Reframing | reframing_intelligence → compiler → resolve_adapter | rules + smoothing | downstream handling weak (§6.2) |
| Editing decisions | editing_intelligence, production_project | rules | three retake detectors (§8) |
| Timeline compile/build | edit_plan_compiler, timeline_builder | pure domain | works since fixes; occasional preflight failures |
| Transitions / VFX / graphics / captions | transition_engine, vfx_engine, motion_graphics_engine, caption_engine | rules + FFmpeg + HyperFrames | graphics slow; vfx matting unavailable |
| Colour | video_improvement (LUT, no OCIO), resolve_adapter CDL (off), visual_style (constants) | hand-written colour maths | GoPro full-range tag not converted (§7) |
| Resolve integration | resolve_adapter | scripting API + Fusion | partial outcomes in every Resolve run (§7) |
| Render / export | render_manager, resolve_export, export_validation, pipeline `deliver` | Resolve render + FFmpeg checks | Layer-6 export path not reachable from CLI |
| QC | quality_control, export_validation, final_polish | FFmpeg filters + NumPy | no ML/OCR/lip-sync |

## 6. Known real weaknesses: root cause and whether a model is to blame

### 6.1 OCR misread "9AX" tagged as a burned-in caption over a whole take

Root cause is the **aggregation rule**, not primarily the OCR model.

* `video_intelligence/domain/text_cues.py:249-269` (`find_overlays`): every tracked text whose box
  starts below `caption_top` (0.68, `profiles.py:389`) is grouped per shot; if the shot has more than
  `caption_min_changes` (2, `profiles.py:390`) **distinct normalised strings**, one
  `BURNED_IN_CAPTION` is emitted from the first to the last member (the whole take), labelled with
  `members[0].text`, confidence `n/(n+2)`.
* `normalise` only lower-cases and collapses whitespace (`text_cues.py:42-43`). Three misreads of
  one static string ("9AX", "9AK", "QAX") are three distinct keys, so a static logo/texture
  satisfies "more than 2 changes". No minimum length, no letter/word check, no requirement that the
  strings follow each other in time, no persistence per string, confidence gate only 0.5
  (`profiles.py:381`). Tracking restarts on a missed OCR frame (`text_cues.py:116-117`), which
  multiplies keys. Watermark grouping needs an exact key (`:223-226`), so misreads fall through to
  the caption branch.
* The model itself: PP-OCRv6 small is multilingual (code comment `profiles.py:84-86`; model card
  "supports 50 languages"). At ≈1 fps on 640 px frames, small text on GoPro footage is hard for any
  recogniser.
* Mitigation already present downstream: caption_engine believes an OCR caption span only if it
  contains a heard word (`caption_engine/domain/engine.py:444-455`, planner v5). Other consumers of
  `OverlayKind` (transition_engine on-screen-text checks, continuity identity) get no such guard.
* Verdict: **fix the rule first** (time-ordered, non-overlapping, distinct-by-edit-distance strings
  with a minimum length/alphabetic share). A better recogniser is a secondary benchmark item
  (MODEL_RESEARCH §4).

### 6.2 Reframing analysis and crop handling

Mostly **not model-bound**; the losses are downstream.

1. Resolve holds an aspect-changing window (16:9 → 9:16) still at the clip's middle frame
   (`resolve_adapter/domain/translate.py:286-289`, `shortcut:` comment; `application/views.py:257-266`).
2. Static crop zoom is derived from `crop.w` only (`translate.py:485-503`); a window whose aspect
   differs from the timeline is mis-scaled vertically.
3. A moving window without the Fusion capability becomes a marker/gap (`translate.py:298-306`).
4. Missing reframing analysis → compiler warning `framing_unresolved`, original framing kept
   (`edit_plan_compiler/domain/units.py:612-650`); `framing_target_mismatch` still applies the crop
   (`:634-642`).
5. Compiler camera moves pivot on a hard-coded point `(0.5, 0.42)` (`edit_plan_compiler/domain/punch.py:36,107-110`),
   not on the tracked face that video_intelligence already stores.
6. Upstream model-bound part: faces are only searched on frames where SSDLite320 found a person
   (`video_intelligence/infrastructure/mediapipe_engines.py:~91-99`); BlazeFace *short-range* is
   meant for faces close to the camera; tracking runs at the 3 fps sample rate; no active-speaker
   detection (reframing README:174-186).

Verdict: items 1-5 are code fixes. Item 6 justifies benchmarking a stronger person detector and
running face detection independent of it (MODEL_RESEARCH §5).

### 6.3 Transcription slips

* Evidence: a month that differs from the script at word confidence 0.72 (baseline §7.7, cause not
  determined: misspoken or misheard); large-v3 "rarely transcribes" immediate repeats and
  broken-off word starts (overhaul README, v4 review) — Whisper models tend to clean up
  disfluencies, which hurts retake/false-start detection that relies on the words.
* There is **no verbatim reference and no WER code** in the repo; the 33-66 % "WER" in the baseline
  measures script paraphrase (baseline §2). Accuracy is unquantified.
* Model-bound: yes (ASR model, decoding options). Beam/VAD options are not exposed
  (`whisperx_engine.py:99-104`).

### 6.4 B-roll: "part of the library or a search model could not be used"

* Message: `media_pipeline/application/executors.py:1068`, emitted when `BrollState.PARTIAL`, which
  `broll_intelligence/domain/engine.py:215` sets when there are retrieval notes **or**
  `pool.skipped` **or** `pool.limits`.
* Real logs: pool `assets=27 searchable=25 shots=98 embedded=95` (7 runs) and
  `assets=25 searchable=25 shots=98 embedded=95` (13 runs); 14 warnings "no video analysis is stored
  for it". So in the observed partial runs **two library assets had no video analysis** (stills and
  non-analysed imports are never analysed) and 3 shots had no embedding. Any un-analysed library
  asset makes the whole run partial.
* Separate, model-bound weakness: German passage text is sent raw to the English CLIP text tower
  (`retrieval.py:53-56`); the overhaul README records that B-roll "found nothing for German
  passages". The multilingual MiniLM stage only reranks the top-12 descriptions, and descriptions
  exist only in the deep profile (SmolVLM).
* Verdict: pool hygiene (analyse stills, do not mark a run partial for skipped non-video assets) is
  a code fix; the text-image encoder language is model-bound (MODEL_RESEARCH §3).

### 6.5 Word timing at sentence ends

Forced alignment with the VoxPopuli base aligner stretched last words over following pauses (one
word 12 s; 769 s of 1278 s were pauses; overhaul README). `snap.py` now trims word edges to measured
energy activity. This is a heuristic patch on top of an aligner weakness: model-bound
(MODEL_RESEARCH §2).

## 7. Real run logs (38 logs, 2026-11-09 … 11-11, `~/.media_house_scratch/baseline/_qa/`)

Counts are line occurrences after normalisation; "runs" = distinct log files.

| Pattern | Count | Meaning |
| --- | --- | --- |
| `timeline_build` not buildable: music tail ends after the timeline | 4 runs | baseline bug, fixed (overhaul README §4) |
| `timeline_build` not buildable: graphic `spec_card` placement; clip audio ends after the timeline | 1 run each | later preflight failures of other items |
| `resolve_execute` failed: project name exists, not made by Media House / another project open | 2 + 1 runs | operator/Resolve state, not code quality |
| `resolve_execute` partial: "instructions Resolve's scripting cannot carry out remain" | 15 | every successful Resolve run leaves gaps for a person |
| `setting timelinePlaybackFrameRate: wanted N, Resolve has N` | 17 | Resolve did not accept the playback rate |
| `<asset> is tagged pc (timeline tv); nothing is converted here` | 80 | GoPro full-range footage on a limited-range timeline, not converted: colour-range risk |
| `<asset> is N/N fps on a N fps timeline: the picture is conformed` | 37 | mixed frame rates conformed |
| `deliver` failed: `audio_mix_engine.backend_failed ... (measure the loudness)` | 1 run | FFmpeg measurement exit code |
| `edit_plan` crashed `KeyError` | 1 run | cascaded to every Layer-4 stage |
| `broll` partial + "no video analysis is stored for it" | 7 + 14 | §6.4 |
| B-roll refusals: "nothing in the footage says which one it is", "technical usability N below N", "illustrative only" | 7 each | evidence gate refusing shots |
| `graphics` partial; `spec_card left out: status fallback` | 9; 12 | graphics fallback |
| "captions exist as text cues only: no rendered caption clip is on the timeline" | 20 | captions not rendered (profile) |
| `video_analysis` partial, `faces: not_available`, `text: not_available` | 3 runs (11-09 only) | `vision` extra was not installed then; later analyses computed 9/9 analyzers |
| torchcodec "Could not find module ... libtorchcodec_core{4..7}.dll" | 6 runs | FFmpeg shared libraries missing for torchcodec; pyannote's own file decoding unusable (WhisperX passes waveforms, so ASR unaffected; matters for diarization) |
| WhisperX pyannote "No active speech found" | 6 | clips without speech |
| WhisperX "No language specified" | 6 | cosmetic when `--language de` pinned at another layer |
| RapidOCR "text detection result is empty" | 170 | normal for frames without text |
| `thumbnail_design` skipped: no owner headline | 6 | by design |

Per-analyzer video timing (25 fresh analyses): shots 1.2 s median, faces 1.2, saliency 2.2, quality
5.8, geometry 16.7, motion 17.6 (max 190), embeddings 28.8 (max 291), text 34.2 (max 841), entities
44.2 (max 749). Whole video analysis per asset: 192-1017 s.

## 8. Overlaps and hidden dependencies

1. **Audio decoded up to four times** per source (inspection FULL, audio_improvement, audio_intelligence
   prep + analyzers, proxy validation); **ffprobe repeated** in media_library, media_inspection (three
   passes) and proxy_manager. Only stream facts are shared.
2. **Four speech-activity detectors**: WhisperX pyannote VAD, audio_intelligence energy activity
   (drives `snap.py` and pauses), audio_improvement percentile frames, pyannote segmentation again if
   diarization is enabled.
3. **Loudness measured by four code paths**; `ebur128` parsing copied in at least four modules.
4. **Two relationship engines**: media_inspection `MediaRelations` vs asset_relationships detectors.
5. **Retake / false-start detection in three places** with different rules: production_project
   `conform.py:307-353`, editing `judge.py:221`, editing `assemble.py:523,585`.
6. **Framing shaped in three places**: reframing windows, compiler camera moves (`punch.py`), VFX
   punch-ins (turned off by the run because they stacked).
7. **Faces depend on the object detector** (SSDLite gate); reframing, thumbnails, alignment and
   captions inherit every person miss.
8. **CLIP identity string** couples video_intelligence, broll and continuity (§3).
9. **content_analysis runs model-free in the pipeline** (`executors.py:842-844`) although MiniLM and
   Qwen are installed and fetched; B-roll and alignment do use MiniLM.
10. **production_project and `CoverWish`** are reachable only through `scratch/run_pipeline.py`.
11. **rembg** is a hard dependency of motion graphics and thumbnails through visual_asset_generation.
12. `fetch_models.py` covers no video-intelligence model; a fresh machine silently degrades to
    `not_available` analyzers.

## 9. Caching and provenance

* Every owner module fingerprints its own inputs, settings and engine identity; the library stores
  derived assets per fingerprint. Pipeline statuses `reused` with `preflight_hit` / `owner_hit`.
* Engine identities include library versions and model ids: WhisperX `("whisperx", <version>)`
  (`whisperx_engine.py:64-73`; device/compute type excluded, README:45), CLIP `repo@revision` +
  transformers + torch, OCR rapidocr + onnxruntime + model label + revision, SSDLite filename@sha,
  embedder `transformers-mean-pool/<tf-version>/l256/<model>`, remote LLM `openai-chat-v1@host`.
* Gaps: the OCR model label `"PP-OCRv6-small-det+rec"` is hard-coded (`rapidocr_text.py:31`) rather
  than read from the wheel; the WhisperX identity does not contain the ASR model name (the config
  fingerprint does), and the Groq engine shares the `TranscriptionConfig(engine="whisperx",
  model="large-v3")` fields (ai-providers.md "Known limits"). No `--force`.

## 10. Evaluation and benchmark code that exists

| File | Measures | Data |
| --- | --- | --- |
| `scratch/bench_pipeline.py` | wall time cold/warm/changed/sequential | any input |
| `tests/integration/video_intelligence/test_accuracy.py` | shot F1, camera-move confusion | generated clips |
| `tests/support/refr_eval.py`, `tests/unit/reframing_intelligence/test_evaluation.py` | crop validity/stability, 15 cases | synthetic |
| `tests/support/cont_eval.py`, `tests/unit/continuity_intelligence/test_evaluation.py` | precision/recall/ranking | synthetic |
| `tests/unit/broll_intelligence/test_retrieval_quality.py` | 14 passages vs 333 shots | synthetic |
| `tests/integration/asset_relationships/test_evaluation_script.py` | smoke test of an opt-in script | synthetic |

Missing: WER/CER, word-boundary error, OCR false-positive rate, detection recall on real footage,
B-roll retrieval on German queries, beat F-measure. Every model decision in MODEL_RESEARCH.md needs
one of these first.

## 11. Optimization potential summary

| Area | Model-bound? | Justification |
| --- | --- | --- |
| ASR speed and verbatim-ness (German) | **Yes** | large-v3 on CPU is the largest analysis cost; disfluencies dropped |
| Word boundaries | **Yes** | VoxPopuli base aligner stretches words; snap.py is a patch |
| B-roll German text → picture | **Yes** | English CLIP text tower |
| Person/face detection for reframing | **Partly** | SSDLite gate; but crop handling downstream is the larger loss |
| OCR caption false positives | **Mostly no** | aggregation rule; model is multilingual |
| Local LLM for story/content wording | **Yes, if used** | 1.5B mostly rejected; currently off in the pipeline |
| Music downbeats | **Partly** | weak downbeat confidence; not a reported render complaint |
| Shots, motion, quality, loudness, DSP, colour, all Layer 3-6 planning | **No** | rules/DSP; calibration on real footage is the lever |
| Throughput | **No** (mostly) | model reload per process, graphics rendering, full-file inspection |
