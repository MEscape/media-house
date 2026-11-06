# timeline_builder (Layer 5, module 25)

Constructs the timeline. It takes the validated `timeline_specification` of
`edit_plan_compiler` and builds what a person (or `resolve_adapter`) opens: tracks that hold their
clips in order, ONE table of source media (original, derivative, proxy) that every clip instance
points at, each picture clip's processing chain in a fixed order, transitions that join two named
clips, mix instructions for `audio_mix_engine`, markers, sections, groups, a dependency manifest,
findings, and a stable id plus provenance on every element.

It decides nothing creative and names no timeline application. An instruction it cannot carry out
is KEPT and REPORTED, never replaced by a look of its own. `resolve_adapter` translates this model
into DaVinci Resolve; nothing here imports it, calls it or knows its API.

See `docs/layer5/README.md` for the architecture assessment, the gap analysis and the design.

## Contract (`application/contracts.py`)

```python
result = builder.execute(
    BuildTimelineCommand(
        spec=spec,  # in memory from the pipeline ...
        spec_asset_id=asset.id,  # ... with its library document
        # or: anchor_asset_id=..., the newest stored specification is read
        config=BuilderConfig(),
        capabilities=Capabilities(),  # what the target application can do (default: the model's)
        expect_identity=IdentityExpectation("mesc", 2),
        checks=(),  # results of checks made on a render
    ),
    ctx,
)
timeline = result.value.timeline  # created=False: an identical build was served
for track in timeline.tracks:
    track.video, track.audio  # ordered clips
timeline.transitions, timeline.mix, timeline.markers, timeline.dependencies, timeline.issues
```

* `TimelineBuilder.execute` builds, validates, stores (or reuses). Blocking library I/O: run it as a job.
* `TimelineEditor.execute(RecordEditsCommand)` records a person's changes as the next version.
* `BuiltTimelineCatalog` (`latest` / `history` / `load`) reads stored builds. Nothing is built.
* Errors: `TimelineNotBuildable` (ERROR findings, nothing stored, reasons in `.problems`),
  `SpecificationRequired`, `InvalidSpecification`, `InvalidHumanEdit`, `NothingBuilt`,
  `InvalidBuilderConfig`.

## What it reads, and from where

| Input | Source | Never |
| --- | --- | --- |
| the specification | handed in by the pipeline, else `TimelineCatalog.latest` (the compiler's catalog) | runs the compiler |
| the files NOW | `MediaLibrary` (exists, file on disk, checksum, name) | probes; trusts the specification's age |
| proxies | `ProxyCatalog.find(source, "edit")`, only a valid one | stores a proxy id (the source stays the identity) |
| the previous build | its own stored document | builds from scratch when it can carry over |

`application/views.py` is the only place that knows both the compiler's types and the builder's
own `SpecFacts` (the domain imports no other module).

## The built timeline (`domain/model.py`)

It is NOT a copy of the specification. The specification lists items per kind; the built timeline
is organised by what a person opens.

* **Settings**: size, reduced aspect, frame rate, sample rate, audio channels, duration, colour
  management, fit mode. All from the specification (colour from `CompilerConfig.color_profile`).
* **Sources**: one `SourceMedia` per file however many clip instances play it (frame rate, VFR,
  colour tags, `original_asset_id`, `relation`, proxy, which clips use it). A clip's `SourceUse`
  is only the part it plays: in/out inside THAT file, and on the recording's own clock
  (`original_in/out`) when a baked VFX file is placed.
* **Tracks** (`V1..V5`, `A1..A5`, extra lanes as the compiler made them), named and grouped for a
  person (`V3 Graphics`, group `Video · Graphics`), each holding its `video` / `audio` clips in time order.
* **Picture clips**: exact times and frames, speed, `TimeMap` (speed changes, ramps, freeze,
  reverse; `applied=False` when the render already shows it), `Transform` (fit mode), crops from the
  reframing analysis with each keyframe placed on the timeline, effects, opacity, blend, zone, the
  design choices it carries out (`identity`), and the CHAIN: time map -> fit -> crop -> effects (by
  time) -> composite, each step numbered and marked applied or not. Nothing reorders it.
* **Sound clips**: exact samples, gain, fades, channels, the picture they are linked to, and the
  mix instructions that act on them. Levels, ducking and crossfades are INSTRUCTIONS for
  `audio_mix_engine`; this module does no mix.
* **Transitions**: a mode in the builder's own vocabulary (`cut`, `dissolve`, `fade`, `dip`,
  `overlay`; anything else is `unsupported`), the two clips it joins, and the handle frames read
  past the outgoing out point (`tail_frames`) and before the incoming in point (`head_frames`).
  The clips keep their positions, so duration and rhythm do not change.
* **Markers, sections, groups, subtitle cues** (the caption text as editable cues), credits.
* **Identity**: a REFERENCE to the Layer 4 creator identity (id, version, style fingerprint,
  format profile, sound identity, fonts, zones, brand slots, design choices). Nothing is copied.
* **Dependencies**, **issues**, **validation**, **counts**, **build info**, **status**.

