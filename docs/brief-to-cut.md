# Brief to Cut

From a client brief and the creator's raw material to a fully prepared, compliant, ready-to-cut
YouTube project in the creator's style. Spec: the "Brief-to-Cut" implementation prompt.
No thumbnails, no music or sound-effect requests, no auto-publishing.

## ALDI audit and repair (November 2026)

The original implementation was validated with a synthetic Lidl fixture. The supplied real ALDI
PDF and `Downloads/yt-video/00_kontext/lidl-nico-transcript.md` exposed different requirements:
an editorial overview, unpriced products, wrapped PDF tables, historical reference prices,
uncertain relabeling, and incomplete regional listings. A Lidl sales template was unsuitable.

| Existing component | Finding and repair |
| --- | --- |
| Qt job runner and job list | A running job without progress still displayed `pending`. Forward start events to the UI; cover queued/running/cancelled/failed completion with real threaded tests. |
| Local text model | CPU inference could occupy import for many minutes. Basic extraction now defaults to deterministic parsing; explicit model enrichment is optional. Local generation observes the existing timeout and cooperative cancellation, including queue wait. |
| Document reader and parser | Preserve PDF column layout, wrapped names and page provenance. Keep unpriced products and distinguish historical products/prices. Extract required-section bullets, uncertainty and asset-rights constraints. Reference-video publication dates and author handles are not project deadlines or mandatory metadata. |
| Script and prompts | Supply actual fact values, sources, creator ideas and original brief context. Reject incomplete/ungrounded model scripts; retain unknowns as slots. Use current technical data, consolidate incompletely documented classics, and avoid inventing ordering/delivery or sponsorship. Full generation records include input fingerprints. |
| Wizard | Persistent rewrite dock, project summary, visible work state, document drop, editable context tabs, script restore, debounced draft lint, grouped material requirements and recording instructions. Preserve unsaved edits and clear dependent views when switching projects. |
| Compliance and export | Historical prices/descriptions cannot authorize current numeric claims. Prices require currency facts. Accepted risk replacements change the actual text; old rewrite proposals cannot overwrite newer scripts. Missing material and stale generated artifacts block export; editorial handoff omits `--sponsored`. |
| Media and persistence | Reuse the existing workflow, project/style SQLite adapters, Media Library contracts and pipeline handoff. No new media store and no changes to original assets. Existing JSON project storage remains compatible; no schema migration was needed. |

Run the repeatable local review with the real PDF:

```powershell
uv run --extra documents python scripts/check_brief_to_cut.py `
  --brief 'C:\Users\L14\Downloads\ALDI Feuerwerk 202627 – Video-Briefing.pdf' `
  --reference-dir 'C:\Users\L14\Downloads\yt-video' `
  --client ALDI --year 2026 --output scratch/aldi-review-final
```

The output contains the extracted model, concept/script drafts, client documents in Markdown,
Word and PDF, recording guide, asset plan, context variants, metadata, compliance findings and
`quality-report.json`. This is a review workspace: it does not bypass the final export gate.
The report separates deterministic safety checks from spoken length, repetition, unresolved
fields and comparison with the manual reference. It makes no creative-equivalence claim.
No external model calls are made by this command. Originals remain in Downloads.

To inspect the native widget at 1280×900 and 1600×1000:

```powershell
uv run --no-sync python -m scratch.preview_brief_to_cut
```

This produces screenshots and geometry evidence in `scratch/brief-to-cut-preview`. On Windows
the offscreen preview registers Segoe UI explicitly; normal desktop font selection is unchanged.
Restart the desktop application to load the fixes. Existing in-memory jobs in an already running
process continue using the previous code.

Focused gates:

```powershell
uv run pytest tests/unit/production_project tests/integration/production_project `
  tests/presentation/test_brief_to_cut_ui.py tests/presentation/test_job_bridge.py `
  tests/unit/content_intelligence/test_local_llm_budget.py tests/architecture
