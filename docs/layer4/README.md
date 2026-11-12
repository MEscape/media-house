# Layer 4: finishing (architecture overview)

Layer 4 turns a decided edit into everything a timeline needs on top of the footage: the channel
look, graphics, captions, transitions, shot effects, music and sound effects. It produces two
things only, both in the Media Library:

* **stored plans**: versioned JSON documents with exact times, frames, asset ids and track intent,
  so a later timeline builder or Resolve adapter recomputes nothing;
* **rendered assets**: derived library assets (graphics with alpha, VFX clips, a sound stem,
  prepared stills), cached by fingerprint and never overwriting a source.

Related documents (read in this order when changing Layer 4):

| Document | What it is |
| --- | --- |
| [`contracts.md`](contracts.md) | BINDING shared rules: ownership, dependency graph, no-speech path, time, storage, QC, configuration |
| [`integration-brief.md`](integration-brief.md) | How the codebase works for a Layer 4 author (upstream contracts, pipeline task recipe) |
| [`channel-discovery.md`](channel-discovery.md) | What the MEsc channel actually is (formats, evidence) |
| [`visual-identity.md`](visual-identity.md) | Every visual token, its evidence and the design decisions |
| [`cross-layer-reassessment.md`](cross-layer-reassessment.md) | Layers 1-3 judged against the channel; what was changed for Layer 4 |
| each module's `README.md` | its contract, `EditPlan -> DTO` mapping, profiles, QC, limits |

## 1. Layer dependency map

```text
Layer 1  foundation   media_library  media_inspection  proxy_manager  asset_relationships
                      audio_improvement  video_improvement
            |
Layer 2  analysis     audio_intelligence  video_intelligence  content_intelligence  audio_visual_alignment
            |
Layer 3  editorial    story_architecture  pacing  broll  continuity  reframing  -> editing_intelligence (EditPlan)
            |
Layer 4  finishing    visual_style_engine
                        |-> visual_asset_generation
                        |-> motion_graphics_engine  (-> visual_asset_generation)
                        |-> thumbnail_design        (-> visual_asset_generation)
                        |     |-> caption_engine
                        |     |-> transition_engine
                        |-> vfx_engine
                      music_and_sound_design  (events of motion, transition, caption; NOT the visual style)
            |
orchestration         media_pipeline: one graph per asset; the ONLY place that maps the EditPlan into the
                      Layer 4 input DTOs (application/edit_view.py -> application/layer4_inputs.py)
```

Imports go only through `application.contracts`. No Layer 4 module imports the `EditPlan` or a
Layer 1-3 producer (`...Analyzer` / `...Planner`); upstream results are read through the owners'
read-only catalogs or handed in memory by the pipeline. Boundary tests
(`tests/architecture/test_<module>_boundaries.py`) enforce the allowlists of `contracts.md` sec. 2.

## 2. Module responsibility map (no overlap)

| Module | Owns | Never does (owner) | Stores | Renders |
| --- | --- | --- | --- | --- |
| `visual_style_engine` | channel identity: palette, fonts, type scale, zones per 16:9/9:16/1:1, caption/title looks, motion tokens, logo rules, format profiles, drift validation | rendering (motion), grading (video_improvement), sound (music) | `visual_style` | - |
| `visual_asset_generation` | still images: selection, frame grabs, cut-outs, stickers/silhouettes/glow/shadow/outline, export, image QC; generation port only | video mattes (vfx), graphics (motion) | `visual_asset_set` | `frame_grab`, `image_adjustment`, `image_enhancement`, `image_export` |
| `thumbnail_design` | YouTube thumbnails: A/B/C variants from the owner's brief, reaction pictures (upload, `reaction` collection, footage moments), feed QC, upload copy | cut-outs/frames (visual_asset_generation), in-video graphics (motion) | none (derived pictures) | `thumbnail_design` |
| `motion_graphics_engine` | building, animating, rendering graphics; executor of caption groups and transition overlays | what/when a caption says (captions), where graphics are needed (editing), identity (style) | `graphics_plan` | `motion_graphic_render` (ProRes 4444, alpha) |
| `caption_engine` | caption mode, selection, segmentation, timing, layout intent, emphasis, emoji, animation requests, SRT/VTT | transcription (audio_intelligence), drawing (motion) | `caption_plan` (+ subtitles inside) | through motion |
| `transition_engine` | transition per boundary (kind, intensity, duration, alignment, handles, fallback) and the audio transition of every cut | cut placement (editing), audio processing (audio_improvement), drawing (motion) | `transition_plan` | overlays through motion |
| `vfx_engine` | shot effects: punch-in, retiming with exact time maps, privacy concealment, looks, footage composites | denoise/grade/stabilise (video_improvement), graphics, transitions, stills | `vfx_plan` | `vfx_render` (ProRes 422 HQ 10-bit) |
| `music_and_sound_design` | sound library inventory and measurements, music/SFX selection and arrangement, silence, ambience preservation, ducking intent, credits, stem | loudness/EQ/mastering/final mix (audio_improvement), transcription | `music_analysis`, `sfx_profile` (per sound), `sound_design_plan` | `sound_stem_render` (WAV f32 48 kHz) |

