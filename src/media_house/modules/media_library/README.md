# Media Library

The core storage and organization system for all assets in the application. It acts as the single source of truth for original media files, processed derivatives (thumbnails, transcripts, adjusted images), and their metadata.

## Where it lives

`modules/media_library/` is a vertical slice that owns the database (`media_library.db`) and the file storage directory (`media/`).
Other modules interact with it **only** through the `media_library.application.contracts.MediaLibrary` protocol.

| Layer | Contents |
| --- | --- |
| `domain` | `MediaAsset`, `MediaGroup`, `MediaType`, search queries, events, errors |
| `application` | `MediaLibrary` use cases: import, lookup, derivation, tags, groups, deletion, DTOs |
| `infrastructure` | SQLite repository, file system storage, FFmpeg/JSON probes, thumbnail generation |
| `presentation` | UI contributors, view models |

## Core Concepts

* **Idempotent Imports**: Importing identical files reuses the same storage and returns `created=False`.
* **Derived Assets**: Operations like transcription or image background removal produce "derived assets". These are linked to their source via a processing fingerprint (`operation` + `config` + `version`), so the same intensive work is never repeated.
* **Stable IDs**: External modules reference assets exclusively by their string `id`, never by file path. If a module requires the file for an external tool, it calls `local_path(asset_id)`.
* **Probes**: The library relies on format probes (e.g., `FfprobeProbe`, `JsonDocumentProbe`) to extract metadata, durations, streams, and types during import.

## Example (from another module)

```python
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.shared.errors import Err, Ok

def process_asset(library: MediaLibrary, asset_id: str, ctx: JobContext) -> None:
    # 1. Fetch asset metadata
    asset_result = library.get(asset_id)
    if isinstance(asset_result, Err):
        return
    asset = asset_result.value

    # 2. Get local file path for a CLI tool
    path_result = library.local_path(asset_id)
    if isinstance(path_result, Ok):
        print(f"File is at {path_result.value}")

    # 3. Derive a new asset
    fingerprint = library.fingerprint(asset_id, "my_custom_operation", {"quality": "high"}).value
    derived = library.find_derived_asset(asset_id, fingerprint)
    
    if not derived:
        # Run expensive operation and register result
        new_file_path = run_expensive_operation(path_result.value)
        library.register_derived(
            source_asset_id=asset_id,
            path=new_file_path,
            operation="my_custom_operation",
            config={"quality": "high"}
        )
```

## Not in scope

Direct audio analysis, background removal, or video encoding. The media library manages storage, cataloging, and thumbnails; other vertical slices (like `audio_intelligence` or `image_adjustment`) do the heavy media processing and store their results back here.
