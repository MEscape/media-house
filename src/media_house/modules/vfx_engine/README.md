# vfx_engine

Shot-level footage effects for the edit: punch-in zooms, slow motion / freeze / reverse with
exact time maps, privacy concealment on tracked regions, restrained looks (vignette, grain, glow,
...), footage composites. Plans are stored as `vfx_plan` documents; treatments are rendered into
derived ProRes 422 HQ 10-bit clips (`vfx_render`). The original footage is never touched.

Not owned here (contracts sec. 1): denoise / sharpen / grade / stabilise (`video_improvement`),
graphics and text (`motion_graphics_engine`), transitions between clips (`transition_engine`),
still-image cut-outs (`visual_asset_generation`).

## Contract (`application/contracts.py`)

```python
class VfxPlanner(Protocol):
    def execute(self, command: PlanVfxCommand, ctx: JobContext) -> Result[VfxResult, PlanVfxError]

@dataclass(frozen=True, slots=True)
class PlanVfxCommand:
    shots: VfxShots
    style: ResolvedStyle | StyleRef | None = None    # read through VisualStyleCatalog
    config: VfxConfig = VfxConfig()
    render: bool = False                             # also render the treatments
    video_analyses: Mapping[str, VideoAnalysis] = {} # supplied analyses (else the catalog)
    use_reframing: bool = True

VfxResult(plan: VfxPlan, asset: MediaAssetDto, created: bool)   # created=False: cache hit

class VfxCatalog(Protocol):
    latest(asset_id, *, edit_plan_id=None, profile=None) -> Result[(VfxPlan, Dto) | None, MediaError]
    history(asset_id) -> list[MediaAssetDto]
    load(plan_asset_id) -> Result[VfxPlan, MediaError | InvalidVfxDocument]

class SubjectMatter(Protocol): identity(); unavailable() -> str | None   # video mattes
class Inpainter(Protocol):     identity(); unavailable() -> str | None   # object removal
class EffectRenderer(Protocol): identity(); unavailable(); supports(); probe(); render()
```

