# Visual Asset Generation

Turns existing library pictures into clean, reusable visual assets: transparent cutouts, optional
enhanced variants (the channel's product "stickers"), exact frame grabs from video, delivery
copies, and quality checks. It prefers reuse over generation and generates nothing on this
installation (there is no image-generation model; see "Limited generation").

This module is the former `image_adjustment`, renamed in place (Layer 4 decision). Its cutout
operation keeps the name `image_adjustment` and version 1, and the default settings keep their exact
`to_config()` identity, so every cutout stored before the rename is still a cache hit
(`tests/unit/visual_asset_generation/test_domain.py` proves it).

```text
library IMAGE ─────────────────────────────┐
library VIDEO + exact source time          │
   │  FFmpeg (ProcessRunner), stored        │
   ▼  inspection -> exact frame             ▼
frame grab (16-bit RGB PNG) ──► clean cutout (RGBA PNG)   operation "image_adjustment"
   derived of the video           derived of the image    (rembg matte, clean-up, decontaminate)
                                        │
                                        ▼  Pillow/numpy, from the clean cutout (no re-segmentation)
                               enhanced variants          operation "image_enhancement"
                               sticker | silhouette | glow | shadow | outline
                                        │
                                        ▼  optional
                               delivery copy              operation "image_export"
                               PNG | WebP | JPEG (fit, never upscale, alpha kept or refused)

batch result: one "visual_asset_set" JSON document of the anchor asset naming every asset id
```

## Where it lives

`modules/visual_asset_generation/` is a vertical slice with **no storage of its own**. Pictures are
derived library assets; the library's derivation record (source asset, operation, version, config,
fingerprint) IS the relationship between original, frame, clean cutout, variant and export. Other
modules import only `visual_asset_generation.application.contracts`.

| Layer | Contents |
| --- | --- |
| `domain` | `values.py` (cutout settings, unchanged identity), `profile.py` (effects, presets, QC and selection knobs with field metadata, `VisualAssetConfig`), `requests.py` (`AssetRequest`, `ExportSettings`), `frames.py` (exact frame addressing), `qc.py` (`QcIssue`, `ImageFacts`, rules), `selection.py`, `generation.py` (policy), `model.py` (`VisualAssetSet`), `serialization.py`, `engine.py` (cache key) |
| `application` | `AdjustImage` (unchanged), `PrepareVisualAssets`, `GrabFrame`, `SelectImages`, `GenerateImage`, `VisualAssetResults`, `derive.py` (render -> measure -> QC -> register), `style.py` (style tokens -> knobs), ports |
| `infrastructure` | `RembgSegmenter`, `PillowImageRenderer`, `PillowCutoutEnhancer`, `PillowImageMeasurer`, `PillowImageExporter`, `FfmpegFrameExtractor`, `UnavailableImageGenerator` |

Consumers (boundary test): `motion_graphics_engine`, `media_pipeline`, `bootstrap`. Reads:
`media_library`, `media_inspection` (`InspectionCatalog`), `broll_intelligence` (`BrollCatalog`),
`visual_style_engine` (`ResolvedStyle`, `VisualStyleCatalog`), all through their contracts only.

## Contracts

```python
ImageAdjuster.execute(AdjustImageCommand(source_asset_id, settings=AdjustmentSettings(), group_ids=(),
                      inherit_source_groups=True), ctx) -> Result[DerivedAssetResultDto, AdjustError]

VisualAssetPreparer.execute(PrepareVisualAssetsCommand(
        requests: tuple[AssetRequest, ...],
        config=VisualAssetConfig(profile="generic", overrides={"variants.sticker.glow.radius": 10}),
        project_id=None, edit: EditRef | None = None,
        style: ResolvedStyle | None = None, style_ref: StyleRef | None = None,   # visual_style_engine types
        anchor_asset_id=None), ctx) -> Result[PreparedVisualAssets(document, asset, created), PrepareError]
AssetRequest(request_id, source_asset_id, source_time: Rational | None = None,
             variants: tuple[VariantName, ...] = (), purpose="", export: ExportSettings | None = None)

FrameGrabber.execute(GrabFrameCommand(video_asset_id, source_time: Rational), ctx)
    -> Result[FrameGrabResult(asset, created, position: FramePosition, inspection, checks, engine), FrameError]
ImageSelector.execute(SelectImagesCommand(concept, project_group_ids=(), config)) -> ImageSelection
ImageSelector.for_gaps(asset_id, *, project_group_ids=(), config=None) -> Result[tuple[GapVisuals, ...], MediaError]
ImageGenerationService.execute(GenerateImageCommand(request: GenerationRequest, ...), ctx)
    -> Result[ImportResultDto, GenerateError]                 # GenerationNotAvailable on this machine
VisualAssetCatalog.latest(asset_id, *, project_id=None) / history(asset_id) / load(set_asset_id)
ImageGenerator (port): identity(), unavailable() -> str | None, generate(request, destination, cancellation)
```

