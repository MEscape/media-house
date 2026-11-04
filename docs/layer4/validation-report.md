# End-to-end validation report: raw media to Layer 4 output

Date: 2026-11-02/03. Scope: the whole system through the real pipeline CLI, from the owner's own
channel videos to the stored Layer 4 plans and rendered assets. `timeline_builder`,
`resolve_adapter` and `quality_control` are NOT built: there is **no Resolve test and no final
video render** in this report, and none is claimed. The "previews" below are a validation-only
composite made by a scratch script, not a product feature and not a timeline builder.

Nothing is committed. All scratch material (inputs, homes, logs, JSON reports, scripts, previews)
lives in the session scratchpad `...\scratchpad\e2e\`.

## 1. Environment

| Item | Value |
| --- | --- |
| OS / shell | Windows 11 Pro 10.0.26200, Git Bash + PowerShell 7 |
| Python / tools | 3.13 (uv, `.venv`), FFmpeg/FFprobe 9.0.2 (gyan full build), Node 24 + HyperFrames 0.9.5 |
| Hardware | CPU only (no CUDA, "Found no NVIDIA driver"); every run used `--cpu-only` |
| Models on disk | WhisperX large-v3 + German wav2vec2 alignment, BiRefNet lite (rembg), MiniLM embedder, Qwen2.5-1.5B |
| Video models | CLIP ViT-B/32, SmolVLM-256M, SSDLite320, MediaPipe face/pose/hand: downloaded once for this validation (owner-approved) by `scripts/prime_video_models.py` (scratch) through the module's own `ModelStore.ensure` / `ensure_snapshot` with allow=True, into the main home's own cache. The pipeline CLI now has `--allow-model-download` (O8, off by default); no run here used it, so the pipeline itself never downloaded |
| Sound library | `C:\Users\L14\Documents\MediaHouseAssets\manifest.json`: 250 sounds imported and measured per home (`scratch/import_sound_library.py`, 4.5 min), 1 not cleared for commercial use, 54 without AI/test permission |
| Homes | `homes\main` (all scenarios, one shared library; reached through the junction `%TEMP%\mhe2e`) and `homes\fresh` (`%TEMP%\mhe2f`, reuse proof). Each was given junctions to the user-cache content models and HyperFrames install before O6 was fixed; after the fix a clean home without junctions (`%TEMP%\mhe2g`, deleted afterwards) resolved the content models and HyperFrames 0.9.5 from the user cache (`AppPaths.install`) |

Interruption: the machine was suspended for about 3.5 h during run 2 (the clock jumped from
23:3x to 03:4x). The B2 `audio_analysis` time (14 378 s) and the architecture-suite time
(4 h 06 min) include that pause and are not performance figures.

## 2. Scenarios and inputs

Excerpts were cut with FFmpeg stream copy (`-ss ... -c copy -avoid_negative_ts make_zero`), so they
start on the preceding keyframe. All are the owner's published 360p videos (already edited: they
carry the channel's own graphics and captions, which matters for findings D3 and O1).

| Id | Source | Excerpt | Shape | Content | Run arguments (besides the full stage list, `--language de --cpu-only --content-uses-video`) |
| --- | --- | --- | --- | --- | --- |
| A | `t_BTkcPy5sw` (owner favourite, Top 10 ranking) | 76 s +136 s = 137.1 s | 640x360 16:9, 30 fps | "Platz 9", "dem achten Platz", "Platz 7", "Platz 6"; prices 14 €, 2,29/2,99, 10 € | `--story-format top_n --pacing-profile top_10_ranking --broll-profile product_review --format-profile ranking --output-format 16:9` |
| B | `nsIVE20ixMM` (haul Short) | whole, 30.5 s | 360x640 9:16 | VO 220 wpm, "600 Euro"; **already has burned-in captions** | `--story-format short --reframe-target 9:16 --format-profile shorts_haul --output-format 9:16` |
| B2 | `hPWgWyzYtq8` (sponsored haul Short) | whole, 52.5 s | 360x640 9:16 | prices 150 €, 9,99, 3,99, 2,49, 4,44, 15,99, 19,99; no word captions | as B plus `--sponsored` |
| C | `y_mj09m0XB4` (NYE uncut) | 240 s +80 s = 84.9 s | 640x360 16:9, 29.97 fps | fireworks, 6 shouted words, clipping peaks | `--format-profile nye_uncut --output-format 16:9` |
| D | brochure review | cut, not run | | | dropped for time |

Stages: `inspect,thumbnail,proxy,relationships,audio_analysis,video_analysis,content_analysis,
alignment,story,pacing,edit_plan,broll,continuity,reframing,layer4` (reframing runs only for B/B2,
the only runs with a target). Script: `scratchpad\e2e\scripts\run_scenarios.sh`.

Runs (main home): run1 = first contact (found D1-D9); run2/2b = after the first fixes; run3-run5
= reuse checks (found D10, D11); run6/run7 = after those fixes; run8/run9 + `C_vfx` = after O1-O3,
O8-O10 (`C_vfx`: C with `--format-profile shorts_product_test --vfx-profile fireworks_showcase`, to
get a VFX render); run10-run13 = continuation (found D12-D14); **run14 = final computation, run15 =
final reuse proof** (A, B2, C); run16 = after O14 (appended end screen), run17 = its reuse proof
(A, C only). B was not rerun after run7. Fresh home: f1/f2/f3 (B only). Since
run9, scenario C in `run_scenarios.sh` also passes `--vfx-profile fireworks_showcase`.

## 3. Per-stage results (Part 18 stages 1-11, 13-14)

Status per stage across A, B, B2, C in the final state (run14/run15 for A, B2, C; run7 for B)
unless a run is named. Timings are
first-computation times from run1 (A, B, C) or the fresh home (B f1).

| # | Stage | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Ingestion / inspection | PASS | 4 originals imported; inspection 0.8-2.7 s; verdict valid; every later stage reuses the stored inspection (`preflight_hit`) |
| 1 | Relationships | PASS (nothing to relate) | task succeeded 0.3 s, 8 analysis marks, 0 claims (excerpts of different videos) |
| 1 | Proxy | PASS | ProRes proxies, 6.7 s (B) / 40 s (A), verdict valid, reused on every rerun |
| 2 | Audio (WhisperX large-v3, de pinned) | PASS | B 135.7 s for 30.5 s, A 396.8 s for 137 s, C 134.3 s for 85 s (CPU); German text with umlauts intact; forced-aligned words (A 358, B 111, B2 131, C 6); C impulse events present (381 events incl. `impulse`, `energy_spike`) |
| 2 | Content re-used the transcript | PASS after fix D1 | run1 (before fix): a SECOND WhisperX transcription with `language=auto` (91 s, two `transcription` documents); after: content 0.2 s, one transcript |
| 3 | Video (shots, quality, models) | PASS | B 114.6 s, A 250.4 s, C 130.4 s; CLIP/VLM/detector/faces/OCR all ran (documents `video_intelligence.{shots,entities,faces,text,embeddings,...}`); OCR found the source's own captions (29 overlays on B). Night preset: no explicit preset knob was set; C was analysed with the standard profile (not verified as "night") |
| 4 | Content / commerce facts | PASS after fixes D6, D7 | A: "Platz 9", "Platz 7" and, after D6, "dem achten Platz" -> rank 8; prices 14 €, 10 €; B2: 150 € and item prices; after D7 "Neuheit 9,99" is no longer the product "Neuheit 9" |
| 5 | Alignment | PASS | 3.8-6 s; reused on reruns after fix D11 |
| 6 | Story | PASS (A, B, B2); C no-speech after fix D5 | C run1 built a story from 6 shouts and the edit kept 4.2 s of an 85 s fireworks take; now `story: skipped_unavailable` -> no-speech path, Layer 4 covers the whole recording |
| 7 | Pacing | PASS after fix D4 | A run1: `pacing failed: invalid_analysis` (validator disagreed with the engine); fixed |
| 8 | Edit plan | PASS | A: 3 contiguous main clips 136.6 s; B: 2 clips 30.3 s; B2: 3 clips 52.5 s; C: none (no-speech path) |
| 9 | B-roll | PASS (nothing qualifying) | A 7.3 s / B2 21 s library-wide search, no satisfying candidate; B: `skipped_unavailable` (no passage needs footage) |
| 10 | Continuity | PASS | B/B2 judged their cuts; A: `skipped_unavailable` (its clips are contiguous: no cut to judge); C: none |
| 11 | Reframing (9:16) | PASS | B/B2 reframing analyses stored; edit clips carry FramingRefs (source already 9:16) |
| 13 | Style resolution | PASS | one `visual_style` per anchor, identity v2; profiles ranking / shorts_haul / nye_uncut; same fingerprint in every visual plan (section 4) |
| 13 | Visual assets (BiRefNet stickers) | PASS (driven by a script through the real modules, not by the pipeline) | the pipeline gives products no pictures (no product image source exists), so `scripts\exercise_sticker.py` drove the real modules: `FrameGrabber` (B2 at 5 s, `frame_grab` rgb48be) -> product crop imported -> `image_adjustment` (`birefnet-general-lite`, background removed) -> `image_enhancement` (glow `#FDFEFE` radius 8 / opacity 0.9, tilt -7.5 deg) -> `visual_asset_set` -> `product_sticker` graphic, status ok, rendered ProRes 4444 1080x1920, 78 frames. Looked at: clean cut-out of the "Jokers Inferno" box, tilted; 2 634 semi-transparent edge pixels with mean RGB (239, 236, 224) = the white glow (still `B2_sticker_birefnet_glow_tilt.jpg`) |
| 13 | Graphics | PASS with open issues | after O1-O3 and D12-D14 (run14): A draws no rank card (the picture shows "PLATZ 9/8/7/6": `editorial.already_on_screen`), no 14,00 / 2,29 / 2,99 tag (the picture shows "13,99 EUR", "2,29 EUR", "2,99 EUR"), the 10,00 € tag at 120.6 s where it is spoken, and the end screen; B2: the Werbung label only where the source's own is missing (0-0.46 s) and 15 price tags, each at its spoken time, one zone at a time (`layout.zone_queued`); renders ProRes 4444 with real alpha; open: O13 (B2 tags duplicate prices that OCR did not read). After O14 (run16/run17): the end screen is appended after the content (A 136.63-156.63 s, C 84.86-104.86 s), the plan declares `timeline_end_with_bookends` |
| 13 | Captions | PASS with fixes D2, D3, O9 | A: disabled (ranking profile, channel evidence); B2: full captions, 39 groups rendered as **one** HyperFrames file of 1 256 frames (O9: `asset_start_frame` per group), German text intact, 3 suspect + 5 low-confidence words not burned in; SRT 16 / VTT cues in the plan; B: run1 full captions on top of the source's own burned-in captions -> after D3 disabled ("already shows burned-in captions"), subtitles still exported |
| 13 | Transitions | PASS | pipeline: hard-cut dominance, every boundary is a cut (`hard_cut`, `jump_cut`, `action_cut`), status `not_needed`; sequence start/end bookends present. Graphic overlay driven by `scripts\exercise_transition.py` (A's stored edit through the pipeline's own `view_of` / `transition_sequence`; the end-screen bookend moved to the end of the last clip, because before O14 the pipeline placed it inside the clip: `bookend.not_at_cut`): `pixel_dissolve`, status `brand`, 18 frames, ProRes 4444 1920x1080 30 fps with alpha; frame 4 is 56 % transparent, frame 9 fully opaque, frame 14 56 % transparent (looked at over a green plate: square red/black pixel blocks); plan checks `frames.off_grid` x2. After O14 the pipeline itself plans it (run16/run17): A and C get an `end:` `pixel_dissolve` (status `brand`, overlay rendered/reused) at the content end, no `bookend.not_at_cut`. The membership-plug zoom tunnel was not exercised |
| 13 | VFX | PASS | A, B, B2: no effect (`slow_motion` rejected, `punch_in` bypassed). C in `C_vfx` (format profile `shorts_product_test`, `--vfx-profile fireworks_showcase`): one burst-peak treatment at the bang at 79.40 s (audio impulse 0.73): normal 0-2368, slow 0.5x 2368-2391, **freeze 2391-2409** (79.78-80.38 s), slow 2409-2481, normal to 2543. `vfx_render` by ffprobe: `prores` profile HQ (`apch`), `yuv422p10le`, 10 bit, 640x360, 30000/1001, 2 543 frames = expected, source unchanged. Frames 2395 and 2405 are byte-identical PNGs (the freeze); the freeze frame shows a burst flash with sparks (still `C_vfx_slowmo_and_freeze_frames_2380_2395.jpg`). QC warning `temporal_spikes` (5 new spikes). In run9-run15 (`nye_uncut`) the same slow-motion is rejected: "intensity 2 is above the ceiling 1", so C's final plan applies no effect. The visual-peak reason reads "median brightness flat 0.000": on a night sky the median is 0, so the peak falls back to the audio bang |
| 14 | Sound design | PASS after fixes D9, O10 | licence policy: only cleared tracks; credits for every CC-BY track (A 3, B 1, B2 1); ducking under speech (A 114, B 44, B2 88 intents); C: no music, no SFX, original fireworks audio kept, now `original_audio.mode = dominant` ("clip sound is the content"; was `speech`, O10); stems WAV f32 48 kHz; A run1 stem FAILED (D9) -> fixed. SFX on graphic events (A, run14): the graphics events reach sound design, but A now draws no rank card (O1), so no rank-reveal accent exists, and every price accent is declined `too_weak` ("strength 0.50 below 0.70 for price accent", O12): A has 0 SFX and so 0 overlaps with its 358 words. B2: 4 soft SFX on caption emoji/keyword events over words, each recorded as a conflict (section 4) |
| - | Rerun = everything reused | PASS after fixes D10, D11 (with one caveat) | final run15 (after run14 recomputed graphics, captions and sound): A 18, B2 20, C 15 tasks `reused`, 0 computed (the rest not requested / not applicable); run7: B 19/19. Fresh home B: f1 computes all, f2 recomputes 8 light tasks (edit refined with the continuity evidence of f1, by design), f3 19/19 reused (caveat O5) |

