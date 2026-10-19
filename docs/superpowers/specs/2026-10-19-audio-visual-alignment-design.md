# audio_visual_alignment: design

Status: draft for approval. Nothing in this document is built yet. Facts marked **(verified)** were read
from the repository on 2026-10-19; facts marked **(unverified)** must be proven by the first tasks of the plan.

## 1. Intent

Media-House can already say what is heard (`audio_intelligence`), what is visible (`video_intelligence`) and
what a recording means (`content_intelligence`). It cannot say which heard thing and which seen thing belong
together. `audio_visual_alignment` connects the existing results into temporally grounded, evidence-backed
relationships, so a later editing stage does not have to rebuild them.

The module produces evidence, candidate relationships, timing and strength. It never decides what to cut,
show or replace, never picks assets, and never touches a timeline.

## 2. Goals, non-goals, anti-goals

Goals (v1):

1. One explicit clock for audio and video, with a measured sync check that reports uncertainty.
2. For each stretch of speech: is the voice coming from a visible person, from off screen, or is it unknown.
3. Link diarized speakers to the anonymous `person_X` identity clusters of video intelligence, accumulated over shots.
4. Link transcript passages to the shots shown while they are spoken, graded by how direct the evidence is.
5. Link audio events (energy spikes and similar) to visual motion, gesture and entry events, always as "possible".
6. Report evidence-only opportunities: intervals where a cutaway would not collide with visible lip movement, and passages that content intelligence marks as wanting a visual while the shot shows only the speaker.
7. Everything stored as one derived Media Library document, cached on the fingerprints of all inputs.

Non-goals (v1): music beat matching, sound classification (footsteps, doors, applause), pronoun and visual-reference resolution,
contradiction detection beyond sync and speaker-attribution flags, any UI, any model not already installed,
speaker recognition by name, multi-face active-speaker detection.

Anti-goals: a second transcription, face, object or semantic pipeline; false precision (a confident match
where the evidence is thin); treating "a face is visible while speech occurs" as "that person speaks";
treating a missing signal as proof of absence; an editorial decision hidden in a result field name.

## 3. What exists and what is missing

Verified reusable (all through `application/contracts.py` only):

| Need | Owner and public type | Notes |
| --- | --- | --- |
| Words, segments, pauses, 20 ms speech activity, events | `audio_intelligence`: `AudioIntelligenceTimeline` | Seconds on the original media timeline; `audio_offset` already added. |
| Shots, tracks, anonymous identities, events, per-frame `mouth_activity` and `visual_speaking` curves, objects, OCR text, descriptions (deep profile only) | `video_intelligence`: `VideoAnalysis` | Times are `FrameTime` (frame, pts, exact rational timebase). |
| Segments, topics, key items, BROLL/GRAPHIC opportunities | `content_intelligence`: `ContentAnalysis`, `ContentCatalog` | Relates audio to video by time overlap only; no lip-sync, no alignment claimed. |
| Stream start and end offsets, VFR, discontinuities | `media_inspection`: `InspectionCatalog.find` | `Synchronization` holds audio-minus-video offsets. |
| Cache | `media_library`: `DerivedDocuments`, `DocumentSpec`, `fingerprint`, `find_derived_asset`, `register_derived` | The only cache. |

Verified gaps:

- **No speaker labels.** Every `speaker` field in `audio_intelligence` is `None`. `whisperx 3.8.6` is installed and brings `pyannote-audio 4.0.7` with it, and `whisperx.diarize.DiarizationPipeline` exists, so diarization needs no new dependency. No diarization model is cached on this machine. The pyannote models are normally gated on Hugging Face (account token and accepted terms) **(unverified; must be checked before the plan commits to a model)**.
- **No read-only lookup** of stored audio or video results (only `execute`, which computes on a miss).
- **Mouth activity only for the main face** of each shot, not per track. Multi-face shots cannot be resolved.
- **No audio sound classifier ships.** Only generic acoustic events exist.
- **No `editing_intelligence` module.** The downstream contract has no consumer yet, so the output must be self-contained and stable.
- **Cross-lingual semantics.** Object labels are English COCO classes; transcripts may be German. The multilingual embedding engine lives in `content_intelligence.application.ports`, which is not a contract. Lexical matching cannot cross languages.
- **Video pts origin (unverified).** Both audio seconds and `FrameTime.seconds` appear to be container-origin based, but `video_intelligence` stores decoder pts without an explicit statement. Task 1 proves this with a fixture whose video starts at a non-zero offset.

