# Video Improvement

Makes video **natural, consistent and technically correct**, using only local and free tools. It
is an independent subsystem next to Audio Improvement: it improves the picture and preserves
everything else (audio streams, metadata, chapters, timecode, timestamps, frame count).

```text
Media Library video asset ──(never modified)──────────────────────────────────────────────┐
        │                                                                                │
        ▼ facts: Media Inspection if it ran, else one minimal ffprobe                    │
   Source profile   which camera / capture is this (explicit > metadata > convention)    │
        ▼                                                                                │
   Measure          24 sampled frames: exposure, colour cast, saturation, noise, detail  │
        ▼                                                                                │
   Plan             measured fact -> decision (within the profile's bounds) -> correction│
        ▼ predict    the colour plan is evaluated on the samples before anything renders│
   Render           one FFmpeg pass: denoise -> [16-bit RGB -> baked 3D LUT -> YUV] ->   │
        │           sharpen -> encode; audio copied; GPU decode/encode when proven       │
        ▼                                                                                │
   Verify           the real output is probed and measured; a stage that made it worse  │
        │           is left out and the video is rendered again without it              │
        ▼                                                                                ▼
   Improved video: a derived asset  ◄───────── provenance + before/after measurements ───┘
```

If the footage needs nothing, nothing is rendered: the result is the **source asset itself**
(`changed=False`). The best output is not the most processed output.

## Why these choices

* **One LUT, not per-frame Python.** Every pixel correction (input transform, exposure, white
  balance, black point, contrast, highlight roll-off, saturation, an optional look) is a function
  of one RGB triple, so the whole colour stage is a single function (`ColorTransform`) sampled
  into a `.cube` 3D LUT in float64 and applied by FFmpeg's `lut3d` with tetrahedral
  interpolation, in 16-bit planar RGB. No frame is ever held in Python, memory does not depend on
  the video length, and the same function predicts the result on the sampled frames.
* **Colour-managed.** The camera's transfer curve is decoded to **linear Rec.709** (the working
  space, one constant in `domain/color.py`), corrected there, rendered, and encoded as Rec.709.
  Linear Rec.709 because delivery is Rec.709: exposure and white balance are physically
  meaningful on linear light, and a wider space such as ACEScg would only add a gamut conversion
  until wide-gamut delivery exists. Gamut matrices are derived from published chromaticities
  (`infrastructure/color_science.py`), never typed in.
* **No new dependency.** NumPy and SciPy (already present) for maths, FFmpeg for everything
  per-frame. colour-science and OpenColorIO were not added: the handful of transfer functions
  needed is a few lines each, tested against published values, and adding one is an entry in a
  table.
* **Unknown stays unknown.** A file whose colour space cannot be determined is never given a
  guessed transform: the colour stage is skipped (reason recorded), while denoising and
  sharpening, which do not depend on it, still run. HDR (PQ/HLG) is refused with a clear error
  rather than degraded.

## Source profiles and processing profiles

Two independent choices, both plain data:

| | Answers | Examples |
| --- | --- | --- |
| **Source profile** (`domain/source.py`) | what the capture IS: colour encoding, how scene-referred footage is rendered | `generic`, `rec709`, `studio_camera`, `gopro_hero_9`, `gopro_flat`, `sony_slog3`, `panasonic_vlog` |
| **Processing profile** (`domain/profiles.py`) | how it should LOOK and how much may be done | `natural` (default, least), `studio`, `outdoor`, `social` |

A camera is one entry in `_BUILTIN`; it never adds processing code. The source profile decides
which processing profile is the default (a GoPro defaults to `outdoor`, a studio camera to
`studio`).

The source profile is decided deterministically, strongest evidence first: **1.** the caller
names it; **2.** camera metadata identifies the camera (from a Media Inspection result, or read
from the file when none exists; the origin is recorded as `inspection` or `embedded_metadata`);
**3.** the file declares Rec.709 tags; **4.** untagged HD video is read as Rec.709 (the
convention every player follows; recorded as `detected`); **5.** otherwise `generic`, whose
colour space is unknown. GoPro Protune Flat cannot be seen in a file, so it is never guessed:
name `gopro_flat`.

