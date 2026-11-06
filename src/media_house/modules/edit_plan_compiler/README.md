# edit_plan_compiler (Layer 5, module 24)

The bridge between creative planning and timeline construction. It turns the edit and the stored
Layer 4 plans (channel style, graphics, captions, transitions, VFX, sound design) into ONE
validated, versioned `timeline_specification`: exact times and frames, tracks with explicit
stacking, every file traced to its recording, transitions with their handles, effects, reframing
crops, audio items and instructions, a dependency manifest and every finding.

It decides nothing creative. A contradiction between plans is reported and both instructions are
kept; a technical fix that cannot change the intent is made deterministically and reported; anything
that makes the timeline unsafe blocks it. It names no timeline application: `timeline_builder`
consumes the specification, `resolve_adapter` owns everything Resolve-specific, `audio_mix_engine`
carries out the audio instructions. None of those exist yet; this module prepares their input.

See `docs/layer5/README.md` for the architecture assessment, the gap analysis and the design.

## Contract (`application/contracts.py`)

```python
source = TimelineSource(
    edit=EditRef(...),
    project_id=...,
    anchor_asset_id=...,
    frame_rate=...,
    frame_rate_assumed=False,
    duration=...,
    story_format=...,
    clips=(...),
)
result = compiler.execute(
    CompileTimelineCommand(
        source,
        style=resolved,
        style_ref=ref,
        graphics=gp,
        captions=cp,
        transitions=tp,
        vfx=vp,
        sound=sp,
        config=CompilerConfig(),
    ),
    ctx,
)
spec = result.value.spec  # created=False: an identical specification was served
```

* `TimelineCompiler.execute` compiles, validates, stores (or reuses). Blocking library I/O: run it as a job.
* `TimelineCatalog` (`latest` / `history` / `load`) reads stored specifications. Nothing is compiled.
* Errors: `TimelineBlocked` (CRITICAL findings, nothing stored, the reasons in `.problems`),
  `NothingToCompile`, `StyleRequired`, `InvalidCompileSource`, `InvalidCompilerConfig`.

## Inputs and where they come from

