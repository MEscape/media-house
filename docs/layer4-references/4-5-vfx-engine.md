# Implementation Prompt — Layer 4: `vfx_engine`

## 1. Objective

Implement the `vfx_engine` as a production-ready, backend-first module within the existing Media House architecture.

The `vfx_engine` is responsible for **shot-level visual effects, compositing, subject isolation, object cleanup, tracking-based effects, and time-based visual treatments** that modify or augment the appearance of individual shots or selected footage sections.

Its purpose is to improve specific shots and create intentional visual effects where they meaningfully improve the final video.

The system must prioritize:
- Purposeful effects over decorative effects.
- Reversibility and preservation of original media.
- Reproducible effect specifications.
- Reliable masking, tracking, compositing, and rendering.
- Compatibility with the existing Media House pipeline.
- Professional restraint and visual consistency.

Do not build a system that automatically adds effects to every shot.

A shot without effects must remain a valid and often preferred outcome.

## 2. Mandatory architecture boundaries

Before implementing anything, inspect the existing repository, architecture, modules, interfaces, data models, media pipeline, processing infrastructure, and tests.

Do not assume that a capability is missing simply because it is not implemented inside `vfx_engine`.

The following ownership boundaries are mandatory:

| Module | Responsibility |
|---|---|
| `vfx_engine` | Shot-level visual effects, segmentation, masking, tracking-based effects, compositing, cleanup, and visual treatments |
| `transition_engine` | Effects that move between shots, clips, or scenes |
| `motion_graphics_engine` | Designed animated text, graphics, shapes, diagrams, and visual elements |
| `video_improvement` | Technical image correction and enhancement |
| `color_pipeline` | Color correction and creative color grading |
| `reframing_intelligence` | Crop, framing, subject positioning, and virtual camera movement |
| `visual_style_engine` | Channel visual identity and approved visual treatment constraints |
| `editing_intelligence` | Editorial decisions about where effects may be useful |
| `timeline_builder` | Timeline placement and timing coordination |
| `resolve_adapter` | Execution of supported operations in DaVinci Resolve |
| `quality_control` | Validation of the final rendered video |

Important distinctions:

- Do not implement ordinary exposure correction, white balance, denoising, general sharpening, or color grading in this module.
- Do not create another animation engine. Reuse a shared keyframe/interpolation system where available.
- Do not implement another subject detection or general-purpose tracking intelligence system.
- Do not build a separate timeline management system.
- Do not duplicate existing media storage, asset management, proxy management, or rendering infrastructure.
- Do not make independent editorial decisions that belong to `editing_intelligence`.

If an existing module lacks a required capability, identify the correct architectural owner and extend that owner where appropriate. Preserve existing interfaces and compatibility, then integrate the capability with `vfx_engine`.

## 3. Shot-level visual effects

Implement a configurable effect system for individual shots or selected sections of footage.

Supported effect categories should include:

### Blur and focus
- Background blur.
- Simulated depth of field.
- Selective blur.
- Focus effects.
- Focus transitions within a shot.
- Depth-based blur.
- Selective sharpness as a localized effect.

### Motion and lens effects
- Motion blur.
- Lens distortion.
- Chromatic aberration.
- Lens flare.
- Glare and reflections.
- Focus breathing simulation, where appropriate.

### Light and appearance effects
- Glow and bloom.
- Light rays.
- Light leaks.
- Vignette.
- Halation.
- Selective desaturation.

### Texture and stylization
- Film grain.
- Film damage and texture.
- Vintage treatments.
- VHS-style effects.
- Analog distortion.
- Glitch and digital distortion.
- RGB separation.
- Posterization.
- Sketch or illustrative treatments.
- Other controlled stylized treatments.

### Additional shot effects
- Distortion and displacement.
- Pixelation and mosaic.
- Freeze-frame effects.
- Time-based visual effects.

Each effect must support appropriate configurable parameters, including:
- Effect type and preset.
- Intensity.
- Duration and temporal range.
- Target region.
- Mask or tracking reference.
- Effect-specific parameters.
- Processing backend.
- Render settings.
- Quality assessment.