## 3. Data flow (which document feeds which)

```text
EditPlan + StoryPlan (in memory)          inspection (stored)        --format-profile / --output-format / --sponsored
        \                                      /                                   |
         +------ media_pipeline EditView -----+                           visual_style  ---- StyleRef ----+
                 (edit, or the whole recording)                                                           |
                          |                                                                               |
     GraphicsBrief <------+--- section words -> content.commerce_facts (rank, price, specs)                |
          |                                                                                               |
     graphics_plan --- facts already shown ---> CaptionTimeline -> caption_plan                           |
          |   \--- end screen / plug bookends -> TransitionSequence -> transition_plan                    |
          |                                      (+ continuity, pacing, words read via catalogs)          |
          |                                   VfxShots (+ speech spans, video analysis) -> vfx_plan        |
          |                                                                                               |
          +-- AnimationEvents -+                                                                          |
     transition_plan -- TransitionEvents --+--> SyncEvents -> SoundtrackTimeline -> sound_design_plan     |
     caption_plan -- keyword / emoji -------+     (+ audio timeline, story, pacing in memory)  -> stem      |
                                                                                                          |
     every visual plan names the same StyleRef <----------------------------------------------------------+
```

* `GraphicsBrief`: sections (label, purpose, timeline span, `rank_of(commerce_facts(words))`, product id),
  editorial GRAPHIC requests (concept from the coverage request), `ProductFacts` per section with a price,
  specs or a name, the sponsored flag, the timeline length, the delivery size.
* `CaptionTimeline`: every placement; `audio=True` only for the clips whose sound the edit plays (the
  audio track, else the main picture), so a picture and its linked sound are captioned once.
* `TransitionSequence`: picture clips with `clip_id = placement_id` (the ids continuity judged),
  sections, TRANSITION requests, J/L bridges, bookends from the graphics plan.
* `VfxShots`: picture clips with the transcript segments inside each (never retimed); REFRAME ->
  `emphasis`, MUSIC_CUE -> `reveal` requests.
* `SoundtrackTimeline`: sound clips, sections with beat functions, J/L bridges and `SyncEvent`s:
  graphic arrivals in music's vocabulary (`rank_reveal`, `price_reveal`, `pop_in`, `fly_in`, `counter`,
  `reveal`, `logo_sting`, emphasis -> `highlight`), `TransitionEvent`s (`kind=kind.value`,
  `strength=intensity/4`), emphasised caption words (`keyword`) and drawn emoji (`emoji`), the edit's
  TRANSITION (`section_change`) and MUSIC_CUE (`reveal`) requests. Music decides with restraint which
  events get a sound and records every declined one.

## 4. Time, frames, identifiers

* Three time domains, never mixed and named by prefix: `source_*` (exact seconds on the recording's
  clock, as `SourceRange`), `timeline_*` (seconds from the start of the edit), `local_*` (seconds from
  the start of one graphic, effect or cue). Source -> timeline only through a clip:
  `timeline = clip.timeline_start + (source - clip.source_start)` at speed 1.
* All times are `core.domain.Rational`. The frame rate is the edit's `Timebase.frame_rate` (on the
  no-speech path the inspected rate; none or VFR -> `frame_rate_assumed`, seconds authoritative).
  Frames are computed once at the edge, `round(t * fps)` half-to-even; a duration in frames is
  `end_frame - start_frame`.
* VFX time maps keep a clip's timeline span (no ripple), so captions, music and transitions of the
  same edit stay valid.
* Ids: assets are library ids; clips are edit placement ids (`clip:<asset>` on the no-speech path);
  plan items have stable ids derived from their inputs (`cap:<hash>`, `cut:<out>><in>`), so a person's
  accept/reject survives a replan.

## 5. Asset relationships

Every plan is a derived JSON document of the ANCHOR (first source asset of the edit) via
`DerivedDocuments`, with a top-level `document_type` and the sec. 5 envelope (`plan_id,
plan_version, supersedes, project_id, edit: EditRef | None, style, created_at, state, checks,
inputs, stages, provenance, warnings, limitations, schema_version`). Renders are
`register_derived` assets: graphics, caption groups and transition overlays of the anchor
(`motion_graphic_render`), VFX clips of the treated recording (`vfx_render`), the stem of the anchor
(`sound_stem_render`), stills of their source image or video. The library's derivation record IS
the relationship; nothing else stores links. A cache hit (`created=False`) computes nothing; a new
key makes `plan_version + 1` with `supersedes`. The pipeline's report JSON collects per asset a
`layer4` package: `path` (`edit` | `no_speech`), the `EditRef`, the style (document, fingerprint,
profile, output), every plan document id and every rendered asset id.