| Input | Source | Never |
| --- | --- | --- |
| the edit (`TimelineSource`) | the pipeline maps the `EditPlan` (`media_pipeline/application/compile_source.py`); no edit = one clip of the whole recording | imports the `EditPlan` |
| channel style | `ResolvedStyle` + `StyleRef` (given, by reference, or the stored one of the anchor) | copies tokens |
| Layer 4 plans | handed in by the pipeline, else read through the owners' catalogs (only when they belong to THIS edit and THIS style; a stale stored plan is reported `not_available`) | runs a producer |
| files | `MediaLibrary` (exists, file on disk, derivation) + `InspectionCatalog` (frame rate, VFR, streams) | probes |
| crops | `ReframingCatalog` by `recommendation_id` / `analysis_id` | decides a crop |
| drift | `StyleValidator` (the style's own validator) on what the plans placed | judges a token |

`application/views.py` is the only place that knows both the plan types and the compiler's facts.

## The specification (`domain/model.py`)

`TimelineSpecification`: `timeline_id`, `creator` (identity id/version, style fingerprint, format
profile, sound identity, rules, fonts, zones, brand assets, reusable presets: REFERENCES), `timebase`
(frame rate, sample rate, size, duration in frames, and the working COLOUR from
`CompilerConfig.color_profile`), `tracks`, `assets` (every file with its
`original_asset_id` and relation: original / improved audio / improved video / proxy / rendered /
library), `sections`, `video`, `overlays` (graphics, captions, transition overlays), `effects`,
`transitions` (with handles and execution), `audio` (with exact samples), `automation` (ducking,
crossfades, music tails), `subtitles` (SRT/VTT sidecar), `credits`, `dependencies`, `issues`,
`inputs`, `compilation` (versions, hashes, change summary), `state`.

Every time is an exact `Rational`; frames are computed once (`round(t * fps)`, half to even; a
duration is `end_frame - start_frame`). Every item carries the plan item it came from.

### Tracks

A fixed channel-wide layout so a project template carries from video to video: `V1` primary, `V2`
cover, `V3` graphics, `V4` captions, `V5` effects / transition overlays; `A1` dialogue, `A2`
production sound, `A3` music, `A4` ambience, `A5` effects. Overlapping items of one role take the next
LANE (inserted after the role's base track). The main picture never gets a lane: an overlap there is
a CRITICAL finding. Graphics, captions and overlays composite `alpha_over`, picture `normal`.

### Findings (`IssueClass`)

* `critical`: not executable safely; nothing is stored (missing asset or file, source range beyond
  the media, overlapping main clips, a plan for another edit or made with another style, audio out of
  sync with its picture, a rendered file shorter than its item, a requested Layer 4 plan that failed).
* `warning`: executable, look at it (gap on the main track, VFR / frame-rate conversion, an
  unrendered graphic / caption / effect, unresolved crop, missing font / brand asset / sound).
* `creative_conflict`: two instructions contradict; BOTH are kept (conflicting framing, crop and
  punch-in, time effects overlapping, graphics in one zone at once, sound plan vs edit, locked channel
  rule broken, a required transition that cannot run).
* `auto_resolved`: a deterministic technical fix with its `resolution` text (a transition without
  its handles or overlay runs as a hard cut, the wanted kind is kept in `wanted_kind`; a crossfade is
  shortened to the handle the file holds).

### Dependencies

`dependencies` lists every file, font, brand asset and identity with `resolved` / `missing` /
`fallback` / `requested`. A `requested` entry names the `owner` module and the pipeline task to ask
(`graphics`, `captions`, `vfx`, `reframing`). The compiler cannot run a producer (boundary tests),
so the request is data: the pipeline waits for those tasks, and a Layer 4 task of the run that did not
deliver is handed over as `undelivered` and blocks the timeline.

### Incremental compilation and determinism

* The library fingerprint of every input digest is the cache: identical inputs return the stored
  specification (`created=False`).
* New inputs make the next `compilation_version` (named `supersedes`). Its units (one per
  placement, plus `graphics`, `captions`, `soundtrack`) are reused when their digest is unchanged;
  `compilation.change` lists added / removed / changed item ids; `units_reused` counts them.
* `spec_hash` covers the content only (no time stamp, version number or change summary):
  identical inputs and config give an identical specification.

## Configuration (`CompilerConfig`, `domain/profile.py`)

`timeline_frame_rate` (None = the edit's), `sample_rate`, `derivative_policy` (`original` |
`prefer_improved`: the edit plan never chooses between a recording and its improved version, so the
config does), `allow_style_mismatch`, `require_rendered_overlays`, `drop_identity_violations`,
`gap_tolerance_frames`, `handle_tolerance_frames`. Every knob carries UI metadata; `problems()`
enforces the same ranges (tested).

### Added for the builder (compiler version 2)

* `Timebase.color` (`rec709` default, `srgb`, `rec2020_hlg`, `rec2020_pq`) and `AssetUse.color` (the
  colour the stored inspection DECLARES for the picture, `None` = not stated): nothing is converted
  here, the builder reports a source tagged otherwise.
* `VideoItem.original_start/end`: the range of the RECORDING a clip shows on the recording's own
  clock. `source_start/end` are positions inside the placed file, which for a baked VFX clip is the
  render, so a crop (stated in recording time) could not be placed without it.
* A clip that is baked into a VFX render holds no footage past its own range, so a transition at its
  cut has no handle there: it falls back to a hard cut (reported like any other missing handle).

## Not done / limits

* The media library has no project ownership: assets are checked for existence and file
  availability, not for membership of the project or a shared library.
* A transition outside the cut / dissolve / fade / dip family is a specification: `timeline_builder`
  must support the vocabulary or report it. Graphic-family transitions are rendered overlays.
* Effects are carried out by the VFX clip `vfx_engine` renders (baked); an effect without a usable
  render falls back to the plan's own fallback (original footage) and is reported.
* Variable-frame-rate recordings are exact in seconds; conforming the picture is the builder's job.
* Levels, ducking and crossfades are instructions for the audio mix engine, not a mix.
* Validated on synthetic fixtures (`tests/unit/edit_plan_compiler`) and one synthetic no-speech clip
  through the real composition root (`tests/integration/media_pipeline/test_timeline_pipeline.py`).
  Not validated on a long edit with speech, rendered graphics and a sound library together.
