# Editing Intelligence

Turns a validated **story plan** and the stored analyses of its recordings into one **edit plan**: what to
keep, cut, shorten, reorder and cover, as exact source ranges placed on a backend-neutral timeline. It
**decides and explains; it never edits media, builds a tool-specific timeline or names a backend**. A later
stage compiles the plan.

```
media_inspection  audio_intelligence  video_intelligence  content_intelligence  audio_visual_alignment
        \                 |                  |                    |                     /
         +----------------+------ stored results (read) ----------+---------------------+
                                             |
story_architecture ----> editing_intelligence ----> edit plan ----> (later: compiler, timeline, backends)
```

## Public API (`application/contracts.py` is the only importable file)

```python
planner = container.resolve(EditPlanner)  # blocking: run as a job
result = planner.execute(
    PlanEditCommand(
        (asset_id,), EditConfig(profile="talking_head", target=TargetDuration(high=90.0))
    ),
    ctx,
)
plan = result.value.plan  # EditPlan (typed, immutable); result.value.created is False on a reuse

for clip in plan.track(TrackKind.VIDEO):  # main picture layer; layer 1 holds cover footage
    clip.source, clip.timeline_start, clip.timeline_duration
for d in plan.decisions_for(Action.CUT):  # why, how strongly, how safely
    d.rationale, d.signals, d.executability, d.evidence
plan.issues, plan.checks, plan.constraints  # what to review, what could not be checked

EditCatalog.latest(asset_id, profile="talking_head")  # newest stored plan, nothing recomputed
EditCatalog.history(asset_id)  # every plan ever made, newest first
```

`PlanEditCommand(asset_ids, config, story_format, story_plan, acquire, project_id, overrides, carry_overrides,
use_audio, use_video, use_alignment, content_analyses, audio_timelines, video_analyses, alignments)`.
Results the caller already holds are used as given; everything else is READ from its owner's catalog.

## What it reads, and what it never does

| Input | How obtained | If absent |
| --- | --- | --- |
| Story plan | supplied, else newest stored for the recordings (`StoryCatalog`); `Acquire.REQUEST` asks `StoryPlanner` | `Err(MissingStoryPlan)`; never a silent fallback |
| Content analysis (sentences, scores, dependencies) | supplied, else `ContentCatalog` | the story's beats become coarse units; every decision is a suggestion |
| Audio timeline (words, pauses, events, emphasis) | supplied, else `AudioCatalog` | no pause/filler trims; cut points rest on sentence times, flagged |
| Video analysis (shots, continuity, usability) | supplied, else `VideoCatalog` | no cover footage qualifies; continuity is `None` |
| Alignment (speaker visibility) | supplied, else `AlignmentCatalog` | visibility unknown, noted |
| Inspection (duration, frame rate, VFR, streams) | stored only (`InspectionCatalog.find`, never probes) | no frames offered; ranges unchecked against the end |

The story plan is validated (`story_architecture.validate_plan`) before use; an invalid one is
`Err(InvalidStoryInput)`. It transcribes, analyses, aligns, probes and renders nothing (an architecture test
forbids the owners' analysers and `ProcessRunner`).

## Time conventions (`domain/timing.py`)

* **Source time** is exact seconds on the recording's container timeline (the alignment's common clock): a
  float from upstream becomes the nearest fraction with a denominator up to a million, stored as `Rational`.
* **Frames** are *derived*: `floor`/`ceil(seconds * fps)` on a constant, known frame rate. For variable or unknown
  rate no frame is offered. **Samples** are `round(seconds * sample_rate)`.
* Ranges are half-open `[start, end)`.
* `Precision` says where a time comes from: `frame_exact` (a shot boundary), `word_aligned`, `segment_aligned`,
  `sentence_span`, `estimated`. Only a shot boundary is frame-exact; nothing else claims frame accuracy.
* **Timeline time** is exact seconds from the start of the edit; timeline frames (at `plan.timebase`, the most
  common constant rate of the recordings, else an assumed 30 fps that says so) are rounded.

## The plan (`domain/model.py`, schema version 1)

