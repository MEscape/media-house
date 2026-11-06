# resolve_adapter (Layer 5, module 26)

Carries a built timeline out in DaVinci Resolve, reads it back to check it, reads a person's
fixes back into Media House, and renders and checks the result. It is the ONLY module that knows
Resolve; nothing else imports its API, and the rest of Media House never sees a Resolve object.

It decides nothing creative. The edit, the look, the captions and the transitions come from the
layers above; this module executes them, says what it could not execute, and never replaces an
instruction by a look of its own.

> **Status: not yet run against a real DaVinci Resolve.** No Resolve is installed on the machine
> this was built on. The adapter is written from the published scripting documentation (Resolve
> 18/19) and tested end to end against a stand-in of that documented object tree
> (`tests/support/resolve_fakes.py`). That proves the logic and the wiring, not that a real Resolve
> behaves as documented. Every capability is therefore `not_verified` until a live run reads it
> back (see "Capabilities"). The first run on a real Resolve is the real test: run
> `uv run python scratch/run_pipeline.py --input clip.mp4 --stages resolve_execute`.

## Fixing small things without running the pipeline again

The point of the design: a wrong word, a transition you want removed, a graphic that sits a few
frames off are fixed **in Resolve**, and nothing upstream runs again.

| You change in Resolve | What happens |
| --- | --- |
| move, trim, disable a clip; change its opacity; delete it | `SyncFromResolve` reads it back and hands it to the timeline builder as your edit (`human_modified`). The next pipeline run and the next execution **keep it**. |
| a transition (change, remove, add) | Scripting cannot place transitions, so there is nothing of ours to overwrite. The cut carries a **marker** saying what the plan wanted; you do it in Resolve. |
| a caption word | Rendered caption clips are pictures. Run with `--resolve-captions rendered_with_editable_cues` and the cues also arrive as a subtitle file in the media pool: correct the words there. |
| a detail inside a rendered motion graphic | It is a rendered file (ProRes with alpha); Resolve cannot edit its insides. Change the request and re-run only `graphics`: every other graphic is a cache hit, and the next execution replaces just that clip in place. |
| anything the adapter did not place (a cutaway you added) | Never touched, never deleted. Only elements it placed (it tags them) are managed. |

What a later run does when **both** sides changed the same field is the execution policy
(below); it is always on record in the report, never silent.

## Contract (`application/contracts.py`)

```python
result = executor.execute(ExecuteTimelineCommand(timeline=built), ctx)  # or anchor_asset_id=...
report = result.value.report  # outcome, every operation, every check
synchronizer.execute(SyncCommand(anchor_asset_id=...), ctx)  # your fixes -> Media House
renderer.execute(RenderCommand(anchor_asset_id=..., render=RenderConfig("youtube")), ctx)
ResolveInspector.execute()  # state, version, edition, capabilities, projects (changes nothing)
AudioLayouts.layout(anchor)  # where each sound is in Resolve (for audio_mix_engine)
```

* Blocking, run as jobs. Errors before Resolve is touched are `Err` (`ResolveUnavailable`,
  `TimelineNotExecutable`, `NothingBound`). Once Resolve is being driven a failure is **the
  report** (state `failed` / `interrupted` / `cancelled`), not an exception.
* `ResolveBackend` (`application/ports.py`) is the one port to Resolve. The default adapter is
  `infrastructure/scripting.py`; it is the only file that calls Resolve's API (an architecture
  test enforces it).

## Execution (`application/execution.py`, one method per state of the report)

`connecting -> configuring_project -> importing_media -> building_timeline -> applying_effects ->
verifying_project -> ready_to_render` (`rendering -> verifying_render` in `RenderTimeline`), ending
`completed` / `partially_completed` / `failed` / `interrupted` / `cancelled`.

1. **Connect**: install check, process check, then a real call. `connected` only if Resolve
   answered with a version and a project manager. States: `not_installed`, `not_running`,
   `api_unavailable` (e.g. external scripting off, library not loadable), `unsupported_version`,
   `connection_failed`, `connected`, `disconnected`.
2. **Project**: a project bound to this timeline is reopened; a project of the same name that is
   not ours is **never taken over** (`adopt_existing_project`); another open project blocks the run
   unless `allow_project_switch`, and is saved first.
3. **Settings**: frame rate, resolution written and read back. They cannot change once a timeline
   holds media, so a mismatch stops the run before anything is built (`ConfigurationNotApplied`).
4. **Media**: files come from the Media Library (`local_path`); proxies from `ProxyCatalog` (the
   source stays the clip, the proxy is linked to it). Pool items are matched by file path, so
   nothing is imported twice, offline items are relinked, folders follow what the file is
   (`Original footage`, `Processed media`, `Graphics`, `Captions`, `Music`, `Sound effects` ...).
5. **Timeline**: tracks in the builder's order and names; every clip placed by track, frame and
   source range, tagged with a marker carrying the builder's element id, picture and sound linked.
   Picture geometry (fit, a static reframing window) becomes zoom/pan/opacity; the maths is in
   `domain/translate.py`.
6. **What scripting cannot do** (`gaps`): transitions, retime, native effects, a moving crop, clip
   gain/fades. Each is reported with its alternatives and, where one step in Resolve finishes it,
   a marker on the timeline. A clip that needs a retime is placed at real speed with a marker
   (`retime_placement=skip` leaves a gap instead). Gain and fades are `audio_mix_engine`'s.
