# Implementation Prompt — Layer 4, Module 20: `transition_engine`

## Project Context

You are working on an existing backend-first video production system called **Media House**.

The system processes raw footage, analyzes audio and video, develops editorial plans, generates creative assets, constructs timelines, and produces finished videos through DaVinci Resolve.

The existing architecture contains:

- **Layer 1 — Media Foundation:** media library, media inspection, asset relationships, proxy management.
- **Layer 2 — Media Intelligence:** audio improvement, audio intelligence, video improvement, video intelligence, content intelligence, audio-visual alignment.
- **Layer 3 — Editorial Planning:** story architecture, editing intelligence, pacing intelligence, B-roll intelligence, continuity intelligence, reframing intelligence.
- **Layer 4 — Creative Design and Asset Generation:** visual style, motion graphics, captions, transitions, VFX, music and sound design, asset generation.

Your task is to implement:

# `transition_engine`

Build a professional, modular transition system responsible for selecting, configuring, validating, and preparing transitions between shots, scenes, and sections of a video.

The objective is not to make every cut look impressive.

The objective is to make videos feel smooth, intentional, professional, and consistent with the channel's visual identity.

**Most edits should remain simple cuts. Every transition effect must have a reason to exist.**

---

## 1. Core Responsibility and Architectural Boundaries

The `transition_engine` owns:

- Determining whether a transition is necessary.
- Understanding the context surrounding a cut.
- Selecting an appropriate transition type.
- Selecting transition intensity.
- Configuring transition parameters.
- Determining transition duration and timing.
- Evaluating compatibility between adjacent shots.
- Planning visual and audio transitions independently.
- Applying channel and format transition profiles.
- Validating transition feasibility.
- Providing structured transition specifications to downstream modules.
- Selecting a safe fallback when an effect is unsuitable.

It does not own the overall editorial decision about which clips belong in the video.

Respect these architectural boundaries:

| Module | Responsibility |
|---|---|
| `editing_intelligence` | Determines the editorial structure and where transitions may be useful |
| `transition_engine` | Selects, configures, and validates transition behavior |
| `visual_style_engine` | Defines channel identity and approved transition aesthetics |
| `motion_graphics_engine` | Creates animated graphic elements used in branded or custom transitions |
| `continuity_intelligence` | Analyzes visual and audio continuity |
| `pacing_intelligence` | Provides timing and rhythm information |
| `audio_intelligence` | Provides speech, music, and sound-event information |
| `audio_visual_alignment` | Provides synchronized audio and visual event references |
| `timeline_builder` | Places transition instructions into the timeline |
| `resolve_adapter` | Executes the timeline in DaVinci Resolve |
| `quality_control` | Validates the resulting video |

Do not create a second editing intelligence system, animation engine, compositing engine, or timeline system.

---

## 2. Transition Decision Intelligence

Build the transition decision system first.

The engine must determine whether a transition is needed before selecting a transition type.

### Default behavior

- Standard hard cuts are the default.
- Adjacent clips must not automatically receive transitions.
- No transition effect should be represented as a valid decision.
- Transitions should only be applied when they improve the viewing experience.
- Allow transitions to be explicitly disabled for individual sections or boundaries.

### Identify transition contexts

Support detection and classification of:

- Natural cuts between continuous actions.
- Changes between camera angles.
- Changes in shot size.
- Changes of topic.
- Changes of location.
- Changes of scene.
- Changes of time.
- Chapter and section boundaries.
- Product reveals.
- Before-and-after comparisons.
- Montages.
- Visual sequences.
- Introductions and conclusions.
- Narrative reveals.

### Decision signals

Consider:

**Visual context**
- Camera angle.
- Shot size.
- Subject position.
- Screen direction.
- Camera movement.
- Background.
- Lighting.
- Composition.
- Matching movement between shots.

**Narrative context**
- Whether the same thought continues.
- Whether a new subject is introduced.
- Whether a major story beat occurs.
- Whether there is a reveal or conclusion.
- Whether time or location changes.

**Audio context**
- Speech continuity.
- Natural pauses.
- Music beats and downbeats.
- Sound effects.
- Ambience.
- Audio crossfades.
- Room tone.

**Pacing context**
- Fast-paced montage.
- Calm explanation.
- Dramatic reveal.
- Important statement.
- Chapter transition.
- Ending or conclusion.

Use information from:

- `continuity_intelligence`
- `pacing_intelligence`
- `story_architecture`
- `audio_intelligence`
- `audio_visual_alignment`
- `editing_intelligence`

Do not independently recreate their analysis.

