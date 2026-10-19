# Content Intelligence

Understands **what a recording communicates**: topics and their boundaries, key statements, how
passages relate (answers, dependencies, repetition, qualification), how important, dense, unique
and self-contained each passage is, what role it plays, and where supplementary visuals or
on-screen text could help. It produces **analysis and advisory evidence — never edits**. It does
not modify media, cut, reorder, render, or decide anything: a low score is not a reason to cut,
a repeat is not automatically redundant, a pause is not "dead air".

```
media_library -> media_inspection -> audio_intelligence / video_intelligence -> content_intelligence -> later editing stages
```

## Public API (`application/contracts.py` is the only importable file)

```python
analyzer = container.resolve(ContentAnalyzer)  # blocking: run as a job
result = analyzer.execute(AnalyzeContentCommand(source_asset_id=asset_id), ctx)  # pipeline
result = analyzer.execute(AnalyzeContentCommand(transcript=my_transcript), ctx)  # standalone
analysis = result.value.analysis  # ContentAnalysis (typed, immutable)

highlights(analysis, minimum=0.6, limit=5)  # advisory candidates, best first
evidence_for(analysis, segment_id)  # text, key items, links, needed context
ContentCatalog.latest(asset_id)  # stored result, nothing recomputed
```

Queries (`query.py`, re-exported by the contract) read the stored result: `segments_between`,
`segments_of_topic`, `topics_at`, `ranked`, `highlights`, `standalone_candidates`,
`removal_candidates`, `related_segments`, `relationships_of`, `redundancy_links`, `opportunities`,
`key_items`, `evidence_for`.

## Inputs and how they are obtained

| Input | Source | If absent |
| --- | --- | --- |
| Transcript | caller (`transcript=`), else `audio_intelligence.AudioEngine` (reuses a stored transcript; transcribes only if none exists) | `Err(TranscriptUnavailable)`; nothing is invented |
| Acoustic cues | `audio_timeline=` supplied, or `audio=AudioUse.ACOUSTIC` asks `AudioAnalyzer` | `audio_timeline: not_available` input record + warning |
| Visual analysis | `video_analysis=` supplied, or `video=VideoUse.REQUEST` asks `VideoAnalyzer` | `video_analysis: not_available` + warning |

`result.analysis.inputs` records, per input, whether it was `supplied`, `reused`, `requested`,
`not_used` or `not_available`. The module never probes or decodes media. `media_inspection` is
not needed: the transcript carries the authoritative duration.

Audio and video are related to sentences **by overlapping time only**. No word-to-frame
synchronisation, lip-sync or cross-modal alignment is claimed. A duration mismatch above one
second between transcript and video is reported as a warning.

## Output (`domain/model.py`, schema version 1)

`ContentAnalysis`: `analysis_id`, `asset_id`, `state`, `source` (transcript fingerprint/asset),
`inputs`, `provenance`, `stages`, `units` (sentences), `segments`, `topics`, `key_items`,
`relationships`, `summary`, `key_takeaways`, `global_observations`, `warnings`, `limitations`.

* **Traceability.** Every value names its sentences (`unit_ids`); a unit is a verbatim slice of
  one upstream segment (`segment_id`, `char_range`). Sentence text is stored (bounded) so results
  are self-contained; the authoritative transcript stays in `audio_intelligence`.
* **Time.** Seconds on the original media timeline. `Span` carries a `TimeBasis` per end:
  `words` (aligned upstream word times), `source` (upstream segment time, unchanged),
  `upstream_estimate`, `interpolated` (by this module, proportional to characters),
  `missing` (no time known; the span is empty, never zero).
* **Missing is not zero.** A score that cannot be computed has `value=None` and a `reason`.
* **Direct vs inferred.** Text is `direct` (verbatim), `paraphrase` (rewritten and checked) or
  `inference`; every value has a `Method`: `observed`, `deterministic`, `heuristic`, `model`.
* **Confidence** is the strength of the evidence (cue weights/measurements) in [0, 1]. It is not a
  calibrated probability. A language model's self-reported confidence is never used; model
  output carries `confidence=None`.
* **Stored form** is `domain/serialization.py` (`document_type`, `schema_version`, strict reader,
  migration hook). Vocabulary additions (roles, kinds, relations) are additive and bump
  `VOCABULARY_VERSION`; readers refuse unknown members instead of reinterpreting them.

### Scores (all advisory, all 0–1, all with evidence and components)

| Score | Meaning | Not an input |
| --- | --- | --- |
| `importance` | centrality to the subject, key content, others depend on it, only it says it, speaker flags it | delivery, volume, emphasis, position |
| `information_density` | distinct content per word, new terms, minus filler/repetition (terms/min reported) | speech speed, pauses |
| `uniqueness` | share not already said **elsewhere in this recording** | cross-asset comparison (not implemented) |
| `standalone` | understandable alone; penalties name what is missing and `requires_context` lists the sentences | duration |
| `highlight` / `hook` | text meaning; measured delivery adds ≤ +0.08 corroboration | visual excitement |
| `removal_candidate` | redundant + tangential + low value, **reduced** by everything arguing to keep it (`counter_evidence`) | pauses, hesitation, slow speech |

