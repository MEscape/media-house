# Implementation Prompt — Layer 4: `music_and_sound_design`

## 1. Objective

Implement `music_and_sound_design` as a production-ready, backend-first module within the existing Media House architecture.

The module is responsible for selecting, arranging, synchronizing, and planning the mix of background music, sound effects, ambience, and original production audio.

Its purpose is to make videos feel more polished, immersive, and engaging through sound, without overwhelming speech or making every edit sound artificially dramatic.

The implementation must prioritize:

- Existing uploaded music and sound-effect assets.
- Contextual music and sound selection.
- A consistent channel sound identity.
- Speech intelligibility and original audio preservation.
- Precise synchronization with visual and narrative events.
- Professional restraint.
- Reproducible, structured sound-design plans.
- Integration with existing audio processing and timeline infrastructure.

**Critical rule: Never silently download, purchase, generate, invent, or introduce new audio assets.**

The existing uploaded music and sound-effect library is the default source of all selected audio assets.

If an appropriate asset is unavailable, report the missing asset and optionally recommend suitable additions. Do not insert an unrelated asset to fill the gap.

---

## 2. Mandatory first step: inspect the existing models and infrastructure

Before implementing the module, inspect the actual repository and identify all existing models, AI providers, model wrappers, inference services, audio-analysis capabilities, and processing backends.

This is especially important because Media House already contains multiple intelligence modules and processing capabilities.

Do not introduce new models or external AI services before understanding what already exists.

### 2.1 Model inventory

Inspect and document:

- All currently configured AI models.
- Model names and versions.
- Model providers.
- Existing model clients and wrappers.
- Local models and externally hosted models.
- Existing audio models.
- Speech recognition and transcription models.
- Audio classification models.
- Music understanding and music classification models.
- Sound-event detection models.
- Embedding models.
- Text and semantic search models.
- Existing multimodal models.
- Existing audio generation capabilities, if any.
- Model routing and selection logic.
- Model configuration and credentials handling.
- Existing inference infrastructure.
- Existing caching and batching mechanisms.
- Model availability and hardware requirements.

For every relevant model, determine:

1. What it actually does.
2. What inputs it accepts.
3. What outputs it produces.
4. Whether it is already integrated into Media House.
5. Whether it can be reused directly.
6. Whether its output quality is sufficient.
7. Whether it is appropriate for this module's workload.
8. Its resource requirements and performance characteristics.
9. Its limitations and failure conditions.

Do not assume a model supports a capability simply because its name suggests it does.

Verify actual interfaces, documentation, implementation, and available outputs.

### 2.2 Existing model reuse is mandatory

Prioritize the following order:

1. Reuse an existing Media House model or service that already provides the required capability.
2. Extend an existing model integration if a small additional capability is needed.
3. Reuse an existing general-purpose model if its actual capabilities are sufficient.
4. Only introduce another model when a concrete missing capability has been identified and justified.

Avoid:
- Duplicate speech recognition.
- Duplicate audio transcription.
- Duplicate audio classification.
- Duplicate embedding infrastructure.
- Duplicate model clients.
- Duplicate model-routing systems.
- Multiple models performing the same analysis without a demonstrated benefit.

If no suitable model exists, document the gap and propose the smallest appropriate extension.

Do not silently replace existing models or change their responsibilities.

### 2.3 Model responsibility mapping

Create a clear mapping between existing models and module capabilities.

For example:

| Capability | Preferred existing owner or infrastructure |
|---|---|
| Speech and transcript understanding | `audio_intelligence` |
| Music characteristics | Existing audio-analysis models, extended where necessary |
| Music structure and beats | Existing audio-analysis infrastructure or a justified music-specific capability |
| Sound-event recognition | Existing audio intelligence or classification infrastructure |
| Semantic asset search | Existing embedding and search infrastructure |
| Narrative and emotional context | Existing content and story intelligence |
| Loudness and dynamics processing | `audio_improvement` |
| Audio/visual event synchronization | `audio_visual_alignment` |
| Timeline placement | `timeline_builder` |
| DaVinci Resolve execution | `resolve_adapter` |

This is a responsibility guide, not permission to assume that every capability already exists.

Inspect and verify the actual implementation.

---

## 3. Mandatory architecture boundaries

Inspect the existing repository before implementation.

The following module ownership boundaries must be respected:

