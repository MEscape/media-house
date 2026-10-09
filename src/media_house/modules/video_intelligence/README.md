# Video Intelligence

Answers, for any video asset: **what** is visible, **when**, **who/what** is involved, **where** in the
frame, **how** it was shot, **how it measures**, and **how sure** the measurement is. It sits after
Media Inspection and Video Improvement and before everything that decides anything.

```text
media_library ─► media_inspection ─► video_improvement ─┐
                                                        ├─► video_intelligence ─► (later stages)
                                       audio_intelligence ┘   (peer: never required)
```

## The three-tier rule

| Tier | Example | Here |
| --- | --- | --- |
| Observation | "the camera pans right at 0.19 frame widths per second" | yes |
| Assessment | "soft focus, confidence 0.8"; a score with reason codes and a rubric version | yes |
| Recommendation / decision | "stabilize this", "cut here", "use as B-roll" | **never** |

No field, enum value, reason code, score name, log message or docstring expresses an action. A test
(`tests/unit/video_intelligence/test_contract.py`) walks the result model and the source to enforce it.

## What is built (milestones 1-5)

| Layer | Analyzer (reads pixels / a model) | Derivation (pure domain) | Result |
| --- | --- | --- | --- |
| L1 | `shots` | shot detection, handles, keyframes | hard cuts, dissolves, fades through black, flashes rejected, durations, black at boundaries, stable margins, representative and sharpest frame |
| L3 | `motion`, `geometry`, `saliency` | camera, picture motion, lighting, horizon | static/handheld/pan/tilt/push-in/pull-out/mixed with direction, speed, shake; horizon and vertical tilt; backlight, shadow contrast, colour balance; attention point and peaks |
| L4 | `quality` | quality | sharpness, noise, exposure, clipping, flicker, flat/log awareness; junk indicators (black picture, slate, covered lens) |
| L2 | `entities` (torchvision detector), `faces` + `body` (MediaPipe), `text` (RapidOCR), `appearance` (CLIP) | tracking, identity, face cues, gestures, text/overlays | tracks with stable ids, entry/exit, occlusion; anonymous `person_A…` clusters; head pose, gaze, eye contact, eyes, smile cues, visual speaking; hand shapes; on-screen text; watermarks, lower thirds, burned-in captions, split screens |
| L5 | `embeddings` (CLIP), `descriptions` (VLM, deep only) | content type, environment, scenes, meaning | zero-shot content type and indoor/outdoor, environment, shot and scene embeddings by reference, keyframe descriptions |
| L6 | (the embeddings) | scenes, retakes, continuity | scene grouping, retake and near-duplicate groups, per-cut similarity, subject position and luma/colour differences, jump-cut likelihood |
| L3/L4 | (the entities) | framing, composition, crop-safe | wide/medium/close-up/extreme close-up, headroom, lead room, thirds, clutter, crop-safe windows for 16:9, 9:16, 1:1 |
| L7 | – | scores, events, curves | seven scores with rubric v1 conditioned on content type, `intentional_likelihood`, one ordered event timeline, time-series curves |

**Timeline / render mode (milestone 5) is NOT built, by the brief's own gate.** It says "only
implement this if the repo already has a canonical timeline representation". It has none
(`core` has `FrameTime`/`TimeRange`, nothing that is an edit), and the brief forbids inventing one
here. The result model already carries everything a sequence-level analysis consumes (ranges, shots,
continuity, curves); a timeline model owned by `core` or by the editing stage is the missing piece.
The optional keyframe-only VLM adapter of milestone 5 IS built (`descriptions`).

## Result model (typed; JSON is only the storage form)

`VideoAnalysis` → `shots` (each: range, boundaries, handles, keyframes, camera, motion, quality,
entities, faces, body, text, scene, content type, meaning, continuity, indicators, framing,
composition, lighting, geometry, crop-safe windows, scores) + `scenes`, `tracks`, `identities`,
`retakes`, `overlays`, `events`, `embeddings`, `curves` + `analyzers` + `provenance` + `inputs_used`
+ `warnings`.

