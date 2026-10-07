# Audio Improvement

Makes audio **cleaner, more consistent and production-ready**: an independent subsystem next to
Audio Intelligence ("what is happening in the audio?"). Improvement answers "how can this sound
better?" and never interprets the content.

```text
Media Library audio/video asset ──(never modified)──────────────────────────────┐
        │                                                                       │
        ▼ decode once: float PCM, source sample rate, container time origin     │
   Analyze      QualityMeasurements before (EBU R128 loudness, SNR, hum, clipping,
        │       reverb, balance, sibilance, dynamics)                            │
        ▼                                                                       │
   Decide       ProcessingPlan: a stage runs only on measured evidence           │
        ▼                                                                       │
   Enhance      clipping → noise → dereverb → EQ → dynamics → de-ess             │
        │       every stage re-measured; a stage that made things worse is      │
        │       reverted (BYPASSED)                                              │
        ▼                                                                       │
   Master       gain to the LUFS target + true-peak limiter, refined by measuring│
        ▼                                                                       │
   Re-check     QualityMeasurements after + delivery checks                      │
        ▼                                                                       ▼
   Improved audio: derived asset  ◄──── provenance + before/after metadata ─────┘
```

`MixAudio` is the second use case: voice + music → music placed `music_offset_lu` below the voice,
ducked by the voice (sidechain), looped/cut to the voice's length, then **mastered after the
mix**. It is a derived asset of the voice and records the music asset it used.

## Where things live

| Layer | Contents |
| --- | --- |
| `domain` | `settings.py` (THE home of every tunable value), `profiles.py` (built-ins + overrides), `measurements.py`, `planning.py` (decide), `verification.py` (stage guards, delivery checks), `provenance.py`. Pure Python, no I/O. |
| `application` | `ImproveAudio`, `MixAudio`, `Mastering` (shared loop), ports (`AudioTranscoder`, `QualityAnalyzer`, `StageProcessor`, `AudioMixer`), `contracts.py` (the public API). |
| `infrastructure` | `FfmpegTool` (the only place that builds FFmpeg commands), transcoder, `SignalQualityAnalyzer` + `quality_metrics.py` (NumPy/SciPy), FFmpeg stage engines, `SpectralDereverb`, `FfmpegMixer`. |

No storage of its own: results are Media Library assets, reached through
`media_library.application.contracts.MediaLibrary`. Other modules import only
`audio_improvement.application.contracts`.

## Public API (`application/contracts.py`)

```python
result = improver.execute(ImproveAudioCommand(asset_id, profile="youtube",
                                              overrides={"mastering.target_lufs": -15.0}), ctx)
result.value.asset            # the improved audio (derived asset of the source)
result.value.provenance       # what was done, per stage, plus the MEASURED loudness of the result
result.value.analysis_before / .analysis_after / .checks / .warnings
read_provenance(asset.metadata)   # the same facts for any asset, without calling this module
```

## Profiles and settings

`default → built-in profile → user overrides → resolved AudioProfile`. Built-ins:
`youtube` (default, −14 LUFS), `social_video` (−14, tighter dynamics, firmer de-essing),
`podcast` (−16, most natural), `cinematic` (−23, wide dynamics), and `custom` (the defaults plus
your overrides). A profile only states how it differs from the default.

Overrides are dotted paths (`"noise.max_strength"`, `"eq.highpass_hz"`, `"mix.duck_ratio"`),
type-checked and validated by the setting they land in (`InvalidProfile` says what is wrong). A
future settings UI edits exactly this mapping. Every value in `domain/settings.py` is a tunable
engine parameter with a conservative default; thresholds say **when** a stage acts, strengths
say **how much**, `max_*`/`min_*` guard values say when a result counts as a regression.

> The thresholds were calibrated on synthetic recordings. Before relying on them, tune the
> defaults (especially `de_ess.trigger_db`, `dynamics.dynamics_trigger_db`, `eq.mud_trigger_db`)
> on real recordings of the target speaker; the profile system exists for exactly that.

