# Audio Intelligence

Turns an audio or video asset of the Media Library into a **typed, word-level timeline**
(`Transcript`) that every later pipeline stage can query without knowing WhisperX exists.

```text
Media Library asset (audio/video)
        │
        ▼  ffprobe + ffmpeg                       derived asset  "audio_extraction"
Prepared audio (16 kHz mono PCM WAV) ───────────► (ordinary, reusable library audio)
        │
        ▼  WhisperX: ASR (faster-whisper)  →  forced alignment (wav2vec2)
RawTranscription  (engine-neutral)
        │
        ▼  domain builder: offset → source timeline, normalise, emphasis hints, validate
Transcript ─────────────────────────────────────► derived asset  "transcription"
                                                  (versioned JSON document)
```

## Where it lives

`modules/audio_intelligence/` is a normal vertical slice (domain / application / infrastructure /
`module.py`). It has **no storage and no database of its own**: audio and transcripts are Media
Library assets, reached only through `media_library.application.contracts.MediaLibrary`.
Other modules import only `audio_intelligence.application.contracts`.

| Layer | Contents |
| --- | --- |
| `domain` | `Transcript`/`Segment`/`Word`, queries, frame maths, text normalisation, validation, builder, JSON schema, configs |
| `application` | `TranscribeAudio` use case, ports `AudioPreparer` and `TranscriptionEngine`, `contracts.py` |
| `infrastructure` | `FfmpegAudioPreparer` (via `ProcessRunner`), `WhisperXEngine` (the only file importing WhisperX/torch) |

The Media Library itself was extended (its documented `FormatProbe` extension point) so that it
can ingest audio and video (`FfprobeProbe`) and typed JSON documents (`JsonDocumentProbe`).

## Reuse: process once, persist once

Both derived assets are found through the library's *processing fingerprint*
(source content + operation + version + canonical config), so nothing is extracted or
recognised twice, also across application restarts.

* Part of the transcript identity: engine **version**, model, language (or `auto`), alignment on/off,
  alignment model, alignment-failure policy, chunk size, preparation (sample rate, channels,
  loudness normalisation, stream) and `TRANSCRIPTION_VERSION`.
* **Not** part of it (speed/memory only): `device`, `compute_type`, `batch_size`.
* Bump `TRANSCRIPTION_VERSION` / `EXTRACTION_VERSION` (`domain/values.py`) when the pipeline's
  output changes; older results then stop matching.

## Timeline rules

* Seconds (float) are canonical and always refer to the **original media timeline**. If the audio
  stream starts after the container origin, that measured offset is added to every time
  (`metadata.audio_offset`). Frames are derived: `transcript.to_frame(t, fps, rounding)`
  (`FLOOR` default = the frame containing the instant; pass NTSC as `Fraction(30000, 1001)`).
* `raw_word` is canonical (punctuation kept); `normalized_word` is derived; re-derive with
  `word.normalized(NormalizationOptions(lowercase=True))`.
* `word.timing` says how trustworthy a time is: `aligned` (measured), `interpolated` (aligner skipped the token),
  `segment_estimate` (no word timing at all). `metadata.alignment_method` is `forced_alignment`,
  `whisper_segment` (explicit fallback) or `none`. Nothing is downgraded silently:
  with the default `AlignmentFailurePolicy.ERROR` an unsupported language fails with
  `AlignmentUnavailable`.
* `validate_transcript` reports (`error`/`warning`) and never edits data; findings are kept in
  `metadata.warnings`.
* `emphasis_hints` (`uppercase`, `long_duration`) are hints, not emotion detection.
* Pauses are preserved: `gap_before`, `gap_after`, `pauses(min_duration)`.

## Hardware

`device="auto"` uses CUDA when PyTorch **and** CTranslate2 both see a GPU, else CPU.
`compute_type=None` picks the best type the hardware supports (`float16` → `int8_float16` →
`int8` → `float32` on GPU; `int8` → `float32` on CPU). Pascal GPUs (GTX 10xx) lack fast fp16 and
get `int8`. Explicit unsupported choices raise `ConfigurationError`; the model is never silently
swapped. Models are cached inside the single `WhisperXEngine` and inference is serialised, so
batch processing reuses one loaded model and cannot exhaust GPU memory with parallel copies.

First use of a model downloads its weights (e.g. large-v3 ≈ 3 GB, wav2vec2 aligner ≈ 400 MB) to
the Hugging Face / torch caches; later runs are offline. torch is installed from the CUDA 12.8
index on Windows/Linux (see `pyproject.toml`).

## Example (from another module)

