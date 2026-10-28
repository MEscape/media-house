# Pacing Intelligence

Measures how the rhythm of a recording helps or hurts its material and recommends how to improve it. It answers
*where is the rhythm wrong, how sure are we, what would help, and what would it cost*. It decides no edit: which
operation applies a recommendation is the editing stage's decision (`editing_intelligence`), and nothing here touches a
timeline, renders, or plans a story.

## Public API (`application/contracts.py` is the only importable file)

```python
command = AnalyzePacingCommand(
    (asset_id,),
    PacingConfig(
        profile="tutorial", strategy=Strategy.CONCISE, target=TargetDuration(maximum=90.0)
    ),
)
result = analyzer.execute(command, ctx)  # blocking, cheap, pure computation: run it as a job
analysis = result.value.analysis  # PacingAnalysis; result.value.created is False on a cache hit
analysis.recommendations  # ranked by priority, each with evidence/confidence/trade-offs
analysis.pauses_of(PauseVerdict.DEAD_AIR)  # what each silence is
analysis.duration, analysis.target  # current/proposed runtime, target check
catalog.latest(
    asset_id, profile=..., story_plan_id=...
)  # read a stored analysis, nothing is computed
```

`PacingAnalyzer` computes and stores; `PacingCatalog` reads (`latest`, `history`, `load`). Errors are `Result`s:
`MissingEvidence` (no speech and no picture), `InvalidPacingConfig`, `InvalidPacingAnalysis` (the analysis rejected
its own result), `InvalidPacingDocument` (a stored document is unreadable).

## What it reads, and what it never does

Everything comes from the owner's stored result through that owner's catalog; pacing never runs a producer
(`AudioAnalyzer`, `VideoAnalyzer`, `ContentAnalyzer`, the aligner, the inspector, the story planner):

| Evidence | Owner | Used for |
|---|---|---|
| words, timings, per-word rate, pauses, events, emphasis, speaker, energy rhythm | `audio_intelligence` | speech rate, pause verdicts, beats |
| shots, motion (`static_fraction`), visual interest | `video_intelligence` | cut rate, static pictures, visual activity |
| units, `INFORMATION_DENSITY`, relations (`Adds`, requires-context) | `content_intelligence` | density, repetition, load |
| alignment corrections | `audio_visual_alignment` | speech/picture timing precision |
| stored inspection (fps, duration) | `media_inspection` | frame grid, recording length |
| story sections, beats, format | `story_architecture` | section pacing, premise, reveals, target format |

A missing input is recorded as `not_available` (the result is `partial`, with an `Issue` and a stage report); a pacing
analysis without a story simply has no section findings and says so. The story is **never requested**: pacing reads the
stored one, and the pipeline orders it after the story task.

The domain imports no other module. The application maps each owner's contract objects into plain facts
(`application/views.py`); the domain works only on those. Architecture tests enforce this
(`tests/architecture/test_pacing_intelligence_boundaries.py`).

## What is measured at each scale

* **Words and pauses** (`domain/speech.py`): every gap at least `min_gap` long gets a verdict `natural`, `meaningful`,
  `long` or `dead_air`. A pause is *meaningful* when a reaction event falls in it, or it precedes an emphasised word, follows a
  question, or precedes a payoff beat. Silence before the first word and after the last word is **not** a pause in speech and
  is not analysed (word-level audio only).
* **Passages** (one per content unit): speech rate (active and elapsed), pause ratio, information density, cognitive
  load estimate, visual activity, repetition, pace class (`slow`/`steady`/`fast`/`unknown`).
* **Shots and cuts** (`domain/picture.py`): shot length, static share, cut rate in 20 s windows (10 s step), runs of
  cuts above the profile's rate, and cuts against the audio energy rhythm.
* **Sections and narrative** (`domain/narrative.py`): runtime per section, time to the premise, setup share, whether
  demonstrations get enough time, reveal holds, abrupt transitions.
* **Whole recording** (`TimelineAnalysis`, `AttentionBin`): per-bin change counts. This is a **prediction from the
  material**, never audience retention.

Score definitions (basis: measured / derived / heuristic) are the module docstring of `domain/values.py`. No score is a
calibrated probability; a value that could not be computed is `None` with a reason, never `0`.

## Recommendations

