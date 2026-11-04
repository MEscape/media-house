# Cross-layer reassessment: Layers 1-3 against the MEsc channel

Status: report only, 2026-11-02, checked against the code at commit `927a843` (working tree; no
source changed). Inputs: `docs/layer4/channel-discovery.md`, `docs/layer4/channel-evidence.json`,
`docs/layer4/integration-brief.md` (sec. 4, sec. 11), `docs/layer4/contracts.md`, `AI_CONTEXT.md`
sec. 11, `CLAUDE.md`, `ARCHITECTURE.md`.

Paths are relative to `src/media_house/modules/` unless they start with `docs/` or `tests/`.
`file:line` points at the code that was read. "Where the change goes" points at the line the
change would touch.

Labels used in every finding:

* **[E] evidence**: read in the code (file:line given).
* **[A] assumption**: a reasoned expectation from the thresholds plus the channel measurements. It
  was not run on real MEsc footage, because no model is downloaded and no clip was analysed.
  Sec. 7 lists what to measure.

Priorities:

* **P0**: Layer 4 cannot work on this channel without it. It produces nothing, or it damages the
  core formats.
* **P1**: important for quality or for the channel rules ("do not destroy good media", German
  text, night footage).
* **P2**: nice to have.

---------------------------------------------------------------------------------------------------

## 1. Verdict per module

| Layer | Module | Suitable as is? | Main reason | Findings |
| --- | --- | --- | --- | --- |
| 1 | media_library | yes | Content-based import with type limits. Action-cam sidecars get imported as separate assets. | F20 |
| 1 | media_inspection | yes | VFR is detected from packets at both depths (`media_inspection/domain/timing.py:95-128`). It raises the WARNING `video.variable_frame_rate` (`rules.py:281-297`). Rotation comes from the display matrix (`ffprobe_normalizer.py:177-186`). | - |
| 1 | asset_relationships | partly | `wall_clock_overlap` needs an aware `creation_time` (`asset_relationships/domain/detection.py:154`). There is no clock-skew correction. | F21 |
| 1 | proxy_manager | yes | VFR passes through (`ffmpeg_encoder.py:68`). Rotation is kept (`:55-56`). The long edge is used, so it does not depend on orientation (`domain/spec.py:99`). Analyses run on the originals. | - |
| 2 | audio_intelligence | partly | German works when a language is set, but the pipeline never sets one. Nothing detects bangs. Bangs count as "speech" activity. | F06, F08, F09 |
| 2 | audio_improvement | partly (opt-in) | Every stage is gated by evidence, which is good. All measurements assume speech. Mastering limits bangs. There is no profile for ambience or fireworks. | F10 |
| 2 | video_improvement | no for night footage (opt-in) | The default `natural` profile lifts exposure and gamma and adds contrast on dark shots. Sharpening runs at 8 bit for 8-bit sources. | F11 |
| 2 | video_intelligence | partly | No night or fireworks awareness. Night shots score as dark, crushed or clipped, and can be flagged black. COCO has no fireworks or smoke class. OCR uses the Chinese recognition model. | F12, F13, F19 |
| 2 | content_intelligence | partly | German is supported. Prices are only matched as strings, never parsed. "Euro" is not a unit. There is no rank or spec fact. "Platz 10" is read as a product designation. | F03, F04 |
| 2 | audio_visual_alignment | partly | The OCR-to-speech match drops pure digits, so on-screen prices never link to spoken prices. `SoundAction` pairs `energy_spike` with motion, but not with FLASH. | F16 |
| 3 | story_architecture | partly | `top_n` exists but does not detect ranks. There is no format for compilation, uncut, meme or versus content. With no speech the story hard-fails. | F01, F04, F07, F18 |
| 3 | editing_intelligence | partly | Gaps between words longer than `pause_max` are cut, which cuts firework proof footage. The effect requests carry no content (the join is possible). No end screen, plug or sponsor slot. | F05, F02, F22 |
| 3 | pacing_intelligence | mostly | It flags only cutting that is too fast. The haul Short is borderline. An uncut take gets `restore_attention`. | F17 |
| 3 | broll_intelligence | mostly | It confirms identity from OCR (`broll_intelligence/domain/match.py:325-348`). It has no notion of third-party credit. | F23 |
| 3 | continuity_intelligence | partly | Audio repairs go to `audio_improvement`, which has no per-cut operation. No Layer 4 owner exists. | F15 |
| 3 | reframing_intelligence | partly | A shot with no tracked subject is never `executable` (`reframing_intelligence/domain/planner.py:770-774`). Night fireworks have no person to track. | F14 |

Most formats fail at the same point. Every Layer 3 module plans from speech. Of the channel's
formats, these have no speech or very little: the meme Shorts (73 uploads, the channel's reach
engine), the versus polls, the uncommented compilations and the NYE uncut takes. None of them
reaches an `EditPlan` today (F01). Layer 4 consumes the `EditPlan` only (contracts.md sec. 2), so
it would get no input for those formats.

## 2. Format coverage (existing names; no new format needed except the no-speech path)

| Channel format | Speech | Story format to use | Editing profile | Pacing profile (`pacing_intelligence/domain/profile.py:18-29`) | Reframing profile (`reframing_intelligence/domain/profile.py:206-217`) | Gaps |
| --- | --- | --- | --- | --- | --- | --- |
| Ranking "PLATZ N" | VO about 146 wpm | `top_n` (PRESERVE, `story_architecture/domain/profile.py:140-147`) | `top_n` | `top_10_ranking` (max 20 cuts/min; 4-6 measured, ok) | `shorts_reels` (odd but harmless) | F03, F04, F05 |
| Brochure review | 172 wpm | `unboxing` (`reveal_sequence`, PRESERVE: page order is kept). Not `review`: it reorders by dependency. | `unboxing` | `unboxing` (max 12; about 6 measured, ok) | `product_review` | F03, F16 |
| Shopping tour | 117 wpm, group banter | `unboxing` (PRESERVE: chronological) | `unboxing` | `unboxing` (about 7 measured, ok) | `product_review` | F05, F15 (jump cuts: continuity has no vlog prior, falls back to `generic`) |
| Unboxing | 138 wpm | `unboxing` | `unboxing` | `unboxing` | `product_review` | F05 |
| Assortment with VO (variant b) | 121 wpm | `top_n` or `unboxing` | same | same | - | F03 |
| Compilation without speech (variant a) | 0 | none: no-speech path | - | - | - | F01 |
| NYE uncut | 0 | none: no-speech path (no story intervention wanted) | - | pacing still runs, see F17 | - | F01, F17, F19 |
| Haul Short | 220 wpm | `short` | `short` | `shorts_reels` (max 30; 31.5 measured, borderline) | `shorts_reels` | F17 |
| Sponsored test Short | little | `short` if speech, else no-speech path | `short` | `shorts_reels` | `shorts_reels` | F01, F14 |
| Meme Short (7 s), versus poll (8 s) | 0 to few words | no-speech path | - | not rated under 10 s (`pacing_intelligence/domain/picture.py:127,211`) | source is already 9:16 | F01, F18 |

Decision (F07, P0, documentation only): use the table above. **Add no new story or editing
format for these.** `top_n`, `unboxing` and `short` cover the speech formats when the order is
preserved. The missing piece is the no-speech path, not more formats. The format profiles of
`visual_style_engine` (`mesc.ranking` ...) should name the Layer 3 format they expect, so that
`--story-format` and `--edit-profile` are chosen from data.

---------------------------------------------------------------------------------------------------

## 3. P0 findings

### F01: no-speech recordings produce no plan, and the story task fails instead of skipping (P0)

* **Problem [E].**
  * For a transcript with no speech, content analysis returns `NOT_APPLICABLE`
    (`content_intelligence/domain/engine.py:149-150,268-275`).
  * The story then returns `Err(NothingToPlan)` (`story_architecture/application/plan_story.py:263-264`).
  * The pipeline maps only codes ending in `no_audio_track`, `no_video_stream`, `nothing_to_cover`,
    `nothing_to_judge` and `nothing_to_frame` to a skip (`media_pipeline/application/executors.py:139-145,203-208`).
  * `story_architecture.nothing_to_plan` is not in that list, so the story task FAILS.
  * `editing_intelligence.nothing_to_edit` (`editing_intelligence/domain/errors.py:45`) is
    missing from the list too.