Formulas and weights are in `domain/scoring.py`; they are design choices, not trained models.

## Pipeline of one run

1. Sentences (`units.py`): verbatim, word-timed when possible, long ones split at pauses/clauses.
2. Similarity: dense embeddings if an engine and model are configured, else TF-IDF word overlap.
3. Segmentation (`segmentation.py`): depth-score valleys + cue/pause/speaker/scene-cut evidence at
   three nested granularities (segment ⊂ topic ⊂ section). A visual cut alone never makes a boundary.
4. Key items, rhetorical markers, relationships, scores, roles, opportunities, extractive summaries.
5. Optional language-model refinement (`refine.py`) with grounding checks (below).
6. Validation (`validation.py`), then storage as a derived Media Library document.

## Models and dependencies

No new dependency was added. Reused: NumPy (infrastructure only), `torch`/`transformers`
(already installed through WhisperX / the `vision` extra), `huggingface_hub`.

**One explicit download step, never at run time:**

```
uv run python scripts/fetch_models.py                 # embedding model + default LLM
uv run python scripts/fetch_models.py --embedding-only
uv run python scripts/fetch_models.py --llm Qwen/Qwen2.5-3B-Instruct
```

Models are plain folders in `<cache dir>/content_intelligence/models/<org>--<name>` (no symlinks;
works on Windows). Behind a TLS-inspecting proxy run it with `uv run --system-certs --with
truststore ...`. A run only looks at that folder, a folder you name, and the Hugging Face cache with
`local_files_only`; a missing model makes its stage `not_available` with the fetch command in the
reason.

| Role | Default model | Licence | Size | Used by |
| --- | --- | --- | --- | --- |
| Sentence embeddings | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Apache-2.0 | 0.5 GB | `EmbeddingSettings(model=...)` |
| Instruct LLM | `Qwen/Qwen2.5-1.5B-Instruct` (3B is better but needs ~10 GB free GPU memory) | Apache-2.0 | 3 GB | `LlmSettings(model=...)` |

* **Deterministic engine** (always available, offline, reproducible): cue lists for **English and
  German** (`lexicon.py`; other languages fall back to language-neutral signals and a warning),
  TF-IDF with a crude 6-letter stemmer, depth-score segmentation.
* **Embedding engine** (`infrastructure/transformers_embedder.py`): mean-pooled Hugging Face encoder.
  GPU when available (`device="auto"`) with automatic CPU fallback; `device="cpu"` forces CPU.
  Vectors are stored as a derived Media Library document (rounded to 5 decimals, so a stored and a
  fresh run are bit-identical) and cached in process; `CacheLayer.EMBEDDINGS` forces recomputation.
* **Language model, in process** (`infrastructure/transformers_llm.py`): greedy decoding (same
  prompt, same reply), fp16 on GPU, bfloat16 on CPU (very slow, logged). A model whose weights plus
  headroom exceed the free GPU memory is **refused** with a clear message instead of silently paging
  through system memory.
* **Language model, served** (`infrastructure/loopback_llm.py`): set `LlmSettings(endpoint=...)` to
  use an OpenAI-compatible server (Ollama, llama.cpp, vLLM). The endpoint must be on this machine
  unless `allow_remote_endpoint=True` (transcripts then leave the machine). No API key is sent.

### Measured with the real models (GTX 1070 8 GB, Windows, CPU otherwise idle)

* Embeddings: paraphrase similarity 0.92, German translation 0.96, same-topic different statement
  0.70, unrelated 0.15. Three topics that share **no words** (cars / pasta / astronomy) are split at
  exactly the two true boundaries by embeddings; word overlap finds none. A restatement in other
  words is detected as an overlap only with embeddings. (`tests/integration/content_intelligence/test_real_models.py`)
* Language model (Qwen2.5-1.5B-Instruct): 289 generated tokens for a 620-token prompt took 52 s
  (~0.18 s/token; the 3B model did not fit and was unusably slow). Replies are deterministic. In the
  recorded run it produced summaries, topic labels and one relationship that passed the checks; it
  also invented role names, a miscounted sentence number and a wrong quotation, all of which the
  grounding checks rejected. Expect small models to need the checks: they are not optional.
* Cold model load from this machine's disk took 3.5-7 minutes (antivirus-scanned folder);
  a warm load took about one minute. The model then stays loaded for the process.
* Running other heavy processes at the same time slowed decoding by about 6x. Do not run analysis
  next to builds or test suites on a small machine.

The real-model tests skip when a model is absent. The language-model test is opt-in because of its
load time: `MEDIA_HOUSE_REAL_LLM_TESTS=1 uv run pytest tests/integration/content_intelligence/test_real_models.py`.

## Language-model grounding (`refine.py`)

