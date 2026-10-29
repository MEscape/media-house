# Continuity Intelligence

Judges the cuts of a PROPOSED edit and explains what could distract or confuse a viewer: where the picture
jumps, the look or light breaks, a direction or eyeline flips, an action or object skips, the person or product
differs, the sound is cut through or steps, or the chronology runs backwards. It answers *does this cut hold
together, what is the evidence, is it probably deliberate, and what could repair it*. It diagnoses and
recommends: it changes no media, timeline, render or decision. `editing_intelligence` decides what to apply;
downstream modules execute it.

A visually different cut is not automatically a bad cut. Every issue carries an ESTIMATE of how likely it is
deliberate, with the reasons; a real but probably deliberate difference is kept as information, not hidden and
not surfaced.

## Public API (`application/contracts.py` is the only importable file)

```python
sequence = ProposedSequence(edit_plan_id, edit_plan_version, clips=(ProposedClip(...), ...))
command = AnalyzeContinuityCommand(sequence, ContinuityConfig("cinematic_dialogue", strictness=1.0))
result = analyzer.execute(command, ctx)  # blocking (library reads): run it as a job
analysis = result.value.analysis  # ContinuityAnalysis; result.value.created is False on a cache hit
for issue in analysis.surfaced():  # worth the editing stage's attention (not info, not rejected)
    issue.type, issue.level, issue.spans, issue.timeline_at
    issue.scores, issue.detectability_confidence, issue.intentional_style_likelihood
    issue.explanation, issue.why_it_matters, issue.evidence, issue.repairs, issue.recommended
analysis.unresolved, analysis.compatible_pairs, analysis.metrics, analysis.transitions
analysis.risk_at(out_asset, leaves, in_asset, resumes)  # one cut's risk, for a consumer
ContinuityCatalog.latest(asset_id, profile=..., edit_plan_id=...)  # read a stored analysis
```

The input is continuity's OWN contract (`ProposedSequence`/`ProposedClip`: exact source ranges on each
recording's common clock, and separate timeline places). The caller builds it from the edit plan (the pipeline
does: `media_pipeline/application/sequence.py`); this module never imports the editing stage, so editing can
read its results without a cycle. A source library alone has no adjacent cuts: only a proposed sequence does.

## What it reads, and what it never does

| Evidence | Owner | Used for |
|---|---|---|
| subject place/size/travel at the cut, face yaw and speaking cue, framing, light, scene, objects, text, embedding (`edge_state`) | `video_intelligence` | jump cut, look, light, direction, eyeline, action, identity |
| word the cut falls inside, breath, level and noise floor, nearest pause | `audio_intelligence` | clipped speech, audio discontinuity, cleaner cut points |
| clock correction, sync probe verdict | `audio_visual_alignment` | container clock, sync uncertainty |
| confirmed alternate takes / overlaps | `asset_relationships` | related takes named as repair directions |
| whether a cutaway exists for the place | `broll_intelligence` | the cover repair is `checked`, `unavailable` or `unchecked` |
| product names in text on screen | `content_intelligence` (`designations`) | product-model mismatch |

It never runs a producer (architecture test). The domain imports no other module. A value an owner could not
state is `None`, never read as "matches" or "differs"; a cut it cannot decide becomes an `Unresolved` item naming
what is missing. Cutaway enter/exit points are not judged as cuts (an insert is intentionally different).

## Upstream capability added for it

`video_intelligence.edge_state(analysis, seconds)` (contract): the frame at one moment. Subject = the shot's main
subject track (else the largest person), place from the nearest track point (only within 0.5 s of the track),
direction from the points within 0.4 s (needs two), face cues, framing, light and the embedding from the shot. A
moment the analysis did not track gives no subject, not a guess. The audio side needed no upstream change.

## Issues (`domain/detect.py`; thresholds are named, uncalibrated design choices)

`jump_cut`, `visual_discontinuity`, `screen_direction`, `eyeline`, `action_continuity`, `object_state`,
`identity_mismatch`, `lighting_mismatch`, `clipped_speech`, `audio_discontinuity`, `sync_uncertain`,
`temporal_inconsistency`, `narrative_confusion`; scope `transition`, `sequence` (chronology) or `section`.

## Scores and what is estimated