Motion graphics example (product stickers for a ranking):

```python
result = preparer.execute(
    PrepareVisualAssetsCommand(
        requests=(
            AssetRequest(
                request_id="rank3",
                source_asset_id=product_photo_id,
                variants=(VariantName.STICKER,),
                purpose="rank 3 sticker",
            ),
            AssetRequest(
                request_id="rank2-teaser",
                source_asset_id=clip_id,
                source_time=Rational(1253, 100),
                variants=(VariantName.SILHOUETTE,),
            ),
        ),
        project_id=plan.project_id,
        edit=EditRef(edit_plan_id=..., plan_version=...),
        style=resolved_style,
    ),
    ctx,
)
sticker_id = result.value.document.asset_for(
    "rank3", "sticker"
)  # variant, or its recorded fallback
```

### Mapping from an edit

This module needs no edit plan. A caller (motion graphics, or the pipeline's mapper) builds one
`AssetRequest` per picture: `source_asset_id` = the library image, or the clip's `SourceRange.asset_id`
with `source_time` on the same common clock (zero = container start, exact `Rational`);
`project_id`/`EditRef` = `EditPlan.project_id`, `plan_id`/`plan_version`. They only name the set.

## Workflows

1. **Clean cutout** (always). Background removal (rembg, default `birefnet-general-lite`),
   conservative matte clean-up that never erodes, edge colour decontamination, crop, fit to the
   canvas (default 1080x1080, padding 60, transparent), RGBA PNG. Identical to `ImageAdjuster`'s
   result for the same settings: both share one library asset.
2. **Enhanced variants** (optional, disabled by default; each a separate asset derived from the
   clean cutout, never re-segmented): `sticker` (white outer glow + tilt), `silhouette` (subject
   filled black, white glow: the "next rank" teaser), `glow`, `shadow` (soft drop shadow),
   `outline` (stroke). Every preset can also enable `rim` (edge light inside the silhouette) and
   `contrast` (subject only) by override. The subject keeps its exact pixels when the effects fit;
   otherwise it is shrunk once, never cropped. Opaque subject pixels are never changed by an effect
   behind them.
3. **Frame grabs**: `-ss` accurate seek, one picture, display orientation, YUV -> RGB48 in one
   accurate conversion. With a stored inspection of constant frame rate the picture on screen at
   `source_time` is addressed exactly (`floor((t - video offset) * fps)`, seek half a frame early);
   otherwise the first picture at or after `t` is taken and marked inexact. Stored as a derived
   IMAGE of the video (`frame_grab`).
4. **Delivery copies**: PNG (optimised, lossless), WebP (lossless by default, `exact` RGB under
   alpha), JPEG (4:4:4). Fitted inside `max_width`/`max_height`, never enlarged, premultiplied
   Lanczos. A transparent picture is never exported to JPEG unless `flatten_onto` names the
   background. A PNG that already fits is handed out as is (no needless recompression).
5. **Existing-image selection** (`ImageSelector`): library text and tag search word by word, ranked
   uploads > approved project images (tag `approved` or in `project_group_ids`) > brand assets
   (tag `brand`) > earlier generated images, then by the share of concept words matched (German
   compounds included). `for_gaps` does it for every b-roll `CoverageGap`.

## Configuration

`VisualAssetConfig(profile, overrides)`; precedence: profile defaults -> channel identity (the
style's tokens, see `application/style.py` for the token -> knob table) -> overrides by dotted path.
Every user-facing knob carries `dataclasses.field(metadata={label, help, unit, min, max, step,
choices, kind, advanced})`; the range checks are derived from that metadata
(`domain/knobs.py`) and `tests/unit/visual_asset_generation/test_profile.py` proves they agree.
Built-in profile: `generic` (sticker: white glow radius 8 / spread 6, tilt -6 deg, from the channel
discovery's 6-10 px / 5-10 deg; colours neutral white/black). With a `ResolvedStyle` the presets
take the channel's glow colour, sticker glow size/opacity and tilt range middle (-7.5 deg) instead.
`PROFILE_VERSION` versions the knob schema.

## Quality control

Each output is measured AFTER writing (`ImageFacts`: decodes fully, real alpha) and judged with
the Layer 4 `QcIssue` shape. ERROR = the output is not registered and its item is `FAILED` with a
`fallback_asset_id` (variant -> clean cutout -> original picture); the set becomes `PARTIAL`.
WARNING/INFO are recorded.

| Check | Severity |
| --- | --- |
| file does not decode, wrong format, no pixels | ERROR |
| no alpha / nothing transparent / no opaque subject (cutouts, variants) | ERROR |
| an effect reaches the canvas edge (clipped glow/stroke/shadow) | ERROR |
| subject reaches the canvas edge (clean cutout, padding 0) | WARNING |
| edge halo (soft edge colour differs from the subject), leftover background specks | WARNING |
| subject enlarged above 1.5x, source smaller than 320 px, subject touches the source edge | WARNING |
| export lost transparency, export larger than asked | ERROR |
| JPEG of a transparent picture without `flatten_onto` | ERROR (refused, PNG kept) |
| segmentation rejected (no subject / background not removed) | ERROR (original kept) |

A missing background model or a missing FFmpeg fails the whole call (`ExternalSystemError`, with
the fetch command) and stores nothing, so the same batch runs again once it is fixed.

## Storage, caching, provenance

* Every picture: `register_derived(parent, file, operation, config, version)`; identical config =
  the stored asset (`created=False`), nothing is rendered. Metadata states how it was made
  (variant, enhancements, scale, mask box, source size, frame index/time, engine).
* The set: `DerivedDocuments.store` under the anchor (first request's source unless given),
  `document_type: visual_asset_set`, cache key = digests of the requests, project, edit, style
  fingerprint, resolved profile and versions. A new key gets `plan_version + 1` and `supersedes`.
* Envelope: `plan_id, plan_version, supersedes, project_id, edit, style, created_at, state, items,
  frames, checks, inputs, stages, provenance (schema/planner/profile versions, configuration
  fingerprint, engines), warnings, limitations, schema_version`.

Background removal never downloads a model during a run: `RembgSegmenter` uses a model only if
rembg already has its file and otherwise fails with
`uv run python scripts/fetch_models.py --background-removal birefnet-general-lite`.

## Limited generation

There is no image-generation model or provider in this repository or on this CPU-only machine.
Implemented: the `ImageGenerator` port, `GenerationRequest`/`GenerationRecord` (full request,
generator, model, version, seed, time), the policy and the use case. Policy: never logos/brand
marks, product photos, evidence or realistic footage; only with a stated reason; only when no
existing library image matches the concept (the selection runs inside the use case). The
registered adapter is `UnavailableImageGenerator`: every allowed request ends in
`GenerationNotAvailable`. A future adapter registers in `module.py`; generated pictures are then
imported as `SourceType.AI_GENERATED`, tagged `ai_generated`, with the record as metadata.

## Limitations

* Picture-to-text similarity for selection is `not_available`: no contract embeds still library
  images (video_intelligence exports only its text tower).
* Cutouts and variants are 8-bit RGBA PNG; the cutout renderer decodes sources at 8 bits per
  channel (frame grabs themselves are 16-bit).
* No manual mask correction or explicit subject/region reference yet; a rejected cutout is
  reported and the original kept.
* Sticker tilt is one fixed angle per profile/style (deterministic), not varied per sticker.
* Validated on synthetic pictures (Pillow shapes, FFmpeg `lavfi` clips) with a colour-key
  stand-in for the segmentation model only; never on real product photos with a real model.