* **Why it matters.**
  * The meme Shorts get the highest reach, and they have no speech. Neither do versus polls,
    uncommented compilations or NYE uncut takes.
  * Layer 4 consumes the `EditPlan` only, so every Layer 4 module gets no input for these formats.
  * A pipeline run on such a clip reports a failure, not a skip.
* **Owner.** `media_pipeline` (orchestrator), plus one line of decision in `docs/layer4/contracts.md`.
* **Smallest change.**
  1. Add `"nothing_to_plan"` and `"nothing_to_edit"` to `_UNAVAILABLE_CODES` (`media_pipeline/application/executors.py:139`).
  2. In `media_pipeline/application/`, add one neutral "edit view" builder next to
     `sequence.py:sequence_of(plan)`. It returns clips, a timebase, `project_id` and an
     `EditRef | None`. It is built from the `EditPlan` when one exists. Otherwise it is built from
     the stored inspection:
     * one clip covering the whole recording, or one clip per shot from `VideoCatalog`;
     * `timebase.assumed = True` when the source is VFR.

     Every Layer 4 mapper reads this view, not the `EditPlan` directly. Uncut, meme and compilation
     sources then reach Layer 4 "as recorded", which is what the channel does for those formats
     (discovery sec. 11, "Midnight uncut: no story intervention").
  3. In `contracts.md` sec. 2, allow `edit: EditRef | None` in the Layer 4 input DTOs. `None`
     means "unedited recording; the clip times are the source times".
* **Interfaces and consumers.**
  * All seven Layer 4 input DTOs. No Layer 1-3 module changes.
  * `TaskStatus` for story and edit_plan becomes `SKIPPED_UNAVAILABLE` on clips with no speech.
* **Backward compatibility.**
  * Additive. Speech recordings are unchanged.
  * A run that "failed" before is now "skipped". That is the right status, but it changes the
    reports.
* **Tests.**
  * `tests/unit/media_pipeline/test_executors.py`: the story and edit tasks on a fake
    `NothingToPlan` / `NothingToEdit` give `SKIPPED_UNAVAILABLE`.
  * A mapper unit test: no `EditPlan` gives one clip covering the inspection duration with an
    exact `Rational` end, and VFR gives `assumed=True`.
* **Note.** Decide this before the seven Layer 4 agents freeze their input DTOs. The cost
  is lowest now.

### F03: no structured price / rank / spec facts (P0)

* **Problem [E].**
  * Numbers and currencies are matched as strings only: `_NUMBER = r"\d[\d.,]*"` and the
    `_STAT_UNIT` list (`content_intelligence/domain/keyinfo.py:29-34`).
  * No amount, currency, rank or spec value is produced anywhere. `KeyKind` has no price, rank
    or spec member (`content_intelligence/domain/values.py:117-132`).
  * The spoken word "Euro" is not a unit, because `\beur\b` does not match "euro"
    (`keyinfo.py:30-33`).
  * "Sekunden", "Schuss" and "Meter" are missing as units. These are the spec terms
    Brenndauer, Schuss and Höhe.
  * Spelled-out German amounts are not recognised.
  * `designations()` finds only a name followed by a digit (`content_intelligence/domain/references.py:37-42`).
    So "Gargoyle WECO" and "Tip Tap Tube" are not products.
  * "Platz 10" IS read as a product (family `platz`), because `platz` is missing from `_NOT_NAMES`
    (`references.py:46-55`). `top` is in that list.
  * OCR text is used only to suppress graphics that duplicate on-screen text
    (`content_intelligence/domain/opportunities.py:106-116`).
* **Why it matters.**
  * The core motion templates need typed values: `rank_card`, `price_tag`, `spec_card` and
    `next_rank_teaser`.
  * The prices must be printed in German comma format ("15,99 €") from an exact amount. They
    must not be reprinted from a raw transcript string.
  * The caption engine has to emphasise prices. Continuity of Layer 4 needs "same product name
    and price" across the card, the sticker and the VO (discovery sec. 11).
  * A false designation "Platz 10" makes broll look for a product called Platz
    (`broll_intelligence/application/views.py:438-439`).
* **Owner.** `content_intelligence` (domain, pure).
* **Smallest change.**
  1. Add a new pure module `content_intelligence/domain/commerce.py` with
     `commerce_facts(text: str, language: str) -> tuple[CommerceFact, ...]`:
     * `kind`: `price | rank | shots | height | duration`
     * `value`: a `Fraction` (cents for prices)
     * `unit`
     * `text` (verbatim)
     * `start` and `end` character offsets

     Patterns to cover: `\d+,\d{2}\s?(€|EUR|Euro)`, `\d+€`, "Platz \d+" / "auf Platz",
     `\d+\s?Schuss`, `\d+\s?m(eter)?` with a Höhe context, and `\d+\s?(s|Sek(unden)?)`.
     Language-specific tables live in the same file, as in `lexicon.py`.
  2. Export it from `content_intelligence/application/contracts.py`, next to `designations`.
     Motion graphics and caption call it on `CoverageRequest.concept`, transcript words and OCR
     `TextItem.text`. The mapper does the join, see F02.
  3. Add `"platz"` and `"rang"` to `_NOT_NAMES` (`references.py:54`).
  4. Add `euro|sekunden|schuss|meter` to `_STAT_UNIT` (`keyinfo.py:31`).
  * No stored schema changes. `ContentAnalysis` stays as it is.
* **Interfaces and consumers.**
  * A new contract function.
  * Consumers: `motion_graphics_engine`, `caption_engine` (keyword emphasis) and Layer 4
    continuity checks.
  * The `_NOT_NAMES` and `_STAT_UNIT` edits change content results.
* **Backward compatibility.**
  * The helper is additive.
  * The two lexicon edits change content analysis output, so bump the content analyzer or
    vocabulary version. Old documents stay readable, and they replan on the next run.
* **Tests.**
  * A table test with these inputs: "15,99 EUR", "2,49€", "5.99€" (dot; tolerate it, keep the
    source text), "neunzehn Euro" (no match; document the limit), "Platz 1", "auf Platz 10",
    "25 Schuss", "Höhe 30 m", "Brenndauer 40 Sekunden", umlauts preserved.
  * A `designations("Platz 10 ist ...")` test that returns nothing.
  * A mypy-strict-clean domain test with no I/O.

### F05: editing cuts firework proof footage between words (P0)

* **Problem [E].**
  * Inside a run, every gap between words longer than `pause_max` becomes a pause trim
    (`editing_intelligence/domain/assemble.py:329-410`).
  * A gap that is not silent (`silence_ratio < pause_confirm`, `:371-375`) only becomes
    `NEEDS_REVIEW`. The cut is still applied to the pieces (`assemble.py:168-177`).
  * The `top_n` profile tightens `pause_max` to 1.2 s (`editing_intelligence/domain/profile.py:128-138`).
  * The only exemptions are: laughter or breath events (`_REACTION_EVENTS`, `assemble.py:58`;
    none is detected), payoff or turning-point owners, a "meaningful" pacing verdict, and an
    emphasised next word.
* **Why it matters.**
  * The ranking (strongest peak), unboxing, tour and test formats show the product's effect
    between short comments: ignition, then 10-60 s of bursts, then a verdict.
  * The channel rule is "preserve uninterrupted firework test shots from ignition to last break"
    (discovery sec. 11). The default plan removes exactly that footage.
  * Layer 4 then times graphics, music and transitions on an edit that has already lost the
    proof.
* **Owner.** `editing_intelligence`.
* **Smallest change.**
  * Add one profile knob to `EditingProfile`: `keep_unconfirmed_gaps: bool = False`
    (`editing_intelligence/domain/profile.py:25-75`).
  * In `_pause_cut`, right after `confirmed` is computed (`assemble.py:371-375`), return `None`
    when `not confirmed and profile.keep_unconfirmed_gaps`. The loud gap is then kept.
  * Set the knob to `True` in `top_n`, `unboxing`, `review` and `product_introduction`.
    Truly silent pauses are still trimmed.
* **Interfaces and consumers.**
  * `EditPlan` placements get longer: fewer trims, longer `timeline_duration`.
  * Every Layer 4 mapper, `continuity_intelligence` (fewer cuts to judge) and the
    target-length reports are affected.
