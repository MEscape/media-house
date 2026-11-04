# Implementation Prompt — Layer 4: `visual_asset_generation`

## Mission

Inspect, understand, and extend the existing image-improvement and image-generation functionality in Media House into the final Layer 4 module: `visual_asset_generation`.

**This is an extension and integration task, not a greenfield implementation.**

The existing image-improvement module is the starting point and source of truth for discovering the current capabilities, architecture, models, processing pipelines, dependencies, and integrations.

Your first responsibility is to understand what already exists. Then determine the smallest, cleanest architectural adjustment that delivers the requirements below.

Do not create a second image-generation system, duplicate image-processing infrastructure, introduce unnecessary models, or rebuild functionality that is already available.

The intended result is a relatively small, specialised backend module focused primarily on preparing reusable visual assets, especially transparent-background cutouts with optional visual enhancement.

---

# 1. Mandatory first step: repository discovery

Before changing code, inspect the complete existing implementation.

### 1.1 Find the existing image-improvement module

Locate and inspect:

- The existing image-improvement module and its complete directory structure.
- Image-generation functionality and provider integrations.
- Existing image-processing and enhancement operations.
- Background-removal and segmentation capabilities, if present.
- Image upload, validation, storage and export functionality.
- Existing model clients, model registries and provider configuration.
- Prompt construction and generation-request handling.
- Asset versioning, provenance and relationship management.
- Existing tests, configuration, dependencies and documentation.
- All modules that already consume or produce image assets.

Search the repository for relevant functionality even if it is not located in the expected module.

Do not assume that the module name accurately describes everything it already does.

### 1.2 Discover the actual models and processing infrastructure

Create an inventory of the existing image-related models and services.

Inspect:

- Configured image-generation models and providers.
- Image-editing and image-to-image capabilities.
- Background-removal and segmentation models.
- Subject detection and edge-refinement capabilities.
- Image enhancement and restoration models.
- Local image-processing libraries.
- Existing alpha/transparency handling.
- Existing GPU or CPU processing infrastructure.
- Model wrappers, shared clients and routing.
- Actual supported input/output formats and limitations.

Verify capabilities through the implementation, provider interfaces, documentation and tests.

Do not infer capabilities from model names.

For each relevant capability, establish:

1. What already exists.
2. Which module owns it.
3. Which model or processing library implements it.
4. Whether it is production-ready or incomplete.
5. Whether it can be reused directly.
6. Whether it needs extension or integration.
7. Whether a genuine capability gap remains.

**Reuse existing models and infrastructure wherever possible.**

Do not introduce a new model or external provider simply because another option exists. Only consider additional dependencies when a concrete, verified requirement cannot be fulfilled by the existing system.

Do not silently replace existing models or providers.

### 1.3 Establish the current architecture

Determine whether the existing image-improvement module should:

- Be renamed to `visual_asset_generation`.
- Be extended in place while preserving its current identity.
- Become an internal component of a small `visual_asset_generation` orchestration module.
- Be integrated into an existing visual-generation workflow.

Choose the smallest solution that fits the real repository.

Preserve existing imports, public interfaces, configuration, stored asset references and backwards compatibility wherever possible.

Do not create a new architectural layer simply to achieve a preferred directory name.

### 1.4 Produce a discovery report

Before implementation, document:

- Existing image-related modules.
- Existing models and their verified capabilities.
- Existing processing libraries and pipelines.
- Existing asset-management interfaces.
- Existing consumers and integrations.
- Identified duplication or architectural problems.
- Missing capabilities.
- Recommended module structure.
- Exact rename, extension or integration strategy.

The report must distinguish verified facts from assumptions and recommendations.

---

# 2. Module responsibility and scope

`visual_asset_generation` is responsible for creating or preparing visual assets that cannot be obtained directly from the existing media library or suitable external sources.

The module should prioritise reuse over generation.

Preferred source order:

