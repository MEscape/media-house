# Layer 4 Shared Contracts (approved by the orchestrator)

Every Layer 4 agent reads this file, `docs/layer4/integration-brief.md` (how the codebase works)
and `docs/layer4/channel-discovery.md` (what the channel actually is) before writing code. The
per-module specs in `docs/layer4-references/4-*.md` are the detailed wish lists; where they
conflict with this file or with the architecture rules, THIS FILE WINS. Where a spec item cannot
be done honestly on this machine (no model, no tool), implement the port, return
`not_available` with a reason, and list it under limitations. Never fake a result.

Scope decided by the owner: Layer 4 produces **stored plans** (JSON documents in the Media
Library) and **rendered assets** (derived library assets). `timeline_builder`, `resolve_adapter`
and `quality_control` are NOT built now. Every plan must still carry everything a later timeline
builder / Resolve adapter needs (exact times, frames, asset ids, track intent), so nothing has to
be recomputed later.

## 1. Ownership (no overlap)

| Responsibility | Owner | Explicitly NOT owned (goes to) |
| --- | --- | --- |
| Channel visual identity: colours, typography, fonts, graphic language, layout zones / safe areas, caption look, title/lower-third look, motion feel (easing, durations, intensity), logo rules, format profiles, versions, drift validation | `visual_style_engine` | rendering anything (motion), footage grading (video_improvement), sound identity (music) |
| Building, animating, rendering graphics: titles, lower thirds, callouts, price tags, rank badges, panels, charts, counters, CTA, intro/outro, logo stings, chapter cards, tracking labels, graphic transition overlays, caption overlays (as executor) | `motion_graphics_engine` | what a caption says or when (caption), where graphics are editorially needed (editing; motion only derives graphic content from editorial requests + content facts), identity (style) |
| Caption decision, mode, segmentation, word timing, emphasis, emoji, caption layout intent, caption animation REQUESTS, subtitle export text | `caption_engine` | transcription (audio_intelligence), drawing/animating (motion) |
| Transition choice between adjacent clips/sections: visual kind + intensity 0-4 + duration + alignment + handles + fallback, AUDIO transition plan (J/L, crossfades, tails) | `transition_engine` | audio processing (audio_improvement), graphic overlay rendering (motion), cut placement (editing) |
| Shot-level footage effects: blur/DoF, background blur/replace, glow/bloom, vignette, grain, lens/chromatic, speed/ramps/freeze/reverse/interpolation, punch-in zoom, privacy blur on tracked regions, masks/tracking use, compositing of footage layers; rendering those derived clips | `vfx_engine` | denoise/sharpen/grade/stabilise (video_improvement), graphics (motion), transitions (transition), still-image cutouts (visual_asset_generation) |
| Music + SFX: library inventory (import, licence metadata, analysis: BPM/beats/energy/sections), music selection/arrangement per section, SFX selection/placement with restraint, silence, ambience preservation, ducking INTENT, sync to graphic/transition events, sound identity, missing-asset recommendations, music/SFX stem render | `music_and_sound_design` | EQ/compression/loudness/mastering/final mix (audio_improvement), transcription (audio_intelligence) |
| Still-image preparation: existing-image selection, background removal, clean + enhanced cutouts (glow/shadow/outline/edge light), frame grabs from video for cutouts, format conversion/optimisation, image QC, limited generation (port only; no model here) | `visual_asset_generation` (= `image_adjustment` renamed in place) | video mattes (vfx), graphics (motion) |
| YouTube thumbnails: the owner's brief (headline, reaction, products, logo, shop phone, story badge) composed into A/B/C variants for Test & Compare, feed-size QC, the 1280x720 upload copy, reaction candidates from stored face cues | `thumbnail_design` | cut-outs and frames (visual_asset_generation), in-video graphics (motion) |

A module that needs something another module owns CALLS that owner's contract (or reads its
stored result). It never re-implements it.

