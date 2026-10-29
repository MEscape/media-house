# B-roll Intelligence

Finds, for the passages of a story that would gain from supplementary footage, the footage of the **whole
library** that genuinely supports them: which asset, which part of it, how well it fits, and why. It
answers *is there footage that shows this, where in it, how sure are we, and if not, what is missing*. It
decides no edit: whether a recommendation is used is the editing stage's decision (`editing_intelligence`);
nothing here cuts, renders, builds a timeline, analyses media or plans a story.

A correct "no suitable footage" is a result. A clip that is only related is labelled illustrative and is
never promoted to evidence.

## Public API (`application/contracts.py` is the only importable file)

```python
result = analyzer.execute(
    AnalyzeBrollCommand(
        (asset_id,),
        BrollConfig(profile="tutorial"),
        scope=LibraryScope(tags=frozenset({"b-roll"})),
    ),
    ctx,
)  # blocking (library reads, optional embedding models): run it as a job
analysis = result.value.analysis  # BrollAnalysis; result.value.created is False on a cache hit
for rec in analysis.recommendations:  # one per passage that needs footage
    rec.need, rec.purpose, rec.requires  # what the passage asks of footage
    rec.placement  # where in the TARGET recording, how long, which audio behaviour
    best = rec.best  # None when nothing qualifies
    best.asset_id, best.source  # the ORIGINAL asset and a source range (exact seconds)
    best.role, best.support, best.scores, best.evidence, best.warnings
analysis.gaps  # places the library cannot cover, with what was searched and what to film
analysis.no_change  # passages that should keep their picture, and why
BrollCatalog.latest(
    asset_id, profile=..., story_plan_id=...
)  # read a stored analysis, nothing computed
```

`AnalyzeBrollCommand(asset_ids, config, scope, story_format, story_plan, use_story, project_id, use_video,
use_alignment, use_pacing, content_analyses, audio_timelines, video_analyses, alignments, pacing,
shot_indexes)`. Results the caller already holds are used as given; everything else is READ from its owner.
Errors are `Result`s: `NothingToCover` (no passage asks for footage), `MissingTargetEvidence`,
`InvalidBrollConfig`, `InvalidBrollAnalysis` (the analysis rejected its own result), `InvalidBrollDocument`.

## What it reads, and what it never does

| Evidence | Owner | Used for |
|---|---|---|
| story visual opportunities (purpose, necessity, verbatim concept), sections, beats | `story_architecture` | the passages, their purpose, narrative fit |
| statements, b-roll opportunities, **references** ("this model"), **stance** (negation/doubt), designations | `content_intelligence` | the passage text, identity, what footage must not imply |
| the library's **shot index** per video; `TextEncoder`; `VisualTextEncoder` | `video_intelligence`, `content_intelligence` | what every shot shows, searched by words, picture embedding and description |
| recording facts, status, tags, groups, derivation | `media_library` | which footage exists, usable, and which asset is the original |
| speech/events of a candidate recording | `audio_intelligence` | `audio_suitability`, audio behaviour |
| clock correction, sound-action pairings, sync probe, speaking face | `audio_visual_alignment` | container clock, synchronised action sound, protecting the speaker |
| alternate takes / overlaps / duplicates | `asset_relationships` | distinct angles, novelty |
| visual-change places | `pacing_intelligence` | "variety" passages |

It never runs a producer (architecture test). A missing optional input narrows the result and says so (a stage
`not_available`, an input record, a pool limit); nothing is invented. The domain imports no other module.

## Upstream capabilities added for it

* `video_intelligence`: **`ShotIndexCatalog`** - a compact, derived, versioned document per asset (one row per
  shot: place, objects, description, read text, environment, quality/motion scores, face/speaking cue, events,
  half-precision embedding), keyed by the stored analysis, built on first use. Searching a library never parses
  full analyses. **`VisualTextEncoder`** - the CLIP text tower of the model that embedded the shots (one shared
  `ClipRuntime`).
* `content_intelligence`: **`designations`** (product-like names, e.g. "Pixel 9"), **`resolve_references`**
  ("this model" -> the named thing, `ambiguous`/`unresolved` kept as such, "the previous version" only when the
  context names an older one) and **`stance_of`** (negation / doubt / supposition cues).
* `editing_intelligence`: reads this analysis (see below) and a latent defect was fixed: the "shot already on
  screen" test never compared recordings, so footage of another recording could not qualify as cover.

## Staged retrieval (`application/pool.py`, `retrieval.py`, `domain/retrieve.py`)

