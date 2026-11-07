# audio_mix_engine (Layer 5, module 27)

Makes the sound of a built timeline one coherent, loudness-checked soundtrack: dialogue, original
production audio, music, effects and ambience are balanced on separate buses, ducked and
processed, rendered as one stem per bus plus the final mix, measured against a delivery profile,
and judged. It knows no editing application and runs without DaVinci Resolve.

It decides nothing creative. Which sounds exist, where they sit, and the intended gains, fades,
ducking and crossfades come from the sound design and the timeline. This module supplies the
technical behaviour of a mix (levels per bus, EQ that makes room for the voice, bus dynamics,
loudness) and carries the timeline's instructions out.

## Who owns what (audited, not assumed)

| Module | Owns | What this module does with it |
| --- | --- | --- |
| `audio_intelligence` | analysis of sound | not repeated; `speech_spans` can be handed in (see limits) |
| `audio_improvement` | repair of ONE recording; `AudioMixing` ducks ONE music file under ONE voice | its improved derivative is what the timeline points at; its mixer is not used or extended |
| `music_and_sound_design` | what music, effects, ambience; ducking INTENT; a stop-gap unmastered stem | read through the timeline; never run; never selects or invents a sound |
| `edit_plan_compiler` | the mix INTENT: `AudioItem` (gain, fades, speed), `AudioAutomation` (`duck`, `crossfade`, `music_tail`) | consumed through the built timeline |
| `timeline_builder` | where every sound is, exact samples, J/L cuts | authoritative; never repositioned |
| **`audio_mix_engine`** | the multi-source mix, stems, loudness, mix validation | this module |
| `resolve_adapter` | Resolve execution | the engine's outputs are library assets it can import (not wired yet, see limits) |

**Decision on the compiler.** The directive asks the compiler to produce an `audio_mix_specification`
if it lacks one. It does not lack the intent: it already carries gain, fades, speed, ducking,
crossfades and tails per sound. A second compiler output would duplicate the timeline's audio and
split the source of truth. What it cannot know is the technical behaviour of a mix (bus EQ and
dynamics, loudness, stems), which is profile data, so the specification is built HERE from the
built timeline + a mix profile + a delivery profile, with the timeline's stable ids as references.
The compiler is unchanged.

## Contract (`application/contracts.py`)

```python
made = creator.execute(CreateSpecCommand(timeline=built))          # the specification, stored/found
done = mixer.execute(RenderMixCommand(timeline=built), ctx)        # stems + final mix + checks
done.value.mix_asset, done.value.stem_assets          # derived assets of the recording
done.value.result.issues, .corrections, .dialogue_levels, .processing
MixCatalog.latest_spec / latest_result                # stored documents, nothing is rendered
```

Blocking: run as a job (progress and cancellation through `JobContext`). Pipeline task `audio_mix`
(opt-in, `--stages audio_mix`, `--mix-profile`, `--mix-delivery`); needs `timeline_build`, nothing
else, in particular not Resolve.

## The specification (`domain/spec.py`)

A versioned, typed document (`audio_mix_specification`, schema 1) with the timeline's stable
references: `specification_id`, `timeline_id`, `timeline_version`, `sources` (asset, original,
relation, rate, channels, checksum, availability), `tracks`, `buses` (stable ids, each feeds the
master), `clips` (exact source range in seconds, position in samples, gain, fades, speed, pan,
`provenance`, `origin_ref` = the timeline element), `automation` (point-based, linear or smooth),
`ducking`, `crossfades`, `loudness`, `render`, `stems`, `validation`, `dependencies` (missing
sources), `warnings`, `provenance` (profile, delivery, engine version, recorded overrides),
`content_hash`. No editing application is named. `specification_id` derives from the hash, which
ignores the clock, so the same timeline + profiles + overrides give the same specification.

`validate_spec` checks structure before anything renders: unique ids, routing (every track feeds a
bus, every bus feeds the master, one master), clips inside the timeline, sources listed, ducks and
automation pointing at real things. `check_capabilities` compares what the mix needs with what the
backend says it can do: an unsupported operation is an ERROR that names it, never a silent
substitute.

## The mix, bus by bus

* **Sources**: the file the timeline names (original or the improved derivative the compiler chose),
  looked up in the library NOW; a missing or changed file stops the render. Originals are never
  touched and nothing is copied.
* **Buses**: dialogue, production, music, sfx, ambience (those in use) -> master. A stem is the bus
  after its own gain, EQ, automation and dynamics, before the master, so stems carry their processing
  and recombine through the same master to the mix.
* **Dialogue levelling**: each dialogue clip is measured (EBU R128, the part it plays) and moved
  only part of the way (`dialogue_levelling`, limited to `max_dialogue_trim_db`) toward the profile's
  dialogue loudness. Natural differences survive; production audio is left as recorded. Every trim is
  in the result.
* **Ducking**: the sound design's `duck` instructions are used as given (a depth is a reduction, its
  size is used). Only when there are none and the profile allows it, a duck is derived from where
  the voice is (provenance `automatic`), with a hold so the music does not pump between words. The
  envelope depends on ranges, not on the dialogue's samples, so the music stem does not change when
  the dialogue does.