## 4. Decisions made with the owner

1. Add tiny read-only catalogs to the owners' contracts: `AudioCatalog` and `VideoCatalog`, same shape as `ContentCatalog.latest`. (Small edit to two existing modules, no behaviour change.)
2. Add diarization inside `audio_intelligence` (not in the new module), then link speakers to people in the new module.
3. No model research now. No new model beyond the pyannote diarization model that WhisperX already pulls in.

Calls made by the author (change them if wrong): module named `audio_visual_alignment`; no UI in v1; no persistence outside the Media Library; lexical text matching in v1 (section 8).

## 5. Architecture

### 5.1 Changes to existing modules

`audio_intelligence`:
- Port `SpeakerDiarizer` in `application/ports.py`; adapter `PyannoteDiarizer` in `infrastructure/` is the only file importing `whisperx.diarize` or `pyannote`.
- Domain `SpeakerTurn(start, end, speaker)`; stored as its own derived document (`diarization`), keyed by prepared-audio fingerprint, model identity, parameters and a `DIARIZATION_VERSION`.
- `AudioIntelligenceConfig` gains `diarization: DiarizationConfig | None = None`. Default `None` keeps every existing fingerprint valid. When set, fusion fills `speaker` on words and segments. Transcription identity does **not** change, so nothing is re-transcribed. The timeline fingerprint includes the diarization identity.
- Without the model or token the stage is `not_available` with the fetch command in the reason (same pattern as the other model stages) and the rest of the timeline is unchanged.
- `scripts/fetch_models.py` gets a diarization step. `device="auto"`, GPU with CPU fallback, device not part of identity.
- `Baseline.speaker` stays reserved; per-speaker baselines are out of scope.

`video_intelligence` and `audio_intelligence` contracts: `VideoCatalog.latest(asset_id, profile)` and `AudioCatalog.latest(asset_id, config)` returning the stored result and its asset, or `None`. Never compute.

`bootstrap/modules.py`: one line.

### 5.2 New module layout

```
modules/audio_visual_alignment/
  domain/        clock.py (time mapping, sync assessment)  model.py (records, enums)
                 speech_source.py  speaker_link.py  text_visual.py  sound_action.py
                 opportunities.py  scoring.py  validation.py  serialization.py  config.py  errors.py
  application/   align_audio_visual.py (use case)  resolver.py  contracts.py  ports.py (only if needed)
  module.py
```

No infrastructure layer in v1 (all deterministic, pure domain; NumPy is allowed in infrastructure only, so any correlation heavy
enough to need it is written with the standard library on 20 ms or per-frame series, which is small). If profiling shows otherwise, a
`numpy_correlation.py` adapter behind a port is the one permitted addition.

### 5.3 Inputs and how they are obtained

`AlignAudioVisualCommand(video_asset_id, audio_asset_id=None, audio_timeline=None, video_analysis=None, content_analysis=None, config, acquire=Acquire.STORED)`.

Per upstream input, one resolver (the only place that talks to other modules) records `supplied`, `reused`, `requested`,
`not_used` or `not_available`, as the other analysis modules do. `Acquire.STORED` (default) reads catalogs and never computes.
`Acquire.REQUEST` asks the owning analyzer, which is the only way expensive work can start, and is explicit.
A missing audio timeline or video analysis is `Err(MissingPrerequisite)` naming the owner and the command that would produce it.
Content analysis is optional; without it text-to-visual matching uses the transcript only and says so.
Media inspection is optional; without it the sync assessment uses the container-origin assumption and flags it.

## 6. Time model