| Module | Responsibility |
|---|---|
| `music_and_sound_design` | Music and SFX selection, sound identity, arrangement, audio event planning, mix intentions, and missing-asset recommendations |
| `audio_intelligence` | Speech, audio-event, and existing audio analysis |
| `audio_improvement` | Audio enhancement, EQ, compression, loudness, dynamics, and actual audio-processing operations |
| `audio_visual_alignment` | Cross-modal timing and alignment |
| `visual_style_engine` | Channel visual identity |
| `content_intelligence` | Semantic and contextual content information |
| `story_architecture` | Narrative structure and emotional progression |
| `pacing_intelligence` | Rhythm, pacing, pauses, and energy progression |
| `editing_intelligence` | Editorial decisions about where music and sound design are useful |
| `motion_graphics_engine` | Graphic generation and animation events |
| `caption_engine` | Caption and emoji animation events |
| `transition_engine` | Visual transitions and their associated visual behavior |
| `continuity_intelligence` | Continuity analysis |
| `timeline_builder` | Timeline placement |
| `resolve_adapter` | DaVinci Resolve execution |
| `quality_control` | Final audio/video quality validation |

Important distinctions:

- This module decides which music and sound effects should be used.
- `audio_improvement` performs the actual audio processing.
- `audio_intelligence` provides audio understanding and analysis.
- `timeline_builder` owns timeline placement.
- `resolve_adapter` executes supported operations in DaVinci Resolve.

Do not build another audio-processing engine, transcription system, timeline system, or media library.

---

## 4. Existing music and sound-effect library

The existing uploaded audio library must be the default and authoritative source.

The module must first inspect and understand the available library before recommending music or sound effects.

### 4.1 Music library

Analyze and catalog existing music files.

Maintain or integrate with the existing media library and asset-management infrastructure.

For each music asset, reuse existing metadata where available and derive additional metadata when necessary.

Relevant characteristics include:

- Genre and subgenre.
- Mood and emotional character.
- Energy and intensity.
- BPM and tempo.
- Beat and downbeat positions.
- Rhythm and rhythmic complexity.
- Instrumentation.
- Vocal presence.
- Dynamic range.
- Build-ups and breakdowns.
- Drops and climaxes.
- Intro and outro characteristics.
- Suitable loop regions.
- Track duration.
- Music structure and section boundaries.

### 4.2 Sound-effect library

Scan and catalog all available sound-effect files.

Support:
- Metadata extraction.
- Searchable descriptions.
- Flexible classification.
- Custom tags.
- Duplicate detection.
- Alternative-version detection.
- Duration analysis.
- Loudness analysis.
- Frequency analysis.
- Useful start and end point detection.
- Loopability detection.
- Favorites.
- Approved assets.
- Source-file references.
- Licensing information.
- Project usage history.

Suggested categories:

| Category | Examples |
|---|---|
| UI sounds | Clicks, taps, notifications |
| Whooshes | Swishes, fast movement, transitions |
| Impacts | Hits, thuds, punches |
| Reveals | Risers, reveals, drops |
| Movement | Drops, falls, spins, flying objects |
| Atmosphere | Wind, rain, room tone |
| Mechanical | Motors, switches, machinery |
| Product sounds | Unboxing, buttons, handling |
| Nature | Birds, water, fire |
| Cinematic | Booms, drones, tension |
| Comedy | Boings, pops, cartoon impacts |
| Texture | Ticks, subtle clicks, small accents |

Categories must be extensible. Support multiple tags and overlapping classifications.

### 4.3 Asset provenance and licensing

For every asset, preserve:
- Original asset reference.
- Source file.
- Source information.
- Licensing status.
- Usage restrictions.
- Approval status.
- Relevant metadata.
- Usage history.

Do not assume that an uploaded audio file is automatically cleared for every publishing purpose.

Do not duplicate the existing asset-management system. Extend or integrate with it.

---

## 5. Music selection intelligence

Select suitable music from the existing uploaded music library.

Selection must be contextual rather than based only on genre.

Match music to:
- Video topic.
- Narrative structure.
- Emotional tone.
- Pacing.
- Format.
- Visual style.
- Target audience.
- Channel sound identity.

Support:
- Multiple tracks within one video.
- Different music for different sections.
- Suitable alternatives from the existing library.
- Music-free sections.
- Intentional silence.
- Reuse avoidance and diversity across projects.
- Music changes based on narrative progression.