## 4. Cross-module consistency (scratch `scripts\validate_plans.py`)

Reads the library read-only (sqlite + stored JSON + ffprobe), independent of module code. Since
the continuation it takes `--report <run.json>` and checks the plans **that run used** (the
`plan_asset_id`s of the report), not the latest stored plan of the anchor: the exercise scripts
and `C_vfx` store plans of the same anchors with other profiles, and "latest by creation time"
then mixed them (false FAILs in the first run13 check). After O14, "all times on the timeline"
accepts times after the edit only inside the declared appended span (`timeline_end_with_bookends`)
and only for the end-screen graphic and its events, the transition plan and the sound plan -
never for captions or VFX; on the no-speech path the content end is the sound plan's clip end.
Post-O14 (run17): A 15 PASS 1 WARN (10 times inside the appended span), C 16 PASS (8). Final results for A, B2, C:
`reports\*_validate_run15.{txt,json}` (exit 0 each: A 15 PASS 1 WARN, B2 15 PASS 1 WARN, C 16
PASS); B: `reports\B_validate_final.json` (run7).

| Check | A (run15) | B (run7) | B2 (run15) | C (run15) |
| --- | --- | --- | --- | --- |
| all 6 Layer 4 plans stored | PASS | PASS | PASS | PASS |
| one StyleRef fingerprint in graphics/caption/transition/vfx plans | PASS `3d677945` | PASS `c8f3fdc9` | PASS `efaa1599` | PASS `25efac61` |
| identity v2 | PASS | PASS | PASS | PASS |
| same EditRef + anchor in every plan | PASS | PASS | PASS | PASS (all `edit: null`, no-speech path; the stale edit plan of O4 is not this run's) |
| all times on the timeline, within the edit | PASS (381) | PASS (206) | PASS (815) | PASS (36) |
| graphics never share a zone at the same time | PASS | PASS | PASS | PASS |
| captions vs graphics zones/rectangles | PASS (no captions) | PASS (no captions) | PASS (39 x 16) | PASS |
| SFX vs speech words | PASS (0 SFX, 358 words) | PASS | WARN: 4 soft accents (-12..-18 dB) on caption emoji/keyword events overlap words; the plan records each as conflict "plays under speech (soft sound)" | PASS (0 SFX) |
| transitions vs VFX speed effects | PASS (no retime) | PASS | PASS | PASS (no retime: under `nye_uncut` the burst slow-motion is rejected, "intensity 2 is above the ceiling 1") |
| rendered assets exist + format | PASS 3 | PASS | PASS 16 | PASS 1 |
| credits for attribution-required tracks | PASS 3/3 | PASS 1/1 | PASS 1/1 | PASS 0 |
| only cleared licences | PASS | PASS | PASS | PASS |
| no CRITICAL QC | PASS | PASS | PASS | PASS |
| graphic numbers from speech | WARN by design: 10,00 = "10 €" (a word-form price, not literally "10,00"); "1" from "Klasse P1" | PASS | PASS (10 prices, all heard) | PASS (no numbers) |

Format note: ffprobe reports every ProRes 4444 file as `yuva444p12le` (FFmpeg's decoder decodes
4444 at 12 bit), so the documented `yuva444p10le` encode depth cannot be confirmed by probing; the
check accepts 4444 + alpha and measures real transparency instead. Stems: `pcm_f32le`, 48 000 Hz.

Inconsistencies found by this script during the runs (all fixed, section 6): caption end 30.636 s
after a 30.300 s edit (D2); stem missing for A (D9); a new plan version for every rerun (D10/D11).

## 5. Preview observations (validation-only composite)

Script `scratchpad\e2e\scripts\preview_composite.py`: main clips of the edit plan in timeline
order, rendered graphics/caption/transition MOVs overlaid at their timeline positions (pre-scaled
to 360p; overlaid in batches because one 35-input graph crashed FFmpeg 9.0.2), the plan's stem
mixed under the edit audio. No transitions, VFX, J/L or loudness. Since the continuation it takes
`--report` (the plans of that run) and places a caption batch file once, at its first group minus
`asset_start_frame` (after O9 one file holds many groups; the first post-O9 B2 preview placed the
shared file once per group and showed 2-3 caption groups at once - a preview-script bug, not a
product one). Outputs: `scratchpad\e2e\preview_A_run15.mp4` (136.6 s), `preview_B2_run15.mp4`
(52.5 s); older: `preview_A.mp4`, `preview_B2.mp4` (run7), `preview_B_run1_fullcaptions.mp4`.
Stills in `docs/layer4/previews/e2e/` (all post-fix except the B run1 one):

* `A_postfix_shown_prices_not_redrawn_end_screen.jpg` (run15, 0:35 / 0:58 / 2:02 / 2:10): the
  source's own "13,99 EUR" and "2,29 EUR" with no tag of ours on top (D13, D14; before, our
  "14,00 €" and "2,29 €" tags were drawn next to them), no duplicate rank card (O1); the 10,00 €
  tag ("Klasse P1") at 2:02 where "10 €" is spoken; the end screen ("ABONNIEREN" / "WEITER
  SCHAUEN") drawn over the last 20 s of content, next to the price tag and the source's
  "PLATZ 6" (run15 state, before O14; since O14 the end screen follows the content - no
  post-O14 still was made, the preview script only covers the edit's own clips).
* `B2_postfix_captions_werbung_prices.jpg` (run15, 0:00.3 / 0:05.5 / 0:25): our small
  "WERBUNG" only before the source's own "Werbung" appears; one caption group at a time; yellow
  "9,99 €" / "19,99 €" tags at the spoken price, but the picture already shows the same prices
  in large green type (O13); the caption band sits on the product cut-out.
* `B2_sticker_birefnet_glow_tilt.jpg`: BiRefNet cut-out with white glow and tilt (section 3).
* `A_end_screen_pixel_dissolve_frames_4_9_14.jpg`: the pixel-dissolve overlay (section 3).
* `C_vfx_slowmo_and_freeze_frames_2380_2395.jpg`: a slow-motion frame and the freeze frame of the
  C burst-peak render (section 3).
* `B_run1_captions_over_burned_in.jpg`: run1 captions above the source's own captions (D3).

Judgement: type, colours and layout match the channel look (red/white outlined cards, yellow
price panel, condensed caps captions with yellow emphasis, correct "Werbung" label); renders are
clean with real alpha, no clipping or broken glyphs, German text intact. After O1-O3 and D12-D14
the A preview no longer duplicates the picture's rank cards and prices and the price cards come
when the price is spoken. Still not professional and channel-fit: (1) B2 keeps price tags that
the picture already shows, because video OCR sampled only 21 frames over 52.5 s and never read
the green prices (O13); (2) the end screen covered 20 s of content on A (fixed afterwards as O14: now appended); (3) in the haul Short the caption band sits on the
product cut-outs (`layout.covers_subject` warnings are raised but the caption stays); the
channel's sponsored hauls carry no captions at all per channel evidence, the `shorts_haul`
profile was this validation's choice. Also visible: WhisperX heard "Inferno Neuheit" as "in
ferner Neuheit" and that wording is burned in (O11).

## 6. Defects found and fixed (each with a regression test)

| Id | Defect (evidence) | Fix (owning module) | Regression test |
| --- | --- | --- | --- |
| D1 | Content analysis re-transcribed the whole recording with `language=auto` although the pipeline had supplied the de-pinned audio timeline (B run1: 2 `transcription` docs, content 91 s) | `content_intelligence/application/analyze_content.py`: a supplied `audio_timeline` is the transcript | `tests/integration/content_intelligence/test_analyze_content.py::...test_a_supplied_audio_timeline_is_the_transcript_and_nothing_is_retranscribed` |
| D2 | Last caption/subtitle held past the edit end (30.636 s > 30.300 s) | `caption_engine/domain/segmentation.py` (+ `engine.py`): `until` = timeline end | `tests/unit/caption_engine/test_segmentation.py::test_the_last_caption_is_never_held_past_the_end_of_the_timeline` |
| D3 | Captions burned in on top of a picture that already has captions, although video OCR reported them (B: 4 `burned_in_caption` + 25 caption-text `lower_third` overlays) | `caption_engine` (facts `BurnedInSpan`, views `burned_in_spans`, `_mode`, profile knobs `already_captioned_share` 0.3 / `already_captioned_window` 1 s): mode disabled when the speech is already on screen; subtitles still exported | `tests/unit/caption_engine/test_selection_and_hearing.py::test_a_picture_that_already_shows_burned_in_captions_gets_none_on_top`, `::test_other_on_screen_text_counts_only_where_it_is_the_spoken_words` |
| D4 | Pacing rejected its own result on A ("proposed runtime is not current + changes"): the validator summed recommendations outside story sections, the engine (correctly) did not | `pacing_intelligence/domain/validation.py` (+ shared `STORY_RUNTIME_BASIS`) | `tests/unit/pacing_intelligence/test_contract.py::test_savings_in_material_the_story_left_out_do_not_change_the_story_runtime` (extended) |
| D5 | NYE uncut with 6 shouted words got a story and a 4.2 s edit of an 85 s fireworks take | `story_architecture` (`Thresholds.min_words_per_minute` 15; `NothingToPlan` below it) -> pipeline no-speech path | `tests/unit/story_architecture/test_engine.py::test_a_few_shouts_over_a_long_take_are_no_story` |
| D6 | "auf dem achten Platz" not recognised as rank 8 (no rank card for section 2 of A) | `content_intelligence/domain/commerce.py`: German ordinal before "Platz" | `tests/unit/content_intelligence/test_commerce.py` (new case) |
| D7 | "Neuheit 9,99" read as product "Neuheit 9" -> price tag headline "Neuheit 9" | `content_intelligence/domain/references.py`: a designation never ends inside a decimal | `tests/unit/content_intelligence/test_references.py` (new case) |
| D8 | The same "14,00 €" price tag drawn twice in one section (two editorial requests) | `motion_graphics_engine/domain/planner.py`: a product's facts card once (INFO `editorial.facts_already_shown`) | `tests/unit/motion_graphics_engine/test_planner_profile_composition.py::test_a_products_price_is_drawn_once_however_many_requests_name_it` |
| D9 | No stem for A ("A sound file could not be read": FFmpeg refused a ducking expression of 114 nested `min()`, limit ~100) | `music_and_sound_design/infrastructure/stem_renderer.py`: balanced `min()` tree | `tests/unit/music_and_sound_design/test_stem_graph.py::test_many_ducks_stay_within_ffmpegs_expression_depth` |
| D10 | A rerun was never fully reused: reframing -> edit -> continuity keys held analysis ids, so each run produced new versions forever (B run3-run5) | `reframing_intelligence/application/views.py`, `editing_intelligence/application/views.py`: identities = content only | `tests/unit/reframing_intelligence/test_views.py::test_a_rerun_continuity_analysis_with_the_same_verdicts_keeps_the_identity`, `tests/unit/editing_intelligence/test_continuity_input.py::test_the_continuity_identity_is_its_verdicts_not_the_analysis_id` |
| D11 | First rerun after a fresh content analysis recomputed alignment (and everything after): a computed score `0` vs a loaded `0.0` serialised differently | `content_intelligence/domain/model.py`: `Score` values/components are floats | `tests/unit/content_intelligence/test_storage_and_grounding.py::...test_a_loaded_result_has_the_same_text_and_so_the_same_identity` |
| O1 | Motion graphics drew rank cards, the Werbung label and price tags the picture already shows (A, B2 stills of run7), although video OCR had the evidence | `motion_graphics_engine/domain/planner.py` (`on_screen`, `already_shown`, `not_on_screen`; INFO `editorial.already_on_screen`; knob `timing.on_screen_window` 1.5 s) + brief field `on_screen` filled by `media_pipeline/application/layer4_inputs.py::on_screen_text` | `tests/unit/motion_graphics_engine/test_planner_profile_composition.py::test_a_card_the_picture_already_shows_is_not_drawn_again_and_werbung_only_where_missing` |
| O2 | Fact cards were timed to the section start, not to the spoken fact (A: "14,00 €" at 0:00.1 for a price spoken at ~0:33) | `motion_graphics_engine/domain/brief.py` (`timeline_at` on `ProductFacts`, `rank_at` on sections), `planner.py` (`spoken`); `layer4_inputs.py` (the word time of each commerce fact) | `tests/unit/motion_graphics_engine/test_planner_profile_composition.py::test_a_price_tag_and_a_rank_card_appear_when_the_fact_is_spoken`; `tests/unit/media_pipeline/test_layer4_inputs.py::test_every_spoken_price_is_its_own_timed_product_and_the_rank_has_its_time` |
| O3 | One price per section (B2 names many prices in one section, only 150,00 € was drawn) | `layer4_inputs.py::_products` (one product per spoken price), `planner.py` (each drawn once, one zone at a time: `layout.zone_queued`) | `tests/unit/motion_graphics_engine/test_planner_profile_composition.py::test_every_spoken_price_of_a_section_gets_its_tag_and_one_zone_holds_one_at_a_time` (+ the layer4_inputs test above). Run14: B2 draws 10 different spoken prices, 15 tags, no zone overlap |
| O6 | Models and the HyperFrames runtime were not found with `--home` (the setup scripts install into the user cache) | `shared/filesystem/paths.py` (`AppPaths.install_root`, `install()`: the home's own copy, else the user cache), `bootstrap/application.py` (`StartupOptions.shared_installs`), module wiring via `paths.install(...)`; `scratch/run_pipeline.py`, `scratch/import_sound_library.py` and now also the desktop CLI `bootstrap/cli.py` set it; README line on machine-level installs | `tests/unit/test_filesystem.py::test_a_home_uses_its_own_install_else_the_machine_level_one`, `tests/integration/test_application_lifecycle.py::test_a_home_is_self_contained_unless_it_asks_for_the_machine_installs`, `tests/integration/test_cli.py::test_a_portable_home_still_finds_the_machine_level_installs` (new, failed before the `cli.py` line). Real check: a clean home `%TEMP%\mhe2g` resolved content models + HyperFrames 0.9.5 from the user cache. Note: the video models of this validation were primed into the main home, not the user cache |
| O7 | A HyperFrames Chrome path over MAX_PATH failed every render with a bare `ENOENT` | `motion_graphics_engine/infrastructure/hyperframes.py::locate`: a clear "over the Windows limit of 260 ... install on a shorter path or use a shorter --home" | `tests/unit/motion_graphics_engine/test_hyperframes_locate.py::test_a_browser_path_over_the_windows_limit_is_reported_clearly` |
| O8 | No pipeline switch for video model downloads | `scratch/run_pipeline.py --allow-model-download` -> `RuntimeConfig.allow_model_download` (off by default) | `tests/unit/media_pipeline/test_executors.py::test_model_downloads_reach_video_analysis_only_when_allowed` |
| O9 | One HyperFrames render per caption group (34 groups = 537 s on CPU) | `caption_engine` (PLANNER_VERSION 4, `RenderRef.asset_start_frame`), `motion_graphics_engine/application/render_graphics.py`: the groups of one caption run render as one file | `tests/integration/motion_graphics_engine/test_render_and_plan.py::test_caption_groups_of_one_run_render_as_one_file_and_each_knows_its_place`. B2: 39 groups in one file of 1 256 frames (its render time was not recorded in a kept report) |
| O10 | C (6 shouted words over 85 s of fireworks) was classified `original_audio.mode = speech` | `music_and_sound_design/domain/profile.py` (knob `original_audio.incidental_speech_share` 0.1), `domain/planner.py` (`_speech_share`: speech coverage of the clip, merged spans), PLANNER_VERSION 2 | `tests/unit/music_and_sound_design/test_planner.py::test_a_few_shouts_over_fireworks_keep_the_take_sound_in_front` (fails with the old `not overlaps_any(span, speech)` condition: checked). C run15 (nye_uncut): `mode = dominant`, "profile nye_uncut: clip sound is the content" |
| D12 | B2's reused graphics plan (v2) was `partial`: `timing.empty` "brand:sponsor:1: no duration", a sub-frame rest of the Werbung span after the source's own label. The plan had been stored by an intermediate planner state and was reused because the planner version was not bumped; the current code plans it `ok` (checked: a version bump made run dbg recompute it, state ok, no `timing.empty`) | `motion_graphics_engine/domain/planner.py::not_on_screen` (a piece under one frame is no label; code already present, untested), `domain/values.py` PLANNER_VERSION 3 | `tests/unit/motion_graphics_engine/test_planner_profile_composition.py::test_a_sliver_left_after_the_shown_werbung_is_no_label_and_no_error` (fails when the one-frame filter is removed: checked) |
| D13 | A: our "2,29 €" / "2,99 €" tags drawn over the source's own "2,29 EUR" / "2,99 EUR" (post-O1 preview). OCR had read them (shot text rows, frames 1780/1870) but they are no overlay kind (watermark / lower third / caption), and the brief read overlays only | `media_pipeline/application/layer4_inputs.py::on_screen_text`: overlays **and** the text tracked in each shot (`shot.text.items`) | `tests/unit/media_pipeline/test_layer4_inputs.py::test_text_the_picture_shows_includes_tracked_shot_text_not_only_overlays` (failed before) |
| D14 | A: "für 14 Euro" spoken while the picture shows "13,99 EUR": our "14,00 €" tag contradicted the picture | `motion_graphics_engine/domain/planner.py::_price_texts`: a whole-euro price also matches the shown `(n-1),9x` price; PLANNER_VERSION 4 | `tests/unit/motion_graphics_engine/test_planner_profile_composition.py::test_a_rounded_spoken_price_is_not_drawn_next_to_the_exact_price_the_picture_shows` (failed before) |
| O14 | The end screen was placed in the last 20 s of the edit, over the content (A 116.6-136.6 s over rank 6, C 64.9-84.9 s over the fireworks); the transition engine then planned no end-screen transition (`bookend.not_at_cut`). Channel evidence: the ~20 s fire end screen is appended after the content and entered with a pixel dissolve | `motion_graphics_engine/domain/planner.py` (`bookends`: END_SCREEN starts at the timeline end with its own length; `add(appended=True)` skips the end clamp), `domain/model.py` + `engine.py::assemble_plan` (additive `GraphicsPlan.timeline_end_with_bookends`), PLANNER_VERSION 5; `music_and_sound_design/domain/timeline.py` (additive `SoundtrackTimeline.appended_until`, `content_end`; `timeline_end` includes the appended span), `domain/planner.py::_sections` (the span after the content is the `tail` OUTRO section, also on the no-section path), PLANNER_VERSION 3; `media_pipeline` (`layer4_inputs.soundtrack_timeline(appended_until=...)`, `executors.py` passes the graphics plan's `timeline_end_with_bookends`). Transitions: unchanged code, the END_SCREEN bookend (graphics start = last clip end) now lands on the sequence end. Captions: unchanged (bounded by the edit's clips) | `tests/unit/motion_graphics_engine/test_planner_profile_composition.py::test_end_screen_is_appended_after_the_content_for_the_style_length` (replaces `test_end_screen_sits_at_the_end_for_the_style_length`, which encoded the old placement: 280 s -> 300 s), `tests/integration/motion_graphics_engine/test_render_and_plan.py::test_no_speech_plan_is_stored_with_not_available_renders` (+ `timeline_end_with_bookends == 80`), `tests/unit/music_and_sound_design/test_planner.py::test_the_appended_end_screen_gets_the_outro_and_no_speech_ducking`, `tests/unit/media_pipeline/test_layer4_inputs.py::test_sound_design_hears_of_the_end_screen_appended_after_the_content` (all fail on the old code: old start 280, missing fields). Run16 computed, run17 fully reused: A 18, C 15 `reused`. A: end screen 136.63-156.63 s, `end:` pixel dissolve 136.33-136.93 s, sound 156.63 s with a `tail` outro music cue and no speech ducking after the content; C: 84.86-104.86 s, pixel dissolve, `nye_uncut` keeps music off in the tail too |
| - | `ruff format --check` failed on two Markdown code blocks (pre-existing, also on HEAD) | formatted `docs/layer4/integration-brief.md`, `reframing_intelligence/README.md` | - |

Checked to fail without the fix: D2-D5, D10, D11 (by stashing the fix), O10, D12, D13, D14 and the
new O6 CLI test (by reverting the line in this session). The O1-O3, O7-O9 tests were written by
the previous validation pass; they pass on the working tree and were not re-reverted here.

## 7. Open defects and findings (not fixed)

O1-O3, O6-O10 and O14 are fixed (section 6). Still open:

* **O4 Stale edit plan on the no-speech path.** After D5, C's old 4.2 s edit plan (v2) is still
  the latest `edit_plan` document of the anchor; the Layer 4 plans correctly carry `edit: null`,
  but a reader of "latest edit plan" gets the stale one (the scratch validator now reads the
  plans of the run report instead).
* **O5 Two runs to the fixpoint.** By design the editor reads the continuity analysis "on its next
  planning": the first rerun of a new recording recomputes 8 light tasks (~20 s), the third run
  is fully reused (fresh home f1/f2/f3).
* **O11 Transcription error burned in.** "Inferno" heard as "in ferner" (not low confidence).
* **O12 Price accents can never play.** Graphic events carry the graphic's priority as strength;
  commerce price tags are planned at priority 0.5 and the `price_reveal` rule needs 0.7, so every
  price accent is declined `too_weak` (A run14: all declined). Rank-reveal accents (no strength
  gate) were not exercised: A draws no rank card after O1. Whether 0.5 vs 0.7 is intended
  restraint is the owner's call.
* **O13 On-screen evidence is only as good as the OCR sampling.** B2's video OCR read 21 frames
  over 52.5 s and never the large green per-item prices, so our yellow tags duplicate them (still
  `B2_postfix_captions_werbung_prices.jpg`). A video_intelligence text-sampling question.
* **Test failures** (section 8): known from HEAD: the unit GPU OOM test (no NVIDIA driver) and
  the integration `asset_relationships/test_evaluation_script.py` (its script was deleted in
  927a843). New in the final integration run: the wall-time bound (< 60 s) of
  `test_model_downloads.py::test_a_missing_speech_model...` (68 s in the full run; passes alone).

## 8. Test suites

Final run on the working tree with all fixes (2026-11-03 evening, after D14; exact counts):

| Suite | Result |
| --- | --- |
| `uv run ruff format --check .` | 1251 files already formatted (two test files were formatted in this pass) |
| `uv run ruff check .` | All checks passed |
| `uv run python -m mypy` | Success: no issues found in 1192 source files |
| `uv run python -m pytest tests/unit -q` | 1 failed, 3375 passed, 1 skipped (111 s); the failure is `video_intelligence/test_model_support.py::test_running_out_of_gpu_memory_moves_the_detector_to_the_cpu_and_carries_on` ("Found no NVIDIA driver") |
| `uv run python -m pytest tests/integration -q` | 1 failed, 924 passed, 16 skipped, 4 errors (41 min 24 s). Errors: the 4 `asset_relationships/test_evaluation_script.py` tests (`FileNotFoundError: scratch\evaluate_relationships.py`, deleted in 927a843). Failure: `audio_intelligence/test_model_downloads.py::test_a_missing_speech_model_fails_fast_offline_and_names_the_fetch_command`, `assert 68.3 s < 60` (a wall-time bound, "no download attempt"); the same file alone: 4 passed, 1 skipped (55 s). A first attempt stopped at collection: Windows application control blocked `skimage\measure\_pnpoly` ("Eine Anwendungssteuerungsrichtlinie hat diese Datei blockiert"); the DLL imported fine a minute later and the rerun above collected everything |
| `uv run python -m pytest tests/architecture -q` | 236 passed (9 min 23 s) |
| after O14: touched modules (`tests/unit/{motion_graphics_engine,music_and_sound_design,transition_engine,caption_engine,media_pipeline}`, `tests/integration/{motion_graphics_engine,music_and_sound_design,transition_engine,media_pipeline}`, `tests/architecture/test_dependency_rules.py`) | 443 passed, 1 skipped (6 min 48 s); ruff format/check and mypy clean again. The full unit/integration/architecture suites above were not rerun after O14 |

Known failures checked on the clean committed HEAD 927a843 (`git worktree add ...\e2e\head HEAD`,
run with `PYTHONPATH=<worktree>\src` and the main `.venv` python, worktree removed afterwards):

* `test_model_support.py::test_running_out_of_gpu_memory...`: fails on HEAD too (1 failed,
  17 passed): environment (no NVIDIA driver), not a regression.
* `test_model_downloads.py::test_a_missing_speech_model_fails_fast_offline...`: did not fail in
  the first pass, neither on the working tree nor on HEAD (HEAD: 4 passed, 1 skipped). In the
  final full integration run it exceeded its 60 s wall-time bound (68 s); alone it passes (55 s
  for the file). Not re-run on HEAD in this pass.
* `tests/integration/asset_relationships/test_evaluation_script.py` (4 errors,
  `FileNotFoundError`): also on HEAD; commit 927a843 deleted the relationship evaluation script
  these tests load. Pre-existing, not fixed (restoring or removing it is the owner's call).
* `tests/integration/visual_asset_generation/test_prepare_visual_assets.py::test_the_wired_application_fails_honestly_without_a_background_model`
  (uncommitted module, not on HEAD) failed because BiRefNet IS installed here, so the wired
  application succeeds. The test now skips when the default model is on disk (its premise); the
  honest-failure path is still tested on a machine without it.

## 9. What was NOT validated, and why

* DaVinci Resolve export, timeline building, a final rendered video, whole-video QC: not built.
* GPU paths (no CUDA on this machine); every model ran on the CPU.
* Real camera originals: 1080p/4K, HDR/10-bit, VFR, multi-stream audio, timecode, GoPro profiles;
  only the owner's published 360p H.264/AAC videos were used, which are already edited (own
  graphics and captions), so creative judgements on them are partly artefacts of the material.
* Diarization (gated model not downloaded), story LLM rewording (`--story-llm` off), image
  generation (no model).
* Through the pipeline itself: BiRefNet stickers (the pipeline has no product image source; only
  a scratch script drove the real modules, section 3). The end-screen pixel dissolve is planned by
  the pipeline since O14 (A, C run16/run17); B and B2 were not rerun after O14. The VFX render was
  produced through the pipeline CLI, but only with the `shorts_product_test` format profile
  (`C_vfx`); with `nye_uncut` the burst slow-motion is rejected by the style ceiling. The
  membership-plug zoom tunnel, rank-reveal SFX (O12), SFX bursts on fireworks peaks (the C
  profiles disable or decline them), video improvement / audio improvement / mixing (not
  requested).
* Scenario D (brochure review) and the night preset of video analysis.
* Loudness of the final mix (owned by `audio_improvement`, not run).