## 6. Configuration architecture

* Each module's knobs live in `domain/profile.py` as nested frozen dataclasses; every user-facing
  knob carries `field(metadata={"label", "help", "unit", "min", "max", "step", "choices", "kind",
  "advanced"})` and `problems()` enforces the same ranges (a unit test walks every field), so a future
  settings UI is generated from the metadata. `knob_fields(...)` lists them.
* `<X>Config(profile, overrides: {dotted.path: value}, section_overrides, decisions)`. Precedence,
  each step touching only the keys it names: profile defaults -> channel identity (style / sound
  identity) -> format profile -> project overrides -> section overrides -> per-item person decisions.
  `PROFILE_VERSION` versions the schema; profile values carry `version`. No creative number outside
  `profile.py`.
* The FORMAT is chosen once: the pipeline resolves the style from `--format-profile` (else the story
  format, else the output shape) and `--output-format` / `--output-resolution` / `--sponsored`; each
  module maps the style's format profile to its own (motion `STORY_FORMAT_PROFILE`, transitions
  `STYLE_PROFILE`, vfx `FORMAT_PROFILE`, music: same names, captions read the style) unless an explicit
  `--<module>-profile` is given. The style's fingerprint is in every visual plan's cache key.

## 7. The no-speech path

A meme Short, a versus poll, an uncommented compilation or uncut New Year footage has no speech, so
Layer 3 makes no `EditPlan` (story and edit end `skipped_unavailable`: `nothing_to_plan` /
`nothing_to_edit`). Layer 4 still runs: the pipeline builds the view from the STORED inspection
(one clip of the whole recording, timeline = source from 0 at speed 1, the inspected frame rate,
no sections, beats, effects or bridges, `project_id=None`, `edit=None`) and every module produces a
sensible plan (captions disabled or key information, hard cuts and bookends, music by format, no
editorial graphics unless the profile mandates brand bookends). An edit that was REQUESTED and
FAILED is never replaced by the whole recording: the Layer 4 tasks report `skipped_dependency`
naming it. The difference travels through dependency skips as `TaskResult.cause` (the status at
the root of the chain).

## 8. How to run it

```text
# once (and after the library changes): import and measure the owner's sound library
uv run python scratch/import_sound_library.py --home <home>

# the whole system as one pipeline, raw media -> analyses -> story -> edit -> Layer 4
uv run python scratch/run_pipeline.py --home <home> --input clip.mp4 \
    --stages inspect,audio_analysis,video_analysis,content_analysis,alignment,story,pacing,edit_plan,layer4 \
    --language de --output-format 9:16 --format-profile shorts_haul --report-json out/report.json

# a speech-free clip: no edit plan, Layer 4 from the inspection
uv run python scratch/run_pipeline.py --home <home> --input meme.mp4 --stages layer4 --output-format 9:16

# plans only (no HyperFrames / FFmpeg renders)
uv run python scratch/run_pipeline.py --home <home> --input clip.mp4 --stages edit_plan,layer4 --no-render
```

Renders of graphics and captions need `uv run python scripts/setup_hyperframes.py` once (without it
they are `not_available` items in a stored plan). A second run reports every task `reused`. Tests:
`tests/unit/media_pipeline/test_layer4_tasks.py`, `test_layer4_inputs.py`,
`tests/integration/media_pipeline/test_layer4_pipeline.py` (a synthetic no-speech clip through
every Layer 4 task, then a fully reused second run; plus one real HyperFrames render).

## 9. What is NOT built

* `edit_plan_compiler`, `timeline_builder` and `resolve_adapter` (Layer 5) now exist: the compiler
  turns these plans into one `timeline_specification`, the builder constructs the timeline from it, the
  adapter carries it out in DaVinci Resolve (`docs/layer5/README.md`; the adapter has not yet been run
  against a real Resolve). Still not built:
* `audio_mix_engine`, the final delivery and `quality_control` (whole-video QC). The plans hold every time, frame, asset id and track intent they need.
* Image generation (no model on this machine: `GenerationNotAvailable`), video matting, inpainting,
  screen replacement, lens flares (`not_available`, never faked), emoji drawing (planned, not drawn
  until an emoji asset set exists), brand asset files (logo, mascot, fire plate: reported missing).
* Loudness, EQ, compression and mastering of the final mix stay in `audio_improvement`
  (`AudioMixing` takes the stem as its music asset).