Avoid repeatedly using the same track across every video.

Avoid music that conflicts with speech, important original audio, or meaningful sound events.

An energetic track may fit a product reveal but be inappropriate during a serious explanation.

### 5.1 Contextual matching

Use information from:
- `story_architecture`.
- `content_intelligence`.
- `pacing_intelligence`.
- `editing_intelligence`.
- `visual_style_engine`.
- Existing audio analysis.

The module should understand the purpose of each section before choosing music.

### 5.2 Music matching results

Every recommendation should include:
- Selected asset reference.
- Matching score or confidence where supported.
- Relevant music characteristics.
- Intended section.
- Reason for selection.
- Potential conflicts.
- Alternative candidates.
- Any relevant restrictions.

Do not invent analysis values when a model cannot provide them.

### 5.3 Music-free sections

Explicitly support:
- `music_enabled`.
- `music_disabled`.
- `intentional_silence`.

Silence is a valid sound-design decision, not an error.

---

## 6. Sound-effect selection and placement

Select existing sound effects only when they meaningfully improve the video.

Potential applications:
- Emphasize an important statement.
- Reinforce a visual reveal.
- Support a product demonstration.
- Accentuate a movement or action.
- Highlight a number or statistic.
- Add subtle interface sounds to screen recordings.
- Support chapter or section changes.
- Emphasize a visual impact.
- Add atmosphere to a scene.
- Reinforce a humorous moment.
- Support animated captions and emojis.

Consider:
1. What is happening visually?
2. What is being said?
3. What is the emotional context?
4. Does the moment actually need a sound effect?
5. Is there already an appropriate sound in the original footage?
6. Would an existing library asset improve the moment?

Do not:
- Add a whoosh to every movement.
- Add a pop to every caption.
- Add an impact to every cut.
- Add sound to every animation.
- Use an unrelated asset merely because it exists.

Support an explicit `no_sound_effect` decision.

Every sound-effect recommendation should contain:
- Asset reference, if selected.
- Target event.
- Intended timing.
- Purpose.
- Reason.
- Intensity.
- Priority.
- Confidence where supported.
- Potential conflicts.
- Alternative candidates.

---

## 7. Sound design for animated graphics and captions

Integrate with `motion_graphics_engine` and `caption_engine`.

The visual modules should expose structured animation events. This module should use those events to select and synchronize suitable existing sound effects.

Examples:

| Visual animation | Possible sound |
|---|---|
| Emoji drops and bounces | Soft impact or boing |
| Text pops into place | Pop |
| Text flies in | Short whoosh |
| Number counter increases | Subtle ticks |
| Important word appears | Soft impact |
| Product reveal | Riser or reveal sound |
| Graphic expands | Subtle sweep |
| Object falls | Falling sound followed by impact |
| Highlight appears | Short accent sound |

Requirements:
- Synchronize sound effects with actual animation events.
- Support offsets before, during, and after visual events.
- Match sound intensity to animation intensity.
- Support different sound choices for different animation presets.
- Avoid repeating identical sounds excessively.
- Allow animation sounds to be disabled independently.
- Reuse existing sound-effect assets.

### Animation event contract

Inspect the actual event interfaces exposed by `motion_graphics_engine` and `caption_engine`.

Do not invent incompatible event formats.

Where an event contract is missing, define a minimal shared interface and integrate it with the owning module.

An animation event should expose the relevant information, such as:
- Event identifier.
- Project and graphic/caption reference.
- Animation preset.
- Event type.
- Event start and end time.
- Key visual moment.
- Animation intensity.
- Timing reference.
- Source/timeline time reference.

The sound-design module must reference the original event rather than duplicate animation timing logic.

Support independent offsets and audio event durations.

---

## 8. Music arrangement and synchronization

Do more than place a music track underneath the entire video.

Support:
- Suitable music start and end points.
- Natural loop regions.
- Music arrangement around narrative sections.
- Beat and downbeat synchronization.
- Build-ups and drops.
- Music changes at important section boundaries.
- Dynamic music energy.
- Multiple music sections.
- Smooth music transitions.
- Fade-ins and fade-outs.
- Natural endings.
- Music-free sections.
- Intentional silence.

Use `story_architecture` and `pacing_intelligence` to understand where music should build, become quieter, change, or stop.

Do not force every visual cut to match a beat.

### Music timing requirements

