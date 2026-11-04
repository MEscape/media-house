# IMPLEMENTATION TASK: MEDIA HOUSE — LAYER 4.1: VISUAL STYLE ENGINE

## 1. Project context

You are working on an existing, partially implemented software system called **Media House**.

Media House is an all-in-one automated video production system. Its purpose is to take uploaded raw footage, understand the material, plan the editorial structure, process the assets, and ultimately produce a complete, professional YouTube video integrated with DaVinci Resolve.

The system already has three implemented architectural layers and an established media-processing pipeline.

Your task is to continue developing the existing system, not to create a separate application or rebuild existing functionality.

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

These layers already provide the media understanding, editorial decisions, narrative structure and editing instructions needed by the production system.

**Layer 4 — Creative design and asset generation**

17. `visual_style_engine` — THIS IS YOUR CURRENT TASK.
18. `motion_graphics_engine`
19. `caption_engine`
20. `transition_engine`
21. `vfx_engine`
22. `music_and_sound_design`
23. `asset_generation`

The current task is exclusively the implementation of module 17: `visual_style_engine`.

Do not implement the other Layer 4 modules yet.

---

## 2. Core objective

Build a production-ready `visual_style_engine` that establishes, manages and distributes the visual identity of the entire YouTube channel.

The most important objective is long-term visual consistency.

If Media House produces 10 different videos, viewers should immediately recognize that all 10 videos belong to the same creator.

The videos may cover completely different subjects, contain different footage, use different narrative structures and require different graphic elements.

Nevertheless, they must share a recognizable visual DNA.

The system must establish this identity through concrete, reusable and technically enforceable design rules.

Do not allow individual production modules or AI agents to invent a new visual identity for every project.

**The channel identity must be established centrally and reused throughout the entire production pipeline.**

---

## 3. First priority: Analyze the actual YouTube channel

Before implementing the visual identity, analyze the existing channel:

https://youtube.com/@mescapyt?si=p9-lqYwRdNq7FCtu

This is the primary visual reference.

Do not immediately invent a visual style based on generic design conventions.

First, investigate how the creator actually presents videos.

### Channel analysis requirements

Inspect as many representative public videos as possible.

Prioritize:
- Recent uploads.
- Videos with different subjects.
- Videos with different footage and editing situations.
- Recurring visual elements.
- Opening sequences.
- Talking-head sections, if present.
- B-roll and illustrative sequences.
- Titles and text overlays.
- Subtitles and captions.
- Transitions and motion graphics.
- Closing sequences.

Analyze the following:

**A. Overall visual identity**
- General aesthetic.
- Visual personality.
- Cinematic versus minimalistic presentation.
- Overall atmosphere.
- Visual complexity.
- Lighting characteristics.
- Contrast and saturation.
- Recurring visual patterns.

**B. Color identity**
- Dominant colors.
- Secondary colors.
- Accent colors.
- Typical color combinations.
- Background colors.
- Text and highlight colors.
- Whether the creator consistently uses particular color treatments.

**C. Typography**
- Fonts or closest identifiable font matches.
- Font weights.
- Headline treatments.
- Text sizes and hierarchy.
- Capitalization.
- Text positioning.
- Emphasis techniques.

**D. Graphic language**
- Shapes.
- Borders.
- Shadows.
- Panels.
- Icons.
- Decorative elements.
- Information graphics.
- Repeated graphic components.

**E. Composition**
- Subject positioning.
- Framing.
- Negative space.
- Text positioning.
- Visual hierarchy.
- Typical shot compositions.
- Relationship between footage and graphic overlays.

**F. Motion identity**
- Animation speed.
- Movement direction.
- Easing.
- Transition characteristics.
- Zoom and camera movement patterns.
- Animation intensity.
- Recurring motion patterns.

**G. Editing-related visual characteristics**

Identify recurring visual presentation patterns that should be communicated to downstream production engines.

Do not take over editorial decisions already handled by Layer 3.

### Important analysis rule

Separate actual observations from assumptions.

For every important design decision, record:
- Observed characteristic.
- Reference video or timestamp where available.
- Confidence level.
- Whether it is a recurring pattern or an isolated occurrence.
- Recommended implementation rule.

If the channel cannot be accessed, do not fabricate its visual identity.

Use available browser, video inspection, image extraction or existing media tools where possible. Report access limitations and identify exactly which additional references are required.

### Result

Create a documented Channel Visual Identity Profile based on the actual creator.

The profile must become the foundation of the entire visual production system.

---

## 4. Central Channel Visual Identity Profile

Implement a reusable, persistent visual identity profile.

It must contain:

