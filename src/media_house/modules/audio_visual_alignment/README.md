# Audio-Visual Alignment

Relates what is **heard** to what is **seen**, from analyses that already exist. It transcribes
nothing, detects nothing and decides nothing: it records *which* heard thing and *which* seen thing
go together, *when*, and *how strong the evidence is*. What to cut, show or replace stays with later
stages.

```
media_library -> media_inspection -> audio_intelligence / video_intelligence / content_intelligence
                                              \                |                  /
                                               `---> audio_visual_alignment ---> later editing stages
```

## Public API (`application/contracts.py` is the only importable file)

```python
aligner = container.resolve(AudioVisualAligner)  # blocking when it must compute: run as a job
result = aligner.execute(AlignCommand(video_asset_id), ctx)  # reads STORED analyses
result = aligner.execute(AlignCommand(video_asset_id, acquire=Acquire.REQUEST), ctx)  # may compute
doc = result.value.document  # AlignmentDocument, typed and immutable
doc.speech_source("12")  # what is on screen while segment 12 is heard
doc.matches_for("seg_0003")  # ranked candidate shots for a passage
AlignmentCatalog.latest(video_asset_id)  # stored result, nothing computed
```

Inputs, each resolved by **supplied → stored (reused) → requested → not available**, recorded in
`doc.inputs`:

| Input | Source | If absent |
| --- | --- | --- |
| Audio timeline | caller, `AudioCatalog`, or `AudioAnalyzer` under `Acquire.REQUEST` | `Err(MissingPrerequisite)` naming the command |
| Video analysis | caller, `VideoCatalog`, or `VideoAnalyzer` under `Acquire.REQUEST` | `Err(MissingPrerequisite)` |
| Content analysis | caller, `ContentCatalog`, or `ContentAnalyzer` under `Acquire.REQUEST` | text stage `not_available` |
| Inspection | `InspectionCatalog`, else `MediaInspector` (a cheap probe, always allowed) | video origin unverified (flag) |

`Acquire.STORED` (default) never computes an analysis. A lookup never decodes, transcribes or
detects anything.

## The clock (read this before using any time)

`common = upstream_seconds + correction`. The common clock is the **container timeline**: zero is
the container's `start_time`. Audio Intelligence already reports seconds on it. Video Intelligence
reports the decoder's presentation timestamps, and FFmpeg does not always shift those by the
container start (measured on this machine's FFmpeg 9.0.2: an MP4 container starting at 0.4768 s
reports its first frame at 0.5232 s, which is correct; the same material in MPEG-TS reports 0.0 s
although the frame is 0.5232 s after the container start). The video correction is therefore
**derived from the stored inspection** (`first presented video pts - container start - analysed
first frame`), is `0` when they already agree, and is recorded with its inputs in
`doc.sync.corrections`. For MP4, MOV and MKV (the formats the library accepts) the real-file test
shows no correction is needed; MPEG-TS cannot be imported at all today. The real frame decoder and
prober were run on MPEG-TS files directly (`tests/integration/audio_visual_alignment/test_mpegts_origin.py`):
with the video starting 1.0 s / 2.0 s after the audio the decoder reports the first frame at 0.0 s while
ffprobe puts it 0.5232 s / 2.0232 s after the container start; the derived corrections (+0.5232,
+2.0232) land on the reference within 2 ms, the audio clock is untouched, and the correction is
applied once.

* Upstream `FrameTime`/`TimeRange` stay on every picture-side reference. Seconds on a record are
  common-clock seconds; nothing is ever silently rewritten.
* A separately recorded audio asset takes a **caller-supplied** shift (`audio_shift_seconds`); an
  unsupplied shift is assumed `0` and a warning says so.
* The **lag probe** (`doc.sync.probe`) compares speech activity with the spread of the jaw opening
  at lags ±3 s. It is a coarse check: the mouth signal is ~3 samples/s (6 on `deep`) of the largest
  face, so it can reveal an offset of about a second or more. It does **not** verify lip-sync to a
  frame, does **not** measure drift, and is **never applied** to any time. States:
  `consistent`, `offset_indicated`, `ambiguous`, `insufficient`.

## What it relates

| Result | Meaning | Evidence |
| --- | --- | --- |
| `speech_sources` | per transcript segment and shot: `speaking_face`, `quiet_face`, `no_face_detected`, `ambiguous`, `visual_unavailable`, `insufficient` | mouth-opening spread while speech is heard |
| `speaker_links` | diarized speaker → anonymous `person_X`; `supported` / `candidate` / `conflict` | seconds of speaking face per shot, quiet-face seconds against |
| `text_matches` | ranked candidate shots per passage; grade `observed_text`, `described`, `entity_label`, `semantic_candidate`, `none_found`, `unknown` | exact words on screen / description / object label / embedding outlier |
| `sound_actions` | strong heard event near a seen event (`candidate`) | offset, how many fall in the window, how many are expected by chance |
| `boundaries` | speech that runs across a cut: seconds either side and what each side shows | consecutive shots |
| `opportunities` | `no_visible_speaker_window` (positive evidence of no moving mouth), `content_passage_context` | see `opportunities.py` |

Never conflated: a face that is only *present* is not a speaker; a *quiet* face is not an
off-screen voice; a missing face track is not "nobody is speaking"; `unknown` (nothing examinable)
is not `none_found` (examined, nothing matched). There is no grade for a demonstrated action or a
contradiction because nothing upstream measures them.

### Scores (all separate, none a probability)

`overlap_seconds`, `coverage_audio`, `coverage_video` (so a short event inside a long passage
shows as such), `correspondence` (Pearson r), `identity_support` (share of supporting seconds that
agree on one identity), `strength`. `strength` (`scoring.py`, `SCORING_VERSION` 1) is the strength
of the evidence, **not calibrated**: the weakest present signal limits it, a missing signal is left
out (never zero), a single signal is discounted, contradicting evidence scales it down.

## Cache and invalidation

One derived library document (`operation = audio_visual_alignment`) keyed by the **content
identity** of each upstream document used (library checksum, or a digest of a supplied object),
the audio/video asset ids, the audio shift, every profile value, the embedding model, and
`ALIGNMENT_VERSION` / `SCORING_VERSION` / `SCHEMA_VERSION` / `VOCABULARY_VERSION`. Not in it: how an
input was obtained, the time, the job, the device (this module runs no model of its own). A cached
document records how its inputs reached the run that *created* it. A damaged document is
discarded and recomputed. If the embedding engine fails during a run, the words-only result is
stored under the **words-only** key (and says so in `warnings`), never under the embedding model's
key, so it cannot be served as the complete result after the engine recovers. Re-tuning a profile value recomputes from stored inputs and touches no
upstream module.

## Profiles

`fast` (speech visibility and speaker links only) and `standard` (all stages).
Every threshold is a field of `AlignmentProfile` (`domain/config.py`); none is hidden in an algorithm.

## Optional multilingual matching

If Content Intelligence's embedding model is **already on disk**
(`uv run python scripts/fetch_models.py --embedding-only`), passages and the readable text of shots
are embedded once per run and a shot that stands out from the others by `semantic_min_z` standard
deviations becomes a `semantic_candidate` (never a stronger grade). Without it, matching is exact
words only; for non-English speech object labels and descriptions (English) can then only match
through on-screen text, and a `language_mismatch` flag says so.

## Known limits (honest list)

* **No speaker labels without diarization.** `audio_intelligence` fills them only when
  `AudioIntelligenceConfig(diarization=DiarizationConfig())` is used and the gated pyannote model
  is on disk. That path is tested with fakes only; no real diarization model was available here.
* Only the **largest face** has a mouth signal, and `people > 1` makes a shot `ambiguous`: crowds
  and over-the-shoulder shots yield few links. `people` is the number of person *tracks* in the shot,
  so a track that fragments counts twice and errs toward `ambiguous`, never toward a false link.
* Sound-to-action uses the events that exist: `energy_spike` against `motion_peak`, `gesture`,
  `track_entered`. No sound classifier ships, so footsteps, doors or applause are not named and no
  musical beats are matched. The `audio_event_kinds` setting passes any new kind through.
* One video and one audio per run. Several cameras, and the same person across clips, are not
  related (identity clusters are per video analysis).
* Alignment is **not measured against labelled real footage**; the thresholds are conservative
  design choices tested on synthetic cases and on real container timestamps only.

## Testing

```
uv run pytest tests/unit/audio_visual_alignment tests/architecture/test_audio_visual_alignment_boundaries.py
uv run pytest tests/integration/audio_visual_alignment   # real library and FFmpeg
```

No test needs a model, a network or a GPU.