Errors: `NothingToTreat` (`vfx_engine.nothing_to_treat`, the pipeline's "nothing to do" code),
`InvalidVfxShots`, `InvalidVfxConfig`, `InvalidVfxPlan` (never stored), library errors,
`ToolNotFoundError` when `render=True` and FFmpeg is missing.

## Input: `VfxShots` (own DTO; the edit plan is never imported)

| `VfxShots` field | From the edit plan (mapper in `media_pipeline/application/`) |
| --- | --- |
| `edit: EditRef(edit_plan_id, plan_version)` | `edit_plan_id`, `plan_version` (None on the no-speech path) |
| `project_id` | `project_id` |
| `frame_rate`, `frame_rate_assumed` | `timebase.frame_rate`, `timebase.assumed` |
| `output_width/height` | the target format's output size |
| `clips: VfxClip(clip_id, asset_id, source_start/end, timeline_start, layer, section_id, beat_id, audio, speech)` | VIDEO-track placements: `placement_id`, `source.asset_id/start/end`, `timeline_start`, `layer`, `section_id`, `beat_id`, `audio` (SOURCE_SYNC / VOICEOVER / MUTED); `speech` = source spans of kept speech in that placement |
| `requests: VfxRequest(request_id, kind, timeline_at, duration, priority, reason, section_id, effect, track_ids, decision_id)` | `effect_requests`: REFRAME/EMPHASIS -> `EMPHASIS`, MUSIC_CUE/REVEAL -> `REVEAL`; GRAPHIC, TRANSITION, SOUND_CUE are other modules' and are not mapped. Personal requests use `EXPLICIT` (+ `effect`) or `PRIVACY` (+ `track_ids`) |
| `format` | story format or the style's format profile (selects the default effect profile) |

No-speech path: `VfxShots.no_speech(((asset_id, duration),), frame_rate=..., width=..., height=...)`
(one clip per recording, timeline = source, no sections, beats or speech).

Facts read by the module itself (catalogs only; nothing is re-detected): `VideoCatalog` (shots,
quality metrics incl. luma/clipping/noise, camera speed, tracks, FLASH events),
`ReframingCatalog.latest(asset, target=<output aspect>)` (executable crop windows),
`InspectionCatalog` (size, frame rate, pixel format, colour tags, timecode; a minimal ffprobe
runs only when no inspection is stored), `AudioCatalog` (stored `impulse` events = bangs),
`AlignmentCatalog` (the stored audio/video clock correction), `VisualStyleCatalog` (only `motion.intensity_ceiling`,
`motion.character` and the palette for an optional glow tint; never branding on footage).

## Effects

| Effect | Status | How / why |
| --- | --- | --- |
| punch-in (hard or eased push) | rendered | per-frame crop (sendcmd) + Lanczos scale in float; anchor = reframing window centre, else the main subject, else the centre; scale limited so that source px per output px >= `min_effective_ratio` (bypassed when the source is already enlarged) |
| slow motion, speed ramp, freeze frame, reverse | rendered | exact time map (below); `framerate` blending (12-bit) by default, exact duplicates, or `minterpolate` only with `allow_8bit_interpolation` (then reported as a low-precision step) |
| privacy blur / mosaic on tracked regions | rendered | masks from `video_intelligence` tracks: margin + motion drift, time padding, gaps <= `max_gap` bridged, longer gaps / low confidence -> the whole picture over the gap (or `drop`: an ERROR naming the source span not to use). Render QC measures the detail left inside every tracked box; a leak refuses the render and retries with the full-frame safe option |
| blur (gaussian / box / directional) | rendered | on explicit request |
| glow / bloom | rendered | threshold (clamped highlights) + gaussian + screen; bypassed when the picture already clips or is a night picture with clipped lights (fireworks) |
| light rays | rendered | 8-tap radial zoom blur of the highlights, screen |
| vignette | rendered | float multiply mask; bypassed on dark pictures |
| film grain | rendered | luma noise in 16-bit, seeded per treatment; bypassed on noisy or night pictures |
| film damage | rendered | seeded exposure flicker + thin scratches |
| chromatic aberration, lens distortion | rendered | `rgbashift` / `lenscorrection` (16-bit) |
| motion blur | rendered | frame blending (`tmix`), only above `min_camera_speed` |
| stylise (monochrome, posterize) | rendered | Rec.709 luma mix / 16-bit LUT |
| composite (PiP of footage, blend modes) | rendered | a higher-layer clip scaled into a corner, float `maskedmerge` (+ `blend` for screen/multiply/...) |
| background blur / replacement, subject isolation | not_available | needs a video matting model (`SubjectMatter` port, none installed). Never faked with a rectangle |
| object removal / inpainting | not_available | needs a video inpainting model (`Inpainter` port). Concealment is offered instead and recorded as concealment, not removal |
| screen replacement | not_available | needs a planar track; `video_intelligence` tracks boxes only |
| lens flare | not_available | needs a flare element library; a drawn flare over real footage would be fake |

## Planning (restraint first)

Gates, in order, each recorded as a reason: person's rejection -> profile `apply`
(`never | request | auto`) -> capability -> intensity ceiling (min of the profile's and the
style's `motion.intensity_ceiling`; an EXPLICIT request may exceed it with a warning) ->
emphasis budget and spacing (punch-ins and time effects, strongest first, `emphasis_per_minute`,
`min_gap`) -> feasibility and "do not destroy good media" guards. A clip nothing passes for gets
an explicit `no_effect` (the original is used). Statuses: `planned`, `rendered`, `reused`,
`bypassed`, `rejected`, `not_available`, `error`. Person decisions: `VfxConfig.decisions`
(`EffectDecision(effect_key="<clip_id>:<effect_type>", ACCEPTED | REJECTED)`).

Time maps (contracts sec. 3): a retimed clip KEEPS its timeline span (no ripple, so captions,
music and transitions on the same edit stay valid). It shows less source; `source_used_end`
records where it stops. Each `TimeSegment` has exact `timeline_*`, `source_*`, `rate` (source
seconds per timeline second; 0 = freeze), frames on the edit grid (`round(t * fps)`,
half-to-even) and an `AudioIntent` for the audio stage: `source_sync` (speed 1),
`keep_original` (the placement's real-time audio keeps playing), `mute`,
`pitch_preserving_retime`. Retiming over synced speech is refused (`retime.protect_speech`).

## Burst peaks (fireworks)

A burst is a picture `FLASH` event of the stored video analysis. Where it found none (its
detector misses local bursts on night footage) the strongest stored audio `impulse` of the clip
(`retime.min_bang_strength`) marks the burst: its audio time is moved onto the picture clock with
the stored alignment (`video = audio + audio correction - video correction`; without a stored
alignment both clocks are taken as equal and the reason says so). Its visual peak is the
brightest sampled frame of the stored `luma_median` curve in `[bang - retime.peak_before,
bang + retime.peak_after]` (1.0 s / 0.5 s: light arrives before the sound, ~0.9 s for a burst
300 m away), when the median brightness rises there by `retime.min_luma_rise`; otherwise the
bang time itself. A `REVEAL` request snaps to the nearest flash, else the nearest bang's peak.
One time effect per clip, and the profile's emphasis budget bounds them across clips.

## Profiles and configuration (`domain/profile.py`)

`clean_enhancement` (default: requested punch-ins only), `talking_head`, `product_detail`,
`demonstration`, `fireworks_showcase` (slow motion 0.5x around the strongest burst, 0.6 s
freeze at the peak, gentle punch-ins, no bloom), `cinematic`, `vintage`, `privacy_protection`,
`creative_effects` (expressive effects only on request), `shorts_energetic`. Format mapping in
`FORMAT_PROFILE` (e.g. `short`/`shorts_haul` -> shorts_energetic, `shorts_product_test` ->
fireworks_showcase, `nye_uncut` and `brochure_review` -> clean_enhancement).

`VfxConfig(profile, custom, overrides={"punch_in.scale": 1.15}, section_overrides=(...),
decisions=(...))`. Every knob carries `field` metadata (`label, help, unit, min, max, step,
choices, kind, advanced`); `problems()` enforces exactly those (unit-tested by walking every
field). `knob_fields(VfxProfile)` lists them for a settings UI.

## Rendering and precision

FFmpeg through the `ProcessRunner` port only. Decode -> `gbrpf32le` (32-bit float RGB, explicit
input matrix/range) -> every effect -> one conversion to `yuv422p10le` -> `prores_ks` profile 3
(HQ), the edit's frame rate, the source size, `-frames:v` = planned frames (segments are padded
and trimmed to their exact counts). Filters that cannot run in float stay at 12-16 bit
(`framerate` 12-bit, `lenscorrection`/`rgbashift`/`pixelize` 16-bit, grain in `yuv444p16le`:
FFmpeg's float `noise` is uncalibrated). 8-bit-only filters (`vignette`, `drawbox`,
`perspective`, `zoompan`) never touch the picture; `drawbox` draws only binary masks, feathered
in float. Each render parses FFmpeg's own format negotiation: any automatic conversion of >= 10-bit
data below 10 bits is reported in `RenderQc.low_precision_steps`. Colour tags and (integer rates)
the source timecode advanced to the clip start are written; audio is not muxed (the plan states
the audio intent; the source keeps its streams).

Cache: every render is `register_derived(source, ..., operation="vfx_render", config={treatment
digest, frames, rate, renderer identity, QC limits, tint, overlays}, version=RENDER_VERSION)`;
an identical treatment is reused (`reused`), its QC read back from the asset metadata. The plan
document is cached by the library fingerprint of the cache key (shots, source facts, style
fingerprint, capabilities, configuration digest, render flag, renderer identity, versions); a new
key makes a new `plan_version` that names its predecessor (`supersedes`).

## Render QC (contracts sec. 6; measured on the file)

ffprobe: size, `pix_fmt` / bits (must be `yuv422p10le`, 10-bit), exact frame count (ERROR on
mismatch). Pixels (16-bit gray decode of output and source span): new frame-difference spikes
(flicker) vs the source, excluding planned segment boundaries (WARNING); clipping increase
(WARNING); privacy: geometric coverage of every trusted track sample (ERROR < 1.0) and the share
of detail removed inside every tracked box (ERROR below `qc.min_concealment`); source file
sha256 before == after (CRITICAL otherwise). An ERROR leaves the treatment `error`, the
original footage (or the recorded privacy fallback) applies and the plan becomes `partial`.

## Limits

* No matting / inpainting model ships: those effects are `not_available` (ports exist).
* Picture times are the analysis' stream clock; audio bangs use the stored A/V alignment
  correction when one exists (else both clocks are taken as equal, stated in the plan).
* Time effects keep the clip's timeline span; a ripple edit is the timeline builder's decision.
* Video only: no audio is processed or muxed.
* `video_intelligence`'s FLASH detector finds no events on burst-rich night footage of the
  owner's NYE video: automatic bursts come from stored audio bangs there (above). The stored
  `luma_median` is a frame MEDIAN: on a night sky (most pixels black) it barely moves with a
  local burst, so the peak often stays at the bang time.

Validated on: synthetic 640x360 H.264 test footage (punch-in, slow motion + freeze, vignette +
grain, tracked privacy blur incl. a lost-track fallback, glow; verified with ffprobe and decoded
pixels) and one owner-approved 360p NYE night excerpt (y_mj09m0XB4, 30000/1001 fps:
fireworks_showcase slow motion + freeze at an editor-marked burst; cinematic look correctly
bypassed on the night sky); and a 60 s excerpt of the same video (270-330 s) with real stored
analyses: 0 picture flashes, 147 audio impulses, alignment correction 0 (inspection), 3 clips
of 20 s -> 3 burst peaks from bangs (17.30 / 30.90 / 46.46 s); `luma_median` stayed 0.000 on
the night sky, so each peak is the bang time; the freeze frames show a visible burst in 2 of 3
clips (the third holds the fading sparks of a burst). Previews: `docs/layer4/previews/vfx/`. Not validated on 4K, HDR,
VFR or 10-bit camera sources, or with a real face tracker's output.