1. **Pool**: the library is paged once (originals, active, capped by `LibraryScope.max_assets`); each asset must
   have a readable file and a stored video analysis (its shot index; an index found on a proxy or improved version is
   attributed to the original). An asset that cannot be searched is listed in `pool.skipped` with the reason.
2. **Words**: one inverted index over the shots' observations; each passage looks up its own reduced words
   (concept and names weight 1, passage 0.5, neighbouring statements 0.25 only for a thin or pointing passage).
3. **Pictures**: the passage in the shots' embedding space; one NumPy matrix product over every embedded shot; the
   similarity is also a standard score against the whole pool. Only if the footage's embedding model is the
   encoder's model (otherwise words only, and the result says so).
4. **Descriptions**: sentence-embedding similarity for a few shortlisted descriptions per passage.
5. **Repeats**: near-identical pictures among the shortlist.
Then only the shortlist is judged in detail and enriched with its recording's own sound and clock.

## Evidence versus illustration (`domain/match.py`, `select.py`)

A shot gets an **`EvidenceRole`** from its own observations: `direct_demonstration` (subject AND action observed:
events or measured movement), `direct_evidence` (the specific thing named is shown: identity confirmed or text
read), `specific_subject` (the kind of thing, not proven), `contextual_illustration`, `generic_illustration`,
`uncertain`, `contradictory` (another model of the same line). Rules: a passage naming a product (or pointing at
one) needs the shot's text/metadata to name the same one; a description never confirms identity; an embedding-only
match is at most contextual; the right subject standing still is not a demonstration; a negating passage cannot be
proven with footage.

**`Support`** is the gate and applies BEFORE scores order candidates: `satisfies` (meets what the need requires),
`illustrative_only` (listed, labelled, never promoted), `rejected` (kept only to explain a gap). Needs:
`required_evidence`, `demonstration`, `illustrative`, `variety`, `jump_cut_cover`, `no_visual_needed` (graphics,
already-covered passages, an emphatic speaking face, too-short passages). A passage with no satisfying candidate is
`missing_coverage` and, where it matters, a `CoverageGap`.

## Scores (definitions in `domain/values.py`)

Thirteen independent scores per candidate, each in [0, 1] or `None` with a reason (never `0` for unknown), with a
basis (`measured`, `derived`, `heuristic`, `model_estimated`): semantic_relevance, evidence_strength,
narrative_relevance, visual_quality, subject_visibility, composition_suitability, visual_activity,
temporal_suitability, continuity_compatibility (a basic environment comparison, **not** a continuity assessment),
duration_suitability, novelty, audio_suitability, confidence (ordinal, not a probability). `overall` is the weighted
mean of the scores that exist (profile weights renormalised; the missing ones are named). Hard refusals: contradicted
identity, measured usability below the profile floor, junk indicators, a speaking face, the shot already on screen,
past the end of the recording, too short. A candidate below `min_overall` is refused, not forced.

## Source ranges, placement, audio

* The range is a bounded part of the shot: the usable part (after the settling head and tail), centred on the
  moments that carry the evidence (action events; text appearing) with a lead-in and a hold; several separate stretches
  give up to two ranges. Only a whole shot has frame-exact ends (`shot_boundary`); anything else is `estimated`. The
  final in/out is the editing stage's.
* `source` is exact seconds on the footage's own common clock (`clock`: `container` when its alignment corrected it,
  else `video_analysis` with a warning). `SuggestedPlacement.target_start/end` is the passage on the TARGET
  recording's clock. Neither is timeline time.
* Audio: `retain_source_audio` only for an action sound its own alignment pairs with the action; `mute_source` when
  speech would clash; `retain_voiceover` when the footage's sound was analysed and is irrelevant; `flag_for_editor`
  when it was not analysed.

## Coverage gaps

`CoverageGap`: stable id, passage and story references, kind, why the library does not cover it (nothing related /
related but not proof / refused), how much was searched (`Searched`), what was considered and why refused,
importance and urgency, whether illustrative footage could be repurposed, what to film, and a fallback (keep the
main picture; do not cover a claim with unrelated footage). Optional illustrations without footage are left alone
unless the profile asks (`report_optional_gaps`).

## Profiles (`domain/profile.py`)

`generic tutorial product_review unboxing talking_head interview_podcast documentary top_10_ranking shorts_reels
cinematic_montage` are data: weights, quality threshold, insert length, whether illustration may stand for a
demonstration or for evidence (`documentary`/`product_review`: never; `cinematic_montage`: demonstrations only),
strict identity, speaker protection, variety appetite, jump-cut cover, context kept around an insert. The story's
format picks the profile (`STORY_FORMAT_PROFILE`); nothing is inferred from the footage.