Track:
- Source audio time.
- Timeline time.
- Music-section-local time.
- Beat positions.
- Downbeat positions.
- Loop boundaries.
- Fade ranges.
- Crossfade ranges.

Account for source and timeline frame rates and timing conventions when coordinating with the video timeline.

Avoid awkward endings, abrupt cuts, and unnatural loops.

---

## 9. Adaptive music volume and ducking

Speech must remain clear and understandable.

Plan appropriate volume relationships between:
- Dialogue and speech.
- Background music.
- Sound effects.
- Original production audio.
- Ambience.

Support:
- Automatically lowering music when someone speaks.
- Increasing music during pauses when appropriate.
- Reducing music during important explanations.
- More prominent music during montages.
- Reducing music under important original sound effects.
- Sidechain-style ducking.
- Configurable attack and release times.
- Natural volume transitions.
- Configurable loudness targets.
- Preservation of natural dynamics.

Use `audio_intelligence` for speech and event information.

Use `audio_improvement` for the underlying loudness, dynamics, and mastering operations.

The sound-design module decides the intended mix balance and provides structured processing instructions. The audio-processing module performs the actual processing.

Do not implement a duplicate compressor, limiter, EQ, loudness processor, or audio mastering system.

Avoid noticeable volume pumping and abrupt level changes.

---

## 10. Original audio and ambience preservation

Do not automatically replace original production audio with music and sound effects.

Preserve important original sounds:
- Product handling.
- Mechanical sounds.
- Footsteps.
- Reactions.
- Environmental sounds.
- Meaningful room tone.
- Relevant natural sound events.

Identify when original audio improves realism.

Support:
- Ambience continuity between shots.
- Room tone and environmental beds.
- Preservation of meaningful sound events.
- Detection of unwanted background noise using upstream analysis.
- Avoidance of unnecessary silence caused by cutting original audio.
- Original-audio-dominant sections.

A product unboxing video, for example, may benefit much more from the real sound of opening packaging than from an additional artificial sound effect.

Use upstream audio analysis to identify useful original sound events.

Do not independently duplicate the audio-intelligence analysis pipeline.

---

## 11. Sound identity

Introduce a persistent, versioned sound identity profile that complements the existing `visual_style_engine`.

The sound identity must be recognizable and consistent across the channel.

It should not randomly select different sound styles for every video.

### 11.1 Sound identity properties

Support:
- Preferred music genres.
- Preferred moods and emotional character.
- Typical music energy.
- Preferred instrumentation.
- Preferred sound-effect categories.
- Preferred sound-effect intensity.
- Preferred transition sounds.
- Preferred UI and notification sounds.
- Preferred impact and reveal sounds.
- Preferred ambience.
- Prohibited or disliked sound styles.
- Music and SFX loudness preferences.
- Frequency of sound effects.
- Preferred use of silence.

### 11.2 Alignment with visual identity

Use `visual_style_engine` as a reference for the channel's overall visual character.

The sound identity should complement the visual identity without necessarily imitating it.

Example:

A modern, clean, premium visual identity might work well with:
- Modern electronic or subtle cinematic music.
- Clean, short whooshes.
- Restrained impacts.
- Minimal UI sounds.
- Subtle reveal effects.
- Little or no cartoon-style sound design unless the content is humorous.

Do not derive arbitrary audio settings from visual tokens. Maintain an explicit sound identity configuration with its own properties and versioning.

### 11.3 Configuration inheritance

Support:
1. Global channel sound identity.
2. Format-specific sound profiles.
3. Project-level overrides.
4. Section-level overrides where justified.

Preserve profile versions so existing projects remain reproducible.

---

## 12. Missing sound and music detection

This is a critical capability.

The engine must identify when an additional audio asset would improve the intended result but no suitable asset exists in the current library.

Examples:
- A product reveal could benefit from a subtle reveal sound, but no suitable asset exists.
- An animation has no matching sound effect.
- A scene needs an atmospheric background.
- An action would benefit from a realistic impact sound.
- A music section lacks a suitable energetic track.

### 12.1 Required behavior

1. Search the existing library first.
2. Evaluate whether an existing asset can reasonably fulfill the purpose.
3. If no suitable asset exists, generate a missing-asset recommendation.
4. Explain what type of sound is needed and where it would be used.
5. Suggest useful characteristics such as duration, mood, intensity, and timing.
6. Assign a priority.
7. Do not substitute an unrelated sound.

Support three recommendation levels:

- `required`: A missing sound is important for the intended result.
- `recommended`: An additional asset would noticeably improve the result.
- `optional`: A sound could add a small amount of polish.

### 12.2 Example recommendation

```json
{
  "recommendation_type": "missing_asset",
  "priority": "optional",
  "required_asset": "Short, subtle product reveal sound",
  "location": "Product reveal at 02:14",
  "characteristics": {
    "duration_seconds": {
      "minimum": 0.5,
      "maximum": 1.2
    },
    "style": "clean, modern, premium",
    "description": "Soft rising texture followed by a restrained impact",
    "vocals": false
  },
  "reason": "No suitable matching sound effect was found in the existing library",
  "action": "manual_asset_acquisition"
}
```

This is an illustrative output contract.

### 12.3 Strict asset acquisition restrictions

The module must never automatically:
- Download audio.
- Purchase audio.
- Generate music.
- Generate sound effects.
- Add external assets.
- Substitute unapproved assets.

Any future external acquisition or generation workflow must require explicit authorization and remain separate from ordinary sound-design execution.

---

## 13. Audio transitions and continuity

Support:
- Smooth music transitions between sections.
- Crossfading compatible tracks.
- Ambience preservation across cuts.
- Avoiding abrupt room-tone changes.
- J-cuts and L-cuts where appropriate.
- Smooth sound-effect tails.
- Avoiding competing overlapping effects.
- Avoiding cut-off sound events.
- Appropriate audio fades.

Visual transitions remain owned by `transition_engine`.

This module plans the corresponding audio behavior and exposes compatible audio transition instructions.

Coordinate with:
- `transition_engine`.
- `continuity_intelligence`.
- `audio_visual_alignment`.
- `timeline_builder`.

Do not duplicate visual transition logic.

---

## 14. Music and sound-effect profiles

Create reusable sound-design configurations.

Implement the following suggested profiles:

| Profile | Sound-design approach |
|---|---|
| `youtube_standard` | Balanced music, selective SFX, clear speech |
| `shorts_energetic` | More rhythmic music and carefully selected accents |
| `product_review` | Modern background music, subtle UI sounds, and product accents |
| `unboxing` | Preserve real product sounds, minimal background music |
| `top_10` | Consistent music bed, restrained ranking and reveal accents |
| `tutorial` | Clear speech, subtle music, minimal UI sounds |
| `talking_head` | Natural voice, quiet music, occasional emphasis effects |
| `documentary` | Atmospheric music, natural ambience, restrained sound design |
| `cinematic` | More dynamic music and carefully designed sound layers |

Profiles must:
- Inherit global sound identity.
- Support project-specific overrides.
- Define music and SFX preferences.
- Define intensity and frequency limits.
- Support music-free sections.
- Support original-audio preservation rules.
- Be versioned and reproducible.

Do not hardcode profiles in a way that prevents extension or configuration.

---

## 15. Expected output structure

Produce a structured, reproducible sound-design plan.

At minimum, support:

```json
{
  "sound_design_plan_id": "string",
  "project_id": "string",
  "sound_identity_id": "string",
  "sound_identity_version": "string",
  "format_profile": "string",
  "music_selections": [],
  "music_segments": [],
  "sound_effect_events": [],
  "original_audio_preservation_rules": {},
  "ambience_segments": [],
  "silence_segments": [],
  "mixing_instructions": {},
  "ducking_rules": [],
  "crossfade_instructions": [],
  "animation_event_references": [],
  "missing_asset_recommendations": [],
  "asset_source_references": [],
  "timeline_positions": {},
  "validation_results": {},
  "warnings": [],
  "configuration_hash": "string",
  "plan_version": "string"
}
```

Each music or sound-effect event should support:

- `event_id`
- `asset_id`
- `source_start_time`
- `source_end_time`
- `timeline_start_time`
- `timeline_end_time`
- `gain`
- `fade_in`
- `fade_out`
- `purpose`
- `reason`
- `priority`
- `confidence`

Also support:
- Asset source references.
- Licensing and approval metadata.
- Event type.
- Original audio references.
- Music section and loop information.
- Beat and downbeat references.
- Animation event references.
- Mix instructions.
- Ducking instructions.
- Crossfade instructions.
- No-music and no-SFX decisions.
- Missing-asset recommendations.
- Validation results.
- Version and configuration metadata.

Never lose the relationship between an audio event and its original library asset.