`EditPlan`: ids and version/`supersedes`, `story` ref, `profile`, `timebase`, `sources`, `decisions`, `placements`
(clips on tracks/layers), `audio_bridges`, `coverage`, `effect_requests`, `constraints`, `issues`, `checks`,
`structural_proposals`, `pacing_notes`, `duration`, `confidence`, `inputs`, `stages`, `provenance`, `warnings`,
`limitations`, `overrides`.

* **Decision** = action (`keep cut shorten extend reorder emphasize cover reaction preserve_audio bridge_audio
  request_effect`), source range, section/beat, separate **signals**, `executability`, rationale, evidence refs.
* **Signals** are distinct and never merged: `keep`, `cut`, `shorten`, `reorder`, `cover_need`,
  `cut_point_confidence`, `continuity_risk`, `narrative_risk`, `engagement`. Each is strength of evidence in
  [0, 1] (not a probability) or `None` with a reason. **Missing is never zero.**
* **Executability**: `executable` only when meaning and boundary safety are established (a filler sentence, a recorded
  copy of kept material, a confirmed silence trim on forced-aligned words, specific cover footage with a
  non-negating passage). Any removal of content is `needs_review`; `unresolved` means evidence is missing.
* **Placements** are clips with separate source and timeline time; picture and sound of a run are linked clips with
  the same range and place. Cover footage sits on layer 1; a cutaway spanning a cut in the speech is an explicit
  `AudioBridge` (J/L). Overlap on one layer without a bridge is a validation error.
* **Requests** for later stages (`EffectRequest`: transition, graphic, music cue, reframe) state intent and timing,
  bounded per minute; nothing is designed here.
* **Validation** (`domain/validation.py`): errors (invalid range, unknown reference, order violated, essential or its
  qualification dropped, overlap/gap, inconsistent duration, unsupported concept) make the plan invalid and it is
  never stored; warnings, recommendations and unresolved items are kept. `checks` lists what was verified and what
  could not be (`not_checked`: black frames without video analysis, silence at joins, rendered sync).

## How the plan is made (`domain/engine.py`, pure)

1. **Units** (`units.py`): sentences in story order, with section, beat function, spoken extent. **Hard constraints**
   (key information and qualifications, context a section needs, question/answer, premise/payoff, the profile's
   protected functions) make a sentence unremovable.
2. **Judgement** (`judge.py`): a sentence is proposed for removal only if unprotected, `cut` reaches the profile
   threshold and beats `keep` by a margin; filler, greeting, repeats that add nothing, a repeat the story marks,
   content removal score, false starts. Dependencies are then restored (`requires_context`, depended-on segments,
   qualifiers); an essential section is never empty. A person's overrides are applied here.
3. **Target length** (`tighten.py`): optional/repeat passages by lowest `keep`, then (proposal; applied only with
   `allow_structural`) an unneeded supporting section. Essential material is never removed to meet a target; a miss is
   reported (`unmet_constraints` for a hard target, a warning for a soft one).
4. **Assembly** (`assemble.py`, `cutpoints.py`): runs of adjacent kept sentences; silence over the profile limit and
   isolated fillers are trimmed (kept: pauses before emphasised words or payoffs, gaps with laughter or breath); cut
   points sit in the silence, never inside a word, on a shot boundary or frame when one fits, with confidence and
   alternatives; a section change splits a run with no cut.
5. **Cover** (`coverage.py`): the story's visual opportunities; candidates must exist in the video analysis, not be
   the shot on screen, be usable, show no speaking face, be long enough; a passage that negates or doubts what it
   names (or a weak association) makes it a suggestion. **Continuity** (`continuity.py`): measured between neighbouring
   shots, heuristic inside one shot, `None` elsewhere. **Effects** (`effects.py`) and **pacing** (`pacing.py`): the baseline section notes, replaced by the ranked
   recommendations of the stored pacing analysis when there is one (see "Pacing input").
6. **Cold open**: the story's recommended opening plays first when it is not the natural start.

## Profiles (`domain/profile.py`)

`generic`, `top_n`, `product_introduction`, `review`, `unboxing`, `talking_head`, `tutorial`, `interview`, `podcast`,
`documentary`, `short`, `montage`. A profile is data: pause limits, filler/repeat policy, protected story functions,
cover appetite, effects budget, whether jump cuts are part of the style. It never switches off a hard constraint.
Default: the profile named like the story's format, else `generic`. Pass your own `EditingProfile` for one run.
`TargetDuration(low, high, mode)`: `soft` (preference) or `hard` (constraint); without one, the story's target is a
soft preference.

