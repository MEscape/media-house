# IMPLEMENTATION TASK: MEDIA HOUSE — LAYER 4.2: MOTION GRAPHICS ENGINE

## 1. Project context

You are working on an existing software system called **Media House**.

Media House is an all-in-one automated video production system. It takes uploaded raw footage, analyzes the material, develops an editorial structure, processes the media and ultimately produces a complete YouTube video integrated with DaVinci Resolve.

The system already has three implemented architectural layers and an established media-processing pipeline.

Layer 4 is currently being implemented.

Your task is to implement module 18: `motion_graphics_engine`.

This is an extension of the existing system, not a standalone application.

### Existing architecture

**Layer 1 — Media foundation**
1. `media_library`
2. `media_inspection`
3. `asset_relationships`
4. `proxy_manager`

**Layer 2 — Media intelligence**
5. `audio_improvement`
6. `audio_intelligence`
7. `video_improvement`
8. `video_intelligence`
9. `content_intelligence`
10. `audio_visual_alignment`

**Layer 3 — Editorial planning**
11. `story_architecture`
12. `editing_intelligence`
13. `pacing_intelligence`
14. `broll_intelligence`
15. `continuity_intelligence`
16. `reframing_intelligence`

**Layer 4 — Creative design and asset generation**
17. `visual_style_engine` — Defines the channel's visual identity.
18. `motion_graphics_engine` — CURRENT TASK.
19. `caption_engine`
20. `transition_engine`
21. `vfx_engine`
22. `music_and_sound_design`
23. `asset_generation`

---

## 2. Core objective

Build a production-ready `motion_graphics_engine` responsible for creating, animating, rendering, validating and delivering visual graphics for Media House videos.

The engine transforms the design specifications supplied by `visual_style_engine` into actual visual elements and animations.

It must produce professional, informative, precisely timed and visually consistent graphics.

Examples include:

- Titles and headlines.
- Section headings.
- Lower thirds.
- Information panels.
- Product specifications.
- Comparison graphics.
- Statistics.
- Charts.
- Diagrams.
- Callouts.
- Annotations.
- Rankings.
- Animated labels.
- Logo animations.
- Intro and outro elements.
- Subscribe and CTA graphics.

Support static and animated graphics.

The system must work across different video formats and resolutions.

### Fundamental architectural distinction

- `visual_style_engine` defines how graphics should look.
- `motion_graphics_engine` defines how graphics are constructed, animated and rendered.
- `editing_intelligence` decides which graphics are required and when they should appear.
- `timeline_builder` places graphics on the timeline.
- `resolve_adapter` executes the timeline in DaVinci Resolve.

Do not merge these responsibilities.

The engine is an execution and rendering system, not another independent editorial decision-making agent.

---

## 3. First priority: Inspect the existing architecture

Before implementing anything, inspect the existing repository.

Understand:

- Existing module structures.
- Existing service interfaces.
- Existing data models.
- Media asset management.
- Pipeline orchestration.
- Existing rendering capabilities.
- Existing DaVinci Resolve integration.
- Existing timeline infrastructure.
- Existing configuration and dependency management.
- The previously implemented `visual_style_engine`.

Do not assume that functionality is missing simply because it is not immediately visible.

Reuse existing infrastructure wherever possible.

Do not create duplicate services, asset libraries, rendering pipelines or configuration systems.

If required functionality is missing from another module, implement it in its correct architectural owner and integrate it cleanly.

---

## 4. Integration with visual_style_engine

This is one of the most important requirements.

The `motion_graphics_engine` must consume the resolved visual identity specifications from `visual_style_engine`.

It must not independently invent:

- Brand colors.
- Typography.
- Font weights.
- Graphic styles.
- Spacing systems.
- Corner radii.
- Shadows.
- Icon styles.
- Title treatments.
- Caption design.
- Motion design principles.

All graphic components must use approved design tokens, templates and motion presets.

### Required inputs

Consume the appropriate resolved properties:

- `visual_style_id`
- `identity_id`
- `identity_version`
- `format_profile`
- `resolved_colors`
- `resolved_typography`
- `resolved_graphic_design`
- `resolved_title_styles`
- `resolved_caption_styles`
- `resolved_layout_rules`
- `resolved_motion_rules`
- `brand_assets`
- `mandatory_rules`
- `optional_preferences`
- `overrides`
- `validation_results`
- `configuration_hash`

Validate that the received style specification is compatible with the requested project.

Preserve profile provenance throughout the rendering process.

If the visual identity changes, determine whether existing cached graphics must be invalidated.

Do not silently mix assets from incompatible identity versions.

---

## 5. Graphic generation system

Implement a reusable graphic generation architecture.

Support the following graphic categories:

### Text graphics
- Titles.
- Headlines.
- Section headings.
- Chapter cards.
- Labels.
- Text panels.
- Important statements.
- Numerical displays.

### Shape graphics
- Rectangles.
- Rounded rectangles.
- Circles.
- Lines.
- Dividers.
- Arrows.
- Geometric elements.
- Animated shapes.

### Image graphics
- Product images.
- Logos.
- Illustrations.
- Background images.
- Reference images.
- Image-based information panels.

### Icon graphics
- Brand icons.
- Informational icons.
- Product indicators.
- Status indicators.
- Custom approved icon assets.

### Composition generation

Allow multiple elements to be combined into one composition.

Support static and animated output.

Every generated composition must be reusable and independently configurable.

---

## 6. Animation engine

Build a reusable animation system.

Support keyframe-based animation and interpolation.

### Animated properties

- Position.
- Scale.
- Rotation.
- Opacity.
- Anchor point.
- Mask.
- Clipping.
- Shape transformation.
- Path movement.
- Blur.
- Glow.

### Animation types

- Fade in/out.
- Slide in/out.
- Scale in/out.
- Pop and bounce.
- Typewriter.
- Word-by-word reveal.
- Character-by-character reveal.
- Mask reveal.
- Highlight animation.
- Number counting.
- Coordinated multi-element animation.

### Easing

Support:

- Linear.
- Ease-in.
- Ease-out.
- Ease-in-out.
- Cubic Bézier.
- Spring-like motion.

### Animation configuration

Every animation should support:

- Start value.
- End value.
- Duration.
- Delay.
- Direction.
- Easing.
- Interpolation.
- Stagger.
- Keyframes.
- Loop configuration.

Support coordinated animation across multiple graphic elements.

Avoid unnecessary complexity and distracting movements.

All animations must follow the motion rules supplied by `visual_style_engine`.

---

## 7. Text animation and typography rendering

Implement a robust text rendering and animation subsystem.

Support:

- Dynamic text content.
- Multi-line text.
- Different font weights.
- Text alignment.
- Letter spacing.
- Line spacing.
- Text wrapping.
- Highlighted words.
- Dynamic numerical values.
- Animated counters.
- Text masks.
- Character and word animations.

Important requirements:

- Prevent clipping and overflow.
- Prevent unintended line breaks.
- Maintain readable font sizes.
- Respect safe margins.
- Handle dynamic text lengths.
- Validate font availability.
- Preserve typography specifications.
- Support font fallbacks only according to approved rules.

The rendering system must produce predictable results at the final output resolution.

---

## 8. Information graphics and data visualization

Implement reusable information visualization components.

Support:

- Bar charts.
- Line charts.
- Pie charts.
- Donut charts.
- Progress bars.
- Timelines.
- Rankings.
- Top 10 lists.
- Comparisons.
- Before-and-after displays.
- Numerical counters.
- Percentage displays.
- Process diagrams.
- Flowcharts.
- Feature breakdowns.

Support structured input data.

Animate chart construction and data changes where appropriate.

Preserve:
- Numerical accuracy.
- Labels.
- Units.
- Data relationships.
- Correct proportions.
- Readability.

Never fabricate missing data or alter values to improve visual appearance.

Use the approved visual design tokens from `visual_style_engine`.