1. Existing uploaded images and videos.
2. Images supplied or explicitly approved by the user.
3. Existing graphic and brand asset libraries.
4. Image generation when a genuinely useful visual asset is missing.

The module must not generate images merely because generation is available.

Its main responsibility is to turn existing images into clean, reusable visual assets for video compositions.

### Core workflows

Keep the implementation focused on three primary workflows:

1. Background removal and transparent asset preparation.
2. Optional enhancement of prepared assets.
3. Occasional image generation when an appropriate visual asset is genuinely missing.

Image conversion, validation, optimisation and asset registration support these workflows.

Do not turn this into a general-purpose creative engine.

---

# 3. Background removal and transparent asset preparation

This is the primary functionality and highest implementation priority.

### Input

Accept existing image assets from the Media House asset infrastructure.

Support:

- Uploaded images.
- Existing image-library assets.
- Product photographs.
- People and portraits.
- Objects and accessories.
- Logos and symbols.
- Decorative elements.
- Individual elements intended for video compositions.

Use existing asset references rather than unnecessarily copying source files.

### Subject isolation

The processing workflow should:

1. Load and validate the source image.
2. Detect or identify the intended main subject.
3. Perform background removal using existing suitable capabilities.
4. Generate an alpha-supported output.
5. Refine subject boundaries.
6. Remove unwanted background remnants.
7. Validate the resulting transparency and subject integrity.
8. Register the output as a derived asset.

Preserve:

- Subject details.
- Original proportions.
- Fine edges.
- Important internal details.
- Original resolution wherever practical.

Pay particular attention to:

- Hair and fur.
- Fine structures.
- Transparent or translucent objects.
- Glass and reflective objects.
- Complex contours.
- Hands and small details.
- Multiple overlapping objects.

Do not assume that every image has one obvious foreground subject. Support an explicit subject or region reference when the existing infrastructure allows it.

### Edge refinement

Reuse existing segmentation and image-processing capabilities.

Where needed, support:

- Alpha matting.
- Edge refinement.
- Feathering.
- Decontamination of background colours.
- Removal of edge halos.
- Refinement of fine structures.
- Cleanup of residual background pixels.

Avoid excessive feathering, artificial cutout edges and loss of important subject detail.

### Output

Produce a clean transparent PNG or another suitable alpha-supported format.

Validate that:

- The file actually contains an alpha channel.
- Transparency is preserved after export.
- The subject is not unintentionally clipped.
- The image dimensions are valid.
- Edge quality is acceptable.
- No unintended background remains.

Preserve the original uploaded image.

The transparent cutout must be a separate derived asset.

### Manual correction

If the existing system supports manual masks or segmentation corrections, integrate with it.

If it does not, design a clean backend interface that can accept correction data in the future.

Do not build a UI as part of this task.

If automatic segmentation fails, preserve the source, report the problem and mark the result for review rather than silently producing a poor cutout.

---

# 4. Optional visual enhancement

After background removal, allow a small number of optional visual treatments.

The default must be **no additional effect**.

Prioritise:

- Subtle outer glow.
- Channel-coloured glow.
- Soft shadow.
- Light outline.
- Subtle edge illumination.

Optional secondary capabilities:

- Background-independent contrast enhancement.
- Very subtle blur or halo.

### Glow

Glow is an important use case.

Support:

- Enable/disable.
- Intensity.
- Radius or spread.
- Opacity.
- Colour.
- Softness.
- Optional directional or edge-based treatment if supported.

Consume approved colours from `visual_style_engine`.

Glow must remain configurable and restrained.

Do not apply exaggerated outlines, strong neon effects or artificial-looking halos by default.

### Shadows and outlines

Implement only where the existing processing infrastructure makes this straightforward and reliable.

Preserve independent parameters and the ability to disable each treatment.

### Original and enhanced variants

Always preserve the unenhanced transparent cutout.

Create enhanced results as separate derived assets.

Never overwrite the original image or the clean transparent version.

---

# 5. Limited image generation

Reuse the existing image-generation module and its configured models.

