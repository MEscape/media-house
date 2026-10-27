# Story Architecture

Turns what the other modules already understand about a recording into **one validated story plan**:
the order the material should follow, what each part is for, which opening works, what is essential
and what is not, what is missing or at risk, and where a picture would help. It is a blueprint for
the editing stage. It **recommends and explains; it never cuts, trims or times anything**, and it
analyses nothing itself.

```
media_library -> media_inspection -> audio_intelligence / video_intelligence -> content_intelligence
                                                       \                          |
                                                        audio_visual_alignment ---+--> story_architecture --> editing stage
```

## Public API (`application/contracts.py` is the only importable file)

```python
planner = container.resolve(StoryPlanner)  # blocking: run as a job
result = planner.execute(PlanStoryCommand((asset_id,), StoryConfig("review")), ctx)
plan = result.value.plan  # StoryPlan (typed, immutable); result.value.created is False on a reuse

for section in plan.ordered_sections():  # the recommended order
    beats = plan.beats_of(section.section_id)  # what each passage does, with its source
opening = plan.opening(plan.recommended_opening)

StoryCatalog.latest(asset_id, format="review")  # newest stored plan, nothing recomputed
StoryCatalog.history(asset_id)  # every plan ever made for the asset, newest first
```

`PlanStoryCommand(asset_ids, config, project_id, acquire, use_alignment, llm, content_analyses, alignments)`.
Several `asset_ids` make one story **in the order given** (the order between recordings is not
inferred). `project_id` is a label the caller keeps; the repository has no project model yet.

## What it reads, and what it never does

| Input | How it is obtained | If absent |
| --- | --- | --- |
| Content analysis (topics, roles, scores, relations, key items, opportunities) | supplied, else `ContentCatalog.latest`; `Acquire.REQUEST` lets `ContentAnalyzer` produce it | `Err(MissingAnalysis)`; nothing is invented |
| Alignment (shots that go with a passage) | supplied, else `AlignmentCatalog.latest`; never requested | the plan is complete, visual opportunities just list no shots (`visuals: not_available`) |
| Wording model (optional) | `content_intelligence.TextGenerator` (the engine that module already owns) | `refinement: not_available` with the reason; the deterministic plan stands |

It does not transcribe, segment, embed, probe, decode, align or detect anything, and `inputs` on the plan
records how each input was obtained. Assets are referenced by library id only.

## Relations between recordings (`domain/links.py`)

For a story of SEVERAL recordings the planner reads how they relate from `asset_relationships`
(`RelationshipCatalog`, read only; `use_relationships=False` switches it off; one recording never asks).
A relation is used in proportion to its certainty, and the certainty is carried into the plan unchanged:

| Certainty | Source | What the plan does |
| --- | --- | --- |
| `recorded` | library or proxy record (`exact_duplicate_of`, `derived_from`, `proxy_of`) | the copy's sections become `redundant_candidate` (with the reason), an info issue says so |
| `confirmed` | a person confirmed a claim | alternate takes: a warning "use one"; overlap: "parallel coverage"; nothing is dropped or reordered |
| `evidence_supported` / `unverified` | a detector proposed it | noted as a possibility ("not confirmed, so the plan did not act on it"); no change to order or priority |
| `not_usable` | rejected or stale | listed with treatment `not_used`; never acted on |

A contradicted claim and a decided claim whose evidence changed since are flagged in the entry, not hidden.
A catalog that cannot answer is recorded in `inputs` and `warnings`; the plan is made without relations.
The relation revisions (id, status, grade, last change) are part of the cache key, so a confirmation or a
re-detection makes a new plan version and keeps the old one. `StoryPlan.asset_relations` lists every
relation with its treatment and affected sections.


## The plan (`domain/model.py`, schema version 1)

`StoryPlan`: `story_plan_id`, `plan_version`, `supersedes`, `project_id`, `target_format`,
`audience_intent`, `narrative_pattern` (observed functions, `source_order`, `recommended_order`,
`changes` with reasons), `main_message`, `premise`, `candidate_openings` + `recommended_opening`,
`sections`, `beats`, `key_information`, `supporting_evidence`, `ordering_constraints`,
`context_dependencies`, `setup_payoff_links`, `visual_opportunities`, `pacing_notes`,
`estimated_duration`, `completeness_issues`, `alternative_structures`, `confidence`, `inputs`,
`stages`, `provenance` (model, versions, configuration fingerprint), `warnings`, `limitations`.

* **Traceability.** Every beat, opening, claim, evidence link and section names a `SourceRef`
  (asset, segments, sentences, seconds on the original timeline, whether the time is exact). A
  recommendation resting on structure rather than a passage says `Basis.INFERRED`; text says
  whether it is `direct`, `paraphrase` or `inference`, and by which `Method`.
* **Missing is not zero.** An opening criterion upstream could not score is `None` with a reason.
* **Claims are never "verified".** `verification` is always `unverified`. "Unsupported" means no
  supporting passage exists in the recordings, and the issue text says exactly that.
* **Advisory only.** Priorities (`essential`, `supporting`, `optional`, `redundant_candidate`),
  scores and `confidence` are strength of evidence from design-choice formulas, not probabilities.
  A repeat is a candidate for the editor to weigh, never a deletion.
* **Stored form** is `domain/serialization.py` (`document_type: story_plan`, strict reader: unknown
  fields, members, schema versions are refused). Vocabulary additions are additive and bump
  `VOCABULARY_VERSION`.

## How the plan is made (`domain/engine.py`, pure: no clock, I/O or model)

1. **Sections and beats** (`sections.py`): the coarsest topics content intelligence found become
   sections; passages it left in no topic stay with the section before them. A role maps to a beat
   function (`hook` -> `HOOK`, `conclusion` -> `PAYOFF` ...); a section with no role gets one inferred
   `UNCLASSIFIED` anchor.