- Channel name and identity.
- Brand description.
- Visual personality.
- Target audience, where supported.
- Desired viewer emotions.
- Overall visual atmosphere.
- Recognizable visual characteristics.
- Design language.
- Reference videos and extracted examples.
- Mandatory design rules.
- Optional design preferences.
- Prohibited visual elements.

Translate abstract characteristics into measurable and reusable properties.

For example, a description such as "modern, premium and cinematic" must become actual rules for typography, colors, composition, spacing and graphic treatments.

---

## 5. Color system

Implement a centralized color design system.

Support:
- Primary colors.
- Secondary colors.
- Accent colors.
- Background colors.
- Text colors.
- Highlight colors.
- Information and warning colors.
- Chart and diagram colors.
- Subtitle colors.
- Lower-third colors.

Store exact HEX, RGB and optionally HSL values.

Define:
- Approved color combinations.
- Background and foreground relationships.
- Contrast requirements.
- Light and dark variants.
- Video-specific accent color exceptions.
- Rules preventing arbitrary color introduction.

Keep graphic design colors separate from recorded footage color correction and grading.

`video_improvement` and the color pipeline remain responsible for footage processing.

---

## 6. Typography system

Create a centralized typography configuration.

Support:
- Primary font.
- Secondary font.
- Optional display font.
- Headline styles.
- Subheadings.
- Body text.
- Captions.
- Numbers and statistics.
- Labels.
- Font weights.
- Letter spacing.
- Line spacing.
- Text alignment.
- Capitalization.

Define resolution-independent typography rules.

Support:
- Font availability detection.
- Local font files.
- Font version and fingerprint tracking.
- Licensing information.
- Font fallback behavior.
- Mobile readability.
- Maximum line lengths.
- Text hierarchy.

Never silently substitute an unavailable font without recording the substitution.

---

## 7. Graphic design language

Establish reusable design tokens and component specifications.

Define:
- Shape language.
- Corner radius.
- Border thickness.
- Shadow styles.
- Outline styles.
- Panel backgrounds.
- Gradients.
- Transparency.
- Dividers.
- Icon styles.
- Illustration styles.
- Spacing.
- Padding.
- Margins.
- Alignment.
- Visual hierarchy.

Recurring components must use the same underlying design specifications across different videos.

---

## 8. Title and headline identity

Define reusable styles for:

- Opening titles.
- Section headings.
- Chapter titles.
- Important statements.
- Product names.
- Specifications.
- Comparisons.
- Conclusions.
- Calls to action.

Each style must specify:
- Typography.
- Color.
- Placement.
- Background treatment.
- Maximum line count.
- Safe margins.
- Emphasis rules.
- Animation guidance.

The engine defines the design specification. `motion_graphics_engine` performs the actual rendering and animation.

---

## 9. Caption identity

Define the visual rules for captions.

Support:
- Standard subtitles.
- Word-by-word captions.
- Emphasized captions.
- Speaker labels.
- Short-form captions.

Configure:
- Font.
- Size.
- Weight.
- Colors.
- Highlight treatment.
- Background or outline.
- Shadow.
- Position.
- Maximum characters per line.
- Maximum lines.
- Line spacing.
- Punctuation conventions.
- Platform safe areas.

Captions must remain readable on mobile devices and must not unnecessarily cover faces, products or important visual information.

Important boundary:

`caption_engine` handles transcription, timing and actual caption generation.

`visual_style_engine` provides the approved visual caption specifications.

---

## 10. Composition and layout identity

Create reusable layout rules for:

- Talking-head videos.
- Product demonstrations.
- Comparisons.
- Tutorials.
- Screen recordings.
- Top 10 videos.
- Shorts and Reels.
- Documentary-style content.

Define:
- Grid system.
- Safe areas.
- Margins.
- Alignment.
- Negative space.
- Text positioning.
- Lower-third positioning.
- Information-panel positioning.
- Split-screen layouts.
- Chart layouts.
- Visual balance.

Integrate with `reframing_intelligence`.

The system must ensure that important subjects are not hidden by titles, subtitles or graphic overlays.

Do not independently make new framing decisions when Layer 3 already provides the required composition instructions.

---

## 11. Motion design identity

Define consistent motion design principles.

Configure:
- Animation speed.
- Easing.
- Motion direction.
- Entrance and exit behavior.
- Text animation preferences.
- Graphic reveal behavior.
- Camera movement preferences.
- Animation intensity.
- Motion blur.
- Repeated animation patterns.

The goal is recognizable motion behavior without making every video identical.

Boundary:

`visual_style_engine` defines motion rules.

`motion_graphics_engine` and `transition_engine` execute them.