Image generation is an exceptional capability, not the default source of visual material.

Suitable cases include:

- A simple illustrative object or scene.
- A conceptual visual for an abstract topic.
- A decorative background.
- A custom visual element for an explanation.
- An illustration for a specific video section.
- A visual that cannot reasonably be sourced from existing footage or approved images.

Before generating anything, the requesting workflow must establish that a visual asset is genuinely needed and cannot reasonably be obtained from available sources.

### Prohibited automatic generation

Do not automatically generate:

- Generic stock-like images for every section.
- Unnecessary decorative elements.
- Replacement product images that misrepresent actual products.
- Fake evidence or realistic-looking scenes presented as genuine footage.
- New logos or alternative brand identities.

Do not use image generation to fabricate factual visual evidence.

Generated assets must be explicitly identified as generated and retain their generation provenance.

### Generation architecture

Reuse existing:

- Image-generation clients.
- Providers and models.
- Prompt management.
- Request schemas.
- Model routing.
- Retry and error handling.
- Caching.
- Output validation.
- Asset registration.

Extend existing interfaces only where necessary.

Do not create duplicate generation clients, model registries, prompt systems or provider abstractions.

---

# 6. Image processing and format preparation

Provide supporting functionality for:

- PNG, WebP and JPEG.
- Other suitable formats already supported by the repository.
- Alpha-channel preservation.
- Resizing for target resolutions.
- Thumbnail and preview derivatives.
- Aspect-ratio preservation.
- Image optimisation.
- Resolution and quality validation.
- Format conversion.
- Avoidance of unnecessary recompression.

Reuse existing image-processing libraries and infrastructure.

Do not introduce a separate general-purpose image-processing pipeline.

Preserve original source references and metadata.

For transparent assets, never export to a format that silently removes transparency.

---

# 7. Visual identity integration

Integrate with `visual_style_engine`.

Consume relevant approved properties, including:

- Brand colours.
- Approved glow colours.
- Preferred outline styles.
- Shadow characteristics.
- Graphic treatment preferences.
- Approved visual references.

For example, a product cutout may receive a subtle accent-coloured glow matching the channel identity.

However, the engine must not automatically apply the same treatment to every asset.

Some assets should remain completely natural.

Respect the existing visual identity version and project overrides.

Do not implement another visual identity configuration system.

---

# 8. Asset provenance and versioning

All source and derived assets must remain traceable.

The following relationships should be supported:

- Original uploaded image.
- Transparent-background version.
- Enhanced transparent version.
- Alternative enhancement variants.
- Generated image.
- Optimised export.

Use the existing `media_library` and `asset_relationships` infrastructure.

Do not create a duplicate asset database or relationship system.

Preserve:

- Original source references.
- Processing history.
- Generation origin.
- Model and processing-library versions where available.
- Configuration and parameter history.
- Visual identity version.
- Creation timestamps.
- Validation results.

Processing must be reproducible where the underlying model and processing method permit it.

---

# 9. Asset validation and quality control

Validate all processed and generated outputs.

### Background removal

- Segmentation quality.
- Subject integrity.
- Remaining background pixels.
- Edge artifacts.
- Hair and fine-detail preservation.
- Alpha-channel correctness.
- Transparency rendering.

### Enhancement

- Glow and shadow clipping.
- Unwanted halos.
- Excessive outlines.
- Colour consistency.
- Subject distortion.
- Unintended transparency changes.

### Image output

- Resolution.
- Aspect ratio.
- Format compatibility.
- Corrupted or missing files.
- Unnecessary quality loss.
- File integrity.

If processing damages important details or creates an inferior result, preserve the original and flag the derived result.

Validation should return structured results, warnings and actionable failure reasons.

Do not silently accept failed outputs.

---

# 10. Integration with Media House

Integrate with the actual existing module interfaces.