Not every effect needs to be implemented immediately. Use a modular effect registry so capabilities can be introduced incrementally.

Do not enable specialized creative effects by default.

## 4. Subject isolation and background manipulation

Implement a reliable subject isolation and compositing foundation.

Required capabilities:

- Person segmentation.
- Object segmentation.
- Foreground/background separation.
- Background removal.
- Background replacement.
- Background blur.
- Subject-specific enhancement.
- Subject masking.
- Edge refinement.
- Hair and fine-detail handling.
- Mask tracking across frames.
- Occlusion handling.
- Multiple-subject isolation.
- Selective effects applied to isolated subjects.

Example applications:
- Blur the background behind a talking-head subject.
- Isolate a product and replace its background.
- Highlight a person or object.
- Remove distracting background elements.
- Create a controlled visual environment around a subject.

Use existing subject and object detection information from `video_intelligence` whenever suitable.

The VFX engine owns the actual segmentation, mask generation/refinement, compositing, and effect execution.

Do not repeatedly analyze footage when sufficiently accurate upstream detection or tracking data already exists.

### Mask quality requirements

Support:
- Frame-level masks.
- Temporal mask consistency.
- Edge refinement.
- Feathering and edge controls.
- Hair and fine-detail preservation.
- Transparent-object limitations and handling.
- Occlusion detection.
- Mask confidence.
- Mask tracking and reacquisition.
- Mask failure detection.

Masks must be represented as reusable, versioned dependencies wherever possible.

## 5. Object removal and visual cleanup

Implement selective removal or concealment of unwanted visual elements.

Capabilities:
- Remove distracting objects.
- Remove unwanted background elements.
- Remove temporary visual obstructions where feasible.
- Hide sensitive information.
- Remove unwanted logos or markings where permitted.
- Clean up small visual distractions.
- Repair selected areas through inpainting.
- Track removal masks across frames.
- Validate reconstructed frames for temporal and visual artifacts.

Mandatory requirements:

1. Preserve original footage.
2. Clearly distinguish actual object removal from blur, pixelation, or covering.
3. Make removal operations reversible.
4. Validate reconstructed frames and sequences.
5. Detect temporal inconsistencies, flicker, and reconstruction artifacts.
6. Record confidence and quality assessments.
7. Flag operations that cannot be performed reliably.
8. Never silently reconstruct important factual details or evidence.

If a removal operation is unreliable, retain the original footage and recommend a safer alternative, such as blur or concealment.

Privacy-related concealment must be clearly represented as concealment, not falsely described as actual removal.

## 6. Tracking-based effects

Support effects that follow subjects, objects, or selected regions throughout a shot.

Required tracking capabilities:
- Object tracking.
- Face tracking.
- Person tracking.
- Point tracking.
- Planar tracking.
- Motion tracking.
- Mask tracking.
- Perspective tracking.
- Track stabilization.
- Track loss detection.
- Occlusion handling.
- Track reacquisition.

Applications include:
- Attaching blur to a moving face.
- Following an object with a localized effect.
- Keeping an effect attached to a moving product.
- Applying selective sharpening or glow to a tracked region.
- Tracking a screen or flat surface for replacement and compositing.

### Tracking integration

Reuse tracking data from existing modules when it is sufficiently accurate and compatible.

The engine must:
- Reference upstream tracking results.
- Check tracking confidence and validity.
- Detect stale or incompatible tracking data.
- Request or perform additional tracking only when necessary.
- Handle temporary occlusion and track loss.
- Support track reacquisition.
- Validate effect alignment throughout the shot.

Never assume a tracking result remains accurate across the entire shot without validation.

## 7. Compositing and layer effects

Implement compositing operations for combining different visual sources into one shot.

Required capabilities:
- Foreground/background compositing.
- Alpha compositing.
- Masks and mattes.
- Luma keying.
- Chroma keying.
- Green-screen removal.
- Screen replacement.
- Picture-in-picture when treated as a shot-level composite.
- Split-screen footage composites.
- Image and video overlays.
- Blend modes.
- Opacity control.
- Shadows and reflections.
- Edge refinement.
- Perspective matching.
- Light and color integration between layers.