Fourteen types: `tighten_pause`, `preserve_pause`, `allow_processing_time`, `tighten_delivery`, `reduce_repetition`,
`add_visual_change`, `hold_shot`, `hold_demonstration`, `establish_premise_earlier`, `reduce_setup`, `hold_reveal`,
`add_section_breath`, `align_cuts_to_beats`, `restore_attention`. Each has a target span (exact `Rational` times on the
recording's own timeline), observed fact, proposal, reason codes, evidence references, confidence, benefit,
trade-offs, limits, a `Safety` class (`safe`, `needs_review`, `expands`) and the seconds it would add or remove.

Ranking is affected seconds (2 s when not estimable) times confidence; when the material misses the target length, the
matching direction counts double and `contributes_to_target` marks the recommendations that together close the gap.
Savings that two findings share (a pause inside a passage) are counted once. When the current runtime is the story's own
length, only recommendations inside a story section change it: material the story left out is not part of that runtime.

## Profiles, strategies, target

* **Profiles** (`domain/profile.py`): `generic` (neutral default), `top_10_ranking`, `product_review`, `unboxing`,
  `talking_head`, `tutorial`, `interview_podcast`, `documentary`, `shorts_reels`, `cinematic_montage`. A profile is a table
  of tolerances (speech-rate band, natural/dead-air/keep pause, load limit, cut rate, static-shot limit, demonstration minimum,
  premise deadline, setup share, reveal hold, section breath, beat preference). When none is given, the story's format picks
  the profile (`STORY_FORMAT_PROFILE`), otherwise `generic`. Nothing is inferred from the footage itself.
* **Strategies**: `concise`, `balanced` (default), `detailed`, `high_energy`, `cinematic` scale the profile's tolerances
  (`STRATEGIES`). All five are computed on every run; `analysis.projections` shows what each would do to the runtime, the
  selected one drives `recommendations`.
* **Target duration**: `minimum`, `maximum`, `preferred_low`, `preferred_high`, all optional. The result states `within`,
  `over` or `under` (inside the limits but outside the preferred range is `within` with a note) with the shortfall and
  which recommendations contribute. A target is never forced: it ranks
  and reports.

## Storage, cache, history

The analysis is a JSON document (`document_type: pacing_analysis`, schema version 1) stored as a derived asset of the
first recording in the Media Library. The cache key is every input identity, the story's facts (not its storage id), the
project, the resolved profile, strategy and target, and `ANALYZER_VERSION`/`SCHEMA_VERSION`/`VOCABULARY_VERSION`.
Identical request: the stored document is returned and `created` is `False`. Changed evidence, story, profile, strategy or
target: a new analysis whose `analysis_version` counts up and whose `supersedes` names the previous one for the same profile
and recordings; earlier documents are never changed or removed. Bump `ANALYZER_VERSION` whenever a rule, threshold or
formula changes.

Several recordings are analysed side by side on **separate clocks**: nothing places one recording's time on another's.

## Energy rhythm baseline (upstream: `audio_intelligence`)

`AudioIntelligenceTimeline.energy_rhythm()` (`domain/analysis/rhythm.py`) estimates a regular pulse from the loudness
envelope outside speech (autocorrelation of positive level rises, 60-180 BPM) and a beat grid. It is an **envelope
baseline, not music analysis**: no key, no downbeat, no genre; weak or irregular material gives low strength and the
beat recommendations stay off (`min_rhythm_strength`).

## Pipeline and editing

Task `pacing` (`--pacing-profile`, `--pacing-strategy` in `scratch/run_pipeline.py`; `--edit-target` also sets the target
maximum): light lane, needs the audio analysis, wants content/video/alignment/story/inspect when they are in the graph. It is
**on by default** because it is a pure read of stored results that decides nothing and costs about a second per thousand
sentences; recordings without audio get no pacing task. `edit_plan` wants it, so a run orders pacing first and hands
the analysis over.

`editing_intelligence` consumes the analysis as evidence through `PacingCatalog` (or the pipeline payload): pauses
marked *meaningful* are kept, *dead air* is shortened below the profile limit, reveal holds are extended where the recording
has room, and sections the analysis wants tightened are cut first when a target must be met. Each of these still passes
the editor's own checks and every content cut stays a suggestion. The editor's earlier built-in `PacingAdvisor` was replaced by
this input.

## Known limitations (unverified behaviour)

* Validated on synthetic recordings, hand-built analyses and real Content Intelligence, Story Architecture and Media
  Library code. **No real footage, real speech/vision model or long recording has been tested**; recommendation quality on
  real material is unmeasured, and every threshold is a design choice that has not been calibrated.
* Information density and repetition are heuristics from the content analysis; cognitive load is an estimate of needed
  time, not a measured load. The attention curve is a prediction, not audience data.
* Picture findings need a video analysis; without one only speech is judged (`partial`).
* Cuts against beats use the energy envelope baseline above, not a music analysis.
* Pause verdicts rely on word-level timing; segment-level audio gives lower confidence and no head/tail filtering.
* Planning 3 000 synthetic sentences with all five strategies takes about 4 s.

## Testing

`tests/unit/pacing_intelligence` (speech, picture/narrative, contract, use case against fake owners with REAL upstream
results, scale), `tests/unit/media_pipeline/test_pacing_task.py`, `tests/unit/editing_intelligence/test_pacing_input.py`,
`tests/integration/pacing_intelligence` (real composition root, Media Library and FFmpeg import; scripted audio),
`tests/architecture/test_pacing_intelligence_boundaries.py`. Builders: `tests/support/pace_fakes.py`.

## Adding things

* **A recommendation type**: add the member to `RecommendationType`, bump `VOCABULARY_VERSION` and `ANALYZER_VERSION`,
  generate it in `domain/recommend.py`, add its reason codes, and decide how `editing_intelligence/domain/facts.py` treats it.
* **A profile**: add it to `PROFILES` (and a format to `STORY_FORMAT_PROFILE` if a story format maps to it), bump
  `ANALYZER_VERSION`.
* **A new evidence source**: add a fact in `domain/facts.py`, map it in `application/views.py` from the owner's
  `application.contracts`, record it in `inputs`, and include its identity in the cache key.
