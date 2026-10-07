# Media Inspection

Establishes the **technical ground truth** of every audio and video asset: what the file is, what
properties it has and whether anything about it is technically wrong. It never improves,
interprets or edits media. Audio Improvement answers "how can this sound better?", Audio
Intelligence "what is happening in the audio?"; Media Inspection answers "what technically is
this, and can I rely on it?".

```text
Media Library asset (never modified)
        │
        ▼ ffprobe: container + streams (JSON)        ffprobe: every packet's timestamp
        │ ffprobe: first-frame side data (HDR)       ffmpeg: decode everything into nowhere (FULL)
        ▼
   ObservedMedia ── what the file SAYS and what was MEASURED, in a stable vocabulary
        │   raw_metadata keeps the tool's own report next to it, untouched
        ▼
   assess():  synchronization (measured)  →  rules  →  findings  →  status
        ▼
   MediaInspection: a versioned JSON document, stored as a derived asset of the source
```

## What an inspection contains

| Section | Content |
| --- | --- |
| `container` | format names, duration, start time, bit rate, size, stream count, probe score, tags |
| `video_streams` | codec, profile, level, encoder, **exact** frame rates and time base as fractions, storage size, sample/display aspect ratio, rotation, pixel format, bit depth, chroma subsampling, alpha, scan type and field order, colour (space, primaries, transfer, range, SDR/HDR10/HLG, mastering display, light level, Dolby Vision), bit rate, B-frames, and measured `timing` |
| `audio_streams` | codec, sample rate, bit depth, channels, layout, language, title, start time, duration, bit rate, measured `timing` |
| `other_streams` | subtitles, data and timecode tracks, cover art: the stream map is complete |
| `timecode` | start timecode (drop-frame aware), where it was read, its position on the clock |
| `synchronization` | each audio stream's start offset, end offset and duration difference against the video |
| `production` | creation time, camera make/model, lens, focal length, aperture, shutter, ISO, white balance, exposure, reel, clip, scene, take, device, software, and every container tag |
| `integrity` | readable or not, whether the streams were decoded, read and decode messages |
| `findings`, `status` | what the rules concluded, and the one-glance answer (below) |
| `raw_metadata` | ffprobe's report, untouched |

### Facts, measurements, conclusions

* `Sourced[T]` carries a value and its **provenance**: `declared` (the file states it),
  `detected` (measured by reading the media), `inferred` (derived by a rule, such as bit depth
  from the pixel format or HDR from the transfer characteristic) or `unknown`. Colour is never
  assumed: an undeclared property stays `unknown` and produces a `color.undeclared` finding.
* Timing is **detected**, not trusted: frame spacing comes from the packet timestamps, so a
  container that claims 30 fps but stores irregular timestamps is reported as variable frame
  rate, with the declared rate still available. Dropped frames, discontinuities, duplicate and
  missing timestamps, audio gaps and overlaps are counted.
* Findings state how firmly they are established (`Certainty`): `measured` is a plain
  observation, `possible` is suspicious without proof (an audio offset might be intentional, a
  low bit rate might be fine), `confirmed` contradicts what the file declares or a format rule
  (decode errors, a timecode frame that cannot exist, a layout that names a different channel
  count). Each finding carries its evidence and, where it matters, a `Concern`
  (`needs_sync_check`, `needs_transcode`, `needs_color_check`, `needs_audio_check`,
  `needs_repair`).
* `status` answers "can I use this?": `verdict` (valid / warning / error), `usable` (no errors)
  and the collected concerns. Facts are never hidden by a finding, and a file with no findings
  still has all of them.

Findings are derived from facts by `domain/rules.py` with the thresholds in `InspectionConfig`
(sync tolerance, interval tolerance, discontinuity length, ...). A damaged file is **reported,
never repaired**: an unreadable container is an inspection with an error finding, not an
exception.

## Public API (`application/contracts.py`)