## Storage, cache, history

JSON document `broll_analysis` (schema 1), a derived asset of the first recording. The cache key is the passages, the
identity of every searched asset's index and relations, the encoders (or their absence), the scope, the story, the
configuration and `ANALYZER_VERSION`/`SCHEMA_VERSION`/`VOCABULARY_VERSION`. Identical key: stored result, no search
(`created` is `False`). A new asset with an analysis, a new analysis of an asset, a changed story/profile/encoder makes
a NEW analysis (`analysis_version`, `supersedes`; earlier ones are kept). Every asset's derived documents (so a new audio
analysis or alignment of a candidate) and its relationships are part of the pool identity. Limit: invalidation is
whole-analysis, never per passage (the expensive part, the shot indexes, is cached per asset).

## Pipeline and editing

Task `broll` (`--stages broll`, `--broll-profile`): light lane, needs `content_analysis`, wants story, alignment, audio,
video analysis, pacing and inspection. It is **opt-in**: it reads every analysed asset of the library, which grows with
the library, and only means something once someone looks for footage. It produces nothing for the library it searches:
those assets need their own video analysis. A run with no passage for footage is `skipped_unavailable`.

`editing_intelligence` reads the stored analysis through `BrollCatalog` (or the pipeline payload): for each story
visual the candidates replace the alignment's baseline (kept as fallbacks), footage of OTHER recordings is loaded as
cover evidence and named in `plan.sources`, the suggested part of the shot is used, the passage is narrowed to the
statement B-roll found inside a section-wide source, `broll_direct` candidates can be `executable`, illustrative ones
are `needs_review`. Satisfied places the story did not name become `PacingNote(level="broll")`; they are not placed.

## Performance and cost

Words are looked up in one index (no scan per passage). Pictures: one matrix product per passage. Models run only on
passages and a shortlist. Measured on synthetic data (CPU, no models): 14 passages vs 333 shots in about 0.02 s; 60
passages vs 20 333 shots in about 0.3 s for the domain (index lookup + assessment); the real costs are reading one small
index document per asset and, when available, one text-encoder call per run. No new dependency: NumPy (already a
dependency) is confined to `infrastructure/numpy_vectors.py`.

## Evaluation and known limitations

* `tests/unit/broll_intelligence/test_retrieval_quality.py`: a **synthetic** labelled set (14 passages, 333 shots: 8
  demonstrations, 3 product claims with a wrong-model and a generic trap, 3 absent subjects): precision@1 = recall@3 =
  MRR = 1.0, misleading-recommendation rate 0, correct rejection 1.0, duplicates 0. It guards regressions; it says
  nothing about real footage.
* **Not validated**: real footage, the real CLIP/sentence models (the integration tests use toy encoders), real
  libraries, any threshold (all are uncalibrated design choices). The visual text encoder (`ClipTextEncoder`) has not
  been run against real shot embeddings here.
* Words are reduced by a small stemmer (English and German stop words); synonyms rely on the embeddings. Product names
  are recognised by shape (a name followed by a number or code); a product without a number is not an identity.
* Shots without observations (no labels, text, description) can only be found by their picture embedding.
* Continuity is a basic heuristic; sound synchronisation is claimed only from the footage's own alignment.
* Only story visuals are placed by the editing stage; other satisfied places are shown as notes.
* Multi-recording stories: each recording's passages are searched against the same pool, on separate clocks.

## Testing

```
uv run pytest tests/unit/broll_intelligence tests/unit/media_pipeline/test_broll_task.py tests/unit/editing_intelligence/test_broll_input.py
uv run pytest tests/integration/broll_intelligence   # real library and content analyzer; needs FFmpeg
uv run pytest tests/architecture/test_broll_intelligence_boundaries.py
```

Builders: `tests/support/broll_fakes.py` (passages, shots, one-call pure run), `tests/support/broll_library.py` (real
library with synthetic footage analyses, toy encoders).

## Adding things

* **A profile**: add it to `PROFILES` (and `STORY_FORMAT_PROFILE`), bump `ANALYZER_VERSION`.
* **A rule or threshold**: change it in the owning file (`match.py`, `ranges.py`, `scoring.py`, `select.py`), bump
  `ANALYZER_VERSION`.
* **A score or role**: add the member to `values.py`, score it in `scoring.py`, bump `VOCABULARY_VERSION`.
* **A new evidence source**: map it in `application/views.py` into a fact, record it in `inputs`, include its identity in
  the cache key.
