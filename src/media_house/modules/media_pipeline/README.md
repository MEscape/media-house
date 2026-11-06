# media_pipeline

Orchestrates the other modules as one dependency graph. It computes no media result and stores
nothing: every result is produced by its owner and kept in the Media Library, so running the same
input again reports `reused`. Public API: `application/contracts.py` (`MediaPipeline`,
`RunPipelineCommand`, `StageSettings`, `PipelineReport`).

## Ownership (audited from the code, not the names)

| Result | Owner (contract) | Reuse mechanism already in the owner |
| --- | --- | --- |
| asset identity, thumbnails, every derived file and JSON document | `media_library` | checksum identity, `fingerprint` / `find_derived_asset` |
| technical facts, integrity verdict | `media_inspection` | stored inspection document |
| proxies | `proxy_manager` | spec fingerprint = library fingerprint; `reused` flag; `peek` |
| improved audio / video | `audio_improvement` / `video_improvement` | derived asset by fingerprint; `created` flag |
| prepared audio, transcript, acoustic timeline | `audio_intelligence` | per-layer derived documents; speech/alignment models cached per process |
| shots, tracks, quality, per-analyzer signals | `video_intelligence` | one FFmpeg decode per asset, per-analyzer signal cache |
| topics, key items, scores | `content_intelligence` | derived document; accepts upstream results as given |
| speech-to-picture relations | `audio_visual_alignment` | derived document; accepts upstream results as given |
| story plan (order, openings, priorities, issues) | `story_architecture` | derived document keyed by every input; accepts upstream results as given; a changed key adds a plan version |
| edit plan (keep/cut/shorten/cover decisions as exact source ranges on a neutral timeline) | `editing_intelligence` | derived document keyed by the story's facts, every analysis, configuration and overrides; opt-in `edit_plan` task; a changed key adds a plan version |
| pacing analysis (rhythm findings and recommendations per scale) | `pacing_intelligence` | derived document keyed by every input identity, the story's facts, profile, strategy and target; default-on `pacing` task (a pure read); a changed key adds a version |
| B-roll analysis (footage of the whole library for the passages that need it, gaps) | `broll_intelligence` | derived document keyed by the passages, the searched assets' indexes, encoders, scope and configuration; opt-in `broll` task (a library-wide read); a changed key adds a version |
| framing recommendations (crop, keyframes, feasibility per shot for one target format) | `reframing_intelligence` | derived document keyed by every shot's evidence, the target format and resolution, the configuration and safe areas; opt-in `reframing` task (a pure read, skipped until a target is given); unchanged shots are carried over |
| continuity analysis (issues at the cuts of a proposed edit, scores, repairs) | `continuity_intelligence` | derived document keyed by every cut's fingerprint, the plan, decisions and configuration; opt-in `continuity` task (a pure read); unchanged cuts are carried over |
| relationship claims | `asset_relationships` | opt-in `relationships` task: `RelationshipDetection.analyze` from the STORED inspection (never probes); marks make a repeat a no-op |

There is deliberately no pipeline cache or artifact registry: the library already is the single
store, and a second one would be a competing source of truth. Cache validity (source identity,
configuration, model and schema versions) is each owner's; the pipeline does not second-guess it.

## Task graph

Stage table: `domain/plan.py` (the only place that says what depends on what). `needs` = cannot run
without it; `wants` = waits for it but runs whatever it did.

