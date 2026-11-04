# music_and_sound_design

Layer 4. Plans the music, sound effects, silence and mix intent of an edit from the owner's
sound library, and renders an optional music + effects stem. It owns the sound library
inventory (import, licence facts, measurements) and the channel's sound identity.

It does NOT own (and never re-implements): loudness, EQ, compression, limiting, mastering and
the final mix (`audio_improvement`), transcription and speech analysis (`audio_intelligence`),
the visual identity (`visual_style_engine`; not consumed), the edit or cut placement
(`editing_intelligence`), graphics/caption/transition events (their owners; they reach this
module as `SyncEvent`s).

## Policies (enforced in code and tests)

1. **The owner's library is the only source.** Nothing is downloaded, generated or
   substituted (no network or model library may be imported; see the boundary test). A need the
   library cannot meet becomes a `MissingAsset` (`required` / `recommended` / `optional`, what
   kind, where, duration, style, why, `action: manual_asset_acquisition`), and the event gets a
   recorded `no_sound_effect` / the cue `music_disabled`.
2. **Licence filter.** Selection uses only assets with `commercial_use: true`. `null` (needs the
   owner's confirmation, today: YungBrooke "Downfall") and `false` are excluded unless
   `SoundDesignConfig(licence=LicencePolicy(allow_unconfirmed=True))`; an asset allowed that way
   is ranked lower and raises a `licence_unconfirmed` QC warning.
3. **Credits.** Every plan carries the manifest's exact `attribution_text` of each used
   attribution-required asset (`plan.credits`, `plan.credit_lines` for the video description),
   in order of first use. An attribution-required asset without text is never selected; a
   missing credit is a CRITICAL check (the plan is not stored).
4. **No library file is test data.** Tests synthesise audio. `ai_training_allowed: false` is
   stored in the asset metadata and counted in the import report; the only test that reads the
   real library is opt-in and skips those files.

## Contract (`application/contracts.py`)

```python
SoundLibraryImporter.execute(ImportSoundLibraryCommand(manifest_path, kinds=..., only=(), analyze=True), ctx)
    -> Result[ImportSoundLibraryReport, MediaError | SoundDesignError]
SoundLibraryCatalog.sounds(kind=None) / get(asset_id) / index() -> SoundIndex / search(SoundQuery) -> list[SoundMatch]
MusicAnalysisCatalog.latest(asset_id) / load(doc_id)            # music_analysis documents
SfxProfileCatalog.latest(asset_id) / load(doc_id)               # sfx_profile documents
SoundDesignPlanner.execute(PlanSoundDesignCommand(timeline, config=SoundDesignConfig(), decisions=(),
        carry_decisions=True, recently_used=(), use_audio_intelligence=True, use_story=True,
        use_pacing=True, audio_timelines={}, story_plan=None, pacing=None), ctx)
    -> Result[SoundDesignResult(plan, asset, created), MediaError | SoundDesignError]
SoundDesignCatalog.latest(anchor_id, *, edit_plan_id=None, profile=None) / history / load
SoundStemRenderer.execute(RenderSoundStemCommand(plan_asset_id, StemOptions(include_ducking=True)), ctx)
    -> Result[StemResult(asset, created, checks), ...]
```

Errors: `NothingToScore` (code `music_and_sound_design.nothing_to_score`: a timeline without
clips; the pipeline may treat it as "unavailable"), `InvalidSoundConfig`, `InvalidManifest`,
`UnreadableSound`, `InvalidSoundDesignPlan` (a bug; never stored), `StemRenderFailed`.

### Input DTO `SoundtrackTimeline` (own type; no EditPlan import)

`edit: EditRef(edit_plan_id, plan_version) | None`, `project_id`, `frame_rate: Rational`,
`frame_rate_assumed`, `clips: TimelineClip(clip_id, asset_id, source_start, source_end,
timeline_start, audio: source_sync|voiceover|muted, section_id, layer)`, `sections:
TimelineSection(section_id, timeline_start, timeline_end, label, role | None, beat_functions)`,
`speech: SpeechSpan`, `bridges: AudioBridge(bridge_id, kind j_cut|l_cut|bridge,
timeline_audio_at, timeline_picture_at)`, `events: SyncEvent(event_id, source
graphics|transition|caption|edit, kind, timeline_at, strength)`, `pacing: PacingCue`,
`original_events: OriginalSoundEvent`, `story_format`. Anchor = first clip's asset.

Mapping the orchestrator writes in `media_pipeline/application/` (`EditPlan` -> DTO):

| DTO | EditPlan |
| --- | --- |
| `edit` | `EditRef(plan.edit_plan_id, plan.plan_version)` |
| `project_id`, `frame_rate`, `frame_rate_assumed` | `project_id`, `timebase.frame_rate`, `timebase.assumed` |
| `clips` | `placements` with `track == AUDIO` (or VIDEO layer 0 when no audio track is planned): `placement_id`, `source.asset_id/start/end`, `timeline_start`, `audio` (`AudioBehavior` value), `section_id`, `layer` |
| `sections` | per `section_id`: min `timeline_start` / max `timeline_end` of its placements; `beat_functions` from the story plan (filled here when empty) |
| `bridges` | `audio_bridges`: `bridge_id`, `kind`, `audio_changes_at`, `picture_changes_at` |
| `events` (source `edit`) | `effect_requests`: `TRANSITION` -> kind `section_change`, `MUSIC_CUE`/`REVEAL` -> kind `reveal`; `at`, `priority` as strength |
| `events` (graphics/transition/caption) | the future motion/transition/caption plans' animation events |
| `story_format` | `target_format` |

`speech`, `original_events` (audio_intelligence `energy_spike` and `impulse` events) and `pacing` (`hold_reveal`,
`add_section_breath`) may be left empty: the planner then reads the stored audio timeline,
story plan and pacing analysis through `AudioCatalog` / `StoryCatalog` / `PacingCatalog` and
maps them through the clips (`application/views.py`).

**No-speech path** (no EditPlan: memes, versus polls, compilations, NYE uncut): `edit=None`,
one clip per recording with `timeline = source`, no sections/speech, `project_id=None`. The
whole timeline is one implicit section with the profile's `music.no_section_role`; music comes
from the format profile, fireworks audio stays in front, nothing is ducked. Never fails.

## Measurements (`music_analysis`, `sfx_profile`)

Infrastructure only (numpy/scipy; FFmpeg via `ProcessRunner` decodes to float PCM and runs
`ebur128`). No new dependency. Per music asset: duration, integrated loudness / LRA / true peak,
onset envelope, onset rate, tempo (BPM, confidence, stability, half/double alternatives),
beat grid (Ellis 2007 dynamic-programming tracker), downbeats (4/4 assumed) with confidence,
energy curve (2 Hz), sections (Foote novelty on beat-synchronous chroma + level, snapped to
downbeats) labelled `intro/build/drop/climax/outro` only with level evidence (else
`unlabeled`), loop region (two downbeats >= 8 bars apart whose next 4 bars are alike), edit
points (track start, section and 4-bar phrase starts, loop points, fade-out start, ending).
`vocal_presence` = `not_available`. Per effect: duration, onset/peak/useful span (samples),
attack, tail, sample peak, loudest 50 ms RMS, EBU loudness when above the gate, spectral
centroid, brightness. Stated values (mood, genre, BPM, description) stay in the asset metadata
and are never mixed with measurements. Cached per asset by analyser + engine versions.

**Accuracy, measured (not claimed):**

* Synthetic suite (click and kick/snare tracks, 60-174 BPM, with/without 10 ms jitter, 36
  cases): tempo within 2 %: 29/36; octave-equivalent (x2 or /2) within 2 %: 36/36; mean beat
  F-measure (+-70 ms) 0.93 (0.99-1.0 when the octave is right); mean downbeat F 0.93; beats
  within +-2 ms of the onset after a one-hop latency correction.
* Real library, 35 incompetech tracks with a stated BPM: 24/35 within 2 % of the stated BPM,
  32/35 within 2 % allowing half/double; misses: "Ouroboros" (130 -> 107), "Local Forecast -
  Elevator" (82 -> 106), "Five Armies" (238 -> 114). Octave answers: Wallpaper, Cipher,
  Hyperfun, Hitman, Drums of the Deep, Late Night Radio, Boogie Party, Scheming Weasel.
  Downbeat confidence on real tracks is low (0.04-0.45): downbeats are a best guess.
* Whole real library (250 files) imports and measures in about 7 minutes on this CPU.

## Planning (pure domain: `domain/planner.py`, `domain/sfx.py`)

Roles from beat functions -> presence per role (feature / bed / off / silence) -> clip sound
classified (`dominant` fireworks content, `speech`, `under_music`, `muted_by_edit`,
`voiceover`) -> music slots minus preserved spans -> short profile gaps bridged -> cues (one
track; a new one when the role's energy or categories change) -> track ranking (stated
category, measured energy = 0.4 onset rate + 0.3 tempo + 0.3 loudness, tempo range, length or
loop, reuse in this video and in `recently_used`, licence) with the top 3 alternatives ->
arrangement (downbeat in-point: the track start for openings, else the track section whose
level fits the energy target; back-timed to the track's own ending before music-free spans or
the end; measured loop region when short) -> levels (speech reference - bed + duck depth, +
feature lift; file loudness measured) -> ducking intents (speech merged over short pauses;
loud original moments) -> tail crossfades (music change follows a J/L audio change; picture is
never moved) -> drop-out before a `hold_reveal` -> effects with restraint (rule per event kind,
minimum strength, never over preserved audio, under speech only soft ones, spacing, per-minute
budget, no repeat within N, peak/start/end alignment on the measured file) -> credits -> QC.
Every declined event is in `plan.no_sfx` with a reason; every music-free span in
`plan.music_free`. Person decisions (`ItemDecision(item_id, accepted|rejected, asset_id)`)
override and are carried to the next plan of the same edit.

## Configuration (`domain/profile.py`)

`SoundDesignConfig(profile=None, custom=None, overrides={dotted.path: value},
section_overrides=(SectionOverride(section_id, {...}),), licence=LicencePolicy())`.
Precedence: defaults -> channel sound identity (`CHANNEL`, identity `mesc_channel` v1) ->
format profile -> project overrides -> section overrides -> person decisions. Format profiles
(from the channel discovery): `generic`, `ranking`, `brochure_review`, `shopping_tour`,
`unboxing`, `compilation`, `nye_uncut`, `shorts_meme`, `shorts_product_test`, `shorts_haul`,
`shorts_versus` (every visual_style_engine format profile has one; unit-tested);
story formats map via `STORY_FORMAT_PROFILE`. Transition SFX rules use transition_engine's
`TransitionKind` values verbatim (`zoom`, `whip_pan`, `slide`, `push`, `wipe`, `flash`,
`glitch`, `zoom_tunnel`, `pixel_dissolve`); cuts, dissolves, fades, dips, motion match, blur,
shape and mask reveal are deliberately silent (`SILENT_TRANSITIONS`); a unit test walks every
`TransitionKind` so the names cannot drift. Groups: `music` (presence/energy/categories per
role, tempo range, cue length, tracks, fades, crossfade, natural ending, beat alignment),
`mix` (speech reference, bed below speech, duck depth/attack/release, merge gap, effect level,
boost cap, headroom), `sfx` (enabled, per minute, spacing, under speech, repeat window, rules),
`silence` (reveal drop-out, shortest music pause, dead-air limit), `original_audio` (preserve
fireworks, content roles, clip sound is content, loud-moment strength, music over preserved
audio off/duck). Every knob has field metadata; `problems()` enforces the same ranges.

## QC (`domain/validation.py`)

`speech_masking_risk`, `sfx_over_speech`, `music_dropout`, `music_too_short`, `clipping_risk`
(measured true peak + gain), `sfx_overlap`, `licence_unconfirmed`, `dead_air` (room tone
belongs to audio_improvement), `missing_asset`, `frame_rate_assumed`, CRITICAL
`credit_missing` / structural checks. Stem render QC: codec `pcm_f32le`, rate, duration vs the
plan (+-50 ms) from the produced file, and a peak warning above 0 dBFS.

## Stem and the audio_improvement hand-off

`sound_stem_render`: WAV `pcm_f32le`, 48 kHz, stereo, the plan's gains/fades/crossfades and
(by default) the planned ducking as gain rides; not normalised, limited or mastered. The
current `AudioMixing.execute(MixAudioCommand(voice_asset_id=<dialogue>,
music_asset_id=<stem asset id>))` takes it as its one music asset (it loops music with
`-stream_loop -1`, which is harmless as the stem already spans the timeline). With ducking in
the stem, pass `overrides={"mix.duck_ratio": 1.0}` so it is not ducked twice; or render with
`StemOptions(include_ducking=False)` and let `AudioMixing` sidechain-duck. audio_improvement
is not edited by this module.

## Limitations

Mood/genre are source-stated only; vocal presence, meter and sound events are not measured
(no model); tempo octave ambiguity remains; fireworks protection relies on section roles,
format profiles and audio_intelligence `energy_spike` / `impulse` events (no recogniser of
fireworks as such); no ambience bed from the
library (the vocabulary has no room tone; dead air is reported for audio_improvement); the
measured energy is a heuristic; cross-video reuse needs the caller's `recently_used`.

Validated on: synthetic click/drum tracks and synthesised WAV libraries (tests), and the
owner's starter library (250 files, 2026-11-02) for import, measurement and one demo plan +
stem. Not validated on real channel edits or by listening.