## Provenance (the no-duplicate-work contract)

Stored in the improved asset's metadata under `audio_processing` (schema-versioned):
profile, processing version, one `StageRecord` per stage (`applied` / `skipped` / `bypassed`,
reason, engine, engine version, parameters), the **measured** integrated LUFS and true peak of the
result, and `leading_pad_seconds`. Convenience facts: `noise_reduced`, `dereverberated`,
`de_essed`, `eq_applied`, `dynamics_processed`, `clipping_repaired`, `true_peak_checked`,
`meets_loudness(target, tolerance, ceiling)`.

Readers decide from facts and treat missing, damaged or newer-schema provenance as **unknown**.
Audio Intelligence uses it in `application/prior_processing.py`: when it is asked to normalise
loudness and the source is *measured* within tolerance of its target, it skips that pass (the
extracted audio asset records `reused_processing`). On any other input it normalises itself, so
both modules work alone and chained. Add further reuse the same way: one small function per
operation, driven by provenance facts, never by "Improvement ran, so it must be done".

## Reuse of Media Inspection (optional)

Decoding a source needs the sample rate, channels, duration and the audio's offset from the
container origin. When Media Inspection has already stored an inspection of the source,
`application/prior_inspection.py` takes these facts from it (through the `InspectionCatalog`
contract) instead of probing the file again; with no inspection, or one that cannot supply them,
the transcoder probes as before. The result is identical either way, so the processing
fingerprint does not depend on it, and this module imports nothing of Media Inspection but its
public contract.

## Determinism, caching, time

* Fingerprint = source content + resolved profile + every engine's identity (name, FFmpeg
  version, mapping revision) + analyzer + transcoder + `PROCESSING_VERSION`. A different setting,
  engine or version is a different result; an identical request is answered from the library (also
  after a restart).
* Engines are deterministic (identical bytes for identical input), tested.
* Time is preserved to the sample: the working format keeps the source sample rate, no engine may
  shift or trim (tested per engine; FFmpeg's `afftdn` latency is compensated explicitly), and the
  stage guard reverts any stage that changes the duration. A video whose audio starts after the
  container origin gets that offset as explicit leading silence, reported as
  `provenance.leading_pad_seconds`, so WhisperX/Audio Intelligence timestamps stay valid.

## Adding or replacing a processing capability

* **Better denoiser / another dereverb / new mastering engine:** write a class implementing
  `StageProcessor` for that stage (see `ffmpeg_stages.py`, `spectral_dereverb.py`) and register it
  in `module.py`. Nothing else changes; its identity retires old cache entries.
* **A new stage:** add it to `ProcessingStage`, a settings group in `settings.py`, a decider in
  `planning.py`, a guard case in `verification.py`, an engine, and its place in
  `ENHANCEMENT_ORDER`. Bump `PROCESSING_VERSION`.
* **A new loudness target / profile:** an entry in `profiles.py`.
* **Speaker-specific or calibrated profiles:** produce an `overrides` mapping; no code changes.
* Neural engines (e.g. DeepFilterNet3) are a drop-in `StageProcessor` for `NOISE_REDUCTION`. They
  are not bundled: the dependency stack (Python 3.13, torch 2.8) is not known to support them, and
  the FFmpeg engine already meets the measured guard. Add one when it earns its weight.

## Honest limits

* Severe clipping cannot be fully restored; it is repaired where measurable and reported as a
  warning (`plan.warnings`), never promised.
* Dereverberation is a conservative statistical engine for clearly wet rooms; the guard reverts it
  unless the measured reverberation time falls.
* Background-music detection inside a single recording is **not** implemented (no reliable local
  method is bundled); mixing takes the music as an explicit separate asset.
* Speech-aware ducking from Audio Intelligence signals is a later layer: it would produce
  different `MixSettings`, not make this module import Audio Intelligence.
* Mono and stereo only (multichannel sources are downmixed to stereo).