```text
inspect (joins every run)   thumbnail                       (light lane)
  | wants
  +-> proxy, audio_improvement, video_improvement           (cpu)
  +-> audio_analysis (gpu)      video_analysis (cpu)        independent of each other
          | needs                      |
      content_analysis (gpu)           |
          | wants                      |
          +-----> alignment (light) <--+ needs audio_analysis + video_analysis
          |                  |
          +-> story (light) <+   needs content_analysis, wants alignment
                |
          pacing (light)   needs audio_analysis, wants content_analysis, video_analysis, alignment,
                |          story, inspect (nothing waits for it except edit_plan)
          broll (light, opt-in)   needs content_analysis, wants story, alignment, audio_analysis,
                |         video_analysis, pacing, inspect
          reframing (light, opt-in)   needs video_analysis, wants story, alignment, inspect
                |                     (skipped until --reframe-target names a format)
          edit_plan (light, opt-in)   needs story, wants alignment, video_analysis, inspection, pacing, broll,
                |                     reframing
                |
          continuity (light, opt-in)  needs edit_plan, wants broll, alignment, audio_analysis,
                                      video_analysis, inspect

Layer 4 (all opt-in; `--stages layer4` = all six). Each WANTS edit_plan (the edit, or else the
whole recording: the no-speech path) and every one but the style NEEDS visual_style + inspect.

          visual_style (light)   wants edit_plan, inspect             (the ONE format choice)
                |
          graphics (cpu)         wants edit_plan, story, audio_analysis
                |
          captions (cpu)         wants graphics, edit_plan, story, audio_analysis, content_analysis,
                |                video_analysis, reframing             (needs audio + picture)
          transitions (cpu)      wants graphics, edit_plan, story, continuity, pacing, audio_analysis,
                |                video_analysis
          vfx (cpu)              wants edit_plan, audio_analysis, video_analysis, reframing
                |
          sound_design (cpu)     wants graphics, transitions, captions, edit_plan, story, pacing,
                                 audio_analysis                        (no picture needed)
```

* Inspection is the only authority on which streams exist, so it is always in the graph. Audio
  stages skip with `skipped_unavailable` ONLY when a usable inspection lists no audio stream. A
  missing, failed or damaged inspection proves nothing: the owner decides, and an unreadable audio
  stream is a `failed` task, not "no audio".
* An inspection with an `error` verdict (damaged, truncated, unreadable media) FAILS the inspection
  task with the findings as warnings, so the run is not `ok`. Tasks that only `want` it still run.
* Audio and video analysis start together; neither waits for the other or for improvement.
* Analyses run on the ORIGINAL. Improvement produces new assets nothing downstream reads, so it
  never delays analysis. Analysing an improved variant is a later, explicit choice.
* `content_analysis` waits for `video_analysis` only with `--content-uses-video`; by default it
  would sit behind a long video decode for optional context.
* `alignment` runs with `Acquire.STORED` and the in-memory upstream results.
* `relationships` (opt-in, not in the default stages) wants only the inspection and nothing waits for it. It proposes
  claims for this asset against the assets already indexed; it is `reused` when nothing changed, `skipped_unavailable`
  when no inspection is stored, `partial` when the candidate cap was hit.
* `story` runs after `content_analysis` (and after `alignment` when that is in the graph) with the in-memory
  results, `Acquire.STORED` (it never asks the planner to analyse) and no language model unless `--story-llm`.
  It is `partial` only when one of its stages failed or a model that was asked for did not deliver; the missing
  picture side of an audio-only recording is not a degradation. Audio-only media get a story and no alignment.
* `pacing` (default-on) reads the stored analyses and the story plan of the run and measures rhythm; it needs only
  `audio_analysis` and waits for the other analyses and the story when they are in the graph. It never plans a story
  and asks no producer for anything; it is `partial` when the analysis is (an input was missing), `reused` when the
  identical analysis is stored, and absent for media without audio. `--pacing-profile` and `--pacing-strategy` choose
  its profile and strategy; `--edit-target` also sets the target length it ranks against. It is on by default because
  it only reads and decides nothing, so a default run stores one small document at most.