Separate, each in [0, 1] or `None` with a reason (definitions in `domain/values.py`): `visual_discontinuity_score`,
`jump_cut_severity`, `spatial_continuity_score` (higher = better), `action_continuity_score` (higher = better),
`identity_mismatch_risk`, `lighting_mismatch_score`, `audio_discontinuity_score`, `temporal_inconsistency_score`,
`narrative_confusion_risk`. Four other answers are never merged with them: `level` (severity: info / low / medium /
high), `detectability_confidence` (evidence coverage, ordinal), `intentional_style_likelihood` (an estimate from the
profile prior, story context and the detector's hints, with `style_reason`), and each repair's `difficulty`. None is a
calibrated probability.

Level = strength x the profile's weight for that kind of break x `(1 - 0.8 x style)`, against the profile's
thresholds scaled by `strictness` (0.5 lenient .. 1.5 strict). Identity is a verdict, not a score:
`confirmed_mismatch` only from text on screen naming another model of the same line; appearance clusters give at
most `probable_mismatch`; cross-recording person differences stay `uncertain` (unresolved), because similar looks are
not identity.

## Repairs

Per issue, ranked checked > unchecked > unavailable, then by difficulty: cut-point adjustment (a real pause found in
the sound), J/L cut, B-roll cover, alternate take / shot, reframing, exposure / colour correction, audio crossfade,
room tone, ambience bridge, speech review, sync check, identity check, order review, and `keep_intentional` when the
style estimate is high. Each names its OWNER and how far it was examined: `checked` (something concrete backs it),
`unchecked` (a direction; an alternate take is named but its range was not compared), `unavailable` (the module does
not exist: `reframing_intelligence` is not built; or looked and found nothing). A repair that conflicts with the story
(reordering across a section boundary) says so in `conflicts`.

## Profiles (`domain/profile.py`)

`generic talking_head interview_podcast product_review unboxing tutorial documentary cinematic_dialogue fast_montage
shorts_reels top_10_ranking` are data: per-issue weights, style priors, level thresholds and a cross-recording prior.
They change the level and the style estimate, never the measurements. The story format picks the profile.

## Storage, cache, incremental analysis, decisions

A JSON document `continuity_analysis` (schema 1), a derived asset of the sequence's first recording. Each cut has a
fingerprint (ranges, flags, the identity of every analysis it rests on, related takes, cutaway availability,
configuration, versions). Cache key = every cut fingerprint + plan id/version + decisions + configuration: an
identical request returns the stored result (`created` False). A changed plan makes a NEW analysis
(`analysis_version`, `supersedes`; earlier ones are kept); cuts whose fingerprint is unchanged are carried over
WITHOUT loading or judging their evidence (`metrics.reused`), and their issue ids are stable
(`is:<fingerprint>:<type>`). A person's accept/reject (`IssueDecision`) follows the issue through every reanalysis.
Limit: the analyses of the recordings are still read for the cuts that are judged; reuse saves the judging, not the
catalog reads.

## Pipeline and editing

Task `continuity` (`--stages continuity`, `--continuity-profile`, `--continuity-strictness`): light lane, needs
`edit_plan`, wants B-roll, alignment, audio and video analysis and the inspection. **Opt-in**: it judges a decision
that only exists once an edit is chosen, and it runs AFTER the edit plan (it cannot be an input of the plan it
judges). `editing_intelligence` reads the stored result on its NEXT planning (`ContinuityCatalog` or
`PlanEditCommand.continuity`): a cut the analysis judged gets its risk as a MEASURED `continuity_risk` signal (and an
editing issue when it reaches the profile's review level); surfaced findings with their best repair become
`PacingNote(level="continuity")`; the analysis' identity is part of the plan's cache key; stage `continuity_analysis`.
Cuts the analysis never saw keep the editor's baseline. Findings are shown, never applied automatically. A run needs
two passes to use what the first found; this is the smallest honest order, because a plan cannot depend on the
analysis of itself.

## Cost

Reads stored results only; no model is run (`metrics.model_calls` is 0). Per judged cut: two `edge_state` lookups, two
audio windows, a handful of arithmetic checks. Judging 18 labelled cuts takes about 4 ms. The cost that grows is
reading the recordings' analyses once per run.

## Evaluation and known limitations

* `tests/support/cont_eval.py` + `tests/unit/continuity_intelligence/test_evaluation.py`: 18 labelled SYNTHETIC cuts
  (talking-head jump cut accepted vs flagged cinematically, direction reversal, two product models, exposure step, a
  word cut through, level steps, a deliberate montage, a flashback, an insufficient-evidence cut ...). Issue precision
  and recall are 1.0 and pairwise severity-ranking agreement is about 0.83. The set was written with the rules, so
  this guards regressions and says NOTHING about real footage; confidence is not calibrated.
* **Not validated**: real footage, real tracking/embedding quality, the sign convention of head yaw (the eyeline check
  asks that question in its issue), any threshold.
* Subject travel needs a tracked subject within 0.5 s of the cut; shot-level face/light/embedding cues are medians, not
  frame values. Action continuity is a motion-extrapolation heuristic, not action-state tracking. Object changes use
  detector labels (low confidence).
* Cuts between shots of one recording whose scene is not known are judged for look and sound, but direction and object
  checks stay unresolved.
* Alternates are not searched: repairs name related takes as directions. Sound-effect and music continuity, 180-degree
  geometry beyond travel/eyeline, and setup/payoff chronology beyond source order are not judged.
* The sync verdict is the recording's own alignment probe; separately recorded audio without an alignment is `unknown`.

## Testing

```
uv run pytest tests/unit/continuity_intelligence tests/unit/video_intelligence/test_edge_state.py
uv run pytest tests/unit/media_pipeline/test_continuity_task.py tests/unit/editing_intelligence/test_continuity_input.py
uv run pytest tests/integration/continuity_intelligence   # real library, FFmpeg
uv run pytest tests/architecture/test_continuity_intelligence_boundaries.py
```

Builders: `tests/support/cont_fakes.py` (sides, cuts, one-call run), `tests/support/cont_library.py` (real library with
synthetic tracked analyses), `tests/support/cont_eval.py` (the labelled set and the report).

## Adding things

* **An issue type**: add it to `IssueType`, write a detector in `domain/detect.py` returning a `Finding`, give it repairs
  in `domain/repair.py`, weights/priors in the profiles, bump `ANALYZER_VERSION` and `VOCABULARY_VERSION`.
* **A profile**: add it to `PROFILES` (and `STORY_FORMAT_PROFILE`), bump `ANALYZER_VERSION`.
* **A threshold**: change it in `domain/detect.py` (named constants), bump `ANALYZER_VERSION`.
