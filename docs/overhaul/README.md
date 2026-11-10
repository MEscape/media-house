# Pipeline overhaul: findings, gaps and roadmap (started 2026-11-09)

Scope: the two master prompts (control-center UI; quality/intelligence/reliability overhaul) plus the
owner's notes (licensed music, project context file, free-tier AI, motion-graphics presets).
The legal/compliance module is out of scope for now (owner's decision).

Companion documents in this folder:

* [baseline-2026-11-09.md](baseline-2026-11-09.md): the current pipeline run on real footage (a
  sponsored product video, 21 GoPro HEVC 1080p29.97 clips). Timings, errors, quality problems.
* [ai-providers.md](ai-providers.md): which free-tier AI provider for which task, limits, NDA notes,
  key setup.

## 1. Architecture as found

* Modular monolith, hexagonal, PySide6 desktop shell; 35 product modules, ~190k lines; rules enforced by
  `tests/architecture`. Persistence: the Media Library (assets, derived assets, documents) is the cache;
  `review_orchestrator` has its own `review.db`.
* `media_pipeline` is the one dependency graph over all modules (`domain/plan.py`), run as a job;
  CLI: `scratch/run_pipeline.py`.
* Every module is real code (no stubs) with an honest README. Almost everything is validated on
  synthetic data only. `resolve_adapter`, `render_manager`, `resolve_export` were never run against a
  real DaVinci Resolve.
* No module calls a cloud model. Planning in Layers 3-6 is rule based; Layer 2 uses local models
  (WhisperX large-v3, CLIP, SSDLite, MediaPipe, RapidOCR, MiniLM, optional Qwen2.5-1.5B).

## 2. Module inventory (status = code exists; "validated" = on synthetic data unless stated)

| Layer | Module | Status | Unit of work | Main limit |
|---|---|---|---|---|
| 1 | media_library | implemented | asset | no PDF/docs; exact (sha256) duplicates only |
| 1 | media_inspection | implemented | asset | no image inspection |
| 1 | asset_relationships | implemented | asset pairs | no perceptual/visual similarity, no projects |
| 1 | proxy_manager | implemented | asset | no batch/retry, no HW encode |
| 2 | audio_improvement | implemented | asset | mono/stereo only |
| 2 | audio_intelligence | implemented | asset | WhisperX on CPU ~1.1-1.6x real time; Groq engine added (opt-in) |
| 2 | video_improvement | implemented | asset | no stabilisation; HDR refused |
| 2 | video_intelligence | implemented | asset | no "product" class |
| 2 | content_intelligence | implemented | asset/transcript | word-overlap topics; small local LLM mostly rejected |
| 2 | audio_visual_alignment | implemented | 1 video + 1 audio | several cameras not related |
| 3 | story_architecture | implemented | several assets, given order | no project model; no script/brief input |
| 3 | editing_intelligence | implemented | several assets | heuristic cuts; 0-0.7 cuts/min on real footage |
| 3 | continuity / broll / pacing / reframing | implemented | edit / library | synthetic validation only |
| 4 | visual_style_engine | implemented | style | brand files missing; identity `mesc` v2, profile `ranking` exist |
| 4 | motion_graphics_engine | implemented | asset | HyperFrames on CPU is the slowest stage (433 s / 85 s clip) |
| 4 | caption / transition / vfx | implemented | asset | vfx has no matting/inpainting |
| 4 | music_and_sound_design | implemented | asset | licence gate exists (only cleared files selected) |
| 4 | visual_asset_generation | partial | image | image generation always "not available" |
| 5 | edit_plan_compiler / timeline_builder | implemented | asset | builder failed on every real run (fixed, see 4) |
| 5 | resolve_adapter / audio_mix_engine | implemented, unverified | timeline | never run on real Resolve / never listened to |
| 6 | quality_control ... resolve_export, review_orchestrator | implemented | timeline | not in the pipeline graph; no UI |

## 3. Gaps that block a real multi-clip video (ranked by impact on the finished video)

1. **No project model.** The pipeline is per asset; story/edit accept several asset ids but the pipeline
   passes one (`media_pipeline/application/executors.py`). A folder of clips + images + script + brief
   cannot become one video. Needed: a `production_project` module (project, asset roles, brief,
   script scenes, feature settings, queue) and a project-level pipeline run.
2. **No brief/script input.** The context file and the scripted transcript (scenes, optional blocks,
   approval-gated sentences, fact tables) are not consumed anywhere. The baseline kept a sentence the
   script allows only with the sponsor's approval.
3. **Editing is far too slow-paced** for the creator style (0-0.7 cut events/min vs 10-14 target;
   17 s silences kept; no punch-ins).
4. **Text cards out of context** (bare numbers, up to 43 s before they are spoken).
5. **Every clip gets a 20 s end screen + music tail**, even a 9.6 s cold open (per-asset thinking).
6. **Graphics rendering cost** (still frames rendered frame by frame; identical end screen re-rendered).
7. **Hot source dialogue** (-11.3 LUFS, -0.4 dBTP) and no mix produced yet to check it.
8. **UI**: none of the six layers is visible or controllable.
9. **Resolve path unverified** on a real Resolve.

## 4. Done in this session

* Music: two more YungBrooke tracks from the official channel ("Sleepless", "Atmospheric Sentimental
  Choir Beat") added to `~/Documents/MediaHouseAssets` with full provenance; all three YungBrooke tracks
  are `commercial_use: true` **on the owner's statement of personal permission** (no public grant
  exists except Downfall's "background music in videos"). LICENSES.md says what proof to keep.
* Free-tier AI: OpenAI-compatible remote LLM with API keys from `MEDIA_HOUSE_SECRET_*`, provider
  presets (groq, cerebras, openrouter, gemini, ollama), retry/Retry-After, usage logging; optional Groq
  Whisper transcription engine; both opt-in, off by default, refused without `--allow-remote-ai`.
* Baseline on real footage (report above).
* Fix: `timeline_build` failed on every real run: the appended end screen's end was content + 20.000 s
  while the render is 599 frames at 29.97 fps; the music tail under it overhung the timeline by a
  fraction of a frame. The bookend now ends on its last rendered frame (motion_graphics planner v6).
* Control-center UI foundation (pipeline overview, Resolve status, sound library with licence state,
  review workspace): see the module README of `control_center` once merged.

### Done 2026-11-09/10 (first full run on the real project)

* Project mode (`--project`): many recordings -> one video; the anchor's project stages wait for every
  member's analyses. Script conformance (`--script`, module `production_project`): recordings ordered
  by the scene they speak; approval-gated sentences cut as recorded manual overrides. Parser fix: a
  slot standing for a whole sentence (`... Bombetten. {KALIBER_MM} {ZERLEGER_EFFEKT} Die Höhe ...`)
  no longer takes the next spoken sentence with it, and slot names are never matched as speech.
* Edit profile `creator_ranking` (tight pauses, frequent picture changes).
* Layer 4 read only the anchor's transcript: in a project no price, spec or product name reached
  graphics, and VFX did not know where members' speech was (retiming risk). Now every recording is
  heard through its own transcript.
* Products: `--product-images DIR` names each picture after its file; a section that speaks the name
  gets it on its card/sticker. Bare figures get their label from the product fact they were spoken as.
* Commerce facts: a price said without the currency after "für/kostet" (with cents, never before a
  unit) is a price; "1,5 Kilogramm NEM" is the NEM (never the per-shot "6 Gramm NEM pro Schuss").
  This narrows the former "no currency = never a price" rule on purpose.
* Sound planner crash (negative crossfade tail when a track ends before its cue) fixed (planner v4).
* Audio mix: dialogue levelling decoded the whole 4K video of every source for each clip (about
  2 min per clip, 60 min per mix); it now seeks to the range and skips the picture (0.2 s per clip).
* Motion-graphics preset system (agent): design tokens, typed `GraphicOrder` slots, text fitting,
  fixed choreography, still fast path (an 84.7 s label 300 s -> 12 s), end-screen reuse.
  See `docs/layer4/preset-audit-2026-11-09.md`.
* Resolve 21.1 Studio verified on the real install: project built, read back and verified. Resolve
  cannot import Ogg Vorbis: the 112 Kenney SFX got WAV copies beside the OGGs (manifest points to the
  WAVs; backup kept). A failed import blocks the render by design.

### Done 2026-11-10 (owner review of v1: "bad cutting, silences, bad music, bad zoom")

Root causes found on the real project and fixed (each with tests):

* **Silences survived every cut.** Forced alignment stretched the last word of a sentence across
  the following pause (one word lasted 12 s), so no stage saw a pause. Word edges are now snapped
  to measured speech activity (`audio_intelligence/domain/analysis/snap.py`, analysis v2). 769 s
  of the 1278 s of talking footage were pauses; the target is about 7-7:30 min.
* **Retakes, false starts and wrong facts were kept.** `production_project.conform` now rejects an
  earlier attempt that is said again, a short false start the next line begins with, a broken-off
  sentence with its restart, and a line that names another month than its script sentence
  ("22. Januar" for "22. November"). Lines are split per sentence from the words, and a German
  ordinal ("22.") no longer ends a sentence (script parser too).
* **Wrong recording order** (the delivery chapter before the fifth battery): a recording is placed
  by the score²-weighted median of its matches, so one shared word cannot pull it forward.
* **Punch-ins popped in and out mid-shot** (a 1.1x hard step for 1.5 s, the whole clip re-rendered).
  The run now turns VFX punch-ins off and the compiler frames every other back-to-back talking
  clip tighter (`CompilerConfig.jump_cut_zoom`, a static window Resolve applies as zoom/position).
* **Music**: four rotating library tracks were chosen because the owner's tracks were in
  categories the ranking profile never asks for. Runs can now pass sound/VFX overrides
  (`sound_overrides`, `vfx_overrides` in the run config); the YungBrooke tracks carry the manifest
  category `signature` and the run asks only for it. Bed level and SFX under speech are set there.
* **"Meaningful" pauses were kept whole** (up to 16.7 s): a pause before an emphasis or a payoff
  is now a 0.6 s beat (editor v7); a breath no longer protects a pause.
* **No B-roll**: the B-roll search found nothing for German passages (CLIP is English-trained) and
  never sees stills. The editor now takes `CoverWish`es (footage asked for over named speech);
  the run config's `cover` list names them by phrase ("over 'in die Schlange vor Lidl' show the
  rush archive"). `lidl_letztes_jahr_überblick` is not used: other brands are visible in it.
* **SFX were blocked**: the presenter's loud words counted as protected "original" bangs (sound
  planner v5), and price/rank reveals carried the motion's strength (0.5) instead of being key
  moments (0.8).
* **Wrong card labels**: a product name labelled a figure said 40 s later ("Pure Impact 79,00 €"
  for the minimum order value); with an owner product list only those names label a card, and only
  the figure said within 12 s of the name.

Result on the real project: 8:25 instead of 15:04, 123 picture changes (about 15 per minute),
dead air cut to beats, music bed about 13 LU under the voice, -14 LUFS / -1.1 dBTP.

## 5. Roadmap (quality before speed)

1. `production_project`: project + asset roles + brief parser + script parser + queue (contracts only).
2. Project-level pipeline run: one story/edit over all project clips in script order; script scenes
   drive section order; approval-gated and unfilled-slot sentences excluded; one end screen per video.
3. Editing intelligence for the creator profile: cut density, silence removal, punch-ins on emphasis,
   take selection (calm vs energetic take), B-roll from roles (packshots, archive, plates).
4. Motion-graphics preset system: design tokens, constrained typed slots per kind ("puzzle surface"),
   text fitting, choreography library, still-graphic fast path (render one frame, hold), end-screen reuse.
5. Brief-driven Layer 4: allowed/forbidden transitions, max overlays, text-card word limit, decimal-comma
   prices, sponsor label span, brand whitelist.
6. Mix on real footage; Resolve verified on the real install; review loop in the UI.
7. Then performance: remote transcription (Groq) where the owner consents, render caching, parallelism.

## 6. Risks

* **NDA**: client footage and text must not go to providers that train on data (Gemini free tier does).
  Remote AI is opt-in per run. Tests and docs in this repo must not contain client text.
* **Music proof**: YungBrooke permission is verbal per the owner; Content ID disputes need it in writing.
  The background fireworks plate in the project folder came from a YouTube "royalty free" upload:
  provenance unverified.
* Mixkit SFX were fetched by script, which Mixkit's terms forbid (see LICENSES.md); delete
  `sfx/mixkit/` to remove doubt.
* Resolve integration is the least proven part; first real run may surface API differences.