* `broll` (opt-in, `--stages broll`, `--broll-profile`) finds supplementary footage for the passages that need it:
  it needs `content_analysis`, takes the run's story, pacing, alignment and analyses as given, and searches the
  library's STORED shot indexes (assets this run did not analyse need their own video analysis). It is opt-in because
  it reads every analysed asset of the library, and a recommendation means something only when someone looks for
  footage. It is `partial` when part of the library or a search model could not be used, `reused` on an identical key,
  and `skipped_unavailable` when no passage asks for footage. `edit_plan` waits for it and receives its analysis.
* `edit_plan` (opt-in, `--stages edit_plan`) runs after `story` with the in-memory story plan and analyses and
  `Acquire.STORED`. It is not in the default stages: it is a decision that needs a chosen profile (`--edit-profile`) and
  target length (`--edit-target`, `--edit-target-hard`), and the default run stores nothing nobody asked for. It is
  `partial` when a hard constraint (a hard target) cannot be met without removing essential material. It plans the
  story of ONE recording (the pipeline is per asset); a story of several recordings goes through `EditPlanner`.
* `reframing` (opt-in, `--stages reframing --reframe-target 9:16`, `--reframe-resolution WxH`, `--reframe-profile`,
  `--reframe-safe-area NAME=x0,y0,x1,y1` repeatable) recommends a crop per shot for ONE target format: light lane, needs
  `video_analysis`, wants story, alignment and inspection. It is skipped (with the reason) until a target is chosen, since a crop
  means nothing without a format. `edit_plan` wants it and is handed its payload (and the target), so within one run the editor
  attaches its references to the clips; it does not want the continuity analysis (that runs after the plan) and reads a stored one.
* `continuity` (opt-in, `--stages continuity`, `--continuity-profile`, `--continuity-strictness`) judges the cuts of the
  run's edit plan: `application/sequence.py` maps the plan to continuity's own `ProposedSequence` (source ranges, which
  cuts are covered, audio bridges, trimmed speech) and the analyzer reads the stored analyses. It needs `edit_plan` and
  runs after it, so it is not an input of the plan it judges; the NEXT `edit_plan` run reads the stored result. It is
  opt-in because it needs a chosen edit. It is `partial` when evidence was missing for some cuts, `reused` on an
  identical key, and `skipped_unavailable` when the plan has nothing to judge (`nothing_to_judge`, fewer than two
  clips).
