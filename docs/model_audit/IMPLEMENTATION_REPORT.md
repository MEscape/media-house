# Implementation report (2026-11-11)

Every change is in the pipeline's code or defaults, not in a per-video brief. Nothing was committed.
Rollback for each item is its config switch or a revert of the named files.

## 1. Short (9:16) quality

| Change | Where | Why |
| --- | --- | --- |
| The compiler and the caption planner find the reframing analysis under the story's anchor recording too | `edit_plan_compiler/application/compile_timeline.py` `_analysis`; `caption_engine/application/plan_captions.py` `_subjects` | one analysis covers all recordings and is stored under the anchor; an excerpt without the anchor clip lost every crop ("requested derivative") |
| An aspect-changing window becomes zoom/pan clip properties | `resolve_adapter/domain/translate.py` (`_reshapes`) | a Fusion zoom keeps the source aspect: a 9:16 window of 16:9 footage was a zoom of 1.0 (letterboxed) |
| The held window is the one at the clip's middle (interpolated) | `resolve_adapter/application/views.py` `_window_at` | the analysis keys the whole take; its first key put the subject off-frame |
| Camera moves inside a reframed window (pivot = the window's centre) | `edit_plan_compiler/domain/punch.py` `_pivot_of`; adapter `_crop`/`_motion` | Shorts had no moves at all |
| The excerpt prefers a stretch that opens its section (a product's introduction) | `editing_intelligence/domain/highlight.py` `_OPENS` | the Short began mid-product; viewers did not know what was happening |
| No end screen on a Short | `media_pipeline/application/executors.py` graphics brief | 20 s of end screen after the payoff |
| Portrait layouts for the full-screen intro and data cards (cut-out on top, 2×2 facts) | `motion_graphics_engine/domain/layout.py` `_portrait_image` | the 16:9 layout overflowed and fell back |
| A 9:16 output picks a Shorts caption profile automatically; `shorts_product_test` has emoji (6/min) and the strongest animation; 19 German emoji stems added | `caption_engine/domain/profile.py` (`SHORT_OF`, vocabulary) | Shorts used the long video's "no captions" profile; the owner wants emoji captions |
| No caption while a full-screen card fills the picture | `caption_engine` `full_screen` input, `selection._off_screens` | the caption band ran through the card text |
| The pipeline hands the run's graphics plan to the caption planner | `PlanCaptionsCommand.graphics` | a stale plan was read from the library (race) |
| OCR misreads are not a burned-in caption (consumer guard + source rule) | `caption_engine/domain/engine.py` `_mode`; `video_intelligence/domain/text_cues.py` | "9AX" over a whole take disabled all captions |

## 2. Automation (no per-video brief)

`media_pipeline` auto brief (`domain/auto_brief.py`, `application/auto_brief.py`): from the project
folder (`00_kontext` … `06_gfx`), the stored analyses and the format profile it derives graphics
wishes, covers, screen windows, background footage, intro/outro and per-format style defaults. Hand
entries in `run.json` still win per key; every automatic decision carries its reason (logged and
stored). Validated against the hand brief: 12/25 graphics, 5/6 covers, the screen window, background,
intro/outro and all style keys reproduced; 13 copy-written graphics and the `drop` phrase are not.

## 3. No garbage

| Change | Where |
| --- | --- |
| Without a project name the pipeline uses its own project `MH <recording>` (`… Short` for 9:16) | `executors._own_project` |
| New policy `regenerate`: a fresh timeline replaces the one Media House made before (deleted, then renamed into place) | `resolve_adapter` values / execution / scripting `delete_timeline` |
| A new edit finds the binding of the same project (no "not made by Media House" refusal) | `store.latest_binding(project=…)` |
| On delivery, older renders of the same video and format and what was made from them are archived (restorable) | `executors._prune` |
| Removing an element Resolve merely rounded is not a person's change | `reconcile._removed` uses `_close` |
| Re-used graphics units no longer duplicate background plates | `edit_plan_compiler/domain/engine.py` |

## 4. Models

No model was replaced: no candidate beat the current one on quality with enough evidence.
`large-v3-turbo` is available now as `transcription_model: large-v3-turbo` (−35 % CPU, −1.5 GB);
the default stays until a verbatim reference confirms accuracy. The OCR problem was a rule and is
fixed without a model change.

## Versions bumped (old cached results are not reused)

editing EDITOR_VERSION 12 · edit_plan_compiler COMPILER_VERSION 7 · motion graphics COMPILER_VERSION 8
· caption PLANNER_VERSION 6, PROFILE_VERSION 2 · video_intelligence PROCESSING_VERSION 3 (stored OCR
and model signals are reused; only the derivation re-runs) · overlays derivation 2.

## Tests

New regression tests for each fix (caption selection, Short profile, reframe translation, window at
clip time, reconcile rounding, regenerate, prune, highlight opening, OCR rule). Affected suites plus
architecture: 1335 passed; video_intelligence 255 passed (1 GPU-only test deselected: no NVIDIA
driver).

## Round 2: the Short (owner review of the first automatic Short)

Owner findings: graphics backgrounds did not fill 9:16; emoji should fly in and out; too little
happens; the Short missed the opener and most items; sound effects inconsistent; music drowned the
voice. Every fix is a rule for any future Short, not this video.

| Finding | Root cause | Fix (where) |
| --- | --- | --- |
| Card background a 16:9 band | plates and cover footage placed with `contain` | portrait: plates and cover fill (`timeline_builder` `_fit`); portrait cards draw an opaque full-bleed ground (`motion_graphics_engine` layout/composition) |
| Opener and items 1–3 missing | the Short was one contiguous highlight | `condensed` excerpt (default): hook, premise, every item's strongest phrases, verdict, CTA, fitted to the range (`editing_intelligence/domain/condense.py`); the Short's story parts are reported to the gate |
| No inserts | cover was tied to exact long-video sentences | footage belongs to its section: one insert per item, the hook's gag clip first (`editing_intelligence/domain/inserts.py`) |
| Letterboxed speaker (regression found by the gate) | (1) a lone speaker whose gesturing arms leave the window was "unreframeable"; (2) the adapter took the camera-beat plan for the 9:16 window | one person carries the shot: clipped arms are a tighter framing (`reframing_intelligence` planner); camera plans are recognised by `target == "source"` (`resolve_adapter` views) |
| Little motion | static talking stretches and 3–6 s static cards | camera beat every 2.2 s, zoom-through cuts (`edit_plan_compiler` punch); hook title, one card per product (≤ 3.2 s), fact punches, cut-out pops, a beat halfway through each held card (`motion_graphics_engine` short_form/animation) |
| Emoji small and static | inline emoji, 6/min | a big sticker that pops in and flies out, 12–14/min, ~190 German stems (`caption_engine` + motion layout/animation) |
| Music over the voice | the Resolve timeline played every clip at 0 dB (the review mix was 27 LU under: inaudible) | every music/SFX clip is measured and placed against the dialogue (Shorts: music 15 LU under speech, impacts 4, whooshes 8, UI 10); Resolve clips get the same `AudioVolume` (`audio_mix_engine` levels, `resolve_adapter` clip volume) |
| Wrong / random sounds | rotation of files, ties by asset id | one sound per event kind per video, chosen by meaning (prefer/avoid lists); a sound on every graphic, emoji and transition in a Short (`music_and_sound_design`) |

### Quality gate with iterations

`media_pipeline/domain/short_gate.py` judges every delivered portrait video, measured on the file
and the mix stems (`application/short_review.py`): fill (no letterbox), longest stretch without a
visible change (≤ 3 s), music under speech (12–22 LU), each effect against the voice and their
spread, effects and emoji per minute, caption coverage outside full-screen cards, the story parts,
duration, loudness and true peak. A failed rule names a remedy; the runner (`gate_iterations`,
default 3) turns that knob one bounded step (`application/short_iterate.py`: music/SFX bus level,
camera beat, SFX spacing, excerpt range) and runs again; a knob at its bound stops the loop.
The gate found the letterbox regression and the static cards above; the change threshold (0.95)
is calibrated on measured frames (caption-only talking head ≥ 0.96, card beat 0.94, cut ≤ 0.90).