* **Backward compatibility.**
  * The default is `False`, so other profiles plan exactly as before.
  * The four profiles change their configuration fingerprint. The next run makes a new
    `plan_version` with `supersedes`, which is the documented replan path.
  * Person overrides survive.
* **Tests.**
  * Unit test in `tests/unit/editing_intelligence/`: a fake audio timeline has two words 8 s
    apart and a loud gap (`silence_ratio = 0.1`).
    * `top_n` keeps one piece.
    * `talking_head` still trims it, as `NEEDS_REVIEW`.
    * A silent gap (`silence_ratio = 0.95`) is trimmed in both profiles.
* **Alternative, once F08 exists.** Add the bang event kind to `_REACTION_EVENTS`. This is more
  precise but depends on a detector, so it is not the smallest change.

### F07: format mapping decision (P0, documentation only)

* **Problem [E].** The Layer 3 format names (`top_n`, `unboxing`, `short`) are chosen per run
  through `StageSettings.story_format` and `edit_profile` (`media_pipeline/application/executors.py:160-165`).
  Nothing records which channel format maps to which name. `review` reorders by dependency, which
  would break the brochure's page order.
* **Change.**
  * Write the sec. 2 mapping into the `visual_style_engine` format profiles, as the expected
    Layer 3 format for each one.
  * No Layer 1-3 code.
* **Tests.** None in Layers 1-3. Layer 4 checks at style load that every format profile names a
  known story format.

---------------------------------------------------------------------------------------------------

## 4. P1 findings

### F06: the transcription language is never pinned (P1; do it with the P0 batch, it is about 5 lines)

* **Problem [E].**
  * `TranscriptionConfig.language` defaults to `None`, which means auto-detect
    (`audio_intelligence/domain/values.py:77-79`).
  * The pipeline passes only `device` (`media_pipeline/application/executors.py:414-421`).
  * `StageSettings` has no language field (`executors.py:151-191`).