### Decision output

The engine should be able to return:

- No transition needed.
- Transition recommended.
- Transition required by an explicit editorial instruction.
- Transition unsuitable, with fallback to a hard cut.
- Transition manually overridden.

Every non-default transition should have a recorded reason and supporting context.

---

## 3. Transition Types and Effect Library

Create a curated, reusable transition library.

Do not implement every effect as a completely separate rendering system. Use existing timeline, rendering, and motion graphics capabilities wherever appropriate.

### A. Basic transitions

These form the foundation:

- Hard cut.
- Cross dissolve.
- Fade to black.
- Fade from black.
- Fade to white.
- Dip to color.
- Audio crossfade.

Hard cuts should remain the dominant transition.

### B. Movement-based transitions

Support:

- Slide left.
- Slide right.
- Push in.
- Push out.
- Smooth zoom.
- Directional wipe.
- Motion blur transition.
- Whip pan.
- Camera movement match.

Use selectively for:

- Energetic sequences.
- Montages.
- Intentional scene changes.
- Movement-based visual storytelling.

Check whether the adjacent footage supports the selected movement.

### C. Stylized transitions

Support optional presets for:

- Light leak.
- Flash transition.
- Glitch transition.
- Blur transition.
- Shape-based wipe.
- Mask reveal.
- Distortion transition.

These effects must not become part of the default visual language unless explicitly approved by the channel's identity profile.

### D. Section and chapter transitions

Support:

- Minimalist title transition.
- Short animated section divider.
- Branded transition element.
- Brief background or color change.
- Chapter title reveal.
- Product or topic introduction.

These are particularly useful for:

- Top 10 videos.
- Tutorials.
- Product reviews.
- Structured educational videos.
- Chapter-based content.

If a branded transition requires animated graphics, delegate graphic creation and animation to `motion_graphics_engine`.

---

## 4. Transition Intensity and Restraint

Implement a configurable transition intensity system.

| Level | Style | Typical application |
|---|---|---|
| 0 | Hard cut | Default for most edits |
| 1 | Very subtle | Short dissolves, gentle fades |
| 2 | Noticeable but clean | Smooth zooms, directional slides |
| 3 | Expressive | Whip pans, stronger movement |
| 4 | Highly stylized | Glitch, distortion, elaborate reveals |

The system must never interpret higher intensity as higher quality.

### Recommended behavior

- Most transitions should be level 0 or 1.
- Level 2 should only be used when it serves the content.
- Levels 3 and 4 should be rare and intentional.
- Avoid consecutive flashy transitions.
- Avoid multiple unrelated transition styles in a short sequence without a creative reason.
- Prefer visual consistency over transition variety.

Support:

- Global intensity defaults.
- Format-specific intensity limits.
- Project-level intensity settings.
- Section-level restrictions.
- Maximum permitted intensity.
- Explicitly approved exceptions.

Implement frequency and repetition controls so that a sequence does not become visually exhausting.

---

## 5. Visual Identity Integration

The transition engine must follow the design system defined by `visual_style_engine`.

It must not invent an independent transition aesthetic.

Consume:

- Brand colors.
- Typography rules where text is involved.
- Shape language.
- Graphic design elements.
- Logo and brand assets.
- Animation speed.
- Easing curves.
- Motion direction preferences.
- Motion intensity limits.
- Approved transition presets.

For example, if the channel identity uses clean typography, restrained movement, rounded shapes, and subtle accent colors, transitions should reflect those characteristics.

### Transition profiles

Support:

- Global channel transition profile.
- Format-specific transition profiles.
- Project-specific overrides.
- Approved transition types.
- Prohibited transition types.
- Maximum transition intensity.
- Preferred transition duration.
- Reusable branded transition templates.

Use explicit inheritance and override rules.

Do not insert the channel logo or brand colors into every transition simply to demonstrate brand identity.

A recognizable identity should come from consistent design language, not repetitive branding.

Preserve visual identity version references so older projects can reproduce their original transitions.

---

## 6. Context-Aware Compatibility Analysis

Before recommending or applying a transition, evaluate the outgoing and incoming shots.

Implement compatibility analysis using existing visual and continuity information.

### Visual compatibility

Evaluate:

- Subject position.
- Subject size.
- Composition.
- Background.
- Lighting.
- Camera angle.
- Screen direction.
- Visual similarity.
- Important objects and text.

### Motion compatibility

Evaluate:

- Camera movement direction.
- Subject movement direction.
- Movement speed.
- Motion continuity.
- Available motion information.
- Whether the movement can support the chosen transition.

Examples:

- Whip pans are more suitable when the outgoing and incoming shots have compatible movement.
- Zoom transitions may look awkward when the compositions differ significantly.
- Slide transitions may obscure important subjects or text.
- Dissolves can create ghosting when subjects move differently.
- Movement-based transitions should not introduce visually confusing motion.

### Composition compatibility

Determine whether the effect:

- Covers a face.
- Obscures a product.
- Hides important information.
- Creates distracting overlap.
- Conflicts with the visual hierarchy.

### Fallback behavior

If a transition is unsuitable:

1. Record the incompatibility.
2. Select an appropriate alternative if one exists.
3. Otherwise fall back to a hard cut.
4. Record the fallback decision and reason.

Do not force an effect merely because it was initially requested.

---

## 7. Transition Timing and Synchronization

Implement a dedicated timing subsystem.

Support:

- Configurable transition duration.
- Frame-accurate timing.
- Transitions centered on cuts.
- Transitions aligned to clip boundaries.
- Overlapping source ranges.
- Timeline offsets.
- Audio/video synchronization.
- Music beat synchronization where appropriate.
- Source frame rates.
- Timeline frame rate.
- Timebase differences.
- Variable frame rate media.

### Important timing requirements

- Do not cut off spoken words unintentionally.
- Avoid obscuring important actions or expressions.
- Avoid transitions that unnecessarily delay the next scene.
- Account for source footage handles.
- Validate available frames before applying overlapping transitions.
- Maintain timing when upstream timeline changes occur.

Transition duration must depend on context, not one fixed global value.

Keep source-media time and timeline time distinct.

Use the existing time and frame conventions established by Media House.

---

## 8. Audio Transitions

Visual transitions and audio transitions must be treated as related but independent decisions.

A visual hard cut may be accompanied by a smooth audio crossfade, J-cut, or L-cut.

Support:

- Audio crossfades.
- Music transitions.
- Ambience continuity.
- Room tone transitions.
- Sound-effect transitions.
- J-cuts.
- L-cuts.
- Audio fades.
- Independent audio transition durations.

### Audio quality rules

- Avoid abrupt background-noise changes.
- Preserve dialogue.
- Preserve important sound events.
- Avoid unnecessary sound effects on every visual transition.
- Do not automatically synchronize every transition to a music beat.
- Respect speech continuity and natural pauses.

Integrate with:

- `audio_intelligence`
- `audio_improvement`
- `audio_visual_alignment`

Do not create a duplicate audio processing system.

---

## 9. Transition Templates and Presets

Create reusable transition profiles rather than designing every transition from scratch.

Initial profiles:

### `clean_default`

- Mostly hard cuts.
- Occasional subtle dissolves.
- Low intensity.
- Natural continuity.

### `modern_youtube`

- Clean cuts.
- Subtle movement.
- Occasional expressive transitions.
- Consistent visual language.

### `shorts_energetic`

- Faster cuts.
- Selective zooms.
- Slides.
- Movement-based transitions.
- Occasional expressive effects.

### `product_review`

- Clean cuts.
- Smooth reveals.
- Occasional comparison transitions.
- Minimal distraction from the product.

### `unboxing`

- Natural cuts.
- Subtle reveals.
- Occasional emphasis transitions.

### `tutorial`

- Clean cuts.
- Screen changes.
- Minimal section transitions.
- Clear instructional flow.

### `documentary`

- Restrained dissolves.
- Natural scene transitions.
- Minimal stylization.

### `cinematic`

- Carefully selected dissolves.
- Fades.
- Movement-based transitions where appropriate.

These are initial presets, not hardcoded restrictions.

Each profile should inherit the channel identity and override only the settings it needs.

Support project-level and section-level modifications.

---

## 10. Integration with Existing Media House Modules

Inspect existing implementations and reuse their interfaces.

### `visual_style_engine`

Provides:
- Visual identity.
- Approved transition styles.
- Colors and shapes.
- Motion preferences.
- Identity version references.

### `editing_intelligence`

Provides:
- Editorial cut decisions.
- Clip ordering.
- Potential transition boundaries.
- Editorial instructions.

The transition engine must not independently decide the entire edit.

### `continuity_intelligence`

Provides:
- Visual continuity information.
- Audio continuity information.
- Shot compatibility signals.
- Continuity issues.

### `pacing_intelligence`

Provides:
- Rhythm.
- Timing recommendations.
- Sequence intensity.
- Appropriate transition timing.

### `story_architecture`

Provides:
- Narrative structure.
- Chapter boundaries.
- Section changes.
- Reveals and conclusions.

