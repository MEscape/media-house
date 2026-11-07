# Layer 5: timeline construction (architecture)

Layer 5 turns what Layers 1-4 decided into an executable, technically correct video project.
`edit_plan_compiler` (module 24), `timeline_builder` (module 25), `resolve_adapter` (module 26) and
`audio_mix_engine` (module 27) exist.

```text
EditPlan --(media_pipeline/compile_source.py)--> TimelineSource --+
visual_style, graphics, captions, transitions, vfx, sound plans --+--> edit_plan_compiler
Media Library + stored inspection (files) ------------------------+    (pure compile)
ReframingCatalog (crops), StyleValidator (drift) ----------------+          |
                                                                          v
                                                          timeline_specification   (what to build)
                                                                          |
Media Library (files NOW) + ProxyCatalog ---------------------+           v
a person's recorded changes -------------------------------------> timeline_builder
                                                                          |
                                                                          v
                                                          built_timeline   (what a person opens)
                                                                          |
                                                              resolve_adapter  --> DaVinci Resolve project
                                                              ^        |   (a person's fixes are read back and recorded
                                                              +--------+    as the builder's human edits)
                                                  audio_mix_engine --> stems + final mix (library assets)
                                                  (reads the built timeline; needs no Resolve)
```

## 1. Architecture assessment

### What the compiler found (module 24)

* Layer 4 stores plans as versioned JSON documents of the clip (`visual_style`, `graphics_plan`,
  `caption_plan`, `transition_plan`, `vfx_plan`, `sound_design_plan`) with exact `Rational` times,
  frames and library asset ids, and renders as derived assets (ProRes 4444 graphics with alpha, ProRes
  422 HQ VFX clips, a WAV stem). `docs/layer4/contracts.md` made them carry what a timeline needs.
* Every plan carries the `EditRef` it was made for and the style it used (`StyleBinding`,
  fingerprint). The sound plan has its own identity (`mesc_sound`), by design not the visual style.