Settings are one mapping everywhere. `ImproveVideoCommand.overrides` takes dotted paths:

```python
result = improver.execute(
    ImproveVideoCommand(
        clip_id,
        source_profile="gopro_hero_9",  # optional; metadata decides when omitted
        processing_profile="outdoor",  # optional; the source's default when omitted
        overrides={"color.saturation_max": 0.38, "denoise.enabled": False},
    ),
    ctx,
)
result.value.asset  # the improved video (or the source when nothing was needed)
result.value.provenance  # what was measured, decided and applied
read_provenance(asset.metadata)  # the same record for any asset, without this module
```

A future UI edits exactly this mapping; every value lives in `domain/settings.py` and is
validated where it is set (`InvalidProfile` says what is wrong). Source-specific values use the
`source.` prefix, e.g. `{"source.input_color.transfer": "slog3"}` for footage whose curve the
file does not declare.

## Exposure: gain, lift and roll-off

A dark picture with bright spots (an indoor scene with a window, LED lights) cannot be fixed with
gain alone: the bright spots would clip. Exposure is raised as far as the highlights allow, where
a highlight roll-off lets the shoulder absorb highlights up to `color.rolloff_absorb` times above
the headroom; what is still too dark after that is lifted with a gamma curve on linear light
(`midtone_lift`: white stays white, shadows and midtones rise, shadow colours soften) down to
`color.min_gamma` at `color.lift_strength`. The grade may not raise the measured noise beyond
`color.guard_max_noise_gain`; if it does, the colour stage is left out.

Which processing profile fits is a scenario choice, not something a file reveals: a GoPro is
detected as a GoPro, but whether the clip is an outdoor shot or an indoor talking head is for the
caller to say (`processing_profile="studio"` or `"outdoor"`). The camera only sets the default.

## Consistent, not random

Adaptive processing decides **within bounds**: each correction has a deadband (below it nothing
happens), a strength and a maximum, all in the profile. Two clips of the same kind get the same
kind of correction in different amounts; no clip gets a new look. Exposure is raised only as far
as the highlights allow, darkened only when highlights are blown, white balance is trusted only
with enough neutral pixels and is capped, saturation is steered into a band and never past a
limit, sharpening never runs on noisy footage, denoising never on clean footage.

## Provenance and measurements

Stored in the improved asset's metadata under `video_processing` (schema-versioned): source
asset, source profile with its origin and evidence, processing profile, **input / working /
output colour spaces**, every operation with its status (`applied` / `skipped` / `bypassed`),
reason, parameters and the **measured numbers the decision rests on**, engine versions
(including the encoder that actually ran), whether Media Inspection supplied the facts
(`facts_source`), the SHA-256 of any look LUT, warnings (for example exposure that changes
through the clip, which a static grade cannot follow) and before/after measurements. Damaged or
newer-schema records read as no provenance.

## Verification

The output is probed and measured. **Hard checks** protect identity (size, frame rate, duration,
frame count, audio streams, rotation): a failure discards the output. **Soft checks** protect
quality (clipping, crushed blacks, noise, lost detail, colour tags, the real result against the
prediction) and name the stage responsible: that stage is left out (`bypassed`, with the
measured reason) and the video is rendered again without it.

## Media Inspection (optional)

`application/prior_inspection.py` takes the source facts (size, exact frame rate, frame count,
pixel format, bit depth, colour tags, interlacing, rotation, audio streams, timecode, camera
metadata) from a stored inspection through the `InspectionCatalog` contract, so the source is
not probed again. With no inspection, or one that cannot supply them, the minimal probe reads
them. The result is identical either way (tested byte for byte), and the processing fingerprint
does not depend on which path was taken.

## Caching and determinism

The fingerprint is source content + both profiles + every setting + the look LUT's content hash +
analyzer, baker and renderer identities + the encoder that will run + `PROCESSING_VERSION`. An
identical request is answered from the library, also after a restart. `execution.hardware` is
deliberately not part of it (it does not change what is done), but the encoder is: a GPU encoder
and the CPU encoder produce different files, so they are different results.