### `audio_intelligence`

Provides:
- Speech timing.
- Music information.
- Sound events.
- Audio boundaries.

### `audio_visual_alignment`

Provides:
- Audio/visual event synchronization.
- Relevant event references.
- Beat and event alignment where available.

### `motion_graphics_engine`

Provides:
- Animated transition graphics.
- Branded transition elements.
- Graphic rendering.
- Animation and compositing.

Do not implement a second animation or compositing engine.

### `timeline_builder`

Receives:
- Transition specifications.
- Clip boundary references.
- Timing information.
- Audio transition instructions.
- Fallback decisions.

The transition engine must not directly manipulate the timeline.

### `resolve_adapter`

Executes the resulting timeline in DaVinci Resolve.

Do not directly control Resolve from `transition_engine`.

### `quality_control`

Receives transition validation information and checks the final rendered video.

Do not create a parallel final-video quality-control system.

---

## 11. Suggested Internal Module Structure

Use this structure as an architectural guide. Adapt it to the repository's existing conventions.

```text
transition_engine/
│
├── transition_decision/
│   ├── transition_need_detection/
│   ├── cut_context_analysis/
│   ├── transition_type_selection/
│   └── transition_intensity_selection/
│
├── transition_library/
│   ├── basic_transitions/
│   ├── movement_transitions/
│   ├── stylized_transitions/
│   ├── audio_transitions/
│   └── branded_transitions/
│
├── compatibility_analysis/
│   ├── visual_compatibility/
│   ├── motion_compatibility/
│   ├── composition_compatibility/
│   ├── continuity_compatibility/
│   └── source_handle_validation/
│
├── timing/
│   ├── duration_selection/
│   ├── frame_alignment/
│   ├── beat_synchronization/
│   └── audio_video_timing/
│
├── style_integration/
│   ├── identity_profiles/
│   ├── format_profiles/
│   ├── transition_presets/
│   └── intensity_constraints/
│
├── audio_transition_planning/
│   ├── crossfade_planning/
│   ├── ambience_continuity/
│   └── audio_transition_validation/
│
├── transition_planning/
│   ├── transition_specification/
│   ├── fallback_handling/
│   └── timeline_integration/
│
└── validation/
    ├── visual_validation/
    ├── timing_validation/
    ├── continuity_validation/
    ├── style_validation/
    └── transition_frequency_validation/
```

Do not create empty directories or placeholder abstractions simply to match this structure. Implement functional components with clear ownership and tests.

---

## 12. Expected Output and Data Contracts

Produce a structured, versioned transition specification for every evaluated boundary.

The output should include:

- `transition_id`
- `project_id`
- `outgoing_clip_id`
- `incoming_clip_id`
- `transition_required`
- `transition_type`
- `transition_preset_id`
- `transition_intensity`
- `duration_frames`
- `start_time`
- `alignment`
- `visual_parameters`
- `audio_parameters`
- `style_profile_id`
- `identity_version`
- `reason`
- `confidence`
- `compatibility_results`
- `warnings`
- `fallback_transition`
- `validation_results`

Additional fields may be necessary to match existing Media House contracts.

### Important behavior

A normal hard cut must be representable explicitly.

For example, the system should be able to return:

- No transition effect.
- Outgoing clip reference.
- Incoming clip reference.
- Reason: continuous action and natural visual continuity.
- Transition intensity: 0.

Do not generate unnecessary transition assets for ordinary hard cuts.

Validate the schema at module boundaries and preserve version compatibility.

---

## 13. Performance and Reliability

Implement:

- Reuse of existing visual analysis.
- Reuse of existing audio analysis.
- Cached compatibility results where inputs remain unchanged.
- Deterministic preset configurations.
- Batch boundary evaluation.
- Incremental recalculation after timeline changes.
- Dependency tracking.
- Efficient validation.
- Clear fallback behavior.
- Reproducible transition specifications.

Recalculate affected decisions when relevant dependencies change, including:

- Outgoing or incoming clip.
- Clip order.
- Source range.
- Timeline timing.
- Visual compatibility information.
- Audio information.
- Story or section structure.
- Pacing recommendations.
- Visual identity version.
- Transition profile.
- Project overrides.

Avoid unnecessary AI calls for deterministic operations such as frame alignment, source-handle checks, and preset validation.

---

## 14. Implementation Priorities

Implement in this order:

| Priority | Component | Importance |
|---|---|---|
| 1 | Transition decision logic | Prevent unnecessary transitions |
| 2 | Hard cuts and basic transitions | Foundation |
| 3 | Transition profiles and style integration | Consistent channel identity |
| 4 | Timing and source-handle validation | Technically correct transitions |
| 5 | Visual and motion compatibility | Avoid awkward effects |
| 6 | Audio transition planning | Smooth sound continuity |
| 7 | Movement-based transitions | Additional creative options |
| 8 | Branded section transitions | Consistent chapter changes |
| 9 | Frequency and repetition validation | Avoid overuse |
| 10 | Timeline and Resolve integration | Reliable execution |

Build a working core before expanding the transition library.

---

## 15. Strict Scope Restrictions

The following restrictions are mandatory:

- Do not build a UI.
- Do not build another editorial decision engine.
- Do not reimplement continuity intelligence.
- Do not reimplement pacing intelligence.
- Do not reimplement story architecture.
- Do not build a second audio intelligence system.
- Do not build another animation engine.
- Do not build a separate compositing engine.
- Do not directly manipulate the timeline.
- Do not directly control DaVinci Resolve.
- Do not create duplicate asset management.
- Do not automatically add transitions between every pair of clips.
- Do not prioritize visual effects over professional editing quality.

This is a backend-first production module.

---

## 16. Testing Requirements

Implement unit, integration, and end-to-end tests.

At minimum, verify:

1. Natural continuous action produces a hard cut by default.
2. Different camera angles do not automatically receive effects.
3. Topic changes can receive an appropriate transition recommendation.
4. Scene and location changes are handled contextually.
5. Chapter boundaries can request branded transitions.
6. Product reveals can request appropriate reveal transitions.
7. Transition intensity limits are enforced.
8. Flashy consecutive transitions are detected.
9. Repeated transition patterns are detected.
10. Visual compatibility analysis works.
11. Motion compatibility analysis works.
12. Composition conflicts are detected.
13. Incompatible transitions fall back to hard cuts.
14. Source handles are validated.
15. Frame-accurate timing is maintained.
16. Different source and timeline frame rates are handled.
17. Variable frame rate media is handled through existing timing infrastructure.
18. Speech is not unintentionally cut off.
19. Music beat synchronization works when requested.
20. Audio transitions remain independent of visual transitions.
21. J-cuts and L-cuts can be represented.
22. Visual identity inheritance works.
23. Format and project overrides work.
24. Branded transitions integrate with `motion_graphics_engine`.
25. Timeline specifications are generated correctly.
26. Timeline changes invalidate affected decisions.
27. Quality-control reports contain actionable information.
28. Existing Media House pipeline tests continue to pass.

Test actual transition execution through the existing rendering and Resolve pipeline, not just the creation of transition metadata.

---

## 17. Definition of Done

The module is complete when:

- The system evaluates whether a transition is needed before selecting an effect.
- Hard cuts remain the normal default.
- Transition decisions are informed by visual, narrative, audio, and pacing context.
- The curated transition library supports the required basic, movement-based, stylized, audio, and branded transitions.
- Transition intensity and frequency are controlled.
- Visual identity is inherited from `visual_style_engine`.
- Adjacent-shot compatibility is evaluated.
- Unsuitable transitions fall back safely.
- Frame-accurate timing and source handles are validated.
- Visual and audio transitions can be planned independently.
- Branded animation uses `motion_graphics_engine`.
- Transition instructions integrate correctly with `timeline_builder`.
- DaVinci Resolve execution uses `resolve_adapter`.
- Validation results are available to `quality_control`.
- Caching and incremental recalculation work correctly.
- Existing modules remain compatible.
- No duplicate editing, animation, compositing, audio, or timeline systems have been introduced.

---

## Final Implementation Instruction

Before writing code, inspect the existing Media House repository.

Understand its module structure, existing data contracts, timeline representation, rendering architecture, frame-rate handling, media references, and integration conventions.

Then implement `transition_engine` as a production-ready backend module.

Do not simply create directories and placeholder classes. Implement functional transition decisions, compatibility checks, timing, configuration, fallback handling, integrations, and tests.

Verify that real transition specifications can pass through the existing timeline and Resolve execution pipeline.

The central design principle is:

**A transition must earn its place.**

- Continuous action: hard cut.
- Different camera angle: usually hard cut.
- Gentle scene or time change: possibly a dissolve.
- New chapter: possibly a subtle branded transition.
- Product reveal: purposeful reveal when appropriate.
- Music and movement support an effect: occasional movement-based transition.
- No meaningful benefit: no effect.

The goal is not for viewers to notice how many transitions the video contains.

The goal is for the video to feel smooth, intentional, professional, and visually consistent.