* `media_pipeline` is the only reader of the `EditPlan` (editing's import allowlist). Layer 4 modules
  each own a narrow input DTO mapped by the pipeline; the compiler follows the same precedent.
* The Media Library is the only store, the derivation record is the only relationship, the document
  fingerprint is the cache. Nothing new is stored elsewhere.

### What the builder found (module 25)

* The compiler's `TimelineSpecification` is the canonical description of WHAT to build: items per
  kind with exact times, frames, samples, asset ids, tracks, transitions with handles, effects,
  crops, mix instructions, a dependency manifest and findings. It is not what a person opens, so
  the builder does not store a second copy of it: it constructs the OTHER representation
  (`BuiltTimeline`: tracks holding ordered clips, a source table, processing chains, transitions
  joining named clips, markers, groups, provenance). One canonical description, one canonical built
  form, each derived from the one before it.
* A domain may import no other module, so the builder owns narrow `SpecFacts` filled by
  `application/views.py` (the same precedent as the compiler and the Layer 4 modules).
* The proxy is a derivative the source never stops being the identity of: the builder asks
  `ProxyCatalog` for the `edit` proxy and records it on the source, never in a clip.
* People edit the result. Nothing in the system modelled that, so the builder owns the
  application-independent half: provenance on every element, tombstones, an edit log, and a
  conflict policy. Resolve-specific change detection and synchronisation stay with `resolve_adapter`.

## 2. Gap analysis

| Needed | Exists | Done |
| --- | --- | --- |
| derivative -> original | library `derivation` + `list_derived` | reused (compiler and builder) |
| source frame rate, VFR, streams | stored inspection | reused, never probes |
| handle availability | transition plan states it, may be `None` | the stricter of the plan and the file's duration |
| **handles of a VFX-baked clip** | the compiler read them from the recording | **fixed in the compiler**: a baked file ends where the clip does, so a dissolve there falls back to a hard cut (found by the builder's own handle check on real fixtures) |
| creator identity version | `ResolvedStyle.identity_id/version`, fingerprint | referenced, never copied |
| drift of what was placed | `StyleValidator` | run on the plans' placements, findings reported |
| crops | `ReframingCatalog` by id | resolved into keyframes; the builder places each on the timeline |
| track / blend intent | not in any plan | the compiler's fixed layout, lanes, alpha |
| which version of a recording to use | nobody chooses | `CompilerConfig.derivative_policy` |
| **colour management** | inspection holds a colour tag per stream; no module set a timeline colour | **compiler extended**: `CompilerConfig.color_profile` -> `Timebase.color`, `AssetUse.color` from the inspection; the builder reports a source tagged otherwise and converts nothing |
| **recording range of a baked clip** | `VideoItem.source_*` is inside the baked file | **compiler extended**: `VideoItem.original_start/end` (the recording's clock), needed to place a crop |
| proxy of a source | `proxy_manager` `ProxyCatalog` | read, recorded on the source table |
| current state of a file | library | looked up again at build time (the specification may be older) |
| edits by people | nothing | `HumanMark`, `Tombstone`, `EditRecord`, `ConflictPolicy` in the builder |
| transition kinds outside cut / dissolve / fade / dip | passed through as native | builder vocabulary; the rest is `unsupported`, kept, reported |
| caption speaker / word timing / styling | baked into the rendered caption clip; the plan has text and look only | not extended (the render carries it); the builder keeps cues + `caption_look` + zone; documented limit |
| nested sequences | the specification has none | not built (it would be a creative choice); sections are markers |
| project ownership of assets | not modelled by the library | limitation |
| running a missing producer | banned by every boundary test | `requested` dependencies + pipeline edges; the builder cannot run the compiler either |

Compiler version 2 carries the two additive fields (older documents still read; a version 1
specification is simply recompiled because `COMPILER_VERSION` is part of its cache key).

## 3. Design

Pipeline: `timeline` (LIGHT) needs `visual_style` + `inspect`, wants the edit, story, reframing and
every Layer 4 plan of the run. `timeline_build` (LIGHT) needs `timeline` and takes its
specification in memory with the library document id; nothing waits for it. A Layer 4 task of the
run that failed makes `timeline` `failed`, which makes `timeline_build` skipped. The report's per-asset
`timeline` and `built_timeline` packages list the document ids, hashes, counts and findings.

The built timeline, in the order a build works:

1. **Preflight** (`domain/preflight.py`): schema and vocabulary versions it can read, every word it
   knows, every reference, files that left the library or changed since compilation, critical
   dependencies a placed clip plays, the expected creator identity. All reasons, not the first.
2. **Construct** (`domain/construct.py`, pure mapping): source table, tracks, picture clips with
   their chain, overlays, sound clips, mix instructions, transitions (mode, handle frames), subtitle
   cues. Each element is carried over from the previous build when its digest is unchanged, kept as
   a person left it, or rebuilt (`domain/reconcile.py`).
3. **Organise**: names a person can read (`Hook · rec1.mov @ 00:00:20.000`), track groups, sections,
   markers, picture+sound and transition groups.
4. **Validate** (`domain/validation.py`) the finished timeline: structure, timing, transitions,
   media and derivatives, picture, sound, identity, execution support, and consistency with the
   specification. Findings are made here on the elements, so a reused element is judged like a new one.
5. **Finish**: severity-ordered findings, status, change set, content hash.

Determinism: the same specification, files, configuration and work by people give the same
`build_hash`; no clock, no run id, no device in any key.

## 3b. The Resolve adapter (module 26)

What it found, and what it does about it. Full contract: `src/media_house/modules/resolve_adapter/README.md`.

* **No Resolve was available** on the machine it was built on. The adapter is written from the
  published scripting documentation and tested end to end against a stand-in of the documented object
  tree (the real composition root, the real library, builder and ffmpeg; only Resolve is simulated).
  Every capability is `not_verified` until a live run reads it back; the first real run is the test.
* **Resolve's scripting API cannot do everything a timeline needs**: no transitions, no retime, no
  native effects, no keyframed clip properties (a moving crop), no clip gain/fades, no font list. These
  are registry entries (`domain/capabilities.py`), reported wherever a timeline element needs them with
  their alternatives, and turned into a marker where one step in Resolve finishes the job. Nothing is
  substituted.
* **People work in Resolve.** That is the point of using it: a wrong word, a transition, a nudged clip
  is fixed there. The adapter tags every clip it places, stores per element the state it wrote and read
  back, and compares three states (last synchronised / Media House now / Resolve now) per field. A
  person's change is kept and read back into Media House as the builder's own `HumanEdit`, so the next
  pipeline run does not rebuild over it. Both sides changing one field is settled by the execution
  policy (default: keep the person's AND leave it open, which blocks rendering).
* **Retry is state-based.** What Resolve holds decides what is left to do; an interrupted run is
  resumed from checkpoints, not repeated.
* **Output is inspected**, never trusted from the job status: the render is registered as a derived
  asset and compared with the timeline by `media_inspection`.

Gap table additions:

| Needed | Exists | Done |
| --- | --- | --- |
| execute the timeline in an editing application | nothing | `resolve_adapter` (one port, one scripting adapter) |
| link between a Media House timeline and a Resolve project | nothing | `resolve_binding` document; Resolve ids as external references |
| a person's changes in the application | the builder's `HumanEdit` (clips) | `SyncFromResolve` records them; what the builder cannot hold stays in Resolve |
| strict JSON codec, 3rd copy | in the compiler, builder, transition engine | hoisted to `core.domain.json_codec`; compiler and builder migrated (transition engine still has its own) |
| pipeline task | none | `resolve_execute`, opt-in, `skipped_unavailable` without Resolve |

## 3c. The audio mix engine (module 27)

Full contract: `src/media_house/modules/audio_mix_engine/README.md`.

* **The intent already exists, upstream.** The compiler carries gain, fades, speed, `duck`, `crossfade`
  and `music_tail` per sound; the builder places every sound in exact samples. A second
  `audio_mix_specification` out of the compiler would duplicate that and split the source of truth, so
  the engine derives its specification from the BUILT timeline plus a mix profile and a delivery profile
  (the technical behaviour no upstream module owns). The compiler is unchanged.
* **Nothing mixed before.** `audio_improvement.AudioMixing` ducks one music file under one voice;
  `SoundStemRenderer` is an unmastered music+effects stem. Neither is a multi-bus mix with loudness
  targets, stems with provenance and checks. Both stay as they are.
* **Stems are the cache.** Each bus is rendered to a stem keyed by a hash of everything that can change
  it; the final mix by the stem hashes, master and delivery. A dialogue change renders the dialogue and
  the mix, not the music; a loudness target renders only the mix.
* **Independent of Resolve.** The engine and its task need only `timeline_build`. Its outputs are
  library assets; `resolve_adapter` importing them is the open follow-up.
* **Judged, not assumed.** Loudness, true peak, clipping, format, duration, stem alignment, music over
  dialogue under speech, silence and stem recombination are measured; each finding states its severity
  and how sure it is.

| Needed | Exists | Done |
| --- | --- | --- |
| multi-bus mix, stems, final render | nothing | `audio_mix_engine` |
| mix specification | intent in the compiler (per sound) | built in the engine from the built timeline + profiles |
| loudness measurement | private `ebur128` parsing in three modules | a fourth in the backend (no module exposes a contract) |
| dialogue-aware ducking | the sound design's ducking intent | used as given; a profile fallback from the voice's place |
| importing the mix into Resolve | nothing | NOT done (outputs are library assets) |

## 4. Shared files edited (and why)

* Compiler (genuine gaps, additive): colour management, `original_start/end`, handle of a baked clip.
* `tests/architecture/test_editing_intelligence_boundaries.py`: the packages of the two built
  modules no longer reserve a name; editing still never depends on them.
* `tests/architecture/test_edit_plan_compiler_boundaries.py`: the builder is an allowed consumer of
  the compiler's contract (with the pipeline and the composition root).
* Consumer allowlists of the Layer 4 modules for the compiler (module 24, earlier).
* `bootstrap/modules.py`, `tests/integration/test_application_lifecycle.py`, `media_pipeline/**`
  (tasks `timeline_build` and `resolve_execute`, executors, report packages, wiring),
  `scratch/run_pipeline.py`, `scratch/resolve_sync.py`.
* `core/domain/json_codec.py` (new, pure): the strict JSON codec and digests; `edit_plan_compiler` and
  `timeline_builder` use it instead of their own copies.
* `tests/architecture/test_timeline_builder_boundaries.py`: the adapter is an allowed consumer.
* `bootstrap/modules.py`, `media_pipeline/**` (task `audio_mix`: stage table, executor, report package,
  wiring), `scratch/run_pipeline.py` (`--mix-profile`, `--mix-delivery`), the lifecycle test.
* `tests/architecture/test_editing_intelligence_boundaries.py` already lists the built modules.

## 5. How to run it

```text
uv run python scratch/run_pipeline.py --home H --input clip.mp4 \
    --stages edit_plan,layer4,timeline_build --output-format 16:9 --report-json out/report.json
uv run python scratch/run_pipeline.py --home H --input meme.mp4 --stages layer4,timeline_build --no-render
uv run python scratch/run_pipeline.py --home H --input clip.mp4 --stages layer4,resolve_execute --no-render
uv run python scratch/resolve_sync.py --home H --anchor <recording asset id>      # read fixes back
uv run python scratch/run_pipeline.py --home H --input clip.mp4 --stages timeline_build \
    --timeline-conflict-policy preserve --timeline-fit cover
```

Tests: `tests/unit/audio_mix_engine`, `tests/integration/audio_mix_engine`,
`tests/architecture/test_audio_mix_engine_boundaries.py`, `tests/unit/media_pipeline/test_audio_mix_task.py`,
`tests/unit/resolve_adapter`, `tests/integration/resolve_adapter`,
`tests/architecture/test_resolve_adapter_boundaries.py`, `tests/unit/media_pipeline/test_resolve_execute_task.py`,
`tests/unit/edit_plan_compiler`, `tests/unit/timeline_builder`,
`tests/unit/media_pipeline/test_timeline_task.py`, `test_timeline_build_task.py`,
`tests/architecture/test_edit_plan_compiler_boundaries.py`, `test_timeline_builder_boundaries.py`,
`tests/integration/media_pipeline/test_timeline_pipeline.py`, `test_timeline_build_pipeline.py`.

## 6. What is NOT built

* Final validation and delivery: Layer 6 (`docs/layer6/README.md`).
* Importing the mix and stems into Resolve (`resolve_adapter` extension), dynamic EQ / multiband /
  gate / de-esser, stereo width, multichannel, ambience-bed management, speech-aware ducking from
  `audio_intelligence` (a hook exists), and any listening evaluation of a real mix.
* Running `resolve_adapter` against a real DaVinci Resolve (see 3b), a timeline interchange import
  (FCPXML / OTIO) that would carry transitions, and any Resolve operation the scripting API lacks.
* Checks that need a render (black frames, missing frames, silence): the interface exists
  (`ExternalCheck`), nothing produces the results yet.
* Validation on a long edit with speech, rendered graphics, VFX and a sound library together, and
  against a real timeline application.