Templates must be reusable with different datasets.

---

## 9. Callouts, highlights and annotations

Implement a system for visually highlighting important elements.

Support:

- Arrows.
- Circles.
- Outlines.
- Rectangles.
- Underlines.
- Pointer lines.
- Spotlight effects.
- Magnification windows.
- Focus frames.
- Animated markers.

Integrate with:

- `video_intelligence`
- `reframing_intelligence`
- `audio_visual_alignment`
- Existing tracking capabilities.

Support annotations attached to:

- People.
- Products.
- Objects.
- Moving subjects.
- Screen regions.
- Interface elements.
- Demonstration areas.

Where tracking data is available, use it to maintain the correct position throughout a shot.

Do not create a duplicate object tracking system if an existing module already provides the required tracking data.

Handle tracking loss and invalid tracking references explicitly.

---

## 10. Product and demonstration graphics

Provide reusable components for product-oriented content.

Support:

- Product name and model.
- Specifications.
- Prices.
- Feature indicators.
- Pros and cons.
- Product comparisons.
- Dimensions.
- Measurements.
- Component labels.
- Product ratings.
- Ranking displays.
- Feature explanations.
- Before-and-after comparisons.
- Animated pointers to buttons, ports and components.

Templates must accept dynamic product data without requiring a new design for every product.

Preserve units, values and source references.

---

## 11. Logo and brand animations

Support:

- Channel logo reveals.
- Logo animations.
- Outro animations.
- Watermark appearances.
- Recurring brand elements.
- Format-specific logo variations.

Allow configuration of:

- Position.
- Size.
- Opacity.
- Duration.
- Animation.
- Logo variant.

Always prioritize existing approved assets from the brand asset library.

Do not generate replacement logos when approved assets already exist.

---

## 12. Layering and compositing

Implement reusable composition and layer management.

Support:

- Layer order.
- Z-index.
- Transparency.
- Alpha channels.
- Masks.
- Clipping.
- Blending modes.
- Shadows.
- Outlines.
- Glow.
- Blur.
- Background panels.

Allow compositions containing:

- Text.
- Shapes.
- Images.
- Icons.
- Multiple animated elements.

Prevent unintended overlap and preserve visual hierarchy.

Support transparent exports suitable for compositing over recorded footage.

Keep complex VFX work within the appropriate future `vfx_engine` boundary.

---

## 13. Audio-reactive and event synchronization

Graphics must be synchronizable with meaningful events.

Support synchronization with:

- Spoken words.
- Important statements.
- Emphasized phrases.
- Music beats.
- Downbeats.
- Sound effects.
- Product reveals.
- Narrative events.

Use existing data from:

- `audio_intelligence`
- `audio_visual_alignment`
- `editing_intelligence`
- `pacing_intelligence`

Examples:

- A statistic appears exactly when it is spoken.
- A callout highlights a product feature during its explanation.
- A graphic enters at a meaningful musical moment.
- A numerical counter finishes when the corresponding statement concludes.

Avoid animating every word or every beat.

Only use synchronization when it adds clarity or meaningful emphasis.

### Timing integrity

Maintain correct synchronization when timeline positions change.

Use stable event references instead of relying exclusively on hardcoded absolute timestamps.

---

## 14. Template and preset system

Implement a reusable template architecture.

Templates should cover:

- Top 10 rankings.
- Product reviews.
- Product comparisons.
- Unboxing.
- Talking-head lower thirds.
- Tutorial step indicators.
- Statistics and facts.
- Chapter transitions.
- Intro and outro.
- Subscribe and CTA graphics.
- Shorts and Reels.
- Documentary information cards.

Each template must support:

- Dynamic text.
- Dynamic numerical values.
- Dynamic images.
- Dynamic icons.
- Approved fonts and colors.
- Configurable timing.
- Animation presets.
- Aspect-ratio variants.
- Global visual identity inheritance.
- Format-specific overrides.

Track template versions and dependencies.