The compositing system must support:
- Layer ordering.
- Alpha and transparency.
- Mask references.
- Transform parameters.
- Blend modes.
- Perspective transforms.
- Edge treatment.
- Color and lighting integration parameters.
- Reproducible compositing graphs.

### Boundary with motion graphics

Designed animated overlays, titles, charts, and graphic compositions belong to `motion_graphics_engine`.

The VFX engine may provide compositing operations needed to integrate these visual sources into footage.

Do not rebuild the motion graphics generation system.

## 8. Motion and time-based effects

Support effects that modify the temporal behavior of footage.

Capabilities:
- Slow motion.
- Speed ramps.
- Time remapping.
- Freeze frames.
- Frame interpolation.
- Optical-flow-based motion effects.
- Selective motion blur.
- Motion trails.
- Temporal blending.
- Frame blending.
- Echo effects.
- Strobing effects.
- Reverse playback.
- Time displacement.

These operations must be coordinated with the existing timeline architecture.

### Time and synchronization requirements

A time-based effect must account for:

- Source time.
- Timeline time.
- Effect-local time.
- Source and timeline frame rates.
- Timebase.
- Variable frame rate footage.
- Available source frames and handles.
- Audio behavior.
- Synchronization.
- Optical-flow quality.
- Duration changes.

Keep source-media time, timeline time, and effect-local time separate throughout the data model and processing pipeline.

For speed ramps and time remapping:
- Define the source-to-timeline time mapping.
- Represent speed changes as structured data.
- Handle audio synchronization explicitly.
- Detect missing or insufficient source frames.
- Validate interpolation quality.
- Update downstream timing dependencies when duration changes.
- Coordinate changes with `timeline_builder`.

Do not independently modify the final timeline.

Avoid slow motion, speed ramps, or other temporal effects merely for visual variety. They must serve the action, explanation, emphasis, or narrative.

## 9. Focus and depth effects

Implement focus and depth-related effects for talking-head, product, and demonstration footage.

Capabilities:
- Simulated shallow depth of field.
- Background defocus.
- Selective focus.
- Focus transitions.
- Subject isolation.
- Depth-based blur.
- Depth maps.
- Foreground/background separation.
- Focus breathing simulation, if desired.
- Selective sharpness.

Where possible, reuse depth estimation and segmentation results from existing infrastructure.

### Quality requirements

Pay particular attention to:
- Hair.
- Hands.
- Transparent objects.
- Fine object details.
- Moving subjects.
- Subject boundaries.
- Occlusions.
- Depth discontinuities.
- Temporal stability.

Do not create obviously artificial edges or unstable blur boundaries.

If convincing separation cannot be achieved, reduce the effect or bypass it.

## 10. Stylization and creative effects

Provide optional, controlled creative treatments.

Supported treatments include:
- Film-look overlays.
- Vintage film treatments.
- Grain and texture.
- Analog distortion.
- VHS-style effects.
- Glitch and digital distortion.
- RGB separation.
- Halation.
- Bloom.
- Light leaks.
- Selective monochrome.
- Sketch or illustrative treatments.
- Posterization.
- Stylized contrast and texture.

These effects must be:
- Optional.
- Profile-driven.
- Configurable.
- Reproducible.
- Compatible with channel visual identity.
- Validated for excessive intensity and visual quality.

Do not duplicate the creative grading functionality of `color_pipeline`.

Color grading remains owned by the color pipeline. VFX may apply localized or effect-specific treatments but must not become a second grading system.

## 11. Effect selection and restraint

Separate effect recommendation from effect execution.

The engine may evaluate effect recommendations using supplied information, but it must not independently decorate every shot or override editorial decisions.

Potential inputs:
- Editing decisions.
- Story context.
- Visual quality.
- Subject and object information.
- Motion and action.
- Pacing.
- Existing visual treatment.
- Channel style profile.

Example treatment guidance:

| Situation | Possible VFX treatment |
|---|---|
| Talking-head explanation | Usually no effect; possibly subtle background separation |
| Important product detail | Selective focus or background blur |
| Distracting background | Object removal or background treatment |
| Fast-moving action | Selective motion blur |
| Product demonstration | Screen or object compositing |
| Historical footage | Optional grain or stylization |
| Privacy-sensitive footage | Tracked face or information blur |
| Dramatic visual moment | Subtle glow, light, or selective emphasis |

Support an explicit `no_effect` decision.

### Effect selection requirements

The system must:
- Accept effect recommendations from the appropriate editorial context.
- Evaluate technical feasibility.
- Evaluate compatibility with the shot.
- Respect project and channel restrictions.
- Apply configured intensity limits.
- Explain why an effect was selected, rejected, or bypassed.
- Prefer no effect when the expected benefit is insufficient.
- Avoid repetitive effects across consecutive shots.
- Avoid effects that conflict with the existing footage treatment.

Do not turn this module into another story, pacing, or editing intelligence engine.

## 12. Visual identity integration

Consume the channel visual identity and creative treatment constraints from `visual_style_engine`.

Relevant inputs include:
- Approved creative effect profiles.
- Preferred effect intensity.
- Texture and grain preferences.
- Glow and bloom preferences.
- Blur preferences.
- Motion treatment preferences.
- Restrictions on stylization.
- Format-specific overrides.

Maintain three distinct responsibilities:

- `visual_style_engine`: Defines the channel's designed visual language.
- `color_pipeline`: Defines the footage's color appearance.
- `vfx_engine`: Defines permitted shot-level visual effects.

For example, a clean and modern channel identity might favor subtle depth separation and restrained motion effects while avoiding excessive glitch, distortion, and film damage.

### Configuration inheritance

Support:
1. Global channel settings.
2. Format-specific profiles.
3. Project-level overrides.
4. Shot-specific settings where justified.

Preserve profile and identity versions for reproducibility.

Project overrides must not silently change the global channel identity.

## 13. Effect presets and profiles

Create reusable presets rather than requiring manual configuration of every effect.

Implement the following suggested profiles:

| Profile | Purpose |
|---|---|
| `clean_enhancement` | Subtle, natural shot-level improvements |
| `talking_head` | Background separation and selective effects |
| `product_detail` | Focus, isolation, and product emphasis |
| `demonstration` | Tracking, screen replacement, and compositing |
| `cinematic` | Controlled depth, glow, motion blur, and stylization |
| `vintage` | Film grain, texture, and analog effects |
| `privacy_protection` | Face, screen, and object concealment |
| `creative_effects` | Optional expressive visual treatments |

Profiles must:
- Inherit global channel settings.
- Support controlled project-level overrides.
- Define approved and restricted effects.
- Define preferred intensity ranges.
- Define effect-specific defaults.
- Be versioned and reproducible.

Do not hardcode these profiles in a way that prevents extension or configuration.

## 14. Effect specification and data contracts

Every effect must be represented as a structured, reproducible specification.

At minimum, support the following fields:

```json
{
  "effect_id": "string",
  "project_id": "string",
  "source_asset_id": "string",
  "source_segment_id": "string",
  "effect_type": "string",
  "effect_preset_id": "string",
  "effect_parameters": {},
  "target_region": {},
  "mask_reference": "string",
  "tracking_reference": "string",
  "source_start_time": 0,
  "source_end_time": 0,
  "timeline_start_time": 0,
  "timeline_end_time": 0,
  "effect_intensity": 0,
  "processing_backend": "string",
  "dependencies": [],
  "style_profile_id": "string",
  "identity_version": "string",
  "render_settings": {},
  "output_asset_reference": "string",
  "quality_assessment": {},
  "confidence": 0,
  "warnings": [],
  "configuration_hash": "string",
  "effect_version": "string"
}
```

The schema is a minimum contract, not a restriction against additional fields required by the existing architecture.