| Module | Responsibility |
|---|---|
| `media_library` | Stores and indexes original and generated assets. |
| `asset_relationships` | Tracks source images and their derived versions. |
| `visual_style_engine` | Supplies approved visual treatments and brand colours. |
| `motion_graphics_engine` | Consumes prepared images in graphic compositions. |
| `vfx_engine` | Handles more complex footage compositing and shot-level effects. |
| `broll_intelligence` | Identifies when an additional visual asset might be useful. |
| `editing_intelligence` | Determines where a visual asset should be used. |
| `quality_control` | Validates resulting visual outputs. |

### Ownership boundaries

`visual_asset_generation` prepares standalone visual assets.

It does not decide independently where assets belong in the edit.

It does not:

- Build animated graphic compositions.
- Create title animations.
- Perform shot-level VFX.
- Replace the VFX engine's segmentation and compositing workflows.
- Define visual identity.
- Replace the media library.
- Replace asset relationship management.
- Manage the final timeline.
- Execute Resolve editing operations.

When an upstream module requests an asset, this module prepares it and returns a structured asset reference.

### Important distinction from VFX

This module focuses on standalone image preparation and generation.

`vfx_engine` owns more complex footage-level subject isolation, tracking, compositing and visual effects.

Avoid duplicating the VFX engine's functionality. If both modules need a shared capability, inspect the existing implementation and integrate through an appropriate shared service or existing owner.

---

# 11. Suggested internal structure

Use this as a functional guide, not an instruction to create every directory regardless of need.

Keep the implementation intentionally compact.

```text
visual_asset_generation/
│
├── image_input/
│   ├── upload_handling/
│   └── source_validation/
│
├── background_removal/
│   ├── subject_detection/
│   ├── segmentation/
│   ├── edge_refinement/
│   └── transparency_validation/
│
├── asset_enhancement/
│   ├── glow/
│   ├── shadows/
│   └── outlines/
│
├── image_generation/
│   ├── generation_requests/
│   ├── prompt_management/
│   └── generated_asset_validation/
│
├── image_processing/
│   ├── format_conversion/
│   ├── resizing/
│   └── optimization/
│
└── asset_management/
    ├── versioning/
    ├── provenance/
    └── export/
```

Do not mechanically reproduce this structure if the existing repository already has a better organisation.

Prefer extending existing components and preserving established naming conventions.

---

# 12. Expected output contract

Each prepared or generated asset should expose the following information, adapted to the repository's existing schema conventions:

```json
{
  "asset_id": "unique_asset_id",
  "source_asset_id": "original_asset_reference",
  "asset_type": "transparent_cutout",
  "processing_type": "background_removal",
  "source_path": "source_reference",
  "output_path": "derived_asset_reference",
  "resolution": {
    "width": 1920,
    "height": 1080
  },
  "aspect_ratio": "16:9",
  "format": "png",
  "has_alpha": true,
  "background_removed": true,
  "enhancements_applied": [],
  "enhancement_parameters": {},
  "visual_style_id": "optional_style_reference",
  "identity_version": "optional_identity_version",
  "generation_method": "existing_model_or_processing_pipeline",
  "model_version": "optional_model_version",
  "quality_validation": {},
  "provenance": {},
  "configuration_hash": "configuration_hash"
}
```

This is an illustrative contract, not permission to introduce a competing asset schema.

Reuse existing identifiers, asset references, metadata conventions and versioning wherever possible.

The original, transparent cutout and enhanced versions must remain separate related assets.

---

# 13. Implementation priorities

Implement in this order:

| Priority | Component | Importance |
|---|---|---|
| 1 | Existing module discovery and integration strategy | Mandatory foundation |
| 2 | Image input and validation | Foundation |
| 3 | Background removal | Main use case |
| 4 | Edge refinement and transparency validation | Professional cutouts |
| 5 | Asset export and versioning | Reusability |
| 6 | Optional glow | Important visual treatment |
| 7 | Format conversion and optimisation | Supporting functionality |
| 8 | Optional shadows and outlines | Additional flexibility |
| 9 | Existing image-generation integration | Occasional use case |