- The **common clock** is seconds on the container timeline of the source, as used by both upstream timelines. It is an explicit value (`ClockBasis.CONTAINER`), never implied.
- Every video-side reference keeps the upstream `FrameTime` (frame, pts, timebase) as the authority; seconds are derived for comparison only.
- A separate audio asset (dual-system recording) carries a `ClockMapping(offset_seconds, drift_ppm=0, source: supplied|measured|assumed)`. v1 supports offset only; drift is detected and flagged, not corrected.
- `SyncAssessment` is a measurement: start offsets from inspection when stored, plus a speech-activity versus mouth-activity lag estimate over shots where a main face is present. It reports `estimated_lag`, `support` (seconds of usable evidence), `agreement` between shots, and flags (`insufficient_evidence`, `lag_varies_over_time`, `no_face_speech_overlap`). It is never applied silently; `config.apply_measured_offset` defaults to false.
- Discontinuities and variable frame rate come from inspection when present. Intervals that straddle a reported discontinuity get an `uncertainty` flag.

## 7. Output model (`AlignmentDocument`, schema version 1)

One typed immutable document: `alignment_id`, `asset_id`, `inputs` (how each was obtained, ids, fingerprints, versions), `clock` (`ClockMapping`), `sync` (`SyncAssessment`), `speech_sources`, `speaker_links`, `text_visual`, `sound_action`, `opportunities`, `flags`, `provenance`, `warnings`, `limitations`.

Every record carries: deterministic id, the audio reference (segment, turn or event id, and a span in seconds), the video reference (shot, track, identity or event id, and a `TimeRange`), an `EvidenceGrade`, separate scores (below), a list of `Evidence` items (kind, source module, source id, measured value), `uncertainty` flags, and `method` (`observed`, `deterministic`, `heuristic`). Ambiguous cases keep **all** plausible candidates, ranked, not only the best.

Scores are never merged and never called probabilities:

- `temporal_overlap` in [0, 1]: fraction of the shorter interval covered, from the common clock.
- `correspondence` in [0, 1]: normalised correlation of two activity series (speech versus mouth, spike versus motion). `None` when the series are too short.
- `identity_support` in [0, 1]: share of usable speech seconds across shots that agree on the speaker-to-cluster link; reported with `support_seconds`.
- `evidence_strength` in [0, 1]: weighted mean of the scores that exist (weights renormalised), documented in `scoring.py`, versioned. This is the "confidence" of the repo conventions: strength of evidence, not calibrated.
- `EvidenceGrade`: `direct`, `contextual`, `weak_topical`, `unsupported`, `unknown`. `unknown` means nothing could be measured; it is not the same as `unsupported`.

Missing is `None` with a reason, never 0.

## 8. Alignment stages (cheap first, stop when evidence is enough)

1. **Clock and sync** (section 6). Pure arithmetic plus one correlation.
2. **Speech source** per diarized turn (or per transcript segment when there is no diarization): compare speech activity with the shot's `mouth_activity` curve. Output `on_screen` (with track and identity), `off_screen` (speech present, a face is present but not moving, or no face), `ambiguous` (several faces, or too little evidence), or `unknown`. A visible face during speech alone is never evidence.
3. **Speaker link**: for each diarization speaker, accumulate agreeing shots into one `speaker → person_X` link with `identity_support` and `support_seconds`. A link needs a minimum of support seconds and at least two independent shots unless the config says otherwise; below that it stays a candidate list. Several speakers on one cluster, or one speaker on several clusters, are reported as `conflict`, not resolved.
4. **Text to visual**: for each passage of the content analysis (or transcript segment), the shots overlapping it in time (extended by a configured lead and lag, because an illustration may precede or follow the statement). Grade by evidence: `direct` when a passage key term appears in the shot's OCR text or description (same-language string match after normalisation) or its object labels (English labels only match English terms); `contextual` for overlap with no textual support; `weak_topical` is not produced in v1 (needs embeddings). v1 therefore **cannot** relate German speech to English object labels except through OCR text and descriptions; this is recorded in `limitations`.
5. **Sound to action**: pair audio events (`energy_spike`, `pitch_*` excluded unless configured) with visual `motion_peak`, `gesture`, `track_entered` events within a tolerance derived from the sync assessment's uncertainty. Always `possible`; off-screen origin is the default reading when nothing visual matches, never an inconsistency.
6. **Opportunities** (evidence only): `lip_sync_free_window` (speech with no visible speaking face, long enough, away from shot boundaries by a margin) and `illustration_gap` (a content-intelligence BROLL/GRAPHIC opportunity whose overlapping shots show only the speaker or nothing matching). Neither names an asset, a shot to use, or an action.
7. **Flags**: sync suspect (lag estimate with strong support and large magnitude), speaker attribution suspect (a speaker linked to a cluster whose mouth was closed during most of its turns), insufficient inputs. Voice-over, reaction shots and montage are listed in `limitations` as deliberate techniques that look like these flags.