Also support:
- Explicit `no_effect` decisions.
- Effect status and execution state.
- Backend capability information.
- Source and generated asset references.
- Mask and tracking dependencies.
- Reversibility and bypass information.
- Fallback strategies.
- Validation results.
- Effect graph dependencies.
- Version and configuration metadata.

Use the repository's existing identifier, timestamp, serialization, and asset-reference conventions wherever possible.

Do not introduce a duplicate asset-reference system.

## 15. Rendering and execution architecture

Separate three stages:

1. **Effect planning:** Represent the intended effect and its parameters as structured data.
2. **Effect execution:** Translate the specification into supported native Resolve operations or external processing operations.
3. **Effect integration:** Make the resulting operation or intermediate asset available to the existing timeline pipeline.

Support multiple execution strategies.

Not every effect needs to be baked into a new video file.

Depending on the effect and backend, execution may use:
- Native DaVinci Resolve effects.
- External processing.
- Generated intermediate assets.
- Reusable effect graphs.
- Existing rendering infrastructure.

Choose execution strategies based on:
- Visual quality.
- Performance.
- Reversibility.
- Backend compatibility.
- Resource requirements.
- Timeline compatibility.
- Reproducibility.

### Backend requirements

Inspect the repository's current rendering and media-processing architecture before choosing or introducing technologies.

Do not introduce an unnecessary new rendering framework.

Use a replaceable backend abstraction so the effect specification is not tightly coupled to DaVinci Resolve or one external processing library.

The backend must expose its supported effects and limitations.

Unsupported operations must produce explicit errors or documented fallback behavior, not silently generate incorrect results.

Preserve original footage and make every applied effect reversible or bypassable.

## 16. Quality validation

Validate effects before they reach the final timeline and rendered output.

Required checks include:

### Segmentation and masks
- Mask edge quality.
- Segmentation consistency.
- Hair and fine-detail preservation.
- Occlusion handling.
- Temporal mask stability.

### Tracking
- Tracking accuracy.
- Track loss.
- Track reacquisition.
- Effect alignment.
- Temporal tracking stability.

### Rendering and visual quality
- Temporal flicker.
- Ghosting.
- Motion artifacts.
- Inpainting artifacts.
- Background replacement errors.
- Unnatural depth effects.
- Incorrect compositing.
- Alpha-channel errors.
- Resolution loss.
- Frame-rate inconsistencies.
- Unintended color changes.
- Excessive effect intensity.
- Render failures.

### Pipeline compatibility
- Missing dependencies.
- Missing source assets.
- Invalid time ranges.
- Unsupported backend operations.
- Source/timeline timing mismatches.
- Invalid output references.
- Incompatible timeline settings.

Every effect should have a confidence or quality assessment where appropriate.

If an effect produces worse footage than the original, the system must be able to:
- Bypass the effect.
- Preserve the original.
- Record the failure or quality warning.
- Recommend a safer alternative where possible.

Return structured validation results with severity, affected frames or time ranges, explanations, and recommended actions.

## 17. Integration with existing Media House modules

Integrate with the existing architecture rather than building parallel systems.

| Module | Integration responsibility |
|---|---|
| `visual_style_engine` | Supplies visual identity and creative treatment constraints |
| `editing_intelligence` | Identifies where an effect could serve the edit |
| `video_intelligence` | Supplies subjects, objects, motion, and visual scene information |
| `continuity_intelligence` | Identifies visual inconsistencies and potential effect-related continuity issues |
| `reframing_intelligence` | Supplies framing and subject-position information |
| `video_improvement` | Handles technical image-quality correction |
| `color_pipeline` | Handles color correction and grading |
| `motion_graphics_engine` | Creates designed animated graphics and visual elements |
| `transition_engine` | Handles transitions between shots and scenes |
| `audio_visual_alignment` | Supplies timing and cross-modal alignment information |
| `timeline_builder` | Places VFX instructions and rendered assets into the timeline |
| `resolve_adapter` | Executes supported effects in DaVinci Resolve |
| `quality_control` | Checks the rendered output |

### Integration rules