The model sees numbered sentences between delimiters (delimiter characters in the transcript are
neutralised) and answers JSON that refers to those numbers. A reply is kept only for what passes:
referenced sentences/passages must exist and be annotatable; a quotation must occur word for word
in the sentences it cites (original wording restored); a summary may not contain a number its
sentences lack, must cite sentences of its own passage and must include the qualifier of any
statement it includes; roles/kinds/relations must be in the vocabulary; backward links must
point backward. Everything else is rejected and counted (`usage["llm_rejections"]`). If merged
output ever fails `validate_analysis`, it is discarded entirely. Transcript text that tries to
instruct the model is only content: it cannot change settings, endpoints or other requests.
Malformed JSON is not retried (temperature 0 would repeat it); only transport failures retry once.

## Execution profiles

| | similarity | neighbours / sentence | window | language model | model context |
| --- | --- | --- | --- | --- | --- |
| `fast` | word overlap | 4 | 3 | never | – |
| `standard` | embeddings if configured | 8 | 4 | passages the deterministic pass left ambiguous | 1 passage |
| `deep` | embeddings if configured | 16 | 5 | every passage | 2 passages |

## Cache and invalidation

All persistence is derived Media Library documents (`DerivedDocuments`); nothing else is stored.

* **Result** — fingerprint over transcript content (text, times, speakers, language), analysis
  settings (profile, thresholds, versions), similarity method and model identity, language-model
  identity/prompt version/settings, visual and acoustic input fingerprints. Any change misses.
* **Model replies** — one document per request, keyed by the exact prompt text plus model
  settings. Prompts use local numbering and contain no times or identifiers, so an unchanged
  passage of an edited transcript is answered from cache.
* `refresh={CacheLayer.RESULT, CacheLayer.LLM}` forces recomputation of a layer.
* A `partial` result (a requested stage failed) is returned but **not stored**, so a retry
  recomputes it (finished model requests are still reused).
* **Embeddings** — one document per exact list of sentences and embedding model.
* Not implemented: cross-asset reuse.

## States and degradation

`ResultState`: `ok`, `ok_with_warnings`, `partial` (a requested stage failed or was unavailable),
`not_applicable` (no speech). Failures that make analysis impossible are `Err`
(`TranscriptUnavailable`, `InvalidTranscriptInput`, `InvalidConfiguration`, upstream errors passed
through). Cancellation raises `OperationCancelledError` as everywhere. Each stage has a
`StageReport` (`ok`/`skipped`/`not_available`/`failed`, cache outcome, seconds, reason). Failing
optional engines degrade (embeddings → word overlap, model → deterministic result) and say so in
`stages`, `warnings`, `provenance`.

## Known limitations

* English and German cues only; other languages get structure, similarity, density, uniqueness and
  summaries but few roles, items or opportunities.
* Topic and redundancy detection without an embedding model is word overlap: paraphrases in
  different words are missed, and segment boundaries can be off by a sentence on short topics.
* Roles, key items, B-roll and graphics ideas come from wording cues plus context; they can be
  wrong or generic. B-roll is a semantic idea only; no footage is ever claimed.
* `ARGUMENT` and `EXPLANATION` items, and nuanced roles/relationships, arise only from the language model.
* `contradicts` is only detected from explicit correction/counter cues.
* Scores are uncalibrated heuristics; weights are design choices that have not been tuned on
  labelled data.
* Without repeated words, topic labels are the most representative sentence, not a short noun
  phrase; a language model produces better labels.
* Small language models (1.5 B) are weak at sentence counting and roles; most of their output is
  discarded by the checks. Larger models need more GPU memory than a typical 8 GB card.
* Humour is detected from text cues or measured laughter only; emotions are never inferred.
* Speaker labels are used as boundary evidence only; no per-speaker analysis.
* Analysis scales roughly linearly: ≈ 1 s per 1 000 sentences, ≈ 10 s for 8 000 (measured, CPU,
  deterministic engine); model-based stages add their own time.

## Testing

```
uv run pytest tests/unit/content_intelligence tests/architecture/test_content_intelligence_boundaries.py
uv run pytest tests/integration/content_intelligence      # real Media Library; needs FFmpeg
uv run mypy src/media_house/modules/content_intelligence
```

No test needs a network beyond loopback or downloads a model. Engines are replaced by fakes in
`tests/support/ci_engines.py`; semantic fixtures are in `tests/support/ci_texts.py`.

## Adding things

* **A language:** add a cue dictionary and stop words in `lexicon.py` (the parity test requires the
  same cue names), bump `PROCESSING_VERSION`.
* **A cue or formula change:** bump `PROCESSING_VERSION` so stored results are not reused.
* **A role / relation / item kind:** add the enum member, handle it where it is produced, bump
  `VOCABULARY_VERSION`.
* **An embedding or language-model engine:** implement `SemanticEngine` / `LanguageModel`
  (`application/ports.py`) in `infrastructure`, register it in `module.py`.