## Speed

Rendering 4K is CPU-heavy: on a 12-core machine a 173 s, 100 Mbps GoPro clip took about 17 minutes
with the `medium` x264 preset. The default preset is now `veryfast` at the same constant quality
(`crf 16`): about twice as fast, the same size, and visually identical (SSIM 0.988 against the
`medium` encode of the same footage; two different lossy encodes never match exactly). The colour
chain (RGB conversion and 3D LUT) and the encoder each use about six cores, so the CPU is
saturated; the remaining levers are a GPU encoder (below), `output.crf` (18 is smaller and faster
and still very high quality) or a slower preset for a smaller file. Measuring and verifying decode
key frames only, so they cost seconds, and progress is reported through the render (`Rendering
37%`), so a long render is no longer silent. Importing a large file into the library and
registering the result (hashing and copying) are Media Library costs and take minutes at this size.

## GPU / CPU

FFmpeg does the per-frame work. If a GPU encoder is present but unusable, the reason is logged
(for example an FFmpeg build that needs a newer NVENC API than the installed driver offers: update
the driver or use an FFmpeg build matching it) and the CPU is used. With `execution.hardware = "auto"` the CUDA decoder and an NVENC
encoder are used when a one-frame trial encode proves them usable and the pixel format fits the
hardware (NVENC: 4:2:0 only; H.264 at 8 bits, HEVC also at 10); a failing GPU render is repeated
on the CPU, and the CPU path needs nothing special. The 3D LUT, denoise and sharpen filters run
on the CPU: FFmpeg has no GPU variant of them in common builds, and moving frames through Python
to use CUDA (the project has PyTorch for WhisperX) would cost more than it saves. No GPU
framework was added.

## Calibration

`CalibrateProfile` measures representative reference clips and returns a plain `overrides`
mapping (target exposure, saturation band, tonal range, noise and softness thresholds) that
leaves footage like the references alone. The caller stores that mapping and passes it back as
`overrides`; runtime output never depends on a model, a service or a prompt.

## Adding or extending

* **A camera or capture:** a `SourceProfile` entry (colour encoding, rendering, optional
  `CameraMatch`).
* **A transfer curve or gamut:** a `Transfer` / `Primaries` name plus one function pair or one
  chromaticity triple in `color_science.py`; a test fails if a name has no implementation.
* **A new look:** a `.cube` file via `look.lut_path` (traceable by hash), or a processing
  profile entry.
* **A new stage** (stabilization, lens correction, rolling-shutter, super-resolution): add it to
  `ProcessingStage`/`STAGE_ORDER`, a settings group, a decision in `planning.py`, a check in
  `verification.py` and its filter in the renderer; bump `PROCESSING_VERSION`. Stages that
  change framing or timing must say so in verification, which already treats size, frame count
  and duration as hard invariants. They are optional stages, never part of the colour pipeline.
* **A better denoiser or encoder:** change the renderer's filter or encoder; its identity retires
  old cache entries.

## Honest limits

* The thresholds (noise, sharpness, saturation band, tonal range) were set on synthetic footage
  with known defects, not on real camera material. They are conservative (they favour doing
  nothing); tune them with `CalibrateProfile` on your own studio and GoPro footage before
  relying on them.
* The GoPro Protune curve is the published base-113 log curve with Rec.709 primaries assumed; the
  S-Log3 and V-Log curves and gamuts follow the manufacturers' documents. Neither was checked
  against real camera files. Log footage is interpreted with the file's declared range.
* Corrections are static for a whole clip: footage whose exposure changes through the clip is
  reported as a warning, not followed.
* White balance is a gray-world estimate on near-neutral pixels. It is capped and skipped when
  too few exist, but a scene lit by coloured light can still be misjudged within the cap.
* Stabilization, lens and rolling-shutter correction are not implemented (see above for where
  they go). HDR input is refused. Interlaced footage is not denoised.
* Chroma resampling in the 4:2:0 -> RGB -> 4:2:0 round trip is inherent to colour work on
  subsampled video and costs a little detail on sharp coloured edges; it only happens when the
  colour stage runs.