Use the repository's existing asset IDs, project IDs, time formats, and serialization conventions wherever possible.

---

## 16. Suggested internal module structure

Use this as a logical starting point, adapting it to the repository's conventions:

```text
music_and_sound_design/
│
├── audio_library/
│   ├── music_catalog/
│   ├── sound_effect_catalog/
│   ├── asset_classification/
│   ├── audio_fingerprinting/
│   ├── duplicate_detection/
│   └── asset_metadata/
│
├── music_intelligence/
│   ├── music_analysis/
│   ├── mood_classification/
│   ├── bpm_and_beat_detection/
│   ├── music_structure_analysis/
│   ├── music_matching/
│   └── music_section_selection/
│
├── sound_effect_intelligence/
│   ├── sfx_classification/
│   ├── semantic_asset_search/
│   ├── sfx_matching/
│   ├── sfx_timing/
│   └── sfx_recommendations/
│
├── sound_identity/
│   ├── global_sound_profile/
│   ├── music_preferences/
│   ├── sfx_preferences/
│   ├── intensity_rules/
│   └── format_profiles/
│
├── sound_design_planning/
│   ├── music_placement/
│   ├── music_arrangement/
│   ├── sound_effect_placement/
│   ├── animation_sound_sync/
│   ├── ambience_planning/
│   └── silence_planning/
│
├── audio_mix_planning/
│   ├── music_ducking/
│   ├── voice_music_balance/
│   ├── sfx_balance/
│   ├── audio_crossfades/
│   └── loudness_targets/
│
├── missing_asset_detection/
│   ├── coverage_analysis/
│   ├── missing_music_detection/
│   ├── missing_sfx_detection/
│   └── acquisition_recommendations/
│
├── timeline_integration/
│   ├── audio_event_mapping/
│   ├── timeline_specification/
│   └── source_reference_mapping/
│
└── validation/
    ├── synchronization_validation/
    ├── loudness_validation/
    ├── clipping_validation/
    ├── continuity_validation/
    ├── asset_validation/
    └── mix_quality_validation/
```

Do not create empty placeholder directories merely to match this structure.

Implement meaningful components using the existing repository conventions.

---

## 17. Performance, caching, and reliability

The module must be suitable for real video projects and large audio libraries.

Implement or integrate:
- Incremental audio analysis.
- Asset metadata caching.
- Audio fingerprinting and duplicate detection.
- Reusable music analysis.
- Reusable SFX classification.
- Semantic search indexing.
- Batch analysis where appropriate.
- Resource-aware processing.
- Dependency-aware caching.
- Deterministic selection and planning.
- Render and processing status.
- Error reporting and recovery.

Cache invalidation must account for:
- Audio source changes.
- Asset metadata changes.
- Analysis model or version changes.
- Embedding model or version changes.
- Sound identity changes.
- Profile changes.
- Project content changes.
- Narrative and pacing changes.
- Timeline timing changes.
- Animation event changes.
- Audio processing configuration changes.

Do not repeatedly analyze unchanged library assets.

Do not reuse stale analysis after relevant dependencies change.

---

## 18. Recommended implementation priority

Implement incrementally in this order:

| Priority | Component | Importance |
|---:|---|---|
| 1 | Existing audio library catalog | Foundation |
| 2 | Music and SFX classification | Makes assets searchable |
| 3 | Music selection intelligence | Selects appropriate existing music |
| 4 | Sound-effect selection | Context-aware sound design |
| 5 | Sound identity profiles | Consistent channel sound |
| 6 | Music arrangement and ducking | Professional audio flow |
| 7 | Animation and visual-event synchronization | Coordinated graphics and sound |
| 8 | Missing asset detection | Identifies library gaps |
| 9 | Audio continuity and transitions | Smooth listening experience |
| 10 | Validation and caching | Reliability and efficiency |

Prioritize reliable selection and planning before advanced automation.

Do not claim a capability is production-ready until it works with the real library, existing models, and downstream pipeline.

---

## 19. Testing requirements

Create tests consistent with the existing repository's conventions.

### Existing model and infrastructure reuse
- Verify model discovery and capability mapping.
- Verify existing model wrappers are reused.
- Verify no duplicate model clients are introduced.
- Verify unsupported model capabilities fail explicitly.
- Verify model version changes invalidate affected analysis.