```python
result = inspector.execute(InspectMediaCommand(asset_id), ctx)  # MediaInspector, a job
inspection = result.value.inspection  # typed, immutable
inspection.primary_video.frame_rate.value  # Rational(30000, 1001)
inspection.audio_stream(0).start_time
inspection.status.usable, inspection.status.concerns
inspection.findings_with("video.variable_frame_rate")

catalog.find(asset_id)  # InspectionCatalog: a lookup only, None if never inspected
relations.execute(asset_id)  # MediaRelations: recorded facts and candidates with confidence
```

Depth: `Depth.FULL` (default) also decodes every stream to find damage, which is the expensive
part; `Depth.PROBE` reads headers and packet timing only. A stored `FULL` inspection satisfies a
`PROBE` request.

## Reuse and caching

An inspection is a JSON document registered as a **derived asset** of its source in the Media
Library; Media Inspection has no storage of its own. The fingerprint is source content +
operation + `INSPECTION_VERSION` + every config field + the tool identity (ffprobe and ffmpeg
versions and this adapter's revision). The same media is therefore probed once, also after a
restart, and a different threshold, depth or tool version is a different inspection. A damaged
stored document is removed and inspected again (the library would otherwise hand the same
damaged asset back for the same fingerprint). The asset metadata carries `verdict`, `usable`,
`concerns` and `depth`, so a status check does not need the document.

### Pipelines without coupling

```text
Media ─► Media Inspection ─► Audio Improvement ─► Audio Intelligence
              │                    ▲                     ▲
              └── stored document ─┴─────────────────────┘   (optional, by lookup only)
```

Audio Improvement and Audio Intelligence take an optional `InspectionCatalog`. When a stored
inspection exists they use its stream facts (sample rate, channels, duration, the audio's offset
from the container origin) instead of probing the source again; when none exists, or it cannot
supply the facts, they probe as before. Results are identical either way (tested byte for byte),
and each module still works alone. The code lives in each module's `application/prior_inspection.py`;
architecture tests keep the seam to that and `module.py`.

## Relationships

`FindRelatedMedia` compares an asset with the library using only what the library records
(duration, size, container, codec, checksum, derivation). Recorded derivation and identical
checksums are facts (confidence 1.0). Everything else is a **candidate** that is never certain
(at most 0.9): a possible duplicate (same length and picture size, different bytes), a possible
proxy (same length and shape, clearly different resolution) or a possible companion (a silent
video and an audio-only file of the same length). Relationships change with the library, so they
are evaluated on demand and never stored.

## Extending

* **New tag vendors or camera fields:** an alias in `domain/production.py`.
* **A new finding:** a function in `domain/rules.py`, appended to `RULES`; bump
  `INSPECTION_VERSION` so old results are retired.
* **A new fact:** a field on the model in `domain/model.py` (the stored document follows the
  dataclasses), read in `infrastructure/ffprobe_normalizer.py`; bump `INSPECTION_VERSION`.
* **Another container or codec:** nothing, unless ffprobe cannot read it; vocabulary is not
  tied to formats.
* **A different probing tool:** implement `MediaProber` and register it in `module.py`.
* **Image sequences, multicam, conform and proxy matching by timecode, advanced audio
  metadata:** extend `ObservedMedia` and `relations.py`; no second inspection mechanism.

## Limits

* Only what ffprobe and a decode pass can show. Mid-stream changes of resolution or colour
  parameters are not detected (that needs per-frame inspection); the first frame's side data is
  read for HDR metadata.
* Interlacing is whatever the container declares; unmarked interlaced content is not detected
  (that needs frame analysis, which would belong here as a deeper `Depth`).
* Clean aperture and rate-control details are not exposed by ffprobe and are not reported.
* Only audio and video assets; images and image sequences are not inspected yet.
* Drift is judged from packet timestamps, with a tolerance of one audio packet and one frame
  for where the last packet ends; it is a possible problem, not a proof of audible drift.
* The decode pass reads the whole file. For very long media use `Depth.PROBE` and decode only
  when something looks wrong.