```python
from media_house.modules.audio_intelligence.application.contracts import (
    AudioEngine,
    TranscribeAudioCommand,
    TranscriptionConfig,
)
from media_house.shared.errors import Err


def build_word_animations(engine: AudioEngine, voiceover_asset_id: str, ctx: JobContext) -> None:
    result = engine.execute(
        TranscribeAudioCommand(voiceover_asset_id, TranscriptionConfig(language="de")), ctx
    )  # blocking: call from a job
    if isinstance(result, Err):
        raise result.error
    timeline = result.value.transcript  # typed domain object, no WhisperX types
    word = timeline.word_at(12.5)
    for w in timeline.words_between(10.0, 15.0):
        start_frame = timeline.to_frame(w.start, fps=30)
        print(w.normalized_word, start_frame, w.emphasis_hint, timeline.gap_before(w))
    audio_path = ...  # library.local_path(result.value.audio_asset.id) for the audio itself
```

JSON interchange: `to_json(transcript)` / `from_json(text)` (`schema_version` 1, microsecond
precision, deterministic). The stored document is the library asset `result.value.asset`.

## Not in scope

Subtitles, B-roll, animations, FCPXML/DaVinci, HyperFrames, rendering, GUI, diarization
(`speaker` fields exist for later), noise reduction/mastering (a future audio-processing module
will consume the extracted audio asset and produce another derived asset).

---

# Audio Intelligence Timeline (speech + acoustics + events + editing signals)

`AudioAnalyzer.execute(AnalyzeAudioCommand(asset_id, AudioIntelligenceConfig()), ctx)` returns an
`AnalysisResult` whose `timeline` (`AudioIntelligenceTimeline`) is the one canonical result.
The transcript-only API above (`AudioEngine`) is unchanged and is reused as the speech stage.

```text
Media Library audio/video asset
   |  prepare once (FFmpeg)                      -> derived asset "audio_extraction"
   v
 prepared 16 kHz mono PCM  ──┬── WhisperX ASR + forced alignment  -> "transcription"
                             └── acoustic extractor (decoded once, analyzers in parallel)
                                   Praat pitch (two-pass), RMS, K-weighted loudness, activity,
                                   pluggable event detectors     -> "acoustic_measurements"
   both finish ─> fusion (pure, deterministic):
      baseline -> local rates -> events + pauses -> word/segment evidence -> scorer
      -> editing signals -> validation                           -> "audio_intelligence"
```

Layers of meaning, never collapsed into one score:

| Layer | Where | Examples |
| --- | --- | --- |
| **Measured** | `AcousticTrack` (frames), `AudioEvent` from detectors | `f0`, `rms_db`, `loudness`, `speech`, laughter |
| **Baseline-relative / local** | `PitchSummary`, `EnergySummary`, `RateSummary` | `relative_st`, `z_score`, `percentile`, `local_deviation_*`, `relative`, `change` |
| **Derived acoustic intelligence** | `WordSignals`, `SegmentAcoustics` | `emphasis`, `expressiveness`, `arousal`, `local_contrast`, `monotony` |
| **Editing signals** | `EditingSignal` | `moment`, `cut`, `broll`, `music_duck`, `music_break`, `zoom_emphasis` |

## Data model

* **Time:** seconds (float) on the ORIGINAL media timeline. Frames: `origin + i*hop` (default hop
  20 ms), `AcousticTrack.origin == metadata.audio_offset`. Measurements are stored in
  prepared-audio time and shifted by `audio_offset` during fusion (words, frames and events alike).
  `timeline.to_frame(t, fps)` derives frames.
* **Two time scales:** continuous frames (`timeline.acoustic_at(t)`, `frames_between`) and
  semantic words/segments. Frames are stored ONCE (columns); words only reference them
  (`first_frame`/`last_frame`) and carry summaries.
* **Missing data:** `NaN` in frames / `null` in JSON / `None` in Python = not measured or not
  enough evidence (unvoiced frame, word without voiced frames, no context). Never 0.
* **Scores:** all in [0, 1]. Each is a weighted mean over the evidence that EXISTS (weights
  renormalised), keeps its `contributors`, and says what it is relative to:
  * `*_level` = baseline-relative magnitude (speaker normal), `local_*` = relative to the
    preceding `local_window` seconds, `*_change` = strongest sudden-change event in the word
    (`strength` = |change| / configured full scale), `duration` = word length per character vs the
    speaker's median.
  * `emphasis`, `expressiveness`, `arousal`, `moment`, ... are HEURISTIC (`heuristic-1`), not
    model output; weights/full-scales live in `ScoringConfig` and are part of the fingerprint.
* **Pitch baseline:** robust (median, percentiles, MAD) over voiced speech frames; pitch is in
  semitones relative to the speaker's median F0 so a deep and a high voice use one scale.
  `Baseline.scope` is `"global"`; `speaker` is reserved for diarization (same structure per speaker).
* **Energy:** RMS dBFS and momentary K-weighted loudness (BS.1770 weighting, 400 ms, ungated).
  dB values are averaged in the power domain, never arithmetically.