* **Time.** Every time is a `core.domain.FrameTime`: frame index (presentation order from the first
  presented frame), pts in ticks of an **exact rational time base** (the decoder's own), so variable
  frame rate and NTSC rates are exact. Ranges are half-open.
* **Space.** Pictures are in **display orientation** (rotation applied, pixel aspect corrected).
  Boxes and positions are **normalised 0-1** of that picture, x to the right, y downwards
  (`domain/geometry.py`). Tilt angles are positive clockwise as seen; `yaw` positive toward the right
  edge, `pitch` positive up, `gaze_x` positive toward the right edge, `gaze_y` positive up.
* **Explicit states.** `ok`, `unknown`, `not_analyzed`, `not_applicable`, `not_available`, `failed`.
  Anything not `ok` carries a reason and no confidence; anything `ok` carries a confidence in [0, 1]
  and the frames that support it (`Assessed` enforces this at construction).
* **Confidence** is the strength of the measurement (correlation peak, detector score times sample
  count, agreement of estimates), not a calibrated probability.
* **Raw vs derived.** Per-frame signals are stored per analyzer; every description is derived from
  them in the pure domain (`domain/derive.py` orchestrates the per-topic modules).
* **IDs** are deterministic from the signals: `shot_<frame>`, `trk_<frame>_<n>`, `scene_<frame>`,
  `evt_<kind>_<frame>_<n>`, `person_A`, `emb_<shot id>`.
* **Measured on.** Camera and quality observations say `measured_on` (`original`, `improved`,
  `unknown`) and whether the footage was `stabilized`.
* **Query.** `find_shots(analysis, ShotFilter(...), RankBy.SHARPNESS)`; a value that was not measured
  never matches a condition on it. Embeddings are reused by reference (`analysis.embedding(ref)`).

## Privacy and ethics

Identity is **anonymous appearance clustering** only: tracks that look alike share a letter, tracks
on screen together never do, and nobody is named. Face-recognition weights are not used (their
licences are non-commercial); a permissively licensed face embedder is one more engine producing
`AppearanceSignals`. No ethnicity, health, age or other sensitive attribute is inferred. Expression
output is **visible cues** (eyes open, smile cue), never emotion. Junk and indicator labels are flags
that are never acted on. Everything runs locally; no media or frame leaves the machine, and no
network is used unless a run explicitly allows a model download.

## Resolver contract: resolve, reuse, request, degrade

`application/resolver.py` is the only place that talks to upstream modules, one implementation for
standalone and pipeline use. It contains no probing code.

| Input | Owner | Behaviour |
| --- | --- | --- |
| geometry, time base, VFR, rotation, colour, camera, timecode | `media_inspection` | stored → `reused`; else the owner is asked → `requested`; if impossible: fail clearly |
| what was done to this asset version | `video_improvement` provenance + library lineage | published record → `reused`; original → `not_applicable`; derived without a record → `not_available`, history `unknown` (never guessed) |
| black/frozen-frame findings | `media_inspection` | it provides none → `not_available` |
| speech/activity hints | `audio_intelligence` | not used by this version → `not_applicable` |
| project brief | `workspace` | none exists → `not_available`, neutral profile |

## Profiles, configuration and the cache key

| Profile | Analyzers | Notes |
| --- | --- | --- |
| `triage` | shots, quality | cheapest; original-footage quality metrics, deterministic |
| `fast` | + motion, saliency | no models |
| `standard` | + geometry, entities, faces, text, embeddings | models; each one that is not installed is `not_available` |
| `deep` | + body, appearance (identity), descriptions (VLM) | densest sampling, 960 px colour frames |

All versioned, all in `domain/profiles.py`; **every threshold is a field**, none is a constant in an
algorithm. A `ProcessingProfile` can also be built in code (`dataclasses.replace`).

* `MeasurementSettings` decide what is **measured**. Each analyzer's key contains only its own
  settings (`config_for`).
* `ShotSettings`, `MotionSettings`, `QualitySettings`, `TrackingSettings`, `FaceSettings`,
  `TextSettings`, `MeaningSettings`, `CinemaSettings` and the **content-type profiles** (`generic`,
  `talking_head`, `vlog`, `tutorial_screen`; a data table, `CONTENT_PROFILES`) decide how stored
  signals are **interpreted**. Changing one re-derives from the stored signals; nothing is decoded and
  no model runs. The zero-shot vocabulary (`domain/vocabulary.py`) is data too: a new content type is an
  edit of the table plus a `VOCABULARY_VERSION` bump.
* **Scores** (`domain/scoring.py`, rubric v1, formulas in its docstring) are conditioned on the content
  type; `RUBRIC_VERSION` must be bumped whenever a score's meaning changes, so scores stay
  comparable across videos.
* `RuntimeConfig`: `device` (`auto`/`cpu`/`gpu`; the model engines use PyTorch on the GPU when there
  is one and the CPU otherwise, results agree within tolerance and the device is never part of a cache
  key) and `allow_model_download` (off).

The Media Library is the only cache. Each analyzer's signals are a derived asset with
`operation = video_intelligence.<analyzer>`; the key holds the settings it uses, the sampling stride,
decode geometry, the decoder's and its own engine's version (for a model: its name and weights
hash), and **the fingerprint of every analyzer whose signals it reads** (so a changed detector
invalidates the face analysis that is tiered by it). The library adds the source content hash. The
result is another derived asset keyed by the profile's thresholds, the signal fingerprints, derivation
versions, history, engines and the set of missing analyzers (so a result without a failed or
unavailable analyzer never answers for the complete one).

## Performance

One FFmpeg process decodes the video **once** into up to three raw proxy files: every frame at
128 px grey (cuts, flicker, handles), every N-th frame at 480 px grey (motion, quality, saliency) and
every M-th frame at 640 px colour (models, geometry); `showinfo` supplies exact per-frame
timestamps from the same pass. Files are read in bounded chunks (never memory-mapped, so scratch
files delete cleanly on Windows). Adaptive plans pick which sampled frames are analysed: dense where
the picture changes, never longer than a gap apart. Tiering: the face and body models read only
frames where a person was found. Models load **once per run** (and are kept per engine); GPU
memory exhaustion moves the engine to the CPU. A cancelled run stores no result; analyzers already
computed stay cached, so a rerun resumes from them.

Disk: the scratch files of one decode are about `frames x 9 KB` (dense) + `samples x 130 KB` +
`colour frames x 0.7 MB`; they are deleted when the decode ends.

## Models, dependencies and licences

Install: `uv sync --extra vision` (MediaPipe, RapidOCR, torchvision, transformers). Model files:

| Analyzer | Engine | Model | Licence | Where it lives |
| --- | --- | --- | --- | --- |
| entities | torchvision SSDLite320 MobileNetV3 | COCO weights | BSD-3 (torchvision) / COCO CC-BY-4.0 data | `<cache>/video_intelligence/models` or the torch hub cache |
| faces, body | MediaPipe Tasks | face detector + landmarker, pose lite, hand landmarker | Apache-2.0 | `<cache>/video_intelligence/models` (URL and pinned SHA-256 in `mediapipe_engines.py`) |
| text | RapidOCR + ONNX Runtime | PP-OCR small (in the wheel) | Apache-2.0 | the wheel |
| embeddings, appearance | transformers CLIP | `openai/clip-vit-base-patch32` | MIT | Hugging Face cache |
| descriptions | transformers VLM | `HuggingFaceTB/SmolVLM-256M-Instruct` | Apache-2.0 | Hugging Face cache |

A missing library or model file makes **that analyzer** `not_available` with a message naming what
to fetch; downloading happens only when a run sets `allow_model_download`, and a file is verified
against its pinned checksum first. Other dependencies: FFmpeg (external process), NumPy and SciPy.
**Not used on purpose:** YOLO-family detectors (AGPL), face-recognition weights (non-commercial),
PySceneDetect / TransNetV2 and OpenCV (not needed: classical signals and FFT phase correlation meet
the bar; OpenCV is installed transitively by MediaPipe and RapidOCR but never imported here).

## How downstream modules consume it

```python
from media_house.modules.video_intelligence.application.contracts import (
    AnalyzeVideoCommand, VideoAnalyzer, find_shots, ShotFilter, RankBy, ScoreName)

result = analyzer.execute(AnalyzeVideoCommand(asset_id, "standard"), ctx)   # blocking: run as a job
analysis = result.value.analysis                                            # typed, never raw JSON
shot = analysis.shot_at(frame=240)
start, end = shot.range.start, shot.range.end                               # FrameTime: frame + pts + timebase
usable = shot.score(ScoreName.TECHNICAL_USABILITY)                          # value, reasons, confidence, rubric
```

Other modules must reuse `curves`, keyframe times and `embeddings` rather than recompute them, and
must treat any state other than `ok` as "not known", never as zero.

## How to add an analyzer

1. `domain/values.py`: add its id to `AnalyzerId`; `domain/analyzers.py`: add its `AnalyzerSpec`
   (version, cost tier, required and optional dependencies, which frame stream it reads).
2. `domain/signals.py`: a frozen signal type (columns or rows); register it in
   `domain/serialization.SIGNAL_TYPES` (the reflective codec needs nothing else).
3. `domain/profiles.py`: its measurement settings and its entry in `_MEASUREMENT_KEYS`; add it to the
   profiles that should run it.
4. `infrastructure/`: an engine implementing `SignalAnalyzer` (`identity`, `unavailable`, `device`,
   `measure`); import heavy libraries lazily, wrap their errors in `ExternalSystemError`, pin model
   checksums, record the licence; register it in `module.signal_analyzers()`.
5. `domain/derive.py` (+ a small pure module): the derivation from signals to result sections; bump its
   entry in `DERIVATION_VERSIONS`. A section whose analyzer did not run is `not_analyzed`.
6. Tests: hand-made signals → derivation (unit), a fake engine through the use case, and the real
   engine on a real picture (skipped when unavailable).

## Known limits (honest list)

* A camera push-in and an optical zoom-in look the same in pixels; both are `push_in`.
* A dissolve between two **moving** shots is not found; a fast whip pan can look like a cut; screen
  recordings with abrupt UI changes produce cuts. Confidence is lower for gradual transitions.
* Picture-in-picture overlays are not detected (watermarks, lower thirds, captions, split screens are).
  COCO has no "product" class, so products are plain objects.
* Tracking works on sampled frames (a few per second): fast movers between samples are followed by
  their centre; long occlusions start a new track. The visual speaking cue is mouth activity at that
  rate, so it says "the mouth moves", not who speaks and not in sync with sound.
* Head pose and gaze come from 2D landmark geometry (good to a few degrees). Eye contact is "head and
  gaze toward the camera", not intent.
* Scenes, retakes, content type and environment need the embedding analyzer; without it they are
  `not_analyzed`. Content-type labels are zero-shot guesses with a margin and are `unknown` when no
  label stands out.
* Slide change and scrolling are inferred from text and motion measurements in tutorial-like footage
  and are lower-confidence events.
* The per-frame shot signals are a JSON document and the library accepts at most 64 MB, about 1.1
  million frames (five hours at 60 fps); longer inputs need a binary signal store.
* Resume is per analyzer, not within one; analyzers run one after another after the single decode.
* Timeline / render mode: see above (blocked by the brief's gate).
* Black/frozen-frame findings are not available: `media_inspection` does not provide them.