### Audio library
- Catalog existing music and SFX assets.
- Extract and preserve metadata.
- Detect duplicates and alternative versions.
- Preserve source references.
- Preserve licensing and approval information.
- Support custom tags and categories.
- Verify incremental analysis.

### Music selection
- Contextual music matching.
- Multiple music sections.
- Alternative candidates.
- Music-free sections.
- Intentional silence.
- Avoiding repetitive selection.
- Correct use of existing library assets only.

### Sound-effect selection
- Contextual SFX selection.
- Explicit `no_sound_effect`.
- Appropriate event timing.
- Animation event synchronization.
- Avoiding repetitive effects.
- Missing-asset recommendations.
- No unrelated fallback assets.

### Audio arrangement and mix planning
- Music transitions.
- Loop boundaries.
- Beat and downbeat references.
- Ducking instructions.
- Speech priority.
- Original audio preservation.
- Ambience continuity.
- Audio crossfades.
- Silence planning.

### Asset restrictions
- No automatic downloads.
- No automatic purchases.
- No automatic audio generation.
- No unapproved external assets.
- Correct missing-asset reporting.

### Pipeline integration
- `audio_intelligence`.
- `audio_improvement`.
- `audio_visual_alignment`.
- `story_architecture`.
- `pacing_intelligence`.
- `visual_style_engine`.
- `motion_graphics_engine`.
- `caption_engine`.
- `transition_engine`.
- `timeline_builder`.
- `resolve_adapter`.
- `quality_control`.

Test actual asset references, timing, and resulting audio behavior where the existing infrastructure supports it. Do not rely exclusively on mocked function calls.

---

## 20. Definition of done

The implementation is complete only when:

1. The existing models and their real capabilities have been inspected and documented.
2. Existing suitable models and infrastructure are reused.
3. No unnecessary duplicate model or processing infrastructure has been introduced.
4. The existing uploaded music and SFX libraries can be cataloged and searched.
5. Music and SFX metadata are preserved and searchable.
6. Music selection is contextual and uses existing assets.
7. Multiple music sections and intentional silence are supported.
8. Sound effects are selected selectively and contextually.
9. Explicit `no_sound_effect` decisions are supported.
10. Animation events can be synchronized with suitable existing sound effects.
11. Music arrangement supports beats, loops, fades, and section changes.
12. Ducking and mix intentions are represented as structured instructions.
13. Original production audio and meaningful ambience are preserved.
14. A persistent, versioned sound identity exists.
15. Sound profiles inherit global identity and support controlled overrides.
16. Missing assets are reported with actionable recommendations.
17. No audio is automatically downloaded, purchased, generated, or invented.
18. Asset licensing and provenance are retained.
19. Audio processing remains owned by `audio_improvement`.
20. Timeline placement remains owned by `timeline_builder`.
21. The resulting plan integrates with `resolve_adapter`.
22. Quality validation covers synchronization, levels, clipping, continuity, and asset integrity.
23. Tests pass, including integration tests.
24. Existing Media House functionality remains compatible.
25. No frontend, dashboard, editor, or other UI has been introduced.

---

## 21. Final implementation instructions

Work within the existing Media House repository.

Start with the actual model and infrastructure inventory. Do not begin by installing new models or creating new AI services.

Then:

1. Inspect the repository and document the existing models, providers, wrappers, and audio capabilities.
2. Map existing models to required module responsibilities.
3. Identify genuine capability gaps.
4. Inspect the existing audio library and asset-management infrastructure.
5. Define the sound-design plan and event contracts.
6. Implement library cataloging and classification.
7. Implement music and SFX selection.
8. Implement sound identity and profiles.
9. Implement music arrangement and mix planning.
10. Integrate animation-event synchronization.
11. Implement missing-asset recommendations.
12. Integrate with the existing timeline and Resolve pipeline.
13. Add validation, caching, and error handling.
14. Add and run tests.
15. Document actual model usage, supported capabilities, limitations, and execution behavior.

Do not silently change existing model responsibilities.

Do not add an AI audio-generation workflow.

Do not introduce a frontend, dashboard, visual editor, or other UI. This is backend implementation only.

Do not replace existing working systems without a demonstrated technical reason.

The final result must be a reliable sound-design system that creates a consistent audio identity, selects intelligently from the existing library, preserves original audio, and uses music and sound effects with restraint.

**The system must be able to recognize when the best sound-design decision is to use no additional sound at all.**