uv run ruff check src/media_house/modules/production_project
uv run mypy --strict src/media_house/modules/production_project
```

Limits that remain: the local deterministic draft is a starting point, not the manually written
final script. Unknown prices, missing primary-source details and the creator's personal judgments
remain open; duration is checked against the brief's explicit target. There are no ALDI recordings
in this fixture, so no final cut, render or DaVinci output has been evaluated. The current context
runner is a timeline planning check, not a validated 75-second rendered A/B/C experiment: excerpt
trimming, context-sensitive model execution and project privacy/asset-policy propagation through
that bridge still need dedicated implementation. Native model loading and a single decoding step
can only stop once control returns to Python.

## Where it lives (audit: reuse before rewriting)

| Need | Owner | What was done |
| --- | --- | --- |
| Scripted transcript, scenes, slots, gates, retakes, recording-to-scene matching | `production_project` (existed, pure) | **Extended**: `[OPTIONAL: name]`, `[CONDITIONAL]`, `[VERIFY]`, `Funktion:`, `Grafik:`, time hint, named gates, digits in slot names |
| The project itself: brief, facts, slots, concepts, script, material plan, manifest, context, metadata, export | `production_project` | New domain, use cases, SQLite store (`production_project.db`), exporters |
| Style profile, learned rules, exemplars, reference styles | `style_memory` (**new**) | Rules with provenance/strength/scope, learning from events, review, import/export; seeded from `docs/layer4` evidence |
| Look (colour, type, graphic language) | `visual_style_engine` (existing) | Not duplicated; the style profile only names it |
| Media, probe data, transcripts, video analysis | `media_library`, `media_inspection`, `audio_intelligence`, `video_intelligence` | Read through their `application.contracts`; nothing is re-probed or re-analysed |
| Language model | `content_intelligence.TextGenerator` | Used when configured and allowed; every step has a deterministic path without it |
| The wizard UI | `control_center` | New pages `Brief to Cut` and `Dein Stil` (view models + passive views) |
| Story wording with the per-video context | `story_architecture`, `media_pipeline` | `PlanStoryCommand.context`, `StageSettings.story_context`, CLI `--context` |

The five-step wizard, the pipeline CLI and the tests all go through one service:
`production_project.application.contracts.ProductionWorkflow`.

## The folder structure is not a requirement

`00_kontext ... 06_gfx` was already implemented as the pipeline's `AutoBriefer` convention (role by
the suffix of a numbered folder, dynamic). The product model is **assets with a role and a slot**
(`AssetRequirement`, `ManifestEntry`); the folder is only an optional import/export form of it:
the scan reads any layout, an unknown folder simply has no role, `07_thumb` is ignored, and the
export writes the numbered folders for the pipeline.

## The pipeline context (system prompt per video)

One structured set of items is rendered at three levels (about 60 / 190 / 460 words), so the three
cannot contradict each other. Every level carries: topic and facts, idea and red thread, look and
pacing, hard rules, asset logic, and what the engine may decide freely. The Kontext-Labor records
runs per variant (automatic checks for hook, fidelity, numbers, overlays, rhythm, asset mapping,
rules; the creator rates the rest), picks the winner (a tie goes to the shorter text) and learns the
best level per format. The winner is written as `pipeline-context.md`; the pipeline reads it with

```
uv run python scratch/run_pipeline.py --project <01_cam> --script <00_kontext/slug-transcript.md> \
    --context <pipeline-context.md> --story-llm --sponsored --language de
```

The context's hash is part of the story model's identity, so a changed context never serves a
cached reply of another one. It is background only: every reworded statement is still grounded.

## Rules that hold everywhere

* **Numbers are code's.** Sums, ratios and differences are `Fraction` computations from the fact
  table; a model writes `{{fact:key}}`, never a number. Derived facts say what they came from.
* **Unknown is a slot.** Technik-Slots (`{KALIBER_X}` ...) for what only the creator knows; a
  sentence with an empty slot drops out cleanly.
* **A model never decides free-form.** Its answer is JSON read strictly into a frozen dataclass
  (`json_codec`), asked again with the error up to three times, then the deterministic path runs.
  Brief items a model adds must quote the brief literally.
* **Confidential mode.** A remote model is refused for a confidential project unless the creator
  allowed it; every step is written to an audit log.
* **Flagged files are never used** until a person settles the flag (`Freigabe liegt vor`,
  `verpixeln`, `ersetzen`). Originals are never moved or changed.
* **Brief constraints beat learned style.** Nothing learned becomes `mandatory` without approval.

## Golden fixture: how to run the demo

`tests/fixtures/brief_to_cut/lidl_brief.txt` is a **synthetic reconstruction** of the Lidl briefing
(the real PDF, transcript and materials were not available to the build). Replace it with the real
text to re-check the acceptance list.

```
uv.exe run --no-sync pytest tests/unit/production_project/test_workflow.py -v   # whole flow
uv.exe run --no-sync pytest tests/unit/production_project tests/unit/style_memory \
    tests/integration/production_project tests/integration/style_memory \
    tests/presentation/test_brief_to_cut_ui.py -q
```

The flow test walks: brief -> Brief Model (constraints with quotes, facts, deadlines, mandatory
elements, open questions) -> risk panel -> three concepts -> 17 scenes -> slots and lint -> material
plan (about 15 files, recording order) -> the real upload (14 talking chunks naturally sorted and
laid on scenes, the missing scene named, stock video flagged for licence proof, prospect page flagged
confidential, `.svg.webp` logo converted to PNG, `07_thumb` ignored) -> flags settled -> three
context variants and an A/B run -> metadata -> gate -> export package.

## Known limits (honest)

* Language-model paths (brief augmentation, concepts, script, scene rewrite) are tested with a
  scripted fake model, not a real one. The quality of real generations is unverified.
* The evidence adapter is typed against the real contracts but not run on real footage. Vision
  tags (`night_sky`, `product`, `brochure`, `browser_ui` ...) need a tagger that no stored analysis
  provides: without one, classification rests on speech, faces, OCR, probe data, then names/folders.
* The A/B run (`ProductionWorkflow.run_variant`, button "Testlauf mit Aufnahmen…") runs the real
  pipeline (story, edit, graphics, timeline; no render) once per variant with the variant as
  `story_context`, reads the built timeline and measures overlays at once, cuts per minute and
  rule violations. Hook time, script fidelity, number accuracy and the graphics taste are NOT
  measured: they stay empty until you rate them. It needs a story model and real footage; with a
  fake model it only proves the wiring (the tests do exactly that).
* Timeline QC reads the stored build (`attach_timeline`, button "Timeline einlesen"; the anchor is
  remembered from the last test run). The label is recognised by the `sponsor_label` zone, overlays
  are graphic/custom clips, captions and the label do not count. It was run against a real built
  timeline of a generated clip, not against a sponsored edit with a label. The edit plan and the
  DaVinci project still come from the existing pipeline stages (the package only says how to
  start them).
* The UI is functional, not polished: no animated streaming slots, no per-slot drop zones, no
  interactive teleprompter or phone QR (the teleprompter text is in the recording guide).
* PDF briefs: `pypdf` (`uv sync --extra documents`) or a simple built-in reader for text PDFs.