Every element has a stable id (the specification's item id), a `provenance` (`ai`, `system`,
`human_modified`, `human_created`), `origin_ref` (the specification item it came from) and
`source_digest` (the digest of the facts it was built from).

## Findings (`Severity`)

* `error`: cannot be built safely; nothing is stored. A schema or vocabulary it cannot read, a
  reference to nowhere, footage or sound that is gone or changed since compilation, a source range
  beyond the media, overlapping clips on a track, a clip beyond the timeline, frames or samples that
  do not match the exact times, audio out of sync with its picture, fades longer than the clip, a
  broken time map, a wrong creator identity, a deleted/changed file a placed clip plays.
* `unsupported`: an instruction the model (or the target application's `Capabilities`) cannot
  carry out: a transition kind outside the vocabulary, insufficient handles, a native effect or
  reverse/ramp the target cannot do. KEPT and reported; `block_on_unsupported` makes it an error.
* `review`: a decision for a person: a creative conflict of the compiler, a human-change conflict.
* `warning`, `info`: gaps on the main picture, VFR, a colour tag that differs from the timeline's,
  a font or brand slot that is missing, an unresolved graphic... The compiler's findings are carried
  over (`origin="specification"`).

Status is `partial` when anything is not `info` or a dependency is still open, else `ok`.

## Checks that need a render

Black frames, missing frames, visual gaps, blank sections, missing audio and the sync of the
rendered result cannot be seen in timeline metadata. `Validation.not_run` lists them until an
`ExternalCheck` (kind, state, tool, frames) is supplied in `BuildTimelineCommand.checks`; a failed
one becomes a finding with `origin="external"`. The builder never makes these results up.

## Incremental builds and a person's work

* The library fingerprint of `{inputs, people}` is the cache: identical inputs AND identical
  work by people return the stored build (`created=False`). `inputs` covers the specification, the
  live files, the configuration, the capabilities, the expected identity, external checks and versions.
* Changed inputs make the next `build_version` (naming `supersedes`). An element whose digest is
  unchanged is carried over (counted in `elements_reused`); `build.change` lists added / removed /
  changed / conflicted ids and the sections they touch. `mode`: `new`, `incremental`, `full`
  (settings or builder version changed, or `full_rebuild`). Content is identical either way:
  `build_hash` covers content only (no time, version or run id).
* `TimelineEditor.execute(RecordEditsCommand(edits=(HumanEdit(...),)))` records a person's
  `modify` / `delete` / `create` of picture and sound clips (named fields, exact values) as the
  next version: the element is marked `human_modified` with the digest of the AI version it was
  changed from, a deletion leaves a `Tombstone`, a creation is `human_created`, and the log keeps
  who/what/when. A change that leaves the timeline invalid is refused whole.
* The next build never overwrites that silently. Unchanged upstream: the person's version stays.
  Changed upstream too = a conflict, resolved by `BuilderConfig.conflict_policy`: `preserve` (keep
  the person's, say so), `replace` (use the specification's, say what was replaced), `review`
  (default: keep the person's, hold the new one, status `partial`). Human-created clips always stay.
* Resolve-specific change detection and synchronisation belong to `resolve_adapter`
  (`SyncFromResolve` reads a person's changes in Resolve and records them here through
  `TimelineEditor`); this is the application-independent half.

## Configuration (`BuilderConfig`, `Capabilities`)

`fit_mode` (contain / cover / stretch / none), `audio_channels`, `conflict_policy`,
`block_on_unsupported`, `block_on_unresolved`, `gap_tolerance_frames`, `section_markers`,
`review_markers`; every knob carries UI metadata. `Capabilities` (transitions, effects, reverse,
speed ramps, max tracks) is what a timeline application declares; the default is the model's own.

## Not done / limits

* Nested or compound sequences are not built: the specification holds none and nesting would be a
  creative choice. Sections are organised as markers and a section table.
* Pixel aspect and field order are not in the specification: square pixels, progressive picture.
* Caption styling, speaker labels and word-level timing are baked into the rendered caption clip and
  are not in the specification: the builder keeps the text as cues and the look as a design reference
  (`caption_look`, zone). Exposing words and speakers would be a `caption_engine` / compiler extension.
* A person's edits are recorded for clips; transitions, mix instructions and tracks always come
  from the specification (an edit that would break a transition is refused).
* Tracking data named by a native mask is reported `unverified`: nothing here can check it.
* Validated on the compiler's synthetic fixtures (`tests/unit/timeline_builder`) and one synthetic
  no-speech clip through the real composition root
  (`tests/integration/media_pipeline/test_timeline_build_pipeline.py`). Not validated on a long
  edit with speech, rendered graphics, VFX and a sound library together, nor against a real
  timeline application.

## Extending

A new transition kind: add it to the sets in `domain/profile.py` (`NATIVE_TRANSITIONS`) and
`transition_mode` in `domain/construct.py`, bump `VOCABULARY_VERSION`. A new check: a function in
`domain/validation.py` added to `_CHECKS`. A new editable field: its parser in `domain/human.py`.