## Manual overrides, cache, history

`ManualOverride(decision_id, KEEP|CUT, note)` (decision ids are `dec:<asset index>:<unit id>`) replaces the automatic
decision, is marked `origin=manual`, and is **carried through replans** of the same recordings and profile unless the
caller replaces it or sets `carry_overrides=False`. If a manual decision removes something the story needs, the
validation warns; it does not overrule the person.

The plan is a derived Media Library document of the first recording (`document_type: edit_plan`). Its fingerprint
covers the story (id, version and every fact the editor reads), the identity of every analysis used, the whole
configuration and profile, the overrides, any advisor and the editor/schema/vocabulary versions; time, job and device
are not in it. A hit returns with no work. Any other key makes a **new** plan with `plan_version` and `supersedes`;
earlier plans are never changed. Bump `EDITOR_VERSION` when a rule or threshold changes. Decisions are computed per
sentence, so changing one override changes only that decision and its neighbours' boundaries.

## Pipeline

Opt-in task `edit_plan` (`--stages edit_plan`, `--edit-profile`, `--edit-target`, `--edit-target-hard` in
`scratch/run_pipeline.py`): light lane, **needs `story`**, wants alignment, video analysis and inspection, runs on
the in-memory upstream results with `Acquire.STORED`. It is not in the default stages: it is a decision that only
means something with a chosen profile and target, so a default run does not store a plan nobody asked for. `partial`
when a hard constraint is not met. The pipeline is per asset, so it plans the edit of one recording's story; a story
of several recordings is planned through `EditPlanner` directly (`story_plan=` with several source assets).

## Pacing input (`pacing_intelligence`)

The editor reads the stored pacing analysis for the story's anchor recording through `PacingCatalog` (or receives one in
`PlanEditCommand.pacing`; `use_pacing=False` ignores it). It is evidence, never an order: `application/views.pacing_facts`
reduces it to `PacingFacts` (pause verdicts with the length to keep, ranked advice, sections to tighten). An analysis of
another recording is ignored and recorded as such. The facts' identity is part of the cache key, so a changed analysis
makes a new plan version, and the input is recorded as `InputRecord(kind="pacing_analysis")`.

* A pause the analysis calls *meaningful* is kept, with a note saying why.
* A pause it calls *dead air* or *long* is tightened to the length it proposes, below the profile's own limit; the editor's
  checks still apply (a pause before a payoff is never trimmed) and the trim is `needs_review` unless the editor confirms
  the silence itself.
* A reveal it wants held is extended where the recording has room (decision `extend`).
* Sections it wants tightened are proposed for cutting first when a target length must be met.
* The remaining advice is passed on as ranked `PacingNote`s (`level="pacing"`, at most 25); the baseline section notes are
  then skipped. Stage `pacing` is `ok`, or `not_available` with the baseline in force.

## B-roll input (`broll_intelligence`)

The editor reads the stored B-roll analysis for the story's recordings through `BrollCatalog` (or receives one in
`PlanEditCommand.broll`; `use_broll=False` ignores it; an analysis of other recordings is ignored and recorded).
`application/views.broll_facts` reduces it to `BrollFacts` (its identity is part of the cache key; input record
`broll_analysis`, stage `broll`):

* For a story visual with candidates, they come FIRST (best first) and the alignment's same-recording shots stay as
  fallbacks. A candidate B-roll found to satisfy the need has grade `broll_direct` (can be `executable`); an illustrative
  one is `broll_illustrative` (always `needs_review`, with B-roll's caution on the cover). Every candidate is still
  validated against the video analysis like any other.
* Footage of OTHER recordings is loaded as cover evidence (video analysis of the version that was analysed, attributed
  to the original asset), judged, placed on layer 1 and named in `plan.sources`. It never joins the story: timebase,
  units and cuts use the story's recordings only.
* The suggested part of the shot (`hint_start`/`hint_end`, estimated) is honoured as far as the shot, its settling head
  and the cover length allow. When the story gives a section as a visual's source, the statement B-roll found inside it
  narrows the covered passage.
* Satisfied B-roll places the story did not name become `PacingNote(level="broll")` (at most 15); they are not placed.
* `shot already on screen` is now judged only against shots of the passage's OWN recording (it previously compared the
  times of any recording, which refused all other-recording cover).

