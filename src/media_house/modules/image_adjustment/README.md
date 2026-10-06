# Image Adjustment

Modifies image assets from the Media Library, producing new derived assets with applied adjustments (like background removal, padding, glowing, centering).

```text
Media Library image asset
        │
        ▼  Rembg (U-2-Net / IS-Net)
Background removed (alpha channel)
        │
        ▼  Pillow: crop, resize, composite onto canvas
Adjusted image (PNG) ───────────────────────────► derived asset  "adjust_image"
                                                  (reusable library image)
```

## Where it lives

`modules/image_adjustment/` is a normal vertical slice. Like `audio_intelligence`, it has **no storage and no database of its own**. It consumes images from the Media Library and registers its outputs back into it.
Other modules import only `image_adjustment.application.contracts`.

| Layer | Contents |
| --- | --- |
| `domain` | `AdjustmentSettings`, `BackgroundRemoval`, `Canvas`, `Centering`, `Glow` |
| `application` | `AdjustImage` use case, `ImageAdjuster` contract, ports |
| `infrastructure` | `RembgSegmenter` (AI background removal), `PillowImageRenderer` (image compositing) |

## Reuse: process once, persist once

Adjustments are expensive (especially AI segmentation). The result is registered in the Media Library using a *processing fingerprint* (source asset + exact adjustment settings + version). If another module requests the exact same adjustment on the same image, it receives the existing derived asset instead of rerunning the operation.

## Example (from another module)

```python
from media_house.modules.image_adjustment.application.contracts import (
    AdjustImageCommand,
    AdjustmentSettings,
    BackgroundRemoval,
    Canvas,
    HexColor,
    ImageAdjuster,
)
from media_house.shared.errors import Err

def make_thumbnail(adjuster: ImageAdjuster, source_asset_id: str, ctx: JobContext) -> None:
    settings = AdjustmentSettings(
        background_removal=BackgroundRemoval(enabled=True),
        canvas=Canvas(width=1920, height=1080, background_color=HexColor("#1A1A1A"))
    )
    
    # blocking: call from a job
    result = adjuster.execute(AdjustImageCommand(source_asset_id, settings), ctx)
    
    if isinstance(result, Err):
        print(f"Failed: {result.error}")
        return
        
    derived_asset = result.value.asset
    print(f"Ready! Stored as library asset {derived_asset.id}")
```
