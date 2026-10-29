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
uv run python scratch/bench_pipeline.py --media DIR --work DIR         # cold/warm/changed/sequential/...
uv run pytest tests/unit/media_pipeline tests/integration/media_pipeline tests/architecture
```

`pipeline.toml` keys: `stages`, `content_uses_video`, `cpu_only`, `gpu_slots`, `cpu_slots`,
`light_slots`, `video_analysis_profile`, `proxy_profile`, `audio_improvement_profile`,
`video_source_profile`, `video_processing_profile`, `story_format`, `story_llm`, `edit_profile`, `edit_target_seconds`, `edit_target_hard` (unknown keys are errors; the command line
wins). Ctrl+C once finishes cleanly, twice stops now. The report is written atomically.

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

## Next steps

1. Owner `peek` for the analyses (exact stored-result check) so every hit skips the lane queue.
2. A long-lived worker (or model server) so speech models load once per session, not per process.
3. Resource sampling in the runner (psutil / NVML) once a lane policy needs measurements.
4. Run analyses on an improved variant on request (provenance already records the variant).
5. Preset layer (project / editorial / export profiles) as validated settings feeding
   `StageSettings` and `RunPipelineCommand`; no editorial rules belong in this module.
6. Relationship detectors as a task once `asset_relationships` slice 4 exists.