Avoid duplicating templates for minor content differences.

---

## 15. Responsive layouts and aspect-ratio adaptation

Support:

- 16:9 YouTube.
- 9:16 Shorts and Reels.
- 1:1 square.
- Custom resolutions.

Do not simply scale a 16:9 composition into 9:16.

Create adaptive layouts that intelligently reposition and reorganize elements.

Integrate with `reframing_intelligence`.

Consider:

- Subject position.
- Face positions.
- Products.
- Hands.
- Important screen regions.
- Existing footage composition.
- Subtitle safe areas.
- Platform-specific safe areas.

Validate readability at the final resolution.

If no valid layout can be generated without obscuring important content, return a validation warning rather than producing an unacceptable composition.

---

## 16. Precise timing system

Every graphic must have accurate timing information.

Support:

- Start time.
- End time.
- Duration.
- Entrance animation.
- Hold duration.
- Exit animation.
- Delays.
- Stagger intervals.
- Overlapping animations.

Support frame-accurate timing.

### Important time architecture

Keep these separate:

1. Source-media time.
2. Timeline time.
3. Graphic-local animation time.

Use explicit timebases and frame-rate information.

Do not mix seconds, frames and timeline ticks without explicit conversions.

Support variable source frame rates where the existing media infrastructure requires them.

Account for timeline changes, clip movement and editing revisions.

The graphic's timing must remain correct when the timeline is rebuilt.

---

## 17. Rendering architecture

Build a rendering system that is independent of DaVinci Resolve wherever practical.

The engine should generate graphics and animation assets that can be consumed by the existing timeline infrastructure.

Support:

- Static image exports.
- Transparent image exports.
- Animated graphic exports.
- Alpha-channel preservation.
- Resolution-specific rendering.
- Frame-rate-specific rendering.
- Preview rendering.
- Final-quality rendering.
- Batch rendering.
- Parallel rendering where supported.

### Rendering backend

Keep the rendering backend replaceable.

Evaluate existing project capabilities before choosing or introducing a technology.

Potential approaches include:

- SVG-based rendering.
- HTML/CSS-based composition rendering.
- Programmatic animation rendering.
- A suitable video-compositing framework.

Do not introduce multiple rendering technologies unnecessarily.

Choose the smallest reliable architecture that satisfies the requirements.

Preserve:
- Accurate typography.
- Alpha transparency.
- Color consistency.
- Deterministic output.
- Frame accuracy.
- Reproducibility.

---

## 18. Performance and caching

Implement intelligent caching.

Requirements:

- Cache rendered graphics.
- Cache animation outputs where appropriate.
- Re-render only when relevant dependencies change.
- Reuse identical templates.
- Reuse approved assets.
- Support batch processing.
- Support parallel rendering when resources allow.
- Use preview quality during development and editing.
- Generate final-quality assets when required.
- Track rendering dependencies.

A cached asset must be invalidated when a relevant dependency changes.

Relevant dependencies include:

- Graphic content.
- Template version.
- Visual identity version.
- Font files.
- Images.
- Animation specifications.
- Timing.
- Resolution.
- Frame rate.
- Rendering backend version.

Use configuration hashes or an equivalent deterministic dependency mechanism.

---

## 19. Graphic quality validation

Every graphic should be validated before delivery to the timeline.

Check:

**Visual quality**
- Text clipping.
- Text overflow.
- Missing fonts.
- Incorrect colors.
- Incorrect typography.
- Low-resolution assets.
- Incorrect aspect ratios.
- Invalid transparency.
- Overlapping elements.
- Safe-area violations.
- Poor contrast.
- Unreadable text.

**Animation quality**
- Incorrect timing.
- Unintended jumps.
- Abrupt animation endings.
- Incorrect interpolation.
- Excessive complexity.
- Frame-rate inconsistencies.
- Broken animation references.

**Technical quality**
- Missing assets.
- Broken references.
- Invalid export settings.
- Incorrect alpha channels.
- Failed rendering.
- Incompatible output formats.