Do not let the less important generation workflow delay the primary background-removal workflow.

---

# 14. Performance, reliability and processing

Reuse existing infrastructure for:

- Processing queues.
- Model execution.
- CPU/GPU resource management.
- Caching.
- Retries.
- File storage.
- Error handling.
- Processing status.
- Asset registration.

Avoid unnecessary reprocessing.

Cache derived outputs based on source identity, processing parameters, model/library versions and relevant configuration.

Invalidate cached outputs when their actual dependencies change.

Keep source assets immutable.

Ensure partial failures do not leave incorrectly registered or corrupted assets.

---

# 15. Testing requirements

Inspect the existing test architecture and extend it.

Tests must cover:

### Existing integration

- Existing image-improvement functionality remains compatible.
- Existing image-generation functionality remains operational.
- No duplicate model clients or provider configuration are introduced.
- Existing asset references remain valid.

### Background removal

- People and portraits.
- Product images.
- Objects and accessories.
- Complex edges.
- Hair and fine details.
- Transparent output.
- Failed segmentation.
- Source preservation.

### Enhancement

- Glow disabled by default.
- Glow enabled with configurable colour and intensity.
- Soft shadow and outline.
- Original transparent cutout remains unchanged.
- No unwanted clipping or halos.

### Image generation

- Existing configured models are reused.
- Generated assets are identified as generated.
- Missing or unsupported capabilities fail explicitly.
- Generation does not occur without a valid request.

### Asset management

- Source and derived relationships.
- Versioning.
- Provenance.
- Correct format and alpha preservation.
- Cache invalidation.
- Export integrity.

### Integration

- `media_library`.
- `asset_relationships`.
- `visual_style_engine`.
- `motion_graphics_engine`.
- `vfx_engine`.
- `quality_control`.

Test the actual asset-processing workflow, not merely isolated helper functions.

---

# 16. Strict implementation constraints

- Backend-first. No UI.
- Discover before implementing.
- Extend the existing image-improvement module rather than building a parallel system.
- Reuse existing models, providers, processing libraries and asset infrastructure.
- Do not invent capabilities that the repository does not have.
- Do not add unnecessary AI models or dependencies.
- Do not automatically generate images when existing assets are suitable.
- Do not overwrite original images.
- Do not apply enhancement effects by default.
- Do not duplicate VFX, motion graphics, visual identity or asset-management responsibilities.
- Preserve compatibility with the existing Media House pipeline.
- Keep the module small and specialised.

If an existing capability is incomplete, extend its actual owner where appropriate instead of creating a competing implementation.

If a requirement cannot be fulfilled with the available infrastructure, document the gap and implement only a justified, minimal solution.

---

# 17. Definition of done

The task is complete when:

1. The existing image-improvement module has been thoroughly inspected.
2. Existing image-generation models, providers and processing capabilities have been inventoried.
3. A justified rename, extension or integration strategy has been implemented.
4. The primary background-removal workflow works end to end.
5. Clean transparent assets can be produced and validated.
6. Optional glow works without modifying the original cutout.
7. Original and derived assets remain traceable through existing asset infrastructure.
8. Format conversion and export preserve transparency.
9. Existing image-generation capabilities are reused for exceptional requests.
10. Generated assets have clear provenance.
11. Visual identity integration works through existing interfaces.
12. Downstream modules can consume prepared assets.
13. Quality validation and failure handling work.
14. Existing functionality and tests remain intact.
15. No unnecessary parallel generation, processing or asset-management system has been introduced.

Deliver a concise implementation report documenting:

- What existed before.
- Which existing models and libraries were reused.
- What was renamed, extended or integrated.
- What functionality was added.
- What functionality deliberately remained in other modules.
- Tests executed and their results.
- Remaining limitations.

**Final architectural principle: Reuse as much as possible. Generate as little as necessary. Make uploaded images easy to transform into clean, reusable, transparent visual assets. Keep this module small, specialised and fully integrated into Media House.**