All thresholds, windows and weights are fields of a versioned `AlignmentProfile` (`fast` = stages 1 and 2 only; `standard` = all stages), none a constant inside an algorithm. Algorithm constants that cannot be settings are pinned by a test, as in `audio_intelligence`.

## 9. Cache, versions, invalidation

One derived Media Library document, operation `audio_visual_alignment`, `document_type` declared at top level, stored through `DerivedDocuments`.
The fingerprint config holds: audio timeline document identity, video analysis asset checksum plus its provenance digest, content analysis id (or none), inspection identity (or none), `AlignmentProfile` values, `ALIGNMENT_VERSION`, `SCHEMA_VERSION`, `scoring` version, and the `ClockMapping`. Any change in any input misses. A re-tuned profile recomputes from stored inputs without touching upstream modules. A partial result (a requested stage failed) is returned but not stored. Device is not part of identity (the module runs no model).

## 10. Errors and degradation

`Err`: `MissingPrerequisite`, `InvalidAlignmentConfig`, `IncompatibleInputs` (different assets, or durations differing beyond tolerance without a supplied mapping), upstream errors passed through. Cancellation raises `OperationCancelledError`. A stage that cannot run is `not_available` with a reason and the other stages still produce records. Stage reports carry state, cache outcome, seconds.

## 11. Testing

- Domain: clock mapping, sync lag estimation on synthetic series with known lag, non-zero stream starts, VFR timestamps; each stage on hand-made timelines; ambiguity keeps all candidates; `unknown` versus `unsupported`; serialisation round trip and strict reader.
- Use case: fake catalogs and `FakeMediaLibrary`; `STORED` never calls an analyzer (a fake analyzer that fails the test if invoked); `REQUEST` calls it once; second run is a cache hit and runs no stage; changing one upstream fingerprint or one profile value misses; partial result not stored; deterministic output (same inputs, same bytes).
- Upstream: diarization stage fills `speaker` and leaves the transcript asset unchanged; default config leaves existing fingerprints identical (pinned); catalogs return `None` and never compute.
- Architecture: new `tests/architecture/test_audio_visual_alignment_boundaries.py` modelled on the existing boundary tests (only `application.contracts` imports from other modules; no editorial vocabulary in result fields, as `video_intelligence/test_contract.py` does).
- Integration: one real-library test with a short generated clip whose video stream starts at a non-zero offset (proves the pts origin question).
- Real diarization is an opt-in test that skips without the model, like the real-model tests elsewhere. No test downloads a model.

## 12. Cost and hardware (estimates, not measurements)

Alignment itself: seconds for hour-long material (series are 50 Hz audio activity and per-frame mouth curves; correlation per shot). No model, no GPU.
Diarization: pyannote on the GTX 1070 8 GB machine, speed and VRAM **unmeasured**; the plan includes one measurement task and records the numbers in the README. It is skipped entirely when `diarization=None`.

## 13. Risks