---

## 12. Recurring brand assets

Implement a central reusable brand asset library.

Support:
- Channel logos.
- Logo variations.
- Intro and outro elements.
- Watermarks.
- Brand icons.
- Background patterns.
- Lower-third templates.
- Title backgrounds.
- Section separators.
- Information panels.
- Reusable illustrations.
- Other recurring visual motifs.

Requirements:
- Preserve original files.
- Track versions.
- Track dependencies.
- Define permitted usage.
- Define placement and transparency.
- Support format variants.
- Reuse existing assets instead of regenerating them unnecessarily.

Integrate with the existing `media_library` and `asset_relationships` infrastructure.

Do not create a parallel asset management system.

---

## 13. Visual consistency across projects

This is a critical requirement.

Implement a visual consistency validation system.

It must detect deviations in:
- Colors.
- Fonts.
- Font weights.
- Graphic styles.
- Spacing.
- Logo usage.
- Caption appearance.
- Title design.
- Animation characteristics.
- Recurring components.

Distinguish between three types of consistency:

**Brand consistency**

Properties that must remain recognizable across all videos.

**Format consistency**

Properties that should follow a particular video format.

**Creative variation**

Properties that may change to make individual videos more interesting.

Support controlled experimentation.

Detect gradual visual style drift across multiple projects.

Keep historical profile versions so previously produced videos remain associated with their original visual identity.

---

## 14. Format-specific profiles

The global channel identity is the foundation.

Implement inheritable profiles for:

- YouTube main videos.
- Shorts and Reels.
- Top 10 videos.
- Product reviews.
- Unboxing.
- Tutorials.
- Talking-head videos.
- Documentaries.
- Future custom formats.

Each format profile must inherit global settings and override only explicitly defined properties.

Do not duplicate the complete global configuration.

Support:
- Explicit inheritance.
- Property-level overrides.
- Validation of overrides.
- Project-specific exceptions.

A Shorts profile, for example, may use larger captions and more prominent text while retaining the same brand typography, colors and graphic language.

---

## 15. Configuration and profile management

Store all visual identity settings in structured, editable and versioned configuration files.

Implement:

- Global channel settings.
- Format-specific settings.
- Project-specific overrides.
- Mandatory rules.
- Optional preferences.
- Locked properties.
- Configuration validation.
- Import and export.
- Profile versioning.
- Change history.
- Asset and font validation.

Suggested organization:

visual_style_engine/
- profiles/
  - channel_identity/
  - format_profiles/
  - project_overrides/
- brand_assets/
- color_system/
- typography/
- graphic_design/
- title_styles/
- caption_styles/
- layout_system/
- motion_design_rules/
- consistency_validation/
- profile_manager/

Adapt this structure to the existing repository conventions rather than forcing a new architecture.

---

## 16. Integration with the existing system

This is a major implementation requirement.

Do not build the engine as an isolated module.

Inspect the existing repository, architecture, service interfaces, data models, configuration system and pipeline orchestration before implementation.

Identify how existing modules communicate.

Use existing interfaces and infrastructure wherever possible.

### Required integrations

`media_library`
- Retrieve and reuse existing brand assets.
- Store and organize new approved visual references and assets.

`media_inspection`
- Use available metadata and technical information when relevant.

`asset_relationships`
- Link visual identity profiles, brand assets, projects and their dependencies.

`proxy_manager`
- Reuse optimized media for reference inspection when supported.

`video_improvement`
- Keep graphic design rules separate from actual footage enhancement.

`content_intelligence`
- Consume content context when determining applicable visual formats.

`story_architecture`
- Consume narrative context for selecting appropriate visual treatments.

`editing_intelligence`
- Receive visual design specifications for planned editorial elements.

`pacing_intelligence`
- Supply motion preferences without overriding existing pacing decisions.

`broll_intelligence`
- Support visual consistency of selected and generated supporting assets without changing its selection decisions.

`continuity_intelligence`
- Share recurring visual rules where relevant.

`reframing_intelligence`
- Exchange layout, safe-area and composition constraints.

`motion_graphics_engine`
- Provide approved design tokens and graphic specifications.

`caption_engine`
- Provide approved caption styles.

`transition_engine`
- Provide approved transition design preferences.

`vfx_engine`
- Provide visual identity constraints for applicable effects.

`asset_generation`
- Provide visual references and approved style specifications for generated assets.

`quality_control`
- Provide machine-checkable visual identity requirements.

### Missing functionality rule

If the implementation requires functionality that does not yet exist in an earlier layer, do not work around it with duplicated logic.

First inspect whether an existing module can be extended.

If functionality is genuinely missing:

1. Identify the missing capability.
2. Identify the correct architectural owner.
3. Implement or extend that capability in the appropriate existing module.
4. Preserve backward compatibility.
5. Add or update tests.
6. Integrate it into the existing media pipeline.
7. Document the dependency.

Do not introduce unnecessary functionality into previous layers.

Do not create duplicate services, asset libraries, configuration stores or processing pipelines.

---

## 17. Output contract for downstream modules

The engine must provide resolved visual specifications, not merely a path to a configuration file.

Create a typed, validated output contract containing:

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

Include appropriate asset references, font metadata, output dimensions and profile provenance where required.

Every downstream module should receive only the relevant resolved specifications through a stable interface.

Downstream modules must not independently invent brand colors, fonts, graphic styles or title designs.

---

## 18. Technical quality and validation

Implement automated validation for:

- Schema correctness.
- Configuration completeness.
- Profile inheritance.
- Invalid overrides.
- Missing fonts.
- Missing assets.
- Asset version mismatches.
- Color compliance.
- Typography compliance.
- Contrast and readability.
- Safe-area compliance.
- Output dimensions.
- Brand consistency.

Support different validation strictness levels.

Produce actionable validation reports with:
- Detected issue.
- Affected property.
- Expected value.
- Actual value.
- Severity.
- Recommended correction.

Do not silently modify mandatory design rules to make invalid outputs pass.

---

## 19. Implementation sequence

Follow this order:

1. Inspect the existing repository and understand the architecture.
2. Map all relevant existing modules and pipeline interfaces.
3. Analyze the actual YouTube channel and extract its visual identity.
4. Create the initial Channel Visual Identity Profile.
5. Implement the color system.
6. Implement typography.
7. Implement the graphic design system.
8. Implement title and caption specifications.
9. Implement composition and layout rules.
10. Implement motion design rules.
11. Integrate the brand asset library with existing media infrastructure.
12. Implement format profiles and inheritance.
13. Implement versioning and project overrides.
14. Implement visual consistency validation.
15. Integrate the engine with the existing media pipeline.
16. Run integration tests and end-to-end pipeline tests.

Do not skip repository inspection or channel analysis.

---

## 20. Testing requirements

Verify that:

- A channel identity can be created and stored.
- The identity can be reused across multiple projects.
- Format profiles correctly inherit global settings.
- Project overrides work without modifying global settings.
- Locked properties cannot be changed by downstream modules.
- Missing fonts and assets are detected.
- Resolved specifications are correctly delivered to downstream modules.
- Existing modules continue working.
- Existing pipeline functionality is not broken.
- Multiple videos retain the same visual identity.
- Controlled creative variations remain possible.
- Historical profile versions remain accessible.

Test with multiple different hypothetical video formats and projects, not just one example.

---

## 21. Strict scope boundaries

This implementation must not:

- Build a new user interface.
- Rebuild the existing Media House system.
- Reimplement Layers 1–3.
- Make new story or editing decisions.
- Perform footage color correction.
- Generate or animate titles itself.
- Generate or time captions itself.
- Execute transitions itself.
- Perform actual VFX compositing.
- Implement music placement.
- Duplicate existing asset management.
- Implement other Layer 4 engines prematurely.

The current objective is the visual identity definition, management, distribution and validation infrastructure.

The system is backend-first.

No UI is required or authorized as part of this task.

---

## 22. Definition of done

The implementation is complete only when:

1. The actual channel has been analyzed and its visual identity documented, with evidence and confidence levels.
2. A reusable Channel Visual Identity Profile exists.
3. The profile contains concrete and machine-readable design rules.
4. Global, format and project-level configurations work.
5. Inheritance and versioning work correctly.
6. Existing brand assets are integrated into the existing media infrastructure.
7. Downstream engines can consume resolved visual specifications.
8. Visual consistency can be validated automatically.
9. Missing dependencies are implemented in their correct architectural owners.
10. The engine is integrated into the existing media pipeline.
11. Automated tests pass.
12. Existing functionality remains intact.

Do not report completion simply because the module's files have been created.

Demonstrate that the engine actually works within Media House.

---

## 23. Final architectural principle

Think of `visual_style_engine` as the central design system of Media House.

It defines the visual identity once, stores it persistently, resolves the correct rules for every project and makes those rules available to all production engines.

It is not another creative decision-making agent.

It is the source of truth for how the channel looks.

**The ultimate goal: Every video should feel individually well-produced, creatively appropriate to its content and unmistakably part of the same YouTube channel.**

Implement this cleanly within the existing architecture and media pipeline, with no unnecessary duplication and no new UI.