2. **Dependencies and order** (`structure.py`): upstream relations (`depends_on`, `answers`,
   `qualifies`, `continues`, `references` ...) and "requires context" sentences become ordering
   constraints (hard/soft, `must_precede` / `keep_adjacent`) and context dependencies. The recorded
   order is kept unless the format allows reordering **and** a hard dependency points forward; then
   only the needed section is pulled ahead of its dependant, and the change is listed with its
   reason. A cycle's closing edge is dropped and reported, so the emitted graph is always acyclic.
3. **Integrity** (`integrity.py`): claims and facts, their support (explicit relation = direct,
   evidence in the same section = inferred), contradictions (both sides kept, neither judged),
   unsupported claims, repeats and what they add, open questions, qualifications that the order
   separates from their claim, expected steps the format needs and the recording lacks.
4. **Message and openings** (`openings.py`): the main message is upstream's own summary (never
   written here); openings are real passages by strategy (`natural_start`, `premise_first`,
   `strongest_hook`, `question_first`, `result_first`), scored on clarity, relevance, promise
   alignment, credibility and curiosity, each with its risks (needs earlier sentences, claim is
   qualified later, unsupported). The recommendation is the best opening **without** risks, else the
   natural start. The natural start is always a candidate.
5. **Visuals** (`visuals.py`): upstream's b-roll/graphic opportunities plus any passage that
   demonstrates something; purpose, necessity (`essential` only when the passage says "look"), and
   the shots alignment ranked for that passage as evidence. No shot is chosen.
6. **Duration and pacing** (`pacing.py`): per-section duration is observed (first to last timed
   word, pauses included) or counted from words at the format's rate, and says which. Advice is one
   of a few narrative actions (add an example/demonstration/recap, break up, tighten, add context)
   for dense, slow, repetitive, under-explained, over-long and monotone stretches. The total is
   exactly the sum of the sections.
7. **Alternatives** (`alternatives.py`): only when different from the recommendation: as recorded,
   another strong opening, essential sections only (when the rest is >= 20% of the time). Compared
   on clarity, completeness, engagement, factual integrity and format fit. The recommended plan is
   never altered by them.

## Formats (`domain/profile.py`)

`generic`, `top_n`, `product_introduction`, `review`, `unboxing`, `talking_head`, `tutorial`,
`interview`, `podcast`, `documentary`, `short`. A format is a data entry: its pattern id, which
steps it expects, whether sections may move (`preserve` for rankings, tutorials, interviews),
how early the premise must land, a target length, and a delivery rate for untimed material. It
changes what is asked of the recording, never what the recording contains: an unmet expectation is
an issue in the plan, not invented material. Thresholds are one `Thresholds` object; pass your own
`FormatProfile` for a one-off format.

## Wording model (optional, `domain/refine.py`)

Off by default (`llm=False`), because the plan must be reproducible. When on, a model may only
**reword** two things: each section's one-line purpose and the main message. It sees numbered
excerpts (never ids), sections in chunks, then one global pass over section summaries. A reply is kept
only if its words and every number in it occur in the text it was shown; anything else is rejected
and counted (`provenance.usage`). Structure, order, priorities and references never depend on it.
A failing or missing model leaves the deterministic wording and says so in `stages`. Content
Intelligence's README documents the measured behaviour of the small default model: expect most
replies from a 1.5 B model to be rejected by these checks.

## Cache, versions, history

The plan is a derived Media Library document of the first source (`operation=story_plan`). Its
fingerprint covers the identity of every analysis/alignment used, the whole configuration,
`project_id`, whether a model was asked for and its identity, and the planner/schema/vocabulary
versions; time, job and device are not in it. A hit is returned with no work. Anything else makes a
**new** plan that records `plan_version` (counting plans of the same sources and format) and
`supersedes`; earlier plans are never changed or deleted. Bump `PLANNER_VERSION` when a rule or
threshold changes.

## Known limitations

* Only as good as the content analysis: roles, scores and relations are upstream heuristics (English
  and German cues); other languages get structure and few roles.
* Relations influence only multi-recording plans, and only lineage changes priorities. A confirmed overlap
  could offer one recording's footage as visual support for the other's passages; that needs the other
  recording's shots and is not built.
* No chronology model: order only changes to give a dependency its context, and recordings are
  concatenated in the order given.
* Durations include pauses and are not cut lengths; untimed material is a words-per-minute guess.
* The fixed alternatives (recorded order, another opening, essential only) are the ones the
  material supports today; there is no search over arbitrary reorderings.
* Validated on synthetic transcripts and hand-built analyses. Real long recordings, several
  speakers and a real language model's output are not validated here. Planning 3 000 synthetic
  passages (200 sections) took about 0.1 s; validating, serialising and reading the 2.7 MB document
  back about 1 s.

## Testing

```
uv run pytest tests/unit/story_architecture tests/architecture/test_story_architecture_boundaries.py
uv run pytest tests/integration/story_architecture      # real Media Library; needs FFmpeg
uv run mypy src/media_house/modules/story_architecture
```

No test needs a model or a network. `tests/support/story_fakes.py` has the library, catalog and
generator doubles and the `seg`/`topic`/`rel`/`item`/`asset` builders for hand-made analyses.

## Adding things

* **A format:** add a `_format(...)` entry in `profile.py` (and a test that its `problems()` are empty).
* **A rule or threshold:** change it in the owning step and bump `PLANNER_VERSION`.
* **A beat function / issue / enum member:** add it in `values.py`, handle it where produced, bump
  `VOCABULARY_VERSION`.