* **Crossfades and tails** become equal-power fades across the cut / a fade on the music clip.
* **Master**: EQ, optional compressor, limiter below the delivery's true-peak ceiling.
* **Loudness**: the mix is measured; outside the delivery's tolerance it is re-rendered with a
  bounded master trim (+9 / -12 dB) up to `max_corrections` times, then the final measurement is
  what is judged. A mix is never squashed to reach a number.

## Profiles

`MixProfile` (generic, talking_head, tutorial, product_demo, cinematic): bus levels, EQ, voice
compressor, ducking defaults, dialogue target, master limiter, how far dialogue must sit over music.
`DeliveryProfile` (youtube, shorts, social, broadcast_r128): integrated loudness and tolerance,
loudness range, true peak, sample rate, bit depth, channels. Chosen explicitly, else from the creator
identity's format profile (already in the timeline, nothing new is stored about a creator), else the
default. Data in `domain/profile.py`.

## Incremental rendering and revisions

Each stem has a hash of everything that can change it (its clips and settings, the sources'
checksums, the ducks that act on it, trims, rate, the backend). The final mix has a hash of the stem
hashes, the master, the delivery and the backend. A stem or mix whose hash is unchanged is found in
the library and reused:

* change a dialogue gain -> the dialogue stem and the mix render again, the music does not;
* change the loudness target -> only the mix renders again, every stem is reused;
* replace one source -> only the buses that play it.

A revision is `MixConfig(overrides=MixOverrides(bus_gain_db=..., clip_gain_db=...))`: recorded in the
specification as `human` provenance, a new specification, earlier outputs untouched (a changed hash
is a new asset, never an overwrite). A mix made in Resolve cannot be read back through its scripting
API (clip gain and fades are not scriptable), so a person's change there is recorded here as an
override, not detected.

## Validation (`domain/output.py`)

Every finding has a severity (`info` / `warning` / `error` / `critical`) AND how sure it is
(`confirmed` / `likely` / `recommendation` / `intentional`): clipping, true peak, integrated
loudness, loudness range, format (rate, channels, codec), duration against the timeline, stems
aligned in length / rate / channels, music not far enough under the dialogue (measured over the
speech ranges), silence (where sound was planned: a likely problem; where nothing was planned:
information, intentional), and the recombination check (stems summed through the same master
reproduce the mix, null-tested). Successful rendering is not the verdict.

## Backend (`application/ports.py`, `infrastructure/ffmpeg_backend.py`)

One port: `capabilities`, `render_stem`, `render_mix`, `measure`, `measure_range`, `loudness_over`,
`silences`, `null_test`. The default drives FFmpeg through `ProcessRunner` (filter graphs written to a
file; sample-exact trimming when the file's rate is known; `normalize=0` everywhere). The backend
identity (FFmpeg version + revision) is part of every hash and recorded in the result.

## Stored documents (Media Library, no storage of its own)

`audio_mix_specification`, `audio_mix_result` (documents of the recording); `audio_mix_stem`,
`audio_mix_render` (derived WAV assets). The result records the backend, profile, delivery, every
stem and its measurements, corrections, dialogue levels, findings and the processing log.

## Not done / limits

* **Listening quality is not claimed.** All checks are objective measurements; no one has listened
  to a mix of real footage. Validated on generated tones and noise, a synthetic sound library and
  one synthetic clip.
* Not implemented from the directive: dynamic EQ, multiband compression, gate, expander, de-esser,
  stereo width, multichannel beyond mono/stereo, ambience-bed continuity management (ambience plays
  as the timeline places it), automatic dialogue volume automation (the specification and renderer
  support point-based automation; nothing generates it yet), and Resolve-native audio execution.
* Speech-aware ducking from `audio_intelligence` is a hook (`MixConfig.speech_spans`), not wired:
  without it the voice is taken to be where the dialogue clips are.
* The limiter is a sample-peak limiter with a 0.5 dB margin; the true peak is verified by
  measurement, not guaranteed.
* `resolve_adapter` does not import the mix or the stems yet (the outputs are library assets with
  stable ids; the adapter needs a small extension to place them).
* Loudness parsing of FFmpeg's `ebur128` exists here too (a fourth copy beside
  `audio_improvement`, `audio_intelligence` and `music_and_sound_design`); no module exposes it as
  a contract, so each carries its own.
* Frame-to-sample: sound positions come from the timeline in exact samples; the engine does not
  detect visual events or move a sound to fit one.

## Attaching the mix to a rendered picture (`MixAttacher`)

An editing application's render carries the raw sound of the clips (it cannot apply the mix).
`MixAttacher.execute(AttachMixCommand(picture_asset_id, mix_asset_id), ctx)` writes the picture's
video untouched (copied) with the final mix as AAC 320k / 48 kHz, registers it as a derivative of
the picture (`delivery_with_mix`) and measures the delivered file. A mix whose length differs from
the picture by more than 0.1 s is refused (the sound would drift). The pipeline's `deliver` task
calls it. It approves nothing and exports nothing.