7. **Verify**: Resolve is read back and compared per clip (position, length, in point, track,
   enabled, properties). `verified` / `partially_verified` / `not_verifiable` / `failed` are
   never mixed up; fonts cannot be inspected through the API and say so.
8. **Bind**: the link to the project (`resolve_binding` document) stores the state that was
   written and read back per element. Checkpoints after the timeline exists and after the clips
   are placed mean an interrupted run loses nothing.

### Retry and interruption

What Resolve holds decides what is still to do, never what was intended. A clip that is already in
place is kept; one placed by a run that died before tagging it is recognised by track, place,
length and file and adopted; nothing is placed twice. The project a crashed run made is
recognised by its checkpoint, not refused as foreign.

## Execution policies (`ExecutionPolicy`)

| Policy | Existing work |
| --- | --- |
| `create_new` | A new timeline next to the old; nothing existing is touched. |
| `update_managed_elements` | In place. A field changed on both sides goes to **Media House**; the value it overwrote is in the report. Everything else of yours is kept. |
| `preserve_manual_edits` | In place. A field changed on both sides stays **yours**; the other change is dropped and reported. |
| `require_conflict_review` (default) | In place. Yours is kept AND the conflict stays open: the report is `partial` and **rendering is blocked** until it is decided. |
| `full_rebuild_with_backup` | The old timeline is duplicated and renamed (never deleted), then a fresh one is built. |

The merge is per field, three-way (`domain/reconcile.py`): last synchronised state, what Media
House wants now, what Resolve holds now. Only Media House changed it: written. Only you changed
it: kept. Both: the policy.

## Capabilities (`domain/capabilities.py`)

A registry of every operation with how it is carried out, limits, alternatives and how it is
verified. States: `supported` (confirmed by a read-back on THIS Resolve version + edition),
`partially_supported`, `unsupported` (a known gap or a missing method), `version_dependent`,
`edition_dependent`, `plugin_dependent`, `not_verified` (the method exists; nothing has confirmed
the whole operation). Confirmed operations are remembered in the binding for that Resolve.

Known gaps in Resolve's scripting API (as documented): transitions, clip speed / retime, native
effects, keyframed clip properties (a moving crop), clip gain and fades, listing fonts.
`connection.external_scripting` is `edition_dependent`: the free edition may only run scripts from
inside Resolve.

## Rendering (`RenderTimeline`)

Settings come from the timeline (size, rate) and a profile (`youtube`: MP4/H.264/AAC; `master`:
MOV/ProRes 422 HQ). Before it starts: the timeline is not empty, no conflict is open, every pool
item is the intended source file (not offline, not a proxy). The file is registered in the library
as a derived asset (a render never overwrites an earlier one), inspected by `media_inspection`, and
compared: exists, size, readable, codec, resolution, frame rate, duration, audio. An unchanged
timeline (build + live state of every clip + profile) is **not rendered again**.

## Audio (`application/audio_layout.py`)

`AudioLayouts.layout()` tells `audio_mix_engine` which Resolve track carries which role and which
Resolve item each sound is. The mix itself stays in the builder's mix instructions; applying a mix
inside Resolve is not scriptable (`audio.gain_fades`), so the mix engine owns its own route.

## Stored documents (Media Library, no storage of its own)

* `resolve_binding`: project, media, element and marker bindings, open conflicts, confirmed
  capabilities. A new document per synchronisation (history kept); the newest `sequence` is current.
* `resolve_execution_report`: one per execution, sync and render.
* Rendered files: derived assets of the recording (`resolve_render`).

Resolve's ids are stored as external references next to the stable internal ids (element id, asset
id); a name or a Resolve id never replaces them. The strict JSON codec is the shared
`core.domain.json_codec`.

## Configuration

`ResolveAdapterConfig`: `policy`, `allow_project_switch`, `adopt_existing_project`,
`project_name`, `root_folder`, `tag_items`, `caption_strategy`, `retime_placement`,
`instruction_markers`, `link_proxies`, `save`, `require_media`, `minimum_version`, and `Quirks`
(inclusive end frame, audio frame units, start timecode) that a first live run may need to
correct. `RenderConfig`: `profile`, `file_name`, `require_final_quality`, `poll_seconds`,
`timeout_seconds`. Install locations are Blackmagic's documented defaults per OS; the CLI scripts take
`--resolve-lib` / `--resolve-api` to override them (library code never reads the environment).

## Not done / limits

* **Not run against a real Resolve.** Behaviours assumed from the documentation and listed in
  `Quirks` and the capability registry: whether `endFrame` is inclusive, what unit audio-only clips
  use, `Offline` / `Proxy Media Path` clip properties, item-marker frame semantics, the property
  names of fit/scaling. A wrong assumption shows up as a **failed verification**, not a silent error.
* The scripting module is loaded in-process from Resolve's own library. A Python version it does
  not support gives `api_unavailable` with the reason. Running it in a separate interpreter is the
  fix if that happens.
* Transitions, retime, native effects, moving crops, clip gain: see "Known gaps". A timeline
  interchange import (FCPXML / OTIO) that carries transitions is the documented alternative and is
  **not built**.
* Colour management is only set for `rec709`; another profile is reported, not forced.
* Fit maths assumes Resolve's default "scale entire image to fit"; the project setting is not set.
* Sync records edits to clips (position, length, in point, track, enabled, opacity, deletion).
  A zoom, a clip you added, a replaced media file stay in Resolve and are reported.
* Nested or compound timelines, multiple databases / cloud projects, and path translation between
  WSL and Windows are not handled. Only the current database folder is searched.
* No UI.