- Reuse existing asset and media references.
- Reuse existing subject/object detection and tracking data where valid.
- Reuse shared keyframe/interpolation infrastructure where available.
- Reuse existing rendering and caching infrastructure where appropriate.
- Preserve source and timeline time distinctions.
- Keep effect planning independent from timeline execution.
- Ensure that timeline changes invalidate affected timing-dependent results.
- Do not directly manipulate the final Resolve timeline from `vfx_engine`.
- Do not duplicate responsibilities owned by other modules.

Define clear input/output contracts and test them against the actual existing implementations.

## 18. Suggested internal module structure

Use the following as a logical starting point, adapting it to the existing repository conventions:

```text
vfx_engine/
│
├── effect_selection/
│   ├── effect_need_detection/
│   ├── effect_recommendations/
│   ├── effect_constraints/
│   └── effect_intensity/
│
├── subject_isolation/
│   ├── segmentation/
│   ├── background_removal/
│   ├── background_replacement/
│   ├── mask_refinement/
│   └── temporal_consistency/
│
├── object_cleanup/
│   ├── object_removal/
│   ├── inpainting/
│   ├── cleanup_tracking/
│   └── artifact_validation/
│
├── tracking/
│   ├── object_tracking/
│   ├── planar_tracking/
│   ├── point_tracking/
│   ├── mask_tracking/
│   └── tracking_validation/
│
├── compositing/
│   ├── alpha_compositing/
│   ├── chroma_keying/
│   ├── luma_keying/
│   ├── screen_replacement/
│   ├── layer_compositing/
│   └── perspective_matching/
│
├── shot_effects/
│   ├── blur_and_focus/
│   ├── motion_blur/
│   ├── glow_and_bloom/
│   ├── lens_effects/
│   ├── distortion/
│   ├── grain_and_texture/
│   └── stylization/
│
├── temporal_effects/
│   ├── time_remapping/
│   ├── speed_ramping/
│   ├── frame_interpolation/
│   ├── freeze_frames/
│   └── temporal_blending/
│
├── effect_profiles/
│   ├── global_profiles/
│   ├── format_profiles/
│   ├── shot_profiles/
│   └── project_overrides/
│
├── rendering/
│   ├── effect_graph/
│   ├── render_backend/
│   ├── caching/
│   └── asset_export/
│
└── validation/
    ├── mask_validation/
    ├── tracking_validation/
    ├── temporal_validation/
    ├── compositing_validation/
    └── output_quality/
```

Do not create empty directories or placeholder modules merely to match this tree. Implement meaningful components that fit the repository's established architecture.

## 19. Performance, caching, and reliability

The engine must be suitable for real video projects, not just isolated demonstrations.

Implement or integrate:
- Dependency-aware caching.
- Incremental effect processing.
- Reuse of masks and tracking data.
- Reusable effect presets and graphs.
- Batch processing where appropriate.
- Parallel processing where safe.
- Resource-aware rendering.
- Preview-quality processing where supported.
- Final-quality rendering.
- Deterministic effect configurations.
- Render progress and execution status.
- Error reporting and recovery.
- Backend capability checks.

Cache invalidation must account for changes to:
- Source footage.
- Source segment and time range.
- Effect parameters.
- Mask data.
- Tracking data.
- Processing backend and backend version.
- Effect preset and version.
- Style profile and identity version.
- Render resolution.
- Frame rate and timebase.
- Relevant dependencies.

Do not rerender unaffected effects unnecessarily.

Never reuse stale masks, tracks, or intermediate assets after their dependencies change.

## 20. Recommended implementation priority

Implement incrementally in this order:

| Priority | Component | Importance |
|---:|---|---|
| 1 | Effect specification and backend architecture | Foundation |
| 2 | Basic shot-level effects | Core functionality |
| 3 | Masking and subject isolation | Highly useful |
| 4 | Tracking integration | Enables effects on moving subjects |
| 5 | Blur, focus, and background effects | Useful for talking-head and product videos |
| 6 | Compositing and screen replacement | Useful for tutorials and demonstrations |
| 7 | Object removal and cleanup | Specialized but valuable |
| 8 | Time-based effects | Advanced editing capabilities |
| 9 | Creative stylization | Optional visual variety |
| 10 | Validation, caching, and optimization | Production reliability |