## Continuity input (`continuity_intelligence`)

`continuity_intelligence` judges the cuts of a PROPOSED edit, so it runs after a plan exists (opt-in `continuity` task)
and the editor reads its stored result on its NEXT planning, through `ContinuityCatalog` (or
`PlanEditCommand.continuity`; `use_continuity=False` ignores it). `application/views.continuity_facts` reduces it to
`ContinuityFacts` (a verdict per judged cut, notes for surfaced findings; its identity is part of the cache key; input
record `continuity_analysis`, stage `continuity_analysis`):

* A cut the analysis judged gets its risk as a MEASURED `continuity_risk` signal (reason: the analysis' finding), which
  replaces the baseline guess for that cut. A cut it could not judge, or never saw, keeps the baseline. Cuts are matched
  by source place (recording, where the outgoing clip leaves, where the incoming one resumes).
* A cut whose risk reaches the profile's review level gets an editing issue (`IssueKind.CONTINUITY`).
* Surfaced findings with their best repair become `PacingNote(level="continuity")` (at most 15). Nothing is applied: a
  repair belongs to the editor's own checks or to a person.
* A finding a person rejected is not a risk; one the analysis rates probably deliberate is information, not a note.

`EDITOR_VERSION` is 4.

## Future modules (not built)

The editor still has optional, **unimplemented** `Advisors` (`CoverageAdvisor.rank`, `ContinuityAdvisor.assess`); an
answer is validated like any other candidate, an advisor's identity is part of the cache key, and `None` means no
opinion. No advisor is wired, no package reserves a name, and a plan without them is complete. (The former
`PacingAdvisor` was replaced by the pacing input above. `continuity_intelligence` is not wired as the `ContinuityAdvisor`:
it judges a finished proposal, so it is read as stored evidence instead.) Reframing, music and sound design, transitions, graphics, style, the compiler, the
timeline builder, the Resolve adapter, quality control and revision are other stages: the editor only *requests*
effects and states intent. A future revision stage can consume findings by passing `ManualOverride`s and a changed
configuration; nothing here compares a plan with a rendered result.

## Known limitations (unverified behaviour)

* Validated on synthetic recordings and hand-built analyses plus real Content Intelligence, Story Architecture and
  Media Library code. **No real footage, real speech/vision model or real long recording has been tested**; edit quality
  on real material is unmeasured.
* Meaning preservation is judged from the story's structure, not by understanding words: every content removal is a
  suggestion. English and German filler/greeting/negation cues only.
* Cut-point confidence and every threshold are design-choice heuristics; they have not been calibrated on real speech.
* The editor's own continuity is shot-level; subject jumps within one shot are a heuristic. Identity, eyelines, direction
  and sound at the cut are judged only by `continuity_intelligence` (see its README) and reach the editor on a second
  pass, because the analysis judges a plan and cannot be an input of that same plan.
* Cover footage comes from the shots the alignment ranked and, when a stored B-roll analysis exists, from its candidates
  (any recording of the library). Without one, other recordings' shots are not searched. Beat/music synchronisation is requested, not computed (no music analysis exists; the pacing analysis only has an
  envelope-rhythm baseline).
* The estimated length is the sum of clips; no timeline has been compiled to confirm it.
* Planning 3 000 synthetic sentences takes about 1 s; its stored document is about 7.6 MB and reads back in about 1 s.

## Testing

```
uv run pytest tests/unit/editing_intelligence tests/unit/media_pipeline tests/architecture/test_editing_intelligence_boundaries.py
uv run pytest tests/integration/editing_intelligence   # real Media Library; needs FFmpeg
uv run mypy
```

`tests/support/edit_fakes.py` builds recordings (`talk`), video analysis (`with_video`) and stories (`story`) from a
description of sentences and gaps. No test needs a model or a network.

## Adding things

* **A profile:** add a `_profile(...)` entry in `profile.py` (its `problems()` must be empty).
* **A rule or threshold:** change it in the owning step and bump `EDITOR_VERSION`.
* **An enum member or plan field:** `values.py` / `model.py`, bump `VOCABULARY_VERSION` or `SCHEMA_VERSION`.