Return structured validation results.

Every issue should contain:
- Severity.
- Affected graphic.
- Affected property.
- Expected value.
- Actual value.
- Recommended correction.

Support automatic correction where safe and deterministic.

Do not silently override mandatory brand rules.

---

## 20. Integration with the existing Media House architecture

Integrate the engine into the existing media pipeline.

Required integrations:

**`visual_style_engine`**

Provides the resolved visual identity, design tokens, typography, layouts and motion rules.

**`editing_intelligence`**

Provides the decisions about which graphics are needed and their intended usage.

**`story_architecture`**

Provides narrative context and important story moments.

**`content_intelligence`**

Provides factual content, statistics and information for visualization.

**`audio_intelligence`**

Provides speech timing, emphasis and audio events.

**`audio_visual_alignment`**

Connects graphics to spoken content and visual events.

**`broll_intelligence`**

Helps avoid graphics competing with important supporting footage.

**`reframing_intelligence`**

Provides composition and safe-area information.

**`pacing_intelligence`**

Provides timing guidance and appropriate display duration.

**`continuity_intelligence`**

Helps maintain consistency across adjacent shots.

**`caption_engine`**

Remains responsible for transcription, segmentation, subtitle content and timing.

The motion graphics engine may provide reusable rendering and animation capabilities for captions through explicit interfaces, but must not take over caption generation.

**`timeline_builder`**

Receives the generated graphic assets, animation metadata, timing references and placement specifications.

It remains responsible for timeline placement.

**`resolve_adapter`**

Consumes the existing timeline representation and executes it in DaVinci Resolve.

The motion graphics engine must not directly manipulate Resolve timelines.

**`quality_control`**

Receives validation information and checks the final rendered result.

### Missing functionality

If an integration requires missing functionality:

1. Identify the missing capability.
2. Determine the correct architectural owner.
3. Inspect whether an existing implementation can be extended.
4. Implement the missing functionality in the correct module.
5. Preserve existing interfaces and backward compatibility.
6. Add tests.
7. Connect it to the existing pipeline.
8. Document the dependency.

Do not implement duplicate functionality inside `motion_graphics_engine` merely to avoid extending an existing module.

---

## 21. Internal module organization

Use the following as a logical reference architecture:

motion_graphics_engine/

- graphic_generation/
  - text_graphics/
  - shape_graphics/
  - image_graphics/
  - icon_graphics/
  - composition_generation/

- animation_engine/
  - keyframe_system/
  - interpolation/
  - easing_curves/
  - motion_presets/
  - text_animation/
  - shape_animation/
  - coordinated_animation/

- graphic_components/
  - titles/
  - lower_thirds/
  - information_panels/
  - callouts/
  - labels/
  - charts/
  - rankings/
  - comparisons/
  - logo_animations/

- data_visualization/
  - charts/
  - statistics/
  - progress_indicators/
  - timelines/
  - diagrams/

- tracking_and_annotations/
  - object_tracking/
  - subject_tracking/
  - screen_annotations/
  - tracking_alignment/

- compositing/
  - layer_management/
  - masks/
  - blending/
  - transparency/
  - visual_effects/

- synchronization/
  - speech_sync/
  - music_sync/
  - beat_sync/
  - event_sync/

- templates/
  - youtube/
  - shorts/
  - top_10/
  - product_review/
  - unboxing/
  - tutorial/
  - talking_head/

- responsive_layout/
  - aspect_ratio_adaptation/
  - safe_area_handling/
  - layout_validation/

- rendering/
  - render_profiles/
  - asset_export/
  - alpha_export/
  - caching/
  - render_optimization/

- validation/
  - visual_validation/
  - timing_validation/
  - asset_validation/
  - consistency_validation/

Adapt this structure to the existing repository conventions. It is not permission to create unnecessary directories or duplicate existing services.

---

## 22. Output contract

The engine must produce structured graphic and animation specifications.

It should not directly manipulate the editing timeline.

Every graphic must have a structured representation containing:

- `graphic_id`
- `project_id`
- `graphic_type`
- `template_id`
- `template_version`
- `visual_style_id`
- `identity_version`
- `content_data`
- `asset_references`
- `composition`
- `layer_definitions`
- `animation_specification`
- `start_time`
- `end_time`
- `duration`
- `frame_rate`
- `resolution`
- `aspect_ratio`
- `alpha_channel`
- `render_settings`
- `output_asset_path`
- `validation_results`
- `configuration_hash`
- `generation_version`

Animation specifications must additionally support:

- `property`
- `keyframes`
- `interpolation`
- `easing`
- `start_value`
- `end_value`
- `duration`
- `delay`
- `stagger`
- `loop_settings`

Use typed schemas and explicit validation.

Include stable references to source events and timeline events wherever necessary.

The output must be usable by the existing `timeline_builder` without requiring it to reinterpret graphic design decisions.

---

## 23. Implementation order

Follow this implementation sequence:

1. Inspect the existing repository and architecture.
2. Map the existing pipeline interfaces.
3. Inspect and integrate `visual_style_engine`.
4. Define graphic and animation data contracts.
5. Implement the reusable template system.
6. Implement text and title graphics.
7. Implement the animation engine.
8. Implement lower thirds and information panels.
9. Implement graphic composition and layering.
10. Implement callouts and annotations.
11. Implement charts and data visualization.
12. Implement responsive layouts.
13. Implement audio and event synchronization.
14. Integrate existing tracking capabilities.
15. Implement the rendering backend.
16. Implement caching and performance optimization.
17. Implement graphic quality validation.
18. Integrate with `timeline_builder`.
19. Integrate with the existing media pipeline.
20. Run automated tests and end-to-end integration tests.

Prioritize a working, reliable foundation over implementing every advanced feature superficially.

---

## 24. Testing and definition of done

Test the actual engine, not just the presence of files or classes.

Verify:

- Static graphics can be generated.
- Animated graphics can be rendered.
- Typography follows the visual identity.
- Colors follow approved design tokens.
- Templates accept dynamic content.
- Multiple elements can be composited.
- Alpha transparency is preserved.
- Animations are frame-accurate.
- Timing remains correct after timeline changes.
- Responsive layouts work across supported aspect ratios.
- Data visualizations preserve numerical accuracy.
- Callouts can use existing tracking information.
- Audio/event synchronization works.
- Cached assets are reused correctly.
- Dependency changes invalidate relevant cached assets.
- Validation catches common rendering problems.
- Downstream modules receive correct output contracts.
- Timeline integration works.
- DaVinci Resolve integration remains compatible.
- Existing Media House functionality is not broken.

Test different formats and realistic production scenarios.

The implementation is complete only when the engine successfully generates usable graphics and animations through the existing Media House pipeline.

Do not report completion based merely on code generation.

---

## 25. Strict scope boundaries

This task does not authorize:

- Building a new user interface.
- Rebuilding Media House.
- Reimplementing Layers 1–3.
- Redesigning the channel's visual identity.
- Making editorial decisions.
- Deciding which graphics should appear.
- Building a replacement timeline system.
- Directly controlling DaVinci Resolve.
- Implementing a complete subtitle engine.
- Implementing a complete VFX engine.
- Implementing music and sound design.
- Building a duplicate asset management system.

This is a backend-first implementation.

No UI is required.

---

## 26. Final architectural principle

The `motion_graphics_engine` is the graphics production system of Media House.

It receives approved visual design rules, editorial requirements and content data.

It transforms them into reusable, animated, precisely timed, technically validated graphic assets.

It then delivers these assets and their metadata to the existing timeline infrastructure.

**The goal is not to create random AI-generated graphics. The goal is to build a reliable graphics production engine that consistently transforms the creator's established visual identity into professional, reusable and synchronized video graphics.**

Every graphic should feel like it belongs to the same channel, regardless of the video topic or format.

Implement it cleanly within the existing Media House architecture and media pipeline.