- Gated diarization model: needs a Hugging Face token and accepted terms; the feature is `not_available` without them. This is an owner action.
- Main-face-only mouth signal: multi-person scenes mostly end `ambiguous`. The honest fix is a per-track mouth series in `video_intelligence`, deferred.
- Lexical text matching is weak across languages; `weak_topical` needs a shared embedding port in `core`, deferred (AI_CONTEXT requires asking before adding a core port).
- Editing the diarization into `audio_intelligence` bumps nothing for existing users while the default is `None`; tests pin that.
- Pts origin may not match (see section 3); if it does not, the mapping is added to the clock stage, not worked around downstream.

## 14. Left to the builder

File-level naming inside the layout, exact field names (following the surrounding modules), threshold defaults (choose conservative and record the reasoning), README and `AI_CONTEXT.md` updates, ordering of tasks.

## 15. Revisions after the red-team review (supersedes sections 3, 6, 7, 8 where they differ)

The draft was checked against the code and against real files. What changed, and why:

1. **Clock.** The draft assumed audio and video seconds share the container timeline. Measured with
   FFmpeg 9.0.2: they do for MP4/MOV/MKV, but a decoder frame timestamp is not always
   container-relative (an MPEG-TS clip put its first frame at 0.0 s although it is 0.5232 s after the
   container start). The video correction is now derived from the stored inspection, recorded with
   observed and expected values, and never taken from a signal. The library cannot import MPEG-TS today,
   so the non-zero path is proven by unit tests and by the TS experiment, not by an importable file.
2. **Lag probe.** The draft called it a sync check. The mouth signal is ~3 samples/s of the largest
   face, so it detects an offset of about a second or more only, needs 30 s of evidence
   (`probe_min_support_seconds`; a 10 s floor was found too lax by a test), is never applied, and does
   not measure drift. Named and documented as a coarse probe.
3. **Visibility vocabulary.** `on_screen / off_screen / ambiguous` conflated four facts. Now
   `speaking_face / quiet_face / no_face_detected / ambiguous / visual_unavailable / insufficient`;
   no state claims an off-screen voice.
4. **Evidence grades.** `direct` is removed (a word in a description does not prove a statement). Now
   `observed_text / described / entity_label / semantic_candidate / none_found / unknown`; no
   `unsupported`, no contradiction grade (nothing upstream measures either). Common words are dropped
   language-neutrally, and the multilingual limit is stated by a flag.
5. **Multilingual matching.** The draft deferred it. The multilingual embedding engine already exists
   in Content Intelligence, so it is exposed through that module's contract as `TextEncoder` (no `core`
   change, no second model) and used only when its model is on disk, only to add `semantic_candidate`.
6. **Scores.** `temporal_overlap` as "share of the shorter interval" overstated a short event inside a
   long passage; replaced by overlap seconds plus both coverages. `evidence_strength` as a weighted mean
   let one strong signal hide a weak one; replaced by a weakest-link rule with a single-signal discount
   and a contradiction factor. `confidence` is no longer a field name.
7. **Speaker links.** The "two independent shots" rule rejected one long clean observation; now seconds
   of support OR shots, plus quiet-face contradiction and shared-identity conflict.
8. **Opportunities.** `illustration_gap` duplicated Content Intelligence's own coverage verdict
   (`already_covered`, `visual_evidence`), so it is replaced by `content_passage_context`, which passes
   that verdict through and adds what the picture shows of the speaker. `lip_sync_free_window` requires
   positive evidence (quiet face, or no face found by an analyser that ran).
9. **Added cheaply:** speech-across-cut relations (`boundaries`), chance-adjusted sound-to-action
   candidates, `audio_event_kinds` pass-through for future detectors.
10. **Diarization** is implemented as an opt-in separate stage that never re-transcribes and is refused
    before heavy work when it cannot run. The real pyannote adapter is untested here (gated model, no token).
11. **Catalogs** (`AudioCatalog`, `VideoCatalog`) follow `ContentCatalog` exactly; no infrastructure
    layer was needed.

Still deferred, with the reason: music beats and sound classes (no upstream detector; a model
decision), multi-face active speaker (needs a per-track mouth signal in `video_intelligence`),
multi-camera and cross-clip identity (no upstream cross-clip identity), drift correction (needs a
validated multi-point offset estimate), a GUI (not requested), measured quality on labelled real
footage (none available).