* **Layer 4** (`visual_style`, `graphics`, `captions`, `transitions`, `vfx`, `sound_design`; opt-in, see
  `docs/layer4/README.md`). Nothing before `edit_plan` waits for them, so there is no cycle; they never
  run analyses (wanted analyses are waited for when in the graph and handed over in memory).
  * **One edit view.** `application/edit_view.py` maps the `EditPlan` (+ the story plan for beat functions and
    section labels) ONCE into a neutral `EditView`; `application/layer4_inputs.py` maps that view into each
    module's own DTO (`GraphicsBrief`, `CaptionTimeline`, `TransitionSequence`, `VfxShots`,
    `SoundtrackTimeline`). Clip ids are placement ids (the ids continuity's `ProposedSequence` uses), times stay
    exact `Rational`s, the frame rate is the edit's timebase, sections span their placements.
  * **No-speech path.** Without an edit plan (no `edit_plan` in the run, or it was `skipped_unavailable`
    because the recording has no speech: the root cause travels as `TaskResult.cause` through dependency
    skips) the view is the whole recording from the STORED inspection: one clip `clip:<asset>`, timeline =
    source, the inspected frame rate (VFR or none = `frame_rate_assumed`), `edit=None`, no sections. The
    task's detail says why. An edit that was asked for and FAILED (or was cancelled, or a failure upstream
    stopped it) is NOT replaced by the whole recording: every Layer 4 task is `skipped_dependency` naming it.
  * **One format choice.** `visual_style` resolves the channel style from `--format-profile`, `--sponsored`,
    the delivery shape (`--output-format`, else `--reframe-target`) and size (`--output-resolution`), the
    edit's project and story format; its `StyleRef` goes to every other task (same fingerprint in every
    plan). Each module maps the style's format profile to its own profile (`graphics`: motion's
    `STORY_FORMAT_PROFILE`, `transitions`: `STYLE_PROFILE`, `vfx`: `FORMAT_PROFILE`, `sound_design`: the same
    name, `captions`: the caption engine reads the style itself) unless `--<module>-profile` is given.
  * `graphics` reads ranks, prices and specs from the words heard in each section (content's
    `commerce_facts` over the transcript through the section's sound clips) and passes the editorial GRAPHIC
    requests; `captions` follows `graphics` (facts shown by a graphic are not captioned again); `transitions`
    gets the TRANSITION requests, J/L bridges and the end-screen / membership-plug bookends the graphics plan
    placed; `vfx` gets REFRAME (emphasis) and MUSIC_CUE (reveal) requests and the transcript segments of each
    clip (speech is never retimed); `sound_design` gets the sound clips (audio track, else the main picture),
    sections, bridges and every visual moment as a `SyncEvent` (graphic arrivals in music's vocabulary,
    `TransitionEvent`s with `kind=kind.value, strength=intensity/4`, emphasised caption words and drawn
    emoji, the edit's TRANSITION / MUSIC_CUE requests); it reads speech, loud moments and pacing cues itself
    from the analyses handed in memory, then renders the stem when music or effects were chosen.
  * Renders (`--no-render` = plans only): graphics and captions through HyperFrames (ProRes 4444 with alpha),
    transition overlays, VFX clips (ProRes 422 HQ), the sound stem (WAV f32). A missing renderer gives
    `not_available` items inside a stored plan, never a failed task.
  * Every Layer 4 task reports the same facts (`plan_asset_id`, `document_type`, `plan_version`, `state`,
    `path`, `anchor_asset_id`, `edit_plan_id`, `edit_plan_version`, `style_asset_id`, `style_fingerprint`,
    `rendered_asset_ids`); `AssetReport.layer4_package()` and the report JSON's per-asset `layer4` section
    collect them for a future timeline builder. There is no separate visual-asset task: product stickers and
    silhouettes are requested by motion graphics through `VisualAssetPreparer`, and B-roll gap images are a
    library selection (`ImageSelector.for_gaps`) a person chooses from; a task would store nothing new.
* **Layer 5** (`timeline`; opt-in, `--stages timeline`, see `docs/layer5/README.md`). LIGHT lane; needs
  `visual_style` + `inspect`, wants `edit_plan`, `story`, `reframing` and every Layer 4 plan of the run;
  nothing waits for it. `application/compile_source.py` maps the `EditPlan` (or the whole recording) into
  the compiler's own `TimelineSource`. The plans of the run are handed over in memory (the stored ones fill
  in the plans the run did not make, when they belong to this edit and style). A Layer 4 task of the run that
  failed or was skipped for a dependency is handed over as `undelivered` and blocks the timeline
  (`failed`); an unavailable one (no audio) is not a failure. `--timeline-derivatives prefer_improved`
  points clips at the stored improved audio / video. Facts: `plan_asset_id`, `timeline_id`, `spec_hash`,
  item counts, `warning_items`, `conflicts`, `auto_resolved`, `units`, `units_reused`; the report JSON's
  per-asset `timeline` section (`AssetReport.timeline_package()`).
* **Layer 5 build** (`timeline_build`; opt-in, `--stages timeline_build`, implies `timeline`). LIGHT lane; NEEDS the `timeline`
  task and takes its specification in memory with the library document id (`plan_asset_id`), so it never builds an old
  specification: a `timeline` that failed or was skipped makes it skipped. `timeline_build` constructs the application-
  independent timeline (`timeline_builder`); a person's earlier changes to the stored build survive a rebuild through the
  conflict policy (`--timeline-conflict-policy preserve|replace|review`, default review), `--timeline-fit` says how a source
  of another shape meets the frame, `--timeline-full-rebuild` rebuilds every element. A partial specification or a timeline
  with unresolved, unsupported or review items is `partial`. Facts: `plan_asset_id`, `timeline_id`, `build_hash`, `mode`,
  track/clip/transition/marker counts, `unresolved`, `unsupported`, `reviews`, `elements_reused`; the report has a per-asset
  `built_timeline` section (`AssetReport.build_package()`). Nothing waits for it.
* **Layer 5 audio mix** (`audio_mix`; opt-in, in no group, `--stages audio_mix`, implies `timeline_build`). CPU lane (FFmpeg
  renders the stems and the mix); NEEDS `timeline_build` only (no Resolve). `audio_mix_engine` makes the specification, one
  stem per bus and the final mix, measures them against the delivery (`--mix-delivery youtube|shorts|social|broadcast_r128`,
  default from the creator identity's format profile) and judges it (`--mix-profile`). A timeline with no sound is
  `skipped_unavailable`; errors against the delivery make it `partial`; a render failure is `failed`; an unchanged mix (every
  stem and the mix found by hash) is `reused`. Facts: `specification_asset_id`, `result_asset_id`, `mix_asset_id`, `state`,
  `profile`, `delivery`, `integrated_lufs`, `true_peak_dbtp`, `master_trim_db`, `stems`, `stems_reused`, `errors`, `warnings`;
  the report has a per-asset `audio_mix` section (`AssetReport.mix_package()`). Nothing waits for it.
* **Layer 5 Resolve** (`resolve_execute`; opt-in, in no group, `--stages resolve_execute`, implies `timeline_build`). LIGHT
  lane; NEEDS `timeline_build` and takes the built timeline in memory. `resolve_adapter` carries it out in DaVinci Resolve and
  reads it back. Resolve not installed / not running / not reachable is `skipped_unavailable` (nothing failed, nothing was
  done); a failed, interrupted or cancelled execution is `failed`; instructions Resolve's scripting cannot carry out (transitions,
  retime, native effects, moving crops) or an open conflict make it `partial`. `--resolve-policy`, `--resolve-project`,
  `--resolve-adopt-project`, `--resolve-allow-switch`, `--resolve-captions`, `--resolve-lib`, `--resolve-api`;
  `--resolve-render youtube|master` renders and checks the file (only after a timeline without open work). Facts:
  `report_asset_id`, `outcome`, `state`, `project`, `resolve_version`, `executed`, `partial`, `unsupported`, `failed`,
  `verified`, `open_conflicts`, `human_modified`, `render_*`; the report has a per-asset `resolve` section
  (`AssetReport.resolve_package()`). A person's fixes made in Resolve are read back with `scratch/resolve_sync.py`.
* Requesting a stage pulls in what it needs (`implied` in the report). A stage the media cannot
  support (picture stages on audio-only media) is `skipped_unavailable`, not a failure.

## Scheduling

* Lanes bound heavy work, they do not serialise independent work: `light=4`, `cpu=2`, `gpu=1`
  (`--light-slots`, `--cpu-slots`, `--gpu-slots`), plus `--max-workers N` for a cap across lanes
  (`1` = strictly one task at a time, the dependency-respecting sequential baseline).
* Ready tasks start longest-waiting-chain first, as soon as a slot is free.
* **Pre-flight cache check.** Owners with an exact, side-effect-free reuse check are asked before a
  task queues (inspection, thumbnail, `ProxyManager.peek`) in a separate bounded pool (2 slots). A
  hit finishes as `reused` (`cache: preflight_hit`) without taking a lane slot. A `None` answer
  means "ask `execute`", never "absent"; an exception in a check is logged and ignored. The
  analyses, improvements, content and alignment have no such check in their owners yet, so their
  hits still pass through a slot (`cache: owner_hit`); they are fast (0.2 to 1 s) but can queue.
* **Single flight.** Two runs in one process asking for the same task on the same asset compute it
  once; the other waits and reports `reused`. A failed or cancelled leader is not inherited: the
  waiter retries by itself. Across processes the owners' own coordination applies.
* Failed `needs` skip dependents (`skipped_dependency`, with the reason); a failed `wants` blocks
  nothing. Cancellation stops new starts and marks the rest `cancelled`. Task exceptions are logged
  with traceback and become `failed` tasks; the run continues. A fuzz test (25 random DAGs with
  random failures and tiny lanes) checks every task ends with a consistent final status.

Statuses: `succeeded` (computed), `reused`, `partial`, `failed`, `skipped_disabled`,
`skipped_dependency`, `skipped_unavailable`, `cancelled`. A cache hit is never `succeeded`.
A run is `ok` when nothing failed, no input failed and nothing was cancelled; `partial` and skips
are not failures. Exit codes of the runner: 0 ok, 1 failure, 2 usage, 130 cancelled.

## Run it

```text
uv run python scratch/run_pipeline.py --input clip.mp4                 # default analysis stages
uv run python scratch/run_pipeline.py --input footage_dir --dry-run    # graph only; imports nothing
uv run python scratch/run_pipeline.py --input clip.mp4 --all --output out/ -v
uv run python scratch/run_pipeline.py --input clip.mp4 --config pipeline.toml --cpu-only
uv run python scratch/run_pipeline.py --asset-id <library id> --stages video_analysis
uv run python scratch/import_sound_library.py [--manifest M] [--home H]   # library for sound_design
uv run python scratch/run_pipeline.py --input clip.mp4 --stages edit_plan,layer4 --output-format 9:16
    --format-profile shorts_haul --report-json out/report.json      # (one line) the Layer 4 package
uv run python scratch/bench_pipeline.py --media DIR --work DIR         # cold/warm/changed/sequential/...
uv run pytest tests/unit/media_pipeline tests/integration/media_pipeline tests/architecture
```

`pipeline.toml` keys: `stages`, `content_uses_video`, `cpu_only`, `gpu_slots`, `cpu_slots`,
`light_slots`, `video_analysis_profile`, `proxy_profile`, `audio_improvement_profile`,
`video_source_profile`, `video_processing_profile`, `story_format`, `story_llm`, `edit_profile`, `edit_target_seconds`,
`edit_target_hard`, the B-roll / continuity / reframing keys, `transcription_language`, and for Layer 4
`output_format`, `output_resolution` (`"WxH"`), `format_profile`, `sponsored`, `graphics_profile`, `caption_profile`,
`transition_profile`, `vfx_profile`, `sound_profile`, `layer4_render` (unknown keys are errors; the command line
wins). Ctrl+C once finishes cleanly, twice stops now. The report is written atomically.

Layer 4 flags: `--stages layer4` (all six), `--output-format 16:9|9:16|1:1`, `--output-resolution WxH`,
`--format-profile`, `--sponsored`, `--graphics-profile`, `--caption-profile`, `--transition-profile`,
`--vfx-profile`, `--sound-profile`, `--no-render`. Report schema 2 adds `assets[].layer4` (null when no Layer 4
stage was asked for): `anchor_asset_id`, `path` (`edit` | `no_speech`), `edit` (`edit_plan_id`, `plan_version`, or
null), `style` (`plan_asset_id`, `fingerprint`, `profile`, `identity_version`, `output`), `plans` (per task:
`plan_asset_id`, `document_type`, `plan_version`, `state`, `profile`), `rendered` (per task: derived asset ids),
`not_made` (per requested task that stored nothing: `status`, `detail`).

Not offered, on purpose: `--force` / `--no-cache`. The owners expose no common way to recompute, so
a flag could only pretend; use a fresh `--home`. `--cpu-only` sets the device of speech
recognition, video analysis and the sentence embedding to CPU (verified in the model-load log:
`device: cpu` instead of `cuda`); GPU use is not otherwise measured.

Video-analysis models are optional: without the `vision` extra or the face model file, those
analyzers report `not_available` and the task is `partial` (never fabricated).

## Measured (GTX 1070 8 GB, 12 cores; 3 clips: two 16 s speech clips + one 6 s silent video)

Same files and stages in every row (default analysis stages, original media). Numbers are the
graph run (`wall`), seconds. They are single runs and **vary a lot**: the first scenario of any
process pays for reading the speech model from disk (about 3 GB), whatever its mode.

| Scenario | wall | notes |
| --- | --- | --- |
| cold, concurrent (3 runs) | 176, 116, 55 | first run of a session slowest |
| sequential (`--max-workers 1`, 3 runs) | 124, 154, 80 | |
| warm, nothing changed (3 runs) | 1.5, 1.6, 1.7 | 12 reused: 6 pre-flight, 6 owner hits; 0 model loads |
| one clip changed | 14.9 | 2 recomputed, 10 reused; 0 model loads |
| content/alignment off | 76 | 5 computed |
| `--cpu-only` | 204 | speech on CPU 76 and 126 s |

Fair pairs (same position in a session, so the same file-cache state): second runs 55 s concurrent
vs 80 s sequential, about 1.45x faster; first runs 116 s concurrent vs 154 s sequential, but the
very first cold run (176 s) was slower than that sequential run, so with n = 3 the claim is only
"concurrent is usually faster, never reliably so under a cold disk". Speech and alignment models
were loaded once for two speech clips in one process (log count), so they are reused across assets.
Across processes every run reloads them (about 75 s here): a long-lived worker would remove that;
not built.

## Known limitations

* Analyses, improvements, content and alignment hits still queue for a lane slot (no owner peek).
* Two runs racing on a thumbnail may both report it computed (the library cannot say who created
  it); only one asset is stored.
* `video_analysis` is on the cpu lane; its model analyzers may also use the GPU. With the `vision`
  extra on an 8 GB card, watch VRAM (not measured) or use `--cpu-slots 1`.
* No retries (owners store partial results per layer, so a rerun resumes). No resource sampling:
  CPU, RAM, GPU and VRAM utilisation are not measured because nothing reports them.
* Damaged media: the inspection says so and fails its task, but tasks that only `want` it (proxy,
  improvement) still run on the damaged file and may succeed; their output is not evidence of health.
* Validated end to end only on synthetic picture and SAPI-synthesised speech. The two transcripts
  (16 s and 15 s) matched the spoken scripts word for word, with 34 and 33 words timed inside the
  media duration (read back through `AudioCatalog`). Real voices, noise, several speakers, HDR,
  multi-camera, long footage and the vision models are not validated.
* Layer 4 is validated end to end on a synthetic speech-free clip only (no-speech path: every plan stored,
  one style fingerprint, a real sound stem from a synthesised library, a fully reused second run, one real
  HyperFrames render); the edit path is covered by mapper and executor unit tests on synthetic edit plans.
  Layer 4 tasks are per asset: an edit of several recordings goes through the owners' contracts directly.
* Without `edit_plan` in the run, Layer 4 plans the whole recording even if an edit plan is stored from an
  earlier run (Layer 4 reads the edit of THIS run); add `edit_plan` (cheap, reused) to plan the edit.

## Next steps

1. Owner `peek` for the analyses (exact stored-result check) so every hit skips the lane queue.
2. A long-lived worker (or model server) so speech models load once per session, not per process.
3. Resource sampling in the runner (psutil / NVML) once a lane policy needs measurements.
4. Run analyses on an improved variant on request (provenance already records the variant).
5. Preset layer (project / editorial / export profiles) as validated settings feeding
   `StageSettings` and `RunPipelineCommand`; no editorial rules belong in this module.
6. Relationship detectors as a task once `asset_relationships` slice 4 exists.