* **Why it matters [A].**
  * Language detection uses the first window. Cold opens here are bangs, music or a meme
    sketch, so a wrong language is plausible.
  * A wrong language corrupts the transcript, content, story, captions ("text must be German
    with correct umlauts") and alignment.
* **Owner.** `media_pipeline`.
* **Smallest change.**
  * Add `transcription_language: str | None = None` to `StageSettings` (`executors.py:151`).
  * Pass it as `TranscriptionConfig(language=..., device=...)` at `executors.py:417-421`.
  * Add a `--language de` flag to `scratch/run_pipeline.py`.
* **Compatibility.** `None` keeps today's behaviour. A pinned language changes the audio cache
  key, which is correct.
* **Tests.** An executor unit test checks that the command carries `language="de"` when it is set.

### F08: no bang / impulse detector; bangs count as speech (P1)

* **Problem [E].**
  * The non-verbal detector protocol exists, but "None ship by default"
    (`audio_intelligence/infrastructure/acoustic_extractor.py:84`); the default is `detectors=()`
    (`:305`).
  * The kinds are listed (`audio_intelligence/domain/analysis/acoustic.py:16-24`, `noise` among
    them), but nothing produces them.
  * "Speech" activity is energy-based (`acoustic_extractor.py:267`), so bangs enter the speech
    baseline (`baseline.py:94-105`).
  * `energy_spike` needs a "speech" frame (`domain/analysis/detection.py:95`), which a bang
    satisfies.
* **Why it matters.**
  * `music_and_sound_design` must not place SFX on bangs, must not duck under bangs, and must
    not compress them flat (discovery sec. 11).
  * `transition_engine` and `motion_graphics_engine` could sync to the bang/flash.
  * `editing_intelligence` could protect bursts (see the F05 alternative).
  * Without events, music has to build its own audio measurement, which the brief already
    expects (sec. 0, risk 6). That duplicates work against the "no duplication" rule.
* **Owner.** `audio_intelligence`.
* **Smallest change.**
  * Ship one detector that implements the existing protocol, registered in
    `acoustic_extractor.py:305`.
  * It is a pure transient detector on the existing `AcousticTrack.rms_db`: a rise of at least
    X dB within one or two hops and outside transcript words.
  * It emits `AudioEvent(kind="impulse", ...)`. Add `"impulse"` to `NONVERBAL_KINDS`
    (`acoustic.py:24`).
  * Optional: exclude the impulse frames from the speech baseline. Do this later, after
    measuring.
* **Interfaces and consumers.**
  * `AudioIntelligenceTimeline.events` gains a new kind.
  * Consumers: `music_and_sound_design`, `transition_engine`, `audio_visual_alignment` (add
    `impulse` to the paired audio kinds, `audio_visual_alignment/config.py:69`, so that bang
    pairs with FLASH; also add `flash` to `:70`).
* **Compatibility.**
  * Additive kind. It changes the audio analyzer version, so audio replans.
  * Stored timelines without impulse events stay valid.
* **Tests.**
  * Synthetic track: one 30 ms +25 dB impulse in silence gives one event, and speech syllables
    give none.
  * Also test that `events_between` returns it.
  * Real calibration needs MEsc clips (sec. 7).

### F09: WhisperX may hallucinate words on bangs (P1, measure first)

* **Problem [E].**
  * No `asr_options` or `vad_options` are passed (`audio_intelligence/infrastructure/whisperx_engine.py:99-104,198-203`).
  * No word-confidence filter exists. Confidence is only stored (`whisperx_engine.py:382`).
* **Problem [A].** VAD may pass loud impulses, and Whisper can then invent words.
* **Why it matters.**
  * Invented words become captions (`caption_engine`) and high `moment` / `zoom_emphasis` signals
    (`audio_intelligence/domain/analysis/scoring.py:366-399`).
  * They also stop content from being `NOT_APPLICABLE` on no-speech clips, so F01's skip would not
    happen and a bogus story would be planned.
* **Owner.** `audio_intelligence`.
* **Smallest change (after F08).**
  * Mark words that lie inside an `impulse` event and have a confidence below a profile
    threshold as `suspect` in `TranscriptWord.timing` or `emphasis_hints`. Do not delete them.
    Layer 4 caption drops suspect words.
  * Until measured, Layer 4 caption must apply its own confidence floor.
* **Tests.** A fake engine returns a low-confidence word over a synthetic impulse, and the word
  is flagged.

### F10: audio_improvement would thin out ambience and squash bangs (P1)

* **Problem [E].** Every stage is gated by evidence (`audio_improvement/domain/planning.py:70-180`),
  and a guard reverts regressions (`verification.py:44-84`). Those parts are good. But all
  measurements assume speech:
  * SNR treats loud frames as "speech" (`audio_improvement/infrastructure/quality_metrics.py:127-130`).
    Bangs become speech and night ambience becomes noise, so `afftdn` denoise triggers below
    35 dB SNR (`ffmpeg_stages.py:84-120`).
  * Blind RT60 on bang tails can trigger dereverb (`quality_metrics.py:287-313`) [A].
  * Mastering is linear gain to -14 LUFS plus `alimiter` with a 5 ms attack
    (`ffmpeg_stages.py:33,178-186`). LRA is measured but never used.
  * A quiet uncut take (-18 LUFS) gets +4 dB, and the bangs are limited.
  * Declip counts flat runs of 3 or more samples near the file peak (`quality_metrics.py:35-39,173-186`).
    It then runs plain `adeclip` on the whole file (`ffmpeg_stages.py:76-81`). Camera-limited
    bangs are flat-topped [A].
* **Why it matters.** This breaks the rule "do not destroy good media". The bang is the product
  being judged. Layer 4 music hands mastering to audio_improvement (brief sec. 6), so the final
  mix goes through this path.
* **Owner.** `audio_improvement`.
* **Smallest change.**
  * Add one data profile, `ambience_preserving`, in `audio_improvement/domain/profiles.py:33-54`
    (no new code):
    * noise, dereverb, high-pass and compression triggers set to unreachable values;
    * declip `min_ratio` raised;
    * mastering kept at -14 LUFS / -1 dBTP.
  * Document that the fireworks format profiles use it.
  * A transient-safe limiter (longer lookahead, LRA guard) is a later P2. Measure first.
* **Compatibility.** A new profile only. Defaults are unchanged.
* **Tests.** A profile table test: the new profile resolves, and `planning` on a synthetic
  "bangs over quiet bed" plans no noise or dereverb stage.

### F11: video_improvement lifts night footage (P1, opt-in)

* **Problem [E].**
  * In the default `natural` profile (`video_improvement/domain/profiles.py:26-81`), exposure
    moves toward `target_median 0.18`, up to 1.5 stops (`domain/settings.py:37-86`).
    * When bursts cover less than 1% of the frame, it applies up to 1.2 stops
      (`planning.py:160-181`).
  * A gamma lift of about 0.84 is applied on dark medians (`planning.py:200-221`).
  * Contrast rises up to 1.25 when the tonal range is small (`planning.py:439-457`).
  * `hqdn3d` denoise runs at sigma ≥ 0.006 (`planning.py:383-393`).
  * After the 16-bit grade, `format=yuv420p` runs before sharpening, so 8-bit sources are
    sharpened at 8 bit (`ffmpeg_renderer.py:45-48,193-204`).
* **Why it matters.**
  * Discovery: "do not grade toward a cinematic look; protect the blacks; at most neutral
    WB/exposure lift with a hard guard against crushing sparks". The sky is 86% of pixels at
    luma ≤16. Lifting it raises noise and banding.
  * `visual_style_engine` must not grade footage (contracts sec. 1), so this module is the only
    place where it would happen.
* **Owner.** `video_improvement`.
* **Smallest change.**
  * Add a data profile, `night_preserve`, in `video_improvement/domain/profiles.py`:
    `max_exposure_stops 0`, `lift_strength 0`, contrast refinement off, denoise trigger raised,
    sharpen off.
  * Moving `format=` after sharpening (`ffmpeg_renderer.py:45-48`) is a separate P2 quality fix.
* **Compatibility.** A new profile. The default is unchanged.
* **Tests.** A planning test on synthetic metrics (median 0.04, p99 1.0, 0.5% clipped) plans
  no exposure, lift or contrast operation.

### F12: video_intelligence has no night / fireworks awareness (P1)

* **Problem [E].**
  * `dark_median 0.15`, `crushed_fraction 0.3` and `clipped_fraction 0.02`
    (`video_intelligence/domain/profiles.py:259-282`) feed `visual_quality`. The exposure penalty
    is `8*clipped + 1.5*crushed + 0.3` (`domain/scoring.py:181-185`).
  * Any indicator flag multiplies `technical_usability` by 0.2 (`scoring.py:62,340-342`).
  * The black-frame test is mean ≤ 0.06 and std ≤ 0.04 (`profiles.py:167-168`). A shot that is
    90% black gets the `black_picture` flag (`domain/meaning.py:33,153`).
  * The CLIP vocabulary has no night, fireworks, smoke, store-aisle or brochure label
    (`video_intelligence/domain/vocabulary.py:15-43`). "outdoor" is "in daylight".
  * The indicators `covered_lens` and `pocket` are described as "dark blurry" pictures (`:41-42`).
  * The detector is COCO `ssdlite` (`infrastructure/torchvision_detector.py:65-72`).
* **Problem [A].** With 86% of pixels at luma ≤16 and mean luma about 0.04, the crushed share
  is probably above 0.3 and the exposure score near 0. If the sky between bursts counts as
  black, a shot gets `black_picture` and usability × 0.2.
* **Why it matters.**
  * Reframing rejects shots below `min_usability 0.35` (`reframing_intelligence/domain/profile.py:89`).
  * broll ranks night test footage last. That is the footage discovery says to prefer ("own
    test footage").
  * Layer 4 style and vfx would read "bad quality" on the product's best moment.
* **Owner.** `video_intelligence` (domain data only; re-tuning never re-decodes).
* **Smallest change.**
  * Add `CONTENT_TYPES["fireworks_night"] = "fireworks exploding in a dark night sky"` and
    `ENVIRONMENTS["night"] = "a photo taken outdoors at night"` (`vocabulary.py:15-36`).
  * Add `CONTENT_PROFILE_OF["fireworks_night"] = "night_fireworks"` (`vocabulary.py:45`).
  * Add one `ContentTypeProfile("night_fireworks", ...)` in `profiles.py:578-588` with exposure
    weight 0 and a high handheld prior.
  * In `scoring.py:194-195`, treat `dark_exposure` as intentional (likelihood about 0.9) for that
    profile, through a profile field. No hard-coded constant.
  * Bump `VOCABULARY_VERSION` (`vocabulary.py:12`) and the rubric version.
* **Interfaces and consumers.** `IndexedShot.content_label` / `environment` and
  `Shot.scores`. Read by broll, reframing, pacing, editing and, in Layer 4, style and vfx.
* **Compatibility.**
  * The new labels change the shot index and scores, so the analyses re-derive.
  * The derived descriptions are cached apart from decoding, so no re-decode is needed.
* **Tests.**
  * Unit: synthetic `ShotMeasures` (p50 0.04, crushed 0.85, clipped 0.01) with content
    `fireworks_night` give a usability above 0.35 and no `dark_exposure` penalty. The same
    measures with `generic` keep today's score.
  * Real-model validation: sec. 7.

### F13: OCR uses the Chinese recognition model; German text and prices are unvalidated (P1)

* **Problem [E].**
  * The engine is `RapidOCR()` with no arguments (`video_intelligence/infrastructure/rapidocr_text.py:81-84`).
    The installed defaults are `lang_type "ch"`, PP-OCRv6.
  * The installed rapidocr ships `LangRec.LATIN` (`.venv/.../rapidocr/utils/typings.py:39`;
    `default_models.yaml:188-189`, `latin_PP-OCRv5_rec_mobile`).
  * The ch dictionary contains äöüß€ (checked by the verifier). Nothing strips umlauts or €.
    Stored text keeps its case (`domain/text_cues.py:110`).
* **Problem [A].** Latin-script German on packaging may read worse with the ch model.
* **Why it matters.** Product names, prices and retailer names on packaging, brochures and
  shelf tags feed F03, broll identity and Layer 4 continuity ("same product name and price").
* **Owner.** `video_intelligence`.
* **Smallest change.**
  * Add `ocr_language: str = "ch"` to the analysis settings (`profiles.py:83`, next to
    `text_min_score`).
  * Pass it to `RapidOCR(params=...)` (`rapidocr_text.py:84`). Check the exact parameter keys
    against the rapidocr 3.x docs before coding.
  * Also put it in the OCR engine identity (`rapidocr_text.py:31`).
* **Compatibility.** The default stays `ch`, so there is no change. `latin` changes only the
  text analyzer's cache key.
* **Tests.**
  * Unit: the settings flow into the engine params (fake `_create`).
  * Integration (skipped without the model): rendered PNGs of "15,99 €", "Höhe 30 m",
    "Brenndauer", "Größe" read back exactly.

### F14: reframing a night sky with no person is never executable (P1)

* **Problem [E].**
  * With no track, `_without_subject` makes a `STATIC_CROP` on `shot.attention` (the saliency
    centroid) or on the centre. It marks the crop `Feasibility.UNCERTAIN`, `executable=False`
    (`reframing_intelligence/domain/planner.py:769-774`).
  * There is no bright-region or burst tracking.
  * Split screen is not supported (README: "A split screen is never proposed").
* **Why it matters.**
  * Turning 16:9 tests into 9:16 Shorts gives `IssueKind.FRAMING` on every sky shot in the edit.
  * `caption_engine`, `motion_graphics_engine` and `vfx_engine` (allowlisted for reframing) get
    no usable crop.
  * Native vertical phone Shorts are not affected: the source already has the target shape, so
    the shot is `ORIGINAL` and executable (`planner.py:762-768`).
* **Owner.** `reframing_intelligence`.
* **Smallest change.**
  * Add a profile field `attention_crop_executable: bool = False` (`reframing_intelligence/domain/profile.py:89`
    area).
  * At `planner.py:770-774`, when the field is set and `shot.attention is not None`, return
    `FEASIBLE_WITH_TRADEOFFS`, `executable=True`, with the reason "attention-point crop, no
    subject tracked".
  * Add a `fireworks_sky` profile (a copy of `shorts_reels` with the field on).
  * Burst tracking (a dynamic crop following the brightest region) is P2. It needs a new video
    signal.
* **Compatibility.** The default is unchanged. The new profile is opt-in.
* **Tests.** A planner unit test: a 16:9 shot with no tracks and attention (0.7, 0.4) under
  `fireworks_sky` gives an executable static crop centred there. Under `shorts_reels` it is
  unchanged.

### F15: continuity repairs with no Layer 4 owner (P1)

* **Problem [E].**
  * `Owner` has six values (`continuity_intelligence/domain/values.py:132-140`).
  * `AUDIO_CROSSFADE`, `ROOM_TONE` and `AMBIENCE_BRIDGE` go to `audio_improvement`
    (`continuity_intelligence/domain/repair.py:142-162`). `audio_improvement` has no per-cut or
    per-range operation (`ImproveAudioCommand` holds only asset, profile and overrides,
    `audio_improvement/application/improve_audio.py:82-87`).
  * Contracts sec. 1 gives the audio transition plan (J/L cuts, crossfades, tails) to
    `transition_engine`, and ambience preservation to `music_and_sound_design`.
* **Why it matters.** Tours and unboxings are jump-cut, with crowd and ambience steps at the
  cuts. The repairs continuity proposes for them have no executor, so they are silently lost.
* **Owner.** `continuity_intelligence`.
* **Smallest change.**
  * Add `TRANSITION = "transition_engine"` and `SOUND = "music_and_sound_design"` to `Owner`
    (`values.py:140`).
  * Re-point `AUDIO_CROSSFADE` to `TRANSITION` and `AMBIENCE_BRIDGE` / `ROOM_TONE` to `SOUND`
    (`repair.py:144,152,160`).
  * Keep `EXPOSURE_CORRECTION` / `COLOR_CORRECTION` on `video_improvement`.
* **Compatibility.**
  * New enum members cannot be read by older code, but older documents read fine with the
    strict codec.
  * Bump the continuity analyzer version so stored analyses replan.
  * The continuity boundary test already allows `transition_engine` as a consumer (contracts
    sec. 2).
* **Tests.** The repair-table unit test: an `AUDIO_DISCONTINUITY` finding yields the new owners.
  A serialization round trip covers the new members.

### F16: on-screen prices never link to spoken prices (P1)

* **Problem [E].**
  * `words()` drops tokens that are pure digits and words shorter than 4 characters
    (`audio_visual_alignment/domain/semantic.py:58-62`). So "15,99" and "10" never match.
  * `SoundAction` pairs only `energy_spike` with `motion_peak` / `gesture` / `track_entered`
    (`audio_visual_alignment/config.py:69-70`). The `flash` event is not paired.
* **Why it matters.** Product facts need speech and the shot showing the packaging to agree
  (price graphics and continuity). Bang plus flash is the natural sync point for transitions
  and SFX restraint.
* **Owner.** `audio_visual_alignment`.
* **Smallest change.**
  * Keep tokens that F03's `commerce_facts` recognises (price or rank) as normalised terms in
    `semantic.py:62`, by calling the content contract helper.
  * Add `"flash"` to the video kinds at `config.py:70` (and `"impulse"` to the audio kinds once
    F08 exists).
* **Compatibility.** The alignment results change, so bump the alignment version.
* **Tests.** Unit: a passage "kostet 15,99 Euro" and OCR "15,99 €" give an `OBSERVED_TEXT`
  match. A synthetic flash at 0.1 s from an energy spike gives a `SoundAction`.

### F04: ranks are not structure (P1)

* **Problem [E].**
  * `top_n` only preserves the recorded order and checks SETUP/PAYOFF
    (`story_architecture/domain/structure.py:344-345`, `engine.py:398`).
  * No rank cue exists. The German `list` cue knows only `erstens` ... `nummer (eins|zwei|drei)`
    (`content_intelligence/domain/lexicon.py:122`).
* **Why it matters.** The `rank_card` and the carousel transition between ranks need a section
  per rank. The editing `TRANSITION` request fires at a section change (`editing_intelligence/domain/effects.py:65-76`).
* **Owner.** `content_intelligence` (lexicon).
* **Smallest change.**
  * Add `r"(?:auf )?platz (?:\d+|eins|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn)"` to the
    German `list` cue (`lexicon.py:122`), and `r"number \d+"` to the English one (`:78`).
  * Whether a cue starts a segment depends on the segmentation weights. Verify this with a
    test before relying on it.
  * The rank value comes from F03.
* **Compatibility.** The lexicon version bumps. Content and story replan.
* **Tests.** A German transcript fixture "Auf Platz 3 ... Auf Platz 2 ..." gives segments that
  start at each rank sentence and a `LIST` opportunity.

### F19: night shot detection must be measured, not assumed (P1)

* **Problem [E].**
  * Flash suppression catches only one-frame outliers (`video_intelligence/domain/shot_detection.py:217-221`,
    `flash_return_ratio 0.4`).
  * Black runs with ramps of 4 frames or more become `FADE_THROUGH_BLACK` boundaries
    (`shot_detection.py:74-104`).
* **Problem [A].** Multi-frame bursts can become cuts. Dark sky between bursts can become fades.
  An 11-minute uncut take could split into many shots.
* **Why it matters.** Shots drive reframing, broll, pacing (cut rate) and `edge_state` for
  continuity. False cuts inflate pacing and give Layer 4 transitions false boundaries.
* **Owner.** `video_intelligence`.
* **Smallest change.**
  * First measure on the uncut sample `y_mj09m0XB4` and the ranking sample.
  * If confirmed, add a `night` shot-detection preset: lower `black_luma`, a larger
    `flash_return_ratio` window. These are profile data in `profiles.py:155-170`.
* **Tests.** A synthetic frame-signal test: a 6-frame bright burst on black is not a cut, and a
  dark sky with std 0.03 between bursts is not a fade.

---------------------------------------------------------------------------------------------------

## 5. P2 findings

| ID | Problem [E/A] | Owner | Smallest change (where) | Compat / tests |
| --- | --- | --- | --- | --- |
| F17 | [E] Pacing flags only fast cutting (`pacing_intelligence/domain/picture.py:111-136`). An uncut take gets `RESTORE_ATTENTION`, because motion and bangs are not "change" (`picture.py:279-297`, `recommend.py:741-765`). The haul Short at 31.5/min goes over the `shorts_reels` max of 30 (`profile.py:223`) in some windows. | pacing_intelligence | Add the `impulse` kind (F08) as a change in `picture.py:279-297`. Raise `shorts_reels` max to 35, or let Layer 4 ignore pacing for uncut. Layer 4 music and transition must treat pacing recommendations as advice. | Profile bump; test that an uncut with impulses gives no `restore_attention`. |
| F18 | [E] The `short` target of 15-60 s (`story_architecture/domain/profile.py:207-216`) flags 7-8 s clips as `UNDER_TARGET_DURATION`. | story_architecture | None while those clips take the no-speech path (F01). If a speaking meme appears, lower the bound to 5 s in the data. | Data-only. |
| F20 | [E] Content-based import takes GoPro `.LRV` (MP4) as a video and `.THM` (JPEG) as an image (`media_library/infrastructure/media_probes.py`, `image_probes.py:112`). Chaptered files are not joined. | media_library | Document "import the main chapters only". A sidecar filter by name pattern is out of the pure-content design, so leave it. | none |
| F21 | [A] Action cams often write naive local `creation_time`. `wall_clock_overlap` skips naive times (`asset_relationships/domain/detection.py:154`). The NYE multi-camera material is then not proposed. | asset_relationships | Leave the code. A person can confirm takes manually. If needed later: a per-device timezone hint in the detection call. | none |
| F22 | [E] `SOUND_CUE` is never generated. No end-screen, plug or sponsor slot exists (search found nothing in editing). | Layer 4 | See sec. 6: Layer 4 derives these. No Layer 3 change. | - |
| F23 | [E] broll has no third-party credit notion (`broll_intelligence/domain/values.py:80-134`). | Layer 4 motion (credit overlay) | A library tag convention `credit:<name>` on imported third-party clips. motion_graphics reads `MediaAssetDto` tags through the media_library contracts. No broll change. | tag-convention test in motion. |
| F24 | [E] The mixer ducks music by a sidechain keyed on the voice track (brief sec. 6), and bangs are on that track, so music would duck under bangs. | music_and_sound_design (Layer 4) | No audio_improvement change. Music renders its own gain automation into the stem and calls `AudioMixing` with `overrides={"mix.duck_ratio": 1.0}`. `duck_ratio ≥ 1.0` is valid (`audio_improvement/domain/settings.py:255,265`). | Music integration test. |
| F25 | [E] Video improvement sharpens at 8 bit after `format=yuv420p` (`video_improvement/infrastructure/ffmpeg_renderer.py:45-48`). | video_improvement | Move the format conversion after sharpening. | Render test on a 10-bit source. |

---------------------------------------------------------------------------------------------------

## 6. Deliberately leave unchanged

| Item | Why it stays |
| --- | --- |
| `EffectRequest` shape (`editing_intelligence/domain/model.py:284-297`; no text/rank/price field, `duration=None`) (F02) | The content is already reachable. A GRAPHIC request's `decision_id` equals the `CoverageRequest.request_id`, which carries the verbatim `concept` and an exact `span` (`editing_intelligence/domain/coverage.py:607-636`, `model.py:262-275`). The media_pipeline mapper joins them and applies F03's `commerce_facts`. Adding fields would duplicate data and edit a shared schema while seven agents depend on it. Test: one mapper test asserts the join. |
| No `SOUND_CUE`, no end-screen / plug / sponsor requests in editing (F22) | Contracts sec. 1 and sec. 2: the end screen and outro are owned by motion_graphics; SFX placement by music, synced to motion and transition events; the sponsor label is a mandatory rule of the style format profile ("whole duration"). The end screen is appended after `EditPlan.duration`, so no timeline shifts. Exception: a membership plug that is INSERTED after the hook would shift every later time. Layer 4 v1 should treat the plug as pre-rendered intro material placed by the person, or defer it. Revisit only if the owner wants automatic plug insertion. |
| Story format list (no `brochure_review`, `shopping_tour`, `compilation`, `nye_uncut`, `shorts_meme`, `versus`) | The speech formats map onto `top_n` / `unboxing` / `short` with the right order policy (sec. 2). The no-speech formats need no story, by design (F01). New formats would add profiles in story, editing, pacing, broll, continuity and reframing for no new behaviour. |
| media_inspection | VFR, rotation and timing are correct, and inspection is reused by video_intelligence (`video_intelligence/application/resolver.py:60-105,170`) and proxy. Layer 4 follows contracts sec. 3 (seconds authoritative, `timebase.assumed`). |
| proxy_manager | Analyses run on the originals (`media_pipeline/domain/plan.py:43-44`). Proxies pass VFR through and are orientation-neutral. Nothing in the channel needs a change. |
| Object detector (COCO) | Adding fireworks or smoke classes needs a new model, and none can be downloaded or validated here. The CLIP labels (F12) cover classification cheaply. Bursts are not entities to track. |
| broll identity gate (`strict_identity=True`) | Correct for product reviews. A test must show the same product the VO names. OCR already confirms identity. |
| audio_improvement defaults and gating | The gates and regression guards are sound. Fireworks need a profile (F10), not changes to the default. |
| Continuity `LIGHTING_MISMATCH` on day/night cuts | It is a WARNING with `intentional_style_likelihood`. The `top_10_ranking` continuity profile exists. Day and night test cuts are information, not errors. |
| Pacing profile mapping (`pacing_intelligence/domain/profile.py:18-29`) | `top_n -> top_10_ranking` and `unboxing -> unboxing` match the measured 4-7 cuts/min. |
| `has_audio` from media type (`media_pipeline/application/run_pipeline.py:131-132`) | Inspection already skips audio work on a usable inspection with no audio stream. |

---------------------------------------------------------------------------------------------------

## 7. Evidence vs assumptions, and what to measure

Evidence-based (read in code): F01, F03, F05, F06, F07, F11, F13 (engine and config), F14, F15,
F16, F04, F17, F18, F20, F22-F25, and every threshold quoted.

Assumptions (need MEsc clips; samples are in the discovery scratchpad `SCR/channel/videos/`):

| # | Assumption | Finding | Measurement |
| --- | --- | --- | --- |
| A1 | Auto language detection picks the wrong language on bang or music cold opens | F06 | Transcribe `t_BTkcPy5sw`, `dru73YpbQ4Y` and `y_mj09m0XB4` with `language=None` and with `"de"`. Compare the detected language and the word error rate on 60 s. |
| A2 | WhisperX invents words on bang-only audio | F09 | Run on `y_mj09m0XB4` (no speech) and `_Ka5c6jMYRg` (no speech). Count the words. Expected: 0. |
| A3 | Night shots fall below `min_usability 0.35` / are flagged `black_picture` | F12 | Run video analysis on the sky section of `y_mj09m0XB4`. Read `crushed`, `clipped`, `technical_usability` and the flags. |
| A4 | Bursts become false cuts, and dark sky becomes fades | F19 | Count the detected shots in `y_mj09m0XB4`. ffmpeg found 1 change in 11 min. |
| A5 | The ch OCR model misreads German packaging | F13 | Stills `SCR/channel/frames/hi/d2ChnPz3FRg_hi_00063.9.png`, `hPWgWyzYtq8_hi_00016.8.png`. Compare ch with latin, exact-match "PLATZ", prices and "Werbung". |
| A6 | Declip / denoise / dereverb trigger on bang audio, and mastering squashes bangs | F10 | `ImproveAudio` (youtube) on `_Ka5c6jMYRg` (TP +3.8) and `y_mj09m0XB4`. Read the planned stages and the LRA before and after. |
| A7 | Action cams write naive timestamps | F21 | Inspect one original GoPro/DJI file from the owner. |

Not verifiable here: real WhisperX VAD defaults (they depend on the installed whisperx version),
the CLIP zero-shot quality of the new labels, and the owner's preference on plug insertion.

## 8. Ranked change list

| Rank | ID | Priority | Owner | One line |
| --- | --- | --- | --- | --- |
| 1 | F01 | P0 | media_pipeline (+ contracts.md) | Skip, not fail, on no speech, and give Layer 4 an "unedited recording" edit view (`EditRef \| None`). |
| 2 | F03 | P0 | content_intelligence | Pure `commerce_facts()` for German price, rank and spec values. Add `platz` to the non-names, `euro/sekunden/schuss/meter` to the units. |
| 3 | F05 | P0 | editing_intelligence | Profile knob `keep_unconfirmed_gaps`, so loud test footage between words is kept in top_n/unboxing/review. |
| 4 | F07 | P0 (doc) | Layer 4 style profiles | Map channel formats to `top_n` / `unboxing` / `short` / the no-speech path. |
| 5 | F06 | P1 | media_pipeline | `StageSettings.transcription_language` passed to `TranscriptionConfig` (pin `de`). |
| 6 | F12 | P1 | video_intelligence | Night/fireworks CLIP labels plus a `night_fireworks` content profile, so dark is intentional and not unusable. |
| 7 | F08 | P1 | audio_intelligence | Ship one `impulse` detector through the existing detector port. |
| 8 | F10 | P1 | audio_improvement | `ambience_preserving` profile (data): no denoise, dereverb or compression on bangs. |
| 9 | F14 | P1 | reframing_intelligence | `attention_crop_executable` flag and a `fireworks_sky` profile for 9:16 night shots. |
| 10 | F13 | P1 | video_intelligence | Configurable OCR language (`latin`) in the identity, plus a German/price fixture test. |
| 11 | F16 | P1 | audio_visual_alignment | Keep price/rank tokens in OCR-speech matching; pair `flash` (and `impulse`). |
| 12 | F04 | P1 | content_intelligence | German "Platz N" rank cue, so ranks become sections. |
| 13 | F15 | P1 | continuity_intelligence | Owners `transition_engine` / `music_and_sound_design` for the crossfade, room-tone and ambience repairs. |
| 14 | F11 | P1 | video_improvement | `night_preserve` profile (no lift, contrast or sharpen). |
| 15 | F09 | P1 | audio_intelligence | Flag low-confidence words inside impulses as suspect (after F08 and A2). |
| 16 | F19 | P1 | video_intelligence | Measure burst cuts and black fades; add a night shot-detection preset if confirmed. |

Order of work: F01 and F07 first, because they change the Layer 4 input contract and are cheap
now. Then F03 and F05, which unblock the ranking format. Then F06, which is cheap. The P1 items
are independent of each other except F09 (needs F08) and F16 (uses F03).

---------------------------------------------------------------------------------------------------

## 9. Implemented (Layer 3 / content / pipeline)

2026-11-02, working tree, not committed. Each item is the smallest change of its finding.

| ID | Change | Where | Tests |
| --- | --- | --- | --- |
| F01 | `nothing_to_plan` and `nothing_to_edit` added to `_UNAVAILABLE_CODES`: a clip without speech gives `story` = `skipped_unavailable` ("The recording has no analysed speech to build a story from."), and the tasks that need its plan are skipped with that reason. The Layer 4 "edit view" (step 2) and `EditRef \| None` in contracts.md (step 3) are NOT done here: the orchestrator owns them. | `media_pipeline/application/executors.py` (`_UNAVAILABLE_CODES`) | `tests/unit/media_pipeline/test_executors.py::test_no_speech_story_and_edit_are_skipped_not_failed` |
| F03 | New pure `content_intelligence/domain/commerce.py`: `commerce_facts(text, language) -> tuple[CommerceFact, ...]` with kinds `price` (exact euros as `Fraction`, unit `EUR`), `rank`, `shots`, `effects`, `height` (m), `duration` (s), `caliber` (mm), `weight` (g/kg), `retailer` (channel list, canonical `name`), `product` (from `designations`, years excluded). Verbatim `text` + offsets; a spec needs its spec word, a price its currency; spelled-out amounts are not read (documented). Exported from `application/contracts.py`. `platz`, `rang`, `preis`, `kaliber`, `gewicht`, `nem`, `brenndauer`, `brennzeit` added to `_NOT_NAMES`; `euro`, `sekunden`, `schuss`, `meter` to `_STAT_UNIT`. `PROCESSING_VERSION` 1 -> 2. Value is euros, not cents as proposed: equally exact, and what a price tag prints. | `domain/commerce.py`, `domain/references.py`, `domain/keyinfo.py`, `domain/values.py`, `application/contracts.py` | `tests/unit/content_intelligence/test_commerce.py` |
| F04 | German rank cue `(?:auf )?platz (\d+\|eins..zehn)` in the `list` cue AND the `transition` lead cue (the `list` cue alone does not move a segment boundary; verified); English `number \d+` in `list`. | `content_intelligence/domain/lexicon.py` | `test_commerce.py::test_each_rank_starts_its_own_segment_and_is_a_list_graphic` (segments start at each "Auf Platz N", three LIST graphics) |
| F05 | `EditingProfile.keep_unconfirmed_gaps: bool = False`; `_pause_cut` returns `None` for an unconfirmed gap when set. On in `top_n`, `unboxing`, `review`, `product_introduction`. | `editing_intelligence/domain/profile.py`, `domain/assemble.py` | `tests/unit/editing_intelligence/test_decisions.py::test_a_loud_gap_is_kept_as_footage_only_where_the_profile_says_so` |
| F06 | `StageSettings.transcription_language: str \| None = None` passed as `TranscriptionConfig(language=..., device=...)`; `--language` flag and `transcription_language` config key in `scratch/run_pipeline.py`. Default unchanged (auto). | `media_pipeline/application/executors.py` (`StageSettings`, `analyze_audio`), `scratch/run_pipeline.py` | `test_executors.py::test_transcription_language_is_pinned_only_when_set` |
| F07 | Format mapping table in the story README ("Formats") and one paragraph in `AI_CONTEXT.md` sec. 11 (story). The `visual_style_engine` side is Layer 4's. | docs | - |
| F14 | `ReframingProfile.attention_crop_executable: bool = False`; a shot with no tracked subject and an attention point gets an executable `STATIC_CROP`, `feasible_with_tradeoffs`, reason "attention-point crop, no subject tracked", confidence still unstated and the `MISSING_EVIDENCE` issue kept. Not when tracking failed. New opt-in profile `fireworks_sky` (= `shorts_reels` + the flag). | `reframing_intelligence/domain/profile.py`, `domain/planner.py` | `tests/unit/reframing_intelligence/test_planner.py::test_a_night_sky_with_no_person_gets_an_executable_attention_crop_under_fireworks_sky` |
| F15 | `Owner.TRANSITION = "transition_engine"`, `Owner.SOUND = "music_and_sound_design"` (values only, no imports); `AUDIO_CROSSFADE` -> TRANSITION, `ROOM_TONE` / `AMBIENCE_BRIDGE` -> SOUND. `ANALYZER_VERSION` 2 -> 3. | `continuity_intelligence/domain/values.py`, `domain/repair.py` | `tests/unit/continuity_intelligence/test_scenarios.py` (audio discontinuity owners + round trip) |
| F16 | Prices and ranks become normalised terms (`price:15.99`, `rank:3`) via `commerce_facts`, computed in the view adapter (the domain never imports another module) and carried as `Passage.commerce_terms` / `ShotFacts.commerce_terms`; on-screen text is read as `de` and `en`. `flash` added to the paired video kinds, `impulse` to the audio kinds (the name audio_intelligence uses; a test checks it against its contracts once exported). `ALIGNMENT_VERSION` 1 -> 2. | `audio_visual_alignment/application/views.py`, `domain/inputs.py`, `domain/semantic.py`, `domain/config.py`, `domain/values.py` | `tests/unit/audio_visual_alignment/test_semantic.py::test_a_spoken_price_meets_the_same_price_on_screen`, `test_events_opportunities.py::TestSoundAction::test_a_bang_pairs_with_a_flash` |

## 10. Implemented (Layer 2)

2026-11-02, working tree, not committed. Each item is the smallest change of its finding; every
default is unchanged except where the finding says otherwise (F08 ships a detector by default).
Measurements ran the real module code on the owner's 360p samples in a throwaway app home in the
scratchpad, CPU only, no model downloaded (shots/quality/motion analyzers; no CLIP, no WhisperX).

| ID | Change | Where | Tests |
| --- | --- | --- | --- |
| F08 | `ImpulseDetector` through the existing `AudioEventDetector` port, on by default (`AcousticExtractor(detectors=None)` = `(ImpulseDetector(),)`; an explicit tuple still replaces it). 5 ms RMS envelope; onset = rise >= 20 dB over the median of the preceding 300 ms, >= 12 dB within 10 ms, level >= -50 dBFS, and the level 40-80 ms after the peak >= 12 dB below it (the decay test separates bangs from syllables, see below). Event = `AudioEvent("impulse", start, end, strength, confidence, details={peak_dbfs, rise_db, background_db})`; confidence 0.5-1 from how far the rise clears 20 dB. Version `transient-1` sits in the measurements' analyzer identity (`detector:impulse`), so stored measurements and timelines re-derive; constants pinned in the constants test. `IMPULSE` added to `NONVERBAL_KINDS` and exported from `application/contracts.py`. | `audio_intelligence/infrastructure/acoustic_extractor.py`, `domain/analysis/acoustic.py`, `application/contracts.py` | `tests/integration/audio_intelligence/test_acoustic_extractor.py::test_a_bang_in_a_quiet_bed_is_one_impulse_event`, `::test_spoken_syllables_are_not_impulses`; `test_timeline_serialization_validation.py::test_impulse_events_are_queryable_like_any_other_event`; `test_algorithm_constants.py` |
| F09 | `TimelineWord.suspect: bool = False`; fusion sets it for a word with a known confidence below `AnalysisConfig.suspect_word_confidence` (0.5) that overlaps an `impulse`. Words are never removed. Stored as `"suspect": true` only when set, so older timelines decode. | `audio_intelligence/domain/analysis/timeline.py`, `fusion.py`, `config.py`, `serialization.py` | `test_timeline_serialization_validation.py::test_low_confidence_words_inside_a_bang_are_suspect_but_kept` (also the JSON round trip) |
| F10 | Profile `ambience_preserving` (data only): noise target SNR -100 dB, dereverb from RT60 100 s, rumble high-pass trigger +100 dB, dynamics trigger 1000 dB, de-esser trigger +100 dB (all unreachable), declip from 0.5 % clipped samples (default 0.05 %); mastering -14 LUFS / -1 dBTP as `youtube`. De-essing is off too (not in the proposal): the crackle of a bang is sibilant-band energy. | `audio_improvement/domain/profiles.py` | `test_planning_and_verification.py::test_ambience_preserving_plans_no_cleanup_on_bangs_over_a_quiet_bed`, `test_profiles.py` |
| F11 | Profile `night_preserve` (data only): `exposure_strength 0` (`max_exposure_stops 0` is refused by the settings), `lift_strength 0`, `max_contrast_boost 1`, `saturation_strength 0` (the sparks would otherwise be pushed into the saturation band), denoise from sigma 0.03, sharpen off. White balance, black point and highlight roll-off stay. | `video_improvement/domain/profiles.py` | `tests/unit/video_improvement/test_planning.py::TestNightPreserve` |
| F12 | `CONTENT_TYPES["fireworks_night"]`, `ENVIRONMENTS["night"]`, `CONTENT_PROFILE_OF["fireworks_night"] = "night_fireworks"`; `ContentTypeProfile.dark_intentional` (default 0.3 = the old constant) used for `dark_exposure`; new `night_fireworks` content profile: quality weights (0.5, 0.5, 0.0), handheld prior 0.8, `dark_intentional` 0.9. `VOCABULARY_VERSION` 1 -> 2, `RUBRIC_VERSION` 1 -> 2. | `video_intelligence/domain/vocabulary.py`, `profiles.py`, `scoring.py` | `test_meaning_cinema.py::test_a_dark_fireworks_night_shot_is_not_judged_as_badly_exposed` (generic keeps 0.3) |
| F13 | `MeasurementSettings.ocr_language = "ch"`, passed as `RapidOCR(params={"Rec.lang_type": ...})` (key checked in rapidocr 3.10 `utils/parse_parameters.py`; one engine per language). It is in the TEXT analyzer's cache key (`config_for(TEXT)`), not in `identity()`, which has no settings: one re-read of text signals after this change, no other analyzer affected. | `video_intelligence/domain/profiles.py`, `infrastructure/rapidocr_text.py` | `test_profiles.py::test_the_ocr_language_keys_only_the_text_signals_and_reaches_the_engine`; `tests/integration/video_intelligence/test_real_models.py::TestOnScreenText::test_german_text_and_euro_prices_are_read_exactly` |
| F19 | `NIGHT_SHOTS = ShotSettings(fade_min_frames=20)` and the opt-in processing profile `night_fireworks` (= `standard` + `NIGHT_SHOTS`). `standard` and the defaults are unchanged. | `video_intelligence/domain/profiles.py` | `test_shot_detection.py::test_bursts_in_a_night_sky_are_not_fades_with_the_night_preset`, `::test_the_night_preset_still_finds_cuts_and_long_fades`; `test_profiles.py::test_the_night_fireworks_profile_only_changes_how_fades_are_read` |

Also: the module READMEs (audio_intelligence, audio_improvement, video_improvement,
video_intelligence) list the new profiles and the detector. `tests/support/analysis_fakes.py`
takes per-word confidences.

### Measured on real footage

**F19 / A4 (confirmed: dark sky becomes fades; bursts do not become cuts).** Fast profile, real
shot/quality analyzers:

| Sample | default (`fade_min_frames` 4) | 8 | 15 | 20 (preset) | `black_luma` 0.02 / 0.01 | `black_std` 0.01 |
| --- | --- | --- | --- | --- | --- | --- |
| `y_mj09m0XB4` (one 11:05 take) | 26 shots: 24 fades, 1 cut | 7 fades | 2 fades | **2 shots: 0 fades, 1 cut** | 23 / 19 fades | 15 fades |
| `y1q4lpEEFcI` (edited night test) | 22 shots: 7 fades, 14 cuts | 2 fades | 0 | 0 fades, 14 cuts | 7 / 7 fades | 0 fades |
| `_Ka5c6jMYRg` (compilation) | 75 shots: 69 cuts, 4 dissolves, 1 fade | 0 fades | 0 | 74 shots, cuts unchanged | 1 / 1 fade | 0 fades |

* The remaining cut in the uncut is real (645 s, end screen); ffmpeg also found 1 change.
  Frames at 143, 288, 428 and 528 s (detected fades) show the same continuous sky.
* Lowering `black_luma` (the first guess in F19) does not help: the sky is as dark as a real
  black frame (shot medians 0.0-0.004). The ramp length is what separates them.
* No burst became a hard cut in the uncut. The very short shots in `_Ka5c6jMYRg` (0.27-0.6 s at
  131.9, 198.3 and 432.2 s) were checked visually: they are the manufacturer's flash
  transitions between products, i.e. real edits. `flash_return_ratio` is therefore unchanged.

**A3 / F12 (partly refuted).** Night sky shots (generic profile, no CLIP): `crushed` 0.44-0.98,
`clipped` <= 0.0003, median luma 0.0-0.035, reasons `crushed_shadows` + `dark_exposure`;
`visual_quality` capped at 0.70 (exposure part 0); `technical_usability` about 0.49 (0.43-0.79).
Below `min_usability 0.35` only with `soft_focus` (3 of 23 sky shots in the uncut, 4 in
`y1q4lpEEFcI`). `black_picture`: 0 shots in all four night samples. The `night_fireworks`
content profile needs the CLIP label to be chosen; its effect on real footage is not measured
here (no model).

**F08 (impulse detector, `transient-1`).** Counts over the whole file. The speech-dominant
counts are an upper bound of false positives, not verified by listening (they include real bangs
in b-roll, cardboard and SFX pops):

| Sample | Content | Impulses | Per minute | Notes |
| --- | --- | --- | --- | --- |
| `y_mj09m0XB4` | midnight uncut, no speech | 580 | 52 | minutes 0-3 (preparation under a headlamp): 4; minutes 4-10 (sky): 576 |
| `y1q4lpEEFcI` | Pyroland night test | 73 | 27 | 53 in the night-test minute |
| `xCoOYkqzgDo` | 21 s sponsored test | 30 | 87 | |
| `_Ka5c6jMYRg` | manufacturer compilation | 260 | 19 | |
| `sgUxhOUxqZ0` | pyro news, speech only | 1 | 0.2 | |
| `t_BTkcPy5sw` | ranking (speech plus test inserts) | 15 | 1.6 | |
| `9etWVDZnyOs` | brochure review, 172 wpm | 26 | 2.1 | |
| `dru73YpbQ4Y` | shopping tour, 117 wpm | 67 | 3.0 | |
| `OkjnKFjRFt4` | unboxing | 97 | 8.2 | cardboard and box handling |

Without the decay test the same detector found 250 events in the brochure review (20/min), 50
in the ranking and 6 in the news, against 609 in the uncut. The level 40-80 ms after the peak
had fallen 16-28 dB (p10-p90) for the night sky and -2 to 12 dB for the brochure review, so the
12 dB decay test removes 90 % of the brochure detections (70 % in the ranking, 5 of 6 in the
news) and keeps 95 % of the night bangs (609 -> 580).

**F13 / A5 (refuted).** The installed rapidocr 3.10 default is the PP-OCRv6 *multilingual*
small recogniser (its route lists `de` and `german`), not a Chinese-only model. `latin` exists
only for PP-OCRv5/v3 mobile, is not in the wheel (it would be downloaded) and is refused for
PP-OCRv6 (`ValueError: Unsupported rec.lang_type='latin'`), so the setting takes the PP-OCRv6
language codes (`de`). Synthetic Arial lines "Größe 19,99 €", "Höhe 30 m", "Brenndauer" and
"PLATZ 1 Werbung" read exactly with `ch` and `de` (scores >= 0.996). Real stills: `d2ChnPz3FRg`
63.9 s gives "Werbung", "PLATZ 9", "DISCOVERY", "ESSENCE"; `hPWgWyzYtq8` 16.8 s gives
"Werbung", "weco", "4,44€" (identical for `ch` and `de`).

**Not measured:** A2 / F09 (WhisperX words on bangs: no ASR model here; the suspect flag is in
place either way), A6 / F10 on real audio, and CLIP choosing `fireworks_night`.
