# Thumbnail Design

The channel's YouTube thumbnails, made the way the owner-approved Lidl × Nico thumbnail was made
(2026-11-10), as **A/B/C variants** for YouTube's "Test & Compare". It composes, it does not
analyse: cut-outs and frames come from `visual_asset_generation`, the palette and font from
`visual_style_engine`, reaction moments from **stored** `video_intelligence` analyses, and every
picture is a library asset.

```text
brief (owner's words + library pictures)
  │  headline 1-2 lines · reaction · ≤3 products · logo · shop phone (product, price, button,
  │  shop page) · story badge (struck through) · fireworks plate · alternate headline
  ▼
variants_of(brief)            a: clean · b: + badge · c: alternate headline   (one change each)
  ▼  per variant
lay_out (fixed slots)  ──►  PillowThumbnailRenderer (1920x1080 PNG)  ──►  judge (QC)
  ▼
derived picture of the video (operation "thumbnail_design"), cached by fingerprint
  ▼  optional
ExportThumbnail: 1280x720 JPEG ≤ 2 MB (the upload copy; the stored picture is untouched)
```

## The look (from the channel, `docs/layer4/channel-discovery.md` 6.8)

Blue glow + grid + speed rays (thumbnail blues, thumbnails only), a real fireworks plate
screened in behind the products, product stickers with a white glow and tilt, the creator
reaction lifted, saturated and rim-lit, the two silver bars (banner chrome) in the bottom-left
and top-right corners, one font for every word (the identity's `headline` face, Poppins
ExtraBold) with a black stroke and a hard comic shadow, the accent line in `highlight` yellow.
Colours and the font are read from the resolved style; the composition constants live in
`domain/layout.py` (the identity has no thumbnail zones yet).

## Reaction pictures: three ways

1. **Upload** (Control Center → Thumbnail → Upload…): imported and tagged `reaction`.
2. **Collection**: every library picture tagged `reaction` (uploads and picked footage frames
   join it), newest first. The pipeline takes the newest when none is given.
3. **From footage**: shots ranked by stored face cues (face size, sharpness, open eyes,
   expression cue; recordings named "reaktion"/"reaction" preferred), taken as exact frames.
   Videos without a stored analysis offer none.

## Contracts (`application/contracts.py`)

```python
ThumbnailDesigner.execute(DesignThumbnailsCommand(brief, anchor_asset_id=None, style=None), ctx)
    -> Result[DesignedThumbnails(variants: (DesignedVariant(name, label, asset, created, issues)),
                                 style_fingerprint, warnings), DesignError]
ReactionSuggester.execute(video_asset_ids, ctx, limit=6) -> Result[tuple[ReactionCandidate, ...], ...]
ReactionCollection.list() / add(path) / mark(asset_id)
ThumbnailExporter.execute(asset_id, destination) -> Result[Path, MediaError]
```

Style: `style` given (the pipeline's) → the stored style of the video → the current identity
resolved in memory (nothing stored). The source of the derived pictures is the video, or the
reaction picture without one.

## Pipeline

Stage `thumbnail_design` (CPU, Layer 4, part of `--stages layer4`): NEEDS `visual_style` and
`inspect`, WANTS `edit_plan` and `video_analysis`. Settings `thumbnail_*` on `StageSettings`;
in `scratch/run_pipeline.py` a `[thumbnail]` table (`headline`, `alt_headline`, `reaction`,
`products`, `logo`, `device_product`, `device_price`, `device_screen`, `badge`, `plate`).
Without a headline the stage is `skipped_unavailable`: the words are the owner's, never invented.
Nothing waits for it; the timeline neither shows nor needs thumbnails.

## Quality checks (`domain/qc.py`)

Size 1920x1080 (error: nothing registered); headline at least 20 px tall in the 320 px feed;
headline, price, button and badge clear of YouTube's duration chip and inside the frame; the
reaction at least 18 % of the picture; headline not into the logo; products without a free slot.
Warnings are stored with the picture and shown on the page.

## Limits

* One fixed layout (creator left looking right, logo top right). A reaction looking left is
  mirrored (`flip_reaction`), not re-laid out.
* Reaction moments are per shot (the analysis keeps no per-frame face rows): the shot middle is
  taken; the person picks among the candidates.
* `pixels.py` mirrors a few of `visual_asset_generation`'s private image operations (another
  module's infrastructure may not be imported).