## 2. Dependency graph (imports only through `application.contracts`)

```text
visual_style_engine            (no Layer 4 deps)
visual_asset_generation   ->   visual_style_engine
motion_graphics_engine    ->   visual_style_engine, visual_asset_generation
thumbnail_design          ->   visual_style_engine, visual_asset_generation, video_intelligence (stored analyses)
caption_engine            ->   visual_style_engine, motion_graphics_engine
transition_engine         ->   visual_style_engine, motion_graphics_engine
vfx_engine                ->   visual_style_engine
music_and_sound_design    ->   motion_graphics_engine, transition_engine, caption_engine (events only; NOT visual_style)
```

Upstream (Layers 1-3) reads: through the owners' read-only catalogs (`...Catalog`), never their
producers (`...Analyzer`/`...Planner` Protocols are banned by boundary tests). Allowlists already
opened by the orchestrator: story -> caption, motion, music, transition; pacing -> music,
transition; reframing -> caption, motion, vfx; continuity -> transition; broll ->
visual_asset_generation. Need another one? Ask the orchestrator; do not edit other modules'
boundary tests yourself.

The `EditPlan` is NEVER imported by a Layer 4 module (editing's allowlist). Each module defines
its OWN narrow input DTO in its contracts (continuity precedent: `ProposedSequence`), e.g.
`CaptionTimeline`, `TransitionSequence`, `VfxShots`, `SoundtrackTimeline`, `GraphicsBrief`. The
orchestrator writes the mappers in `media_pipeline/application/` later; you describe in your
README which `EditPlan` fields map to your DTO. Put an `edit: EditRef(edit_plan_id, plan_version)
| None` and `project_id` in the DTO so every plan names the edit it was made for.

NO-SPEECH PATH (binding; reassessment F01): a large share of the channel (meme Shorts, versus
polls, uncommented compilations, NYE uncut footage) has no speech, so Layers 3 produce NO
`EditPlan` for it. Every Layer 4 input DTO must therefore accept `edit=None`. The pipeline then
builds the DTO from the inspection: one clip per source asset, `timeline = source` (timeline
start 0, speed 1), the inspection's frame rate, no sections/beats/speech spans (empty tuples),
`project_id=None`. Modules must produce a sensible plan from that (e.g. captions `disabled` or
`key_information` only, hard cuts, music by format profile, no editorial graphics unless
requested) and never fail because sections or speech are absent. Anchor = the first source
asset.

Each new module writes `tests/architecture/test_<module>_boundaries.py` (copy reframing's) with:
domain imports no other module; no producer Protocols; no clock/logging/random/print in domain;
no timeline-backend names; and its consumer allowlist taken from the graph above
(+ `media_pipeline`, `bootstrap`).

## 3. Time, frames, identifiers

* Three time domains, never mixed, never converted without an explicit mapping:
  `source_*` = exact seconds on the recording's common clock (container time, as EditPlan
  `SourceRange`), `timeline_*` = exact seconds from the start of the edit (EditPlan
  `Placement.timeline_start`), `local_*` = seconds from the start of one graphic/effect/cue.
  Field names carry the prefix. Mapping source -> timeline exists only through a clip
  (`timeline = clip.timeline_start + (source - clip.source_start)` for speed 1).
* All times are `media_house.core.domain.Rational` (exact). No float seconds as an address.
* Frame rate: the edit's `Timebase.frame_rate` (Rational) travels in the input DTO. Frames are
  computed ONCE, at the edge (render/plan output), as `round(t * fps)` with Python's
  half-to-even rounding on the exact value; a duration in frames is `end_frame - start_frame`,
  never `round(duration * fps)`. VFR sources: say so (`timebase.assumed`) and keep seconds
  authoritative.
* Identifiers: assets = Media Library ids only. Project = `EditPlan.project_id`. Every plan item
  has a stable id derived from its inputs (e.g. `cap:<speech-span-hash>`), so person decisions
  (accept/reject overrides) survive replans.

## 4. Visual identity hand-off

`visual_style_engine` publishes in its contracts a resolved, immutable `ResolvedStyle` (exact
fields defined by that module) containing at least: identity id + version, format profile id +
version, applied overrides, resolved design tokens (colours with HEX, typography with font
family/weight/size scale/tracking/leading + font resource references, spacing, radii, strokes,
shadows), layout zones / safe areas per output size, caption look, title/lower-third/callout
look, motion tokens (easings, duration ranges, intensity ceiling, stagger, overshoot), logo
rules, mandatory rules vs preferences, output dimensions, and a `fingerprint` (sha256 of the
canonical tokens). Consumers:

* take a `StyleRef` (document id + fingerprint) or a `ResolvedStyle` in their command, put the
  fingerprint in their cache key and the `StyleRef` in their plan;
* never define their own colours, fonts, easings or layout zones;
* report deviations through the style's drift validator (owned by visual_style_engine) instead
  of checking tokens themselves.

Layout rule against visual overlap: the style defines named, non-overlapping zones per format
(e.g. caption band, lower-third band, top title band, corner badge, platform UI keep-out for
9:16). Captions use only the caption zone, graphics only graphic zones; a request outside its
zones is a QC error. Reframing receives these as `SafeArea`s from the caller.

Old projects keep their identity: a resolved style is stored as a library document of the
project's anchor asset; consumers resolve through the catalog (pinned) and a new identity
version is used only when the command asks for it.

## 5. Plans, storage, caching (same shape in every module)

* Anchor = the first source asset of the edit (`EditRef` DTO's first asset). Plans are derived
  JSON documents of the anchor via `DerivedDocuments.store(...)` with a top-level
  `document_type`: `visual_style`, `graphics_plan`, `caption_plan`, `transition_plan`,
  `vfx_plan`, `sound_design_plan`; sound library analyses are documents of each sound asset
  (`music_analysis`, `sfx_profile`).
* Rendered outputs are derived assets via `MediaLibrary.register_derived` (operation names:
  `motion_graphic_render`, `vfx_render`, `sound_stem_render`; image ops keep
  `image_adjustment`). Graphics: ProRes 4444 MOV `yuva444p10le` (true alpha; Resolve-ready).
  VFX clips: ProRes 422 HQ 10-bit (`yuv422p10le`), never 8-bit mid-pipeline. Audio stems: WAV
  `pcm_f32le` 48 kHz. Images: PNG RGBA.
* Cache key = everything that changes the result and nothing else: input identities, the
  `EditRef`, style fingerprint, `digest(config.fingerprint(profile))`, person decisions,
  versions. Digests only (64 KiB cap). Library fingerprint is the cache; a hit returns
  `created=False` and computes nothing. New key -> new `plan_version` + `supersedes`.
* Envelope fields every plan has (module-own types, same names): `plan_id, plan_version,
  supersedes, project_id, edit: EditRef, style: StyleRef | None, created_at, state (OK | PARTIAL),
  <items>, checks: tuple[QcIssue], inputs: tuple[InputRecord], stages: tuple[StageReport],
  provenance (schema/analyzer/vocabulary/profile versions, configuration_fingerprint, engines),
  warnings, limitations, schema_version`.
* Serialization: copy the strict generic codec (`reframing_intelligence/domain/serialization.py`).
* Missing optional input -> `InputRecord(how=NOT_AVAILABLE)` + degrade; missing required input
  -> `Err`. A failing OPTIONAL creative item falls back safely (hard cut / no graphic / no SFX /
  original footage) and is recorded; it never fails the whole plan.

## 6. QC (consistent across Layer 4)

Each module defines in its domain:

```python
class Severity(StrEnum): CRITICAL = "critical"; ERROR = "error"; WARNING = "warning"; INFO = "info"
class QcCategory(StrEnum): TECHNICAL = "technical"; CREATIVE = "creative"
@dataclass(frozen=True, slots=True, kw_only=True)
class QcIssue: code: str; severity: Severity; category: QcCategory; message: str; item_id: str | None = None
               timeline_start: Rational | None = None; timeline_end: Rational | None = None; evidence: str = ""
```

CRITICAL = the plan is invalid and is never stored (`Err(Invalid<X>Plan)`). ERROR = one item is
unusable: it is replaced by its safe fallback, the plan becomes PARTIAL. WARNING / INFO are
recorded. A CREATIVE finding (differs from a profile preference) is never above WARNING.
Render QC checks the produced file (ffprobe facts: dims, fps, pix_fmt/alpha, duration in frames,
audio peak/clipping) — not the intent.

## 7. Configuration (future settings UI; no UI now)

Approved convention (integration brief §10): each module's knobs live in `domain/profile.py` as
nested frozen dataclasses; every user-facing knob has `dataclasses.field(metadata={"label",
"help", "unit", "min", "max", "step", "choices", "kind", "advanced"})`; `problems()` enforces the
same ranges (a unit test walks the fields and proves metadata and checks agree); built-in
profiles in `PROFILES` (format profiles refined from channel discovery); `<X>Config(profile,
overrides: Mapping[str, JsonValue] (dotted paths, copy audio_improvement's resolver),
section_overrides: tuple[SectionOverride(section_id, overrides)], decisions)`. Precedence, each
step only touching the keys it names: profile defaults (system) -> channel identity (style /
sound identity) -> format profile -> project overrides -> section overrides -> per-item person
decisions. `PROFILE_VERSION` versions the schema; profile values carry `version`. No hard-coded
creative number outside `profile.py` (math constants are fine).

## 8. Who touches shared files

Only the orchestrator edits: `bootstrap/modules.py`, `tests/integration/test_application_lifecycle.py`,
other modules' boundary tests, `media_pipeline/**`, `scratch/run_pipeline.py`, `pyproject.toml`
/ `uv.lock` (ask for a dependency; justify it), `AI_CONTEXT.md`, `ARCHITECTURE.md`. To test your
module end to end before integration, register it in your own integration-test fixture's
container or tell the orchestrator the two `bootstrap/modules.py` lines you need. Exception:
the `visual_asset_generation` agent performs the image_adjustment rename and updates the tests
that name `image_adjustment` (listed in the brief §5).

## 9. Environment facts (check, do not assume)

CPU only (no CUDA). No ML model is downloaded; model features must degrade to `not_available`
and be testable with fakes. FFmpeg/FFprobe 9.0.2 (Gyan full build) are on PATH
(`~/.local/bin`; ProRes 4444 via `prores_ks`, VP9 alpha, `pcm_f32le` available); still gate
FFmpeg tests with `skipif(shutil.which(...) is None)`. Node v24 + npx exist (HyperFrames). Run tools as `uv run python -m pytest` / `uv run python -m mypy` (bare exes are blocked).
Architecture suite is slow (~4 min): run `tests/architecture/test_dependency_rules.py` + your
boundary test while iterating, the full suite once at the end. Starter sound library:
`C:\Users\L14\Documents\MediaHouseAssets\` with `manifest.json` + `LICENSES.md`.

## 10. Definition of done per module

Spec items implemented or honestly listed as limitations; README (contract, inputs, mapping
from EditPlan, config, QC, limits, "validated on ... only"); unit tests (domain + use case with
fakes), integration test with a real Application (skip FFmpeg/Node parts when tools are absent),
config-metadata test, boundary test; `ruff format`, `ruff check`, `mypy` (strict, your files)
clean; no file outside your module/tests/docs changed except those allowed in §8. Final report:
what was built, reused, not built (and why), contracts exposed, EditPlan fields needed, test
commands + results (actually run), open questions.