Prioritize a small set of reliable, high-value capabilities first:
- Masking.
- Tracking.
- Background separation.
- Blur and focus.
- Compositing.
- Selective cleanup.

Keep complex cinematic effects as optional extensions.

Do not claim that a capability is production-ready until it has working implementation, integration, and tests.

## 21. Testing requirements

Create tests consistent with the existing repository's testing conventions.

Cover at least:

### Effect specifications
- Valid and invalid effect configurations.
- Parameter validation.
- Versioning.
- Explicit no-effect decisions.
- Reversibility and bypass behavior.
- Serialization and reproducibility.

### Subject isolation and tracking
- Person and object segmentation integration.
- Mask edge behavior.
- Temporal consistency.
- Occlusion handling.
- Tracking loss and reacquisition.
- Reuse and invalidation of upstream tracking data.

### Effects and compositing
- Basic shot effects.
- Background blur.
- Selective focus.
- Alpha compositing.
- Chroma and luma keying.
- Screen replacement.
- Object removal and inpainting.
- Time remapping and speed ramps.
- Audio synchronization behavior where applicable.

### Quality validation
- Mask artifacts.
- Temporal flicker.
- Ghosting.
- Inpainting artifacts.
- Compositing errors.
- Alpha-channel correctness.
- Resolution and frame-rate consistency.
- Excessive intensity.
- Unsupported operations and fallback behavior.

### Pipeline integration
- Integration with `video_intelligence`.
- Integration with `visual_style_engine`.
- Integration with `video_improvement` and `color_pipeline` boundaries.
- Integration with `timeline_builder`.
- Integration with `resolve_adapter`.
- Integration with `quality_control`.
- Correct dependency invalidation.
- No regression in existing Media House functionality.

Use representative video fixtures and synthetic test footage where appropriate. Test actual rendered outputs rather than relying exclusively on mocked function calls.

## 22. Definition of done

The implementation is complete only when:

1. `vfx_engine` exists as a functional module in the existing Media House architecture.
2. Effect specifications are structured, versioned, and reproducible.
3. Original footage remains untouched.
4. Effects can be applied, bypassed, and reversed.
5. Basic shot-level effects execute successfully.
6. Subject isolation and masking work through the actual processing pipeline.
7. Tracking-based effects can follow supported moving subjects or objects.
8. Background separation and blur produce validated results.
9. Compositing operations work with supported visual sources.
10. Object cleanup is validated and reports unreliable results.
11. Time-based effects correctly account for source and timeline timing.
12. Visual identity constraints are consumed correctly.
13. Technical image improvement and color grading remain in their existing modules.
14. Effects are represented independently from timeline placement.
15. Supported operations integrate with `timeline_builder` and `resolve_adapter`.
16. Quality validation can reject, bypass, or flag degraded effects.
17. Caching and dependency invalidation work correctly.
18. The tests pass, including integration and rendered-output tests.
19. Existing Media House functionality remains compatible.
20. No UI or duplicate subsystem has been introduced.

## 23. Final implementation instructions

Work within the existing repository and architecture.

Start by inspecting the actual implementation, identifying existing capabilities, dependencies, interfaces, and missing functionality.

Then:
1. Document the current architecture and relevant integration points.
2. Define the effect specification and backend abstraction.
3. Implement the foundational capabilities in the recommended order.
4. Integrate with existing modules through explicit contracts.
5. Add validation, caching, and error handling.
6. Add and run tests.
7. Verify real processing and rendered outputs.
8. Document supported effects, backend limitations, configuration, and execution behavior.

Do not replace existing working systems without a demonstrated technical reason.

Do not introduce a frontend, dashboard, visual editor, or other UI. This task is backend implementation only.

Do not create unnecessary abstractions, duplicate services, or speculative infrastructure.

The final result must be a reliable, reversible, modular VFX subsystem that improves selected shots when there is a clear visual reason—and leaves footage untouched when there is not.