* **Speaking rate:** words per second over `rate_window`, long pauses excluded from speaking time;
  `change` = tanh(log2(rate after / rate before)).
* **Events:** `AudioEvent(kind, start, end, strength, confidence, source)`. Built in:
  `pitch_rise/fall`, `energy_spike/drop` (sudden, sustained-level aware), `silence`, `pause`.
  `confidence` is the detector's own and `None` otherwise. Non-verbal detectors (`laughter`,
  `breath`, ...) plug in via `AudioEventDetector` (see `infrastructure/acoustic_extractor.py`);
  none ships by default because no reliable local model is bundled. Nothing is fabricated.
* **Not emotions:** terms are `arousal`, `expressiveness`, `emphasis`, `monotony` (acoustic
  descriptors). No happy/sad/angry, no irony claim; the evidence is kept for later layers.

## Reuse and versions

| Asset (operation) | Identity (fingerprint) |
| --- | --- |
| `transcription` | engine version, model, language, alignment, chunking, preparation |
| `acoustic_measurements` | analyzer versions (pitch/energy/loudness/activity/detectors), `AcousticConfig`, preparation |
| `audio_intelligence` | all of the above + `AnalysisConfig` + `ScoringConfig` + the scorer's identity + every pipeline version (transcription, extraction, measurements, analysis) |

All three documents go through one `DerivedDocuments` helper (`application/derived_documents.py`):
look up by fingerprint, treat an unreadable document as absent, register a new one. A stage
never reimplements it.

Constants that are part of an algorithm (not settings) are named in the module that uses them and
pinned by `tests/unit/audio_intelligence/analysis/test_algorithm_constants.py`: change one, bump
the matching version, update the snapshot.

Changing only scoring or analysis settings re-fuses (milliseconds) and keeps transcript and
frames; a new analyzer version re-measures but keeps the transcript; a new speech model
re-transcribes but keeps the frames. Versions: `TRANSCRIPTION_VERSION`, `MEASUREMENTS_VERSION`,
`ANALYSIS_VERSION`, `ScoringConfig.version`, JSON `schema_version`.

## Audio Improvement (optional, independent)

Audio Intelligence works on any audio. When its input is an asset produced by Audio Improvement it
reads that asset's *processing provenance* through `audio_improvement.application.contracts`
(`application/prior_processing.py`) and skips a pass only when the provenance proves, by
measurement, that the result is already what the pass would produce (today: loudness
normalisation, when the measured loudness is within `LOUDNESS_TOLERANCE_LU` of the preparation
target and the true peak is under its ceiling). The skipped operation is recorded in the prepared
audio asset's `reused_processing` metadata. Anything unproven is simply done again. The original
signal is untouched by Improvement, so pitch and prosody analysis can also run on the original.

## Media Inspection (optional, independent)

Preparing audio needs the source's duration and the offset of the chosen audio stream from the
container origin. When Media Inspection has stored an inspection of the source,
`application/prior_inspection.py` takes these from it (through the `InspectionCatalog` contract)
instead of probing the file; with no inspection, or when the requested audio stream is not in
it, the preparer probes (and reports a missing track) as before. The prepared audio and the
transcript are identical either way.

## Parallelism

Speech (model bound) and measurement (CPU) run concurrently on exactly two worker threads;
audio is prepared once and decoded once for the analyzers. WhisperX inference stays serialised and
its models are shared, so batch work cannot multiply GPU memory.

## Querying and debugging

```python
tl = result.value.timeline
word = tl.word_at(12.43)        # TimelineWord: .pitch .energy .rate .pitch_change .emphasis_score ...
tl.explain_word(word)           # {"emphasis": {"score": 0.91, "energy_level": ..., ...}, ...}
tl.acoustic_at(12.43)           # frame with f0, relative_pitch_st, relative_energy_db, ...
tl.events_between(10, 15, kinds=["pitch_rise"])
pause = tl.pause_after(word)    # raw duration, kind short/medium/long, silence_ratio
tl.moment_at(42.1)              # EditingSignal(score, contributors) for the spoken word
tl.signals_between(0, 60, "music_break")
tl.signal_curve("moment", hop=0.1)   # (time, score); uncovered instants omitted
```

Editing signals are evidence aggregates (`kind, start, end, score, contributors, anchor`), never
decisions: this module does not cut, zoom, duck music or pick footage.

`validate_timeline` reports (never repairs): non-finite/impossible values, ordering, ranges of
confidences and scores, frame/word synchronisation, source identity. Warnings and transcript
findings are kept in `metadata.warnings`. An ERROR in the analysis layers (`validate_analysis`)
makes fusion raise `InvalidTimeline`; `AnalyzeAudio` returns it as an `Err` and stores nothing.

Not yet defined: partial failure. A failing event detector or acoustic analyzer fails the whole
analysis. Add an explicit per-analyzer availability field (and keep such a result out of the
cache) together with the first detector that ships.
