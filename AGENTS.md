# Media-House: Global AI Context & Rules

This document outlines the core architectural and design rules for the **Media-House** project. You must strictly adhere to these principles whenever generating code, adding new features, or modifying existing systems.

## 1. Stack and Tooling
* **Language:** Python >= 3.13 (use PEP 695 generics).
* **Package Manager:** `uv`.
* **UI:** PySide6 (Widgets). 
* **Database:** stdlib `sqlite3` behind repository ports. 
* **Validation:** pydantic (for external data).
* **Typing & Linting:** Strict typing (`mypy --strict`), `ruff` for format/lint.
* **Imports:** Absolute imports only (`from media_house.x.y import Z`). No relative imports.
* **Search Tools:** Use `jg` (jev grep) instead of standard grep when appropriate, as it is installed in this environment.

## 2. Hard Architectural Rules (Hexagonal/DDD)
The app is a modular monolith. 
* **Domain:** Pure Python. No I/O, no Qt, no `os`, no `subprocess`. Can only import stdlib, own domain, `core.domain`, and `shared.*`.
* **Application:** Use cases and DTOs. Orchestrates domain and ports. No Qt, no infrastructure, no DB logic.
* **Infrastructure:** Adapters implementing ports. Wrap library errors. No Qt, no presentation logic.
* **Presentation:** Passive widgets, viewmodels, Qt signals. No business logic, no domain imports.
* **Cross-Module Imports:** You are ONLY allowed to import from another module's `application.contracts`. Never reach into another module's internals (domain, infra, presentation).

## 3. Independent & Shared Pipeline Operation
All media processing modules (e.g., Audio Intelligence, Audio Improvement, Video Improvement) must be designed to work both standalone and in a pipeline:

* **Standalone:** `Video -> Improvement -> Output`
* **Shared Pipeline:** `Video -> Media Inspection -> Improvement -> Output`

**CRITICAL RULES FOR PIPELINES:**
* **No duplication of processes:** If a module like **Media Inspection** has already probed or analyzed the media, downstream modules MUST reuse that information (e.g., resolution, fps, codecs, timebase) through the `MediaLibrary` or `application.contracts`. Do not re-probe the file if the data exists.
* **Graceful Degradation:** If prior inspection data is missing, the module must still function (falling back to minimum necessary probing).
* **Do not build new storage systems:** Always use the existing **Media Library** to store, cache, and retrieve assets.

## 4. Processing & Quality Standards
When building video, audio, or media processing pipelines, adhere to the following principles:

* **Source-Aware Processing:** Modules must know what kind of source they are processing. Explicit external config, embedded metadata (via Media Inspection), or default fallback. Don't rely on fragile guessing if the user provided a profile (e.g., `gopro_hero_9`, `studio`).
* **Consistency Over Randomness:** Produce stable, deterministic, and predictable results. Adaptive AI processing must make decisions within controlled boundaries, not invent a new look for every clip.
* **Scientific & High-Precision:** Use proper math and color science (e.g., OpenColorIO, float32 working spaces). Avoid 8-bit conversions mid-pipeline to minimize quantization errors and banding.
* **Do Not Destroy Good Media:** If the source already looks/sounds good, do less. Avoid excessive sharpening, denoising, artificial HDR, or oversaturation.
* **Preserve Originals:** Never modify the original asset. Output derived assets. Preserve original audio streams, metadata, and exact frame/timecode synchronization when processing video.
* **Processing Provenance & Caching:** Every processed output must know exactly how it was created (source profile, applied operations, engine versions). Reuse existing processing results via caching/fingerprinting instead of re-processing.
* **Independent Testing:** Ensure all modules can be tested cleanly in isolation (using mock files and fakes) and together in integration.

## 5. Definition of Done
* Does the code strictly follow the Hexagonal layer rules?
* Are cross-module dependencies limited to `application/contracts.py`?
* Is media inspection data being reused instead of re-calculated?
* Are results stable, deterministic, and correctly cached?
* Do the architectural tests pass (`uv run pytest tests/architecture`)?
