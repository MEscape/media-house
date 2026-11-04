# Layer 4 Integration Brief

Read this instead of re-exploring the codebase. Every fact below was checked against the code at
commit `927a843` (2026-11-02). Paths are relative to `src/media_house/` unless they start with
`tests/`, `scripts/`, `scratch/` or `docs/`. `file:line` points at the definition.

Scope (owner decision): Layer 4 produces **stored plans** (JSON documents in the Media Library)
and **rendered assets** (derived library assets). `timeline_builder`, `resolve_adapter` and
`quality_control` do not exist and will not be built now. Do not name or import them.

---------------------------------------------------------------------------------------------------

## 0. Read first: blockers and risks

| # | Fact | Consequence for you |
| --- | --- | --- |
| 1 | `tests/architecture/test_editing_intelligence_boundaries.py:24` `FUTURE_MODULES` lists `music_and_sound_design`, `transition_engine`, `motion_graphics_engine`, `visual_style_engine`. Test `:166 test_no_empty_package_reserves_the_name_of_a_module_that_does_not_exist_yet` asserts no folder with those names exists. | Creating any of those four modules FAILS this test. One coordinated edit must split the list: keep the names for the "editing does not import them" check (`:143`), drop them from the "no folder" check. Do this once, before the parallel work merges. |
| 2 | Consumer allowlists. Only listed packages may import these modules (even their contracts): `editing_intelligence` (only `media_pipeline`, `bootstrap`; `test_editing_intelligence_boundaries.py:172`), `reframing_intelligence` (pipeline, editing, bootstrap; `test_reframing_intelligence_boundaries.py:132`), `pacing_intelligence` (pipeline, editing, broll, bootstrap; `test_pacing_intelligence_boundaries.py:127`), `continuity_intelligence` (pipeline, editing, reframing, bootstrap; `test_continuity_intelligence_boundaries.py:131`), `broll_intelligence` (pipeline, editing, continuity, bootstrap; `test_broll_intelligence_boundaries.py:146`), `story_architecture` (pipeline, editing, pacing, broll, reframing, bootstrap; `test_story_architecture_boundaries.py:57`). | A Layer 4 module that imports `EditCatalog`/`EditPlan` breaks the editing test. Recommended: follow the continuity precedent (sec. 4.10). Each Layer 4 module defines its OWN input DTO in its own `contracts.py`. `media_pipeline/application/<x>.py` maps the `EditPlan` to it. Reading story/pacing/reframing/continuity directly needs an allowlist line in that test. That edit is shared, so coordinate it. |
| 3 | No FFmpeg/FFprobe on PATH on this machine. No `npx hyperframes` in the npx cache (Node v24.21.0 and npx exist). | `MediaLibrary.import_file`/`register_derived` of audio/video runs `ffprobe` and fails with `ToolNotFoundError`. Integration tests that make media with FFmpeg skip or fail. Every render adapter must treat a missing tool as `ToolNotFoundError`. |
| 4 | No model is downloaded: no `%USERPROFILE%\.cache\huggingface`, no `~/.u2net`, no torch hub, no `%LOCALAPPDATA%\media-house`. GPU = AMD Radeon (integrated). `torch 2.8.0+cu128`, `torch.cuda.is_available() == False`. | Everything runs on the CPU. Every model adapter must degrade to `not_available`. Real-model tests cannot run here. |
| 5 | The app-control policy blocks `pytest.exe` and `mypy.exe` (os error 4551). `ruff.exe` works. | Use `uv run python -m pytest ...` and `uv run python -m mypy`. `scripts/check.py` calls the bare executables, so its type and test gates cannot spawn here. |
| 6 | Nothing detects music, beats in music, sound events or bangs. `audio_intelligence` ships NO non-verbal detector (`audio_intelligence/infrastructure/acoustic_extractor.py:84`). `energy_rhythm` is a level-envelope pulse estimate, not music analysis. No librosa or soundfile. `torchaudio`, `scipy`, `numba` and `av` are installed. | `music_and_sound_design` must build its own audio measurement adapter, or degrade. |
| 7 | The library accepts only PNG/JPEG/WEBP images, FFprobe-readable audio/video and JSON with a top-level `document_type` (`media_library/module.py:85-87`). HTML, SVG, fonts, `.ass`/`.srt`, `.cube` and `.lottie` are refused (`UnsupportedMediaType`). | Keep HyperFrames HTML, caption/subtitle text and similar inside the JSON plan document. Store only the render outputs (MOV/PNG/WAV) as assets. |
| 8 | `DocumentSpec.config` and asset `metadata` are each capped at 64 KiB of canonical JSON (`media_library/domain/values.py:20,313`). | Put digests (sha256 of a canonical key) in the cache key, as reframing does. Never put whole inputs in it. |
| 9 | `tests/integration/test_application_lifecycle.py:86` holds the exact set of installed module names. `tests/unit/media_pipeline/test_*_task.py` assert `set(executors().table()) == set(TaskKind)`. | Each new module adds its name to the lifecycle test. Each new pipeline task must appear in the executor table. Both files are shared, so coordinate. |

---------------------------------------------------------------------------------------------------

## 1. Module blueprint, as practised (reframing_intelligence, pacing_intelligence)

### 1.1 Folder layout (reframing, 5.5 kLoC; pacing has the same shape)

```text
modules/<name>/
  __init__.py                 (empty)
  README.md                   contract, conventions, limits ("Validated on ... only")
  module.py                   registration; the only place ports meet adapters
  application/
    contracts.py              PUBLIC API: Protocols + re-exports (+ __all__)
    analyze_<x>.py            the use case: Command, Result, Error alias, class with execute(cmd, ctx)
    results.py                <X>Results: the read-only Catalog over the library (latest/history/load)
    views.py                  anti-corruption: upstream contract DTOs -> own domain "facts"
  domain/                     pure: no I/O, no clock, no logging, imports NO other module
    values.py                 versions, DOCUMENT_TYPE, RESULT_OPERATION, StrEnums, score docs
    profile.py                profiles = data, <X>Config, resolve(), fingerprint()
    facts.py                  the input facts the engine reads (own types, filled by views.py)
    model.py                  the result tree (frozen dataclasses, kw_only)
    engine.py                 cache_key(), digest(), plan_<x>(inputs, config, now=..., meta=...)
    serialization.py          analysis_to_json / analysis_from_json (strict, generic codec)
    validation.py             validate_analysis(result) -> list[Issue] (Severity.ERROR = never stored)
    errors.py                 <X>Error(DomainError) + subclasses with code/user_message
  (no infrastructure/, no presentation/ in analysis modules: confirmed for reframing, pacing,
   editing, story, broll, continuity)
```

Rendering modules (motion graphics, VFX, music, visual assets) also need an `infrastructure/`
folder for adapters (ProcessRunner/FFmpeg, HyperFrames, Pillow, torch). The architecture does not
allow third-party packages in `domain` or `application` (`tests/architecture/analysis.py:24`
`THIRD_PARTY_OK = {"domain": set(), "application": set()}`). numpy, PIL and torch may appear ONLY
in `infrastructure`. No analysis module has a `presentation/` folder or a `strings.py`. Add a UI
only if asked.

### 1.2 "Profiles are data" (`reframing_intelligence/domain/profile.py`)

```python
@dataclass(frozen=True, slots=True)
class Composition: anchor_x: float = 0.5; headroom: float = 0.08; ...   # one group per concern
@dataclass(frozen=True, slots=True)
class ReframingProfile:
    name: str; description: str; version: int = PROFILE_VERSION
    composition: Composition = field(default_factory=Composition); motion: Motion = ...; ...
    def problems(self) -> list[str]: ...           # range checks, returned, not raised
PROFILES: Final[dict[str, ReframingProfile]] = {p.name: p for p in (_p("generic", "..."), ...)}
DEFAULT_PROFILE: Final = "generic"
STORY_FORMAT_PROFILE: Final[dict[str, str]] = {"review": "product_review", "short": "shorts_reels", ...}

@dataclass(frozen=True, slots=True)
class ReframingConfig:                                    # profile.py:237
    profile: str | None = None                            # None -> by story format -> DEFAULT
    custom: ReframingProfile | None = None                # a profile built in code wins
    safe_areas: tuple[SafeArea, ...] = ()                 # run config, never part of a profile
    def resolve(self, story_format: str | None = None) -> ReframingProfile   # raises Invalid<X>Config
    def fingerprint(self, profile) -> dict[str, object]   # {"profile": asdict(profile), "safe_areas": [...]}
```

`PacingConfig` (`pacing_intelligence/domain/profile.py:319`) has the same shape:
`profile: str | PacingProfile | None`, `strategy: Strategy = BALANCED`,
`target: TargetDuration | None`, and `resolve(story_format)`. A name chooses a built-in profile. A
dataclass instance is a custom profile. `dataclasses.replace` tunes one value.

### 1.3 Cache key, versions, document identity

`reframing_intelligence/domain/values.py`:
`ANALYZER_VERSION = SCHEMA_VERSION = VOCABULARY_VERSION = PROFILE_VERSION = 1`,
`DOCUMENT_TYPE = RESULT_OPERATION = "reframing_analysis"`.

`domain/engine.py:193 cache_key(inputs, target, config, profile) -> dict` returns "everything that
can change the result, nothing that cannot (time, job, device)". It contains the input identities
(library asset ids, upstream analysis ids and versions, per-item fingerprints), a
`digest(config.fingerprint(profile))`, the person's decisions, and
`{"versions": {analyzer, schema, vocabulary}}`. `digest()` (`engine.py:106`) is the sha256 of
`json.dumps(sort_keys=True, default=str)`.

Use case flow (`application/analyze_reframing.py:167-255`):

```python
key = cache_key(...)
spec = DocumentSpec(
    RESULT_OPERATION, ANALYZER_VERSION, _json(key)
)  # _json: json round-trip -> JsonValue
fp = self._documents.fingerprint(anchor, spec)  # Result[str, MediaError]
hit = self._documents.load(anchor, fp.value, analysis_from_json)  # (T, MediaAssetDto) | None
if hit:
    return Ok(Result(hit[0], hit[1], created=False))  # reuse: nothing computed
version, supersedes = self._history(anchor, target, profile)  # :366
result = plan_reframing(
    inputs, target, config, now=self._clock.now(), previous=..., meta=AnalysisMeta(...)
)
errors = [i for i in validate_analysis(result) if i.severity is Severity.ERROR]
if errors:
    return Err(InvalidReframingAnalysis(errors))  # never stored
stored = self._documents.store(
    anchor_asset,
    analysis_to_json(result),
    filename="reframing_analysis.json",
    spec=spec,
    display_name=f"{name} (reframing {target})",
    metadata={
        "target": ...,
        "profile": ...,
        "analysis_version": ...,
        "state": ...,
        "story_plan_id": ...,
    },
)
```

- Anchor: the result is a derived document of the FIRST asset in `command.asset_ids`. The library
  fingerprint is `sha256(scheme, source checksum, operation, version, canonical config)`
  (`media_library/domain/values.py:382`).
- Plan versioning (`_history`, `analyze_reframing.py:366`): count the earlier documents with the
  same `processing_type` and the same discriminating metadata (target, profile). The new document
  gets `analysis_version = count + 1` and `supersedes = newest.id`. History is never changed.
  `EditPlan` uses the same idea: `plan_version`, `supersedes` (`editing_intelligence/domain/model.py:457`).
- Carry-over: `previous = results.latest(...)`. Unchanged items, which have their own `fingerprint`
  field, are copied over, not recomputed.

### 1.4 Catalog pattern (`application/results.py`)

```python
class ReframingCatalog(Protocol):  # contracts.py:170
    def latest(
        self, asset_id: str, *, target: str | None = None, profile: str | None = None
    ) -> Result[tuple[ReframingAnalysis, MediaAssetDto] | None, MediaError]: ...
    def history(self, asset_id: str) -> list[MediaAssetDto]: ...  # newest first
    def load(
        self, analysis_asset_id: str
    ) -> Result[ReframingAnalysis, MediaError | InvalidReframingDocument]: ...
```

The implementation filters `library.list_derived(asset_id)` by `metadata["processing_type"] ==
RESULT_OPERATION` and the discriminating metadata keys. It sorts by `(created_at, id)` descending
and skips unreadable documents with a warning. Nothing is computed. Register the same instance
for both the concrete class and the Protocol (sec. 1.8).

### 1.5 Acquire.STORED / REQUEST

Only `audio_visual_alignment` (`domain/values.py:34`), `story_architecture`
(`application/plan_story.py:90`) and `editing_intelligence` (`application/plan_edit.py:143`)
have it. Each defines its OWN `Acquire(StrEnum)` with `STORED = "stored"` (read the catalog, else
fail `MissingPrerequisite`/`MissingStoryPlan`) and `REQUEST = "request"` (ask the owner's producer
Protocol to compute). Reframing and pacing have NO `Acquire`. They only read catalogs and accept
results the caller supplied (`video_analyses: Mapping[str, VideoAnalysis]`,
`story_plan: StoryPlan | None`, and so on). A required input that is missing is an `Err`. An
optional input that is missing is an `InputRecord(how=NOT_AVAILABLE, detail=...)` plus a
`StageReport`. For Layer 4: read stored results and accept supplied ones. Do not run producers.
Every boundary test bans producer Protocols (`AudioAnalyzer`, `VideoAnalyzer`, `EditPlanner`, ...).

### 1.6 Provenance / inputs_used

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord: kind: str; how: InputHow; asset_id: str | None = None; identity: str | None = None; detail: str = ""
class InputHow(StrEnum): SUPPLIED; REUSED; NOT_AVAILABLE            # editing adds REQUESTED
class StageReport: stage: str; state: StageState; detail: str = ""  # StageState: OK, SKIPPED, NOT_AVAILABLE
class Provenance: schema_version; analyzer_version; vocabulary_version; profile: str; profile_version: int;
                  configuration_fingerprint: str; engines: tuple[tuple[str, str], ...] = ()
```

The helper `_read(supplied, asset_id, catalog.latest, kind, records)`
(`analyze_reframing.py:439`) returns the supplied result, else the stored one, else `None`. It
records how the result was obtained. Copy it. A result also carries `warnings` and `limitations`
(each stated once, "what this result cannot tell you"), plus `state: ResultState (OK | PARTIAL)`.

### 1.7 Manual override (accept/reject) pattern

- Reframing: `SegmentDecision(segment_key: str, decision: Decision, note: str = "")` with
  `Decision = ACCEPTED | REJECTED`. The command has `decisions: tuple[...]` and
  `carry_decisions: bool = True`. `_decisions()` (`analyze_reframing.py:387`) merges new decisions
  over the previous analysis's. Decisions are part of the cache key. The key is stable across
  reanalysis (`asset_id:shot_id`).
- Editing: `ManualOverride(decision_id, action: Action, note="")`, `carry_overrides=True`
  (`editing_intelligence/domain/model.py:404`).
- Continuity: `Decision.ACCEPTED | REJECTED` follows the issue by its fingerprint.
- Rule: automation never changes a person's decision.

### 1.8 module.py and bootstrap

```python
class ReframingIntelligenceModule:  # reframing_intelligence/module.py:30
    name: str = "reframing_intelligence"

    def register(self, container: Container) -> None:
        container.register_factory(
            AnalyzeReframing,
            lambda c: AnalyzeReframing(
                c.resolve(MediaLibrary),
                c.resolve(VideoCatalog),
                ...,
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(ReframingAnalyzer, lambda c: c.resolve(AnalyzeReframing))
        container.register_factory(
            ReframingResults, lambda c: ReframingResults(c.resolve(MediaLibrary))
        )
        container.register_factory(ReframingCatalog, lambda c: c.resolve(ReframingResults))
```

`bootstrap/modules.py` needs TWO lines: one import and one entry in the `installed_modules()`
tuple. Factories are lazy singletons, so order does not matter, but a duplicate `name` raises.
Always resolvable: `AppSettings, AppPaths, ErrorHandler, EventBus, EventPublisher, JobScheduler,
ProcessRunner, Clock`.

### 1.9 Serialization and validation

`domain/serialization.py` holds a generic strict codec for frozen dataclass trees. It walks the
type hints and handles Enum, datetime, tuples, `X | None` and nested dataclasses. `Rational` is a
dataclass, so it round-trips as `{numerator, denominator}`. Unknown, missing or wrongly typed
fields, unknown enum members and a wrong `schema_version` are refused. The writer emits
`{"document_type": DOCUMENT_TYPE, **fields}` with
`sort_keys=True, ensure_ascii=False, allow_nan=False, indent=1`. Copy the file. It is about 130
lines and imports only its own domain. `validation.py` returns issues. ERROR means the use case
returns `Err(Invalid<X>Analysis)` and stores nothing.

---------------------------------------------------------------------------------------------------

## 2. Shared kernel facts

### 2.1 core.domain (`from media_house.core.domain import Rational, FrameTime, TimeRange`)

```python
@dataclass(frozen=True, slots=True) class Rational:     # core/domain/rational.py:17
    numerator: int; denominator: int                   # reduced on construction; numerator >= 0, denominator > 0
    @classmethod parse(text) -> Rational | None          # "30000/1001", "16:9", "25"; None for 0/garbage
    value -> float; reciprocal -> Rational; __str__ -> "n/d"
    # NOT negative, NO arithmetic: convert with Fraction(r.numerator, r.denominator) (editing/sequence.py:_f)
@dataclass(frozen=True, slots=True) class FrameTime:    # core/domain/media_time.py:79
    frame: int (>=0, presentation order); pts: int (may be <0); timebase: Rational
    seconds -> float; as_seconds_text() -> "%.6f"
@dataclass(frozen=True, slots=True) class TimeRange:    # media_time.py:100, half-open [start, end)
    start: FrameTime; end: FrameTime                    # same timebase required
    frames -> int; seconds -> float; contains_frame(frame) -> bool
```

Time conventions: `video_intelligence` uses `FrameTime` and `TimeRange` (display-oriented
pictures). Editing, reframing and pacing use exact `Rational` seconds on the recording's container
(common) clock. `audio_intelligence`, content and alignment use float seconds, and
`common = upstream + correction` (`AlignmentDocument.video_correction`). Frames exist only for a
known constant rate (`SourceRange.frame_rate`).

### 2.2 shared.errors

`from media_house.shared.errors import Ok, Err, Result, DomainError, ApplicationError,
ValidationError, NotFoundError, ConflictError, InvariantViolation, ExternalSystemError,
ToolNotFoundError, ProcessFailedError, ProcessTimeoutError, OperationCancelledError,
ConfigurationError, MediaHouseError`.

- `Ok[T](value)`, `Err[E](error)`, `type Result[T, E] = Ok[T] | Err[E]` (`shared/errors/result.py`).
- `MediaHouseError(message, *, user_message=..., details={...})` has `.code`, `.category`,
  `.error_id`, `.user_message` and `.details`. A module error is
  `class FooError(DomainError): code = "<module>.<snake>"`.
- Pipeline convention: an error whose `code` ends in a code from `_UNAVAILABLE_CODES`
  (`media_pipeline/application/executors.py:139`: `no_audio_track`, `no_video_stream`,
  `nothing_to_cover`, `nothing_to_judge`, `nothing_to_frame`) becomes `SKIPPED_UNAVAILABLE`
  instead of `FAILED`. A Layer 4 "nothing to do" error must be added to that tuple.

### 2.3 shared.configuration

`AppSettings` (`shared/configuration/settings.py:299`) has the sections `environment`, `logging`,
`concurrency.max_workers`, `ui.theme` and `secrets`. All are pydantic with
`extra="forbid", frozen=True`. **There is no per-module section, no registry and no schema of
module settings.** Adding a section means editing `shared/`, which is forbidden. Modules receive
their settings as the `Config` dataclass on the Command. The pipeline passes them through
`StageSettings` (`executors.py:151`) from `scratch/run_pipeline.py` flags or `--config` JSON.
Executables are constructor defaults in adapters (`ffmpeg: str = "ffmpeg"`, for example
`audio_improvement/infrastructure/ffmpeg_tool.py:47`) and are resolved on PATH. No setting
configures a path. The only schema-like things are the pydantic `Field(ge=, le=)` ranges in
AppSettings and the dotted-path override resolver in `audio_improvement/domain/profiles.py:62`
(sec. 10).

### 2.4 Events, logging, paths, processes, jobs

```python
from media_house.shared.events import DomainEvent, ApplicationEvent, EventBus, EventPublisher

#   @dataclass(frozen=True, kw_only=True, slots=True) class X(ApplicationEvent): ...  (occurred_at required)
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_log.info("msg", key=value)
from media_house.shared.filesystem import (
    AppPaths,
)  # config_dir, data_dir, cache_dir, log_dir, temp_dir

#   with paths.temporary_directory(prefix="x-") as tmp: ...   (always removed)
#   Windows: data/config = %LOCALAPPDATA%\media-house, cache = %LOCALAPPDATA%\media-house\Cache
from media_house.core.application.ports import (
    ProcessRunner,
    ProcessSpec,
    ProcessResult,
    OutputLine,
    Clock,
)

#   ProcessSpec(executable, args=(), cwd=None, env={}, timeout_seconds=None, check=False, max_captured_lines=10_000)
#   runner.run(spec, cancellation=ctx.cancellation, on_output=cb) -> ProcessResult(exit_code, stdout, stderr, duration_seconds)
#   SubprocessRunner resolves the executable with shutil.which(spec.executable, path=env PATH): no shell
#   (core/infrastructure/process/subprocess_runner.py:63). Raises ToolNotFoundError / ProcessTimeoutError /
#   ProcessFailedError(check=True) / OperationCancelledError.
from media_house.shared.concurrency import JobContext, CancellationToken
#   ctx.job_id; ctx.cancellation (.cancel/.is_cancelled/.wait); ctx.progress.report(cur, total, "msg");
#   ctx.raise_if_cancelled(); JobContext.detached() in tests
```

Only `core/infrastructure/process/subprocess_runner.py` may import `subprocess`. Ruff bans
`os.environ` and `os.getenv`. Adapters that wrap FFmpeg have this shape: `FfmpegTool(runner)`
with `run(args, cancellation)` and `probe_audio(path, cancellation)`
(`audio_improvement/infrastructure/ffmpeg_tool.py`).

**HyperFrames note**: on Windows `npx` is `npx.cmd`. `shutil.which("npx")` finds it, but
CPython runs `.cmd` files through `cmd.exe` and applies batch-argument escaping rules. Prefer
`ProcessSpec("node", [<resolved hyperframes CLI .js>, "render", ...])` or a pinned local install.
Keep HTML paths free of `^&|<>%`. Set `timeout_seconds` and pass `cancellation`.

### 2.5 Model snapshots (core.infrastructure; infrastructure layer only)

`from media_house.core.infrastructure.models import cached_snapshot, local_model_path`
(`core/infrastructure/models/snapshots.py`). `cached_snapshot(repo, cache=None)` checks the Hugging
Face cache only (`local_files_only=True`). `local_model_path(name, model_dir)` returns an explicit
folder, else `<model_dir>/<org>--<name>`, else the cached snapshot. It never downloads.

---------------------------------------------------------------------------------------------------

## 3. media_library contracts (`modules/media_library/application/contracts.py`)

```python
class MediaLibrary(Protocol):
    import_file(path, *, display_name=None, group_ids=(), tags=(), metadata=None,
                source_type=SourceType.LOCAL_FILE, source_url=None, source_provider=None
               ) -> Result[ImportResultDto(asset, created), MediaError]      # identical content stored once
    get(asset_id) -> Result[MediaAssetDto, MediaError]; get_many(ids) -> list; find_by_checksum(sha) -> Dto | None
    search(query: MediaQuery | None = None) -> Page[MediaAssetDto]          # Page(items, total, page, page_size, has_next)
    local_path(asset_id) -> Result[Path, MediaError]                        # use, never store
    fingerprint(source_asset_id, operation, config: Mapping[str, JsonValue], version=1) -> Result[str, MediaError]
    find_derived_asset(source_asset_id, processing_fingerprint) -> MediaAssetDto | None
    register_derived(source_asset_id, path, *, operation, config, version=1, display_name=None,
                     metadata=None, role=AssetRole.DERIVED) -> Result[DerivedAssetResultDto(asset, created), MediaError]
    list_derived(source_asset_id) -> list[MediaAssetDto]
    thumbnail(asset_id, max_size=256) -> Result[Dto | None, ...]; ensure_thumbnail(asset_id, max_size=256)  # IMAGE+VIDEO only, .webp, role PREVIEW
    rename, set_favorite, archive, restore, update_metadata(asset_id, patch), add_tags, remove_tags, list_tags(prefix=, limit=)
    create_group(name, parent_id=, description=), list_groups, add_to_group, remove_from_group, delete(asset_id, include_derived=True)
type MediaError = ValidationError | NotFoundError | ConflictError
```

`MediaAssetDto` (`application/dto.py`): `id, media_type, mime_type, extension, original_filename,
display_name, file_size, checksum, role, status, is_favorite, source_type, created_at, updated_at,
width, height, duration_seconds, technical: Mapping, metadata: Mapping, tags: tuple[str, ...],
group_ids, source_url, source_provider, derivation: DerivationDto | None` (`is_derived`).
`DerivationDto(source_asset_id, operation, version, config, fingerprint)`.

Enums (`domain/values.py`): `MediaType = IMAGE | AUDIO | VIDEO | OTHER` (`:27`; JSON documents are
`OTHER`). `AssetRole = ORIGINAL | DERIVED | PREVIEW` (`:36`; PREVIEW is hidden from default
searches). `SourceType = LOCAL_FILE | WEB_DOWNLOAD | UPLOAD | GENERATED | AI_GENERATED |
EXTERNAL_SERVICE | DERIVED | UNKNOWN` (`:49`). `AssetStatus = ACTIVE | ARCHIVED`.

`DerivedDocuments(library, paths)` and `DocumentSpec(operation, version, config)`
(`application/derived_documents.py:70,78`) provide `fingerprint(source_id, spec)`,
`load(source_id, fp, parse) -> (T, Dto) | None` and
`store(source_dto, text, *, filename, spec, display_name, metadata) -> Result[Dto, ...]`. `load`
DELETES a stored document that `parse` rejects (ValueError or MediaHouseError), so the caller
recomputes. `store` always adds `{"processing_type": spec.operation, "source_asset_id": ...}` to
the metadata and registers with role DERIVED. A JSON document needs a top-level `document_type`,
or the import refuses it (`infrastructure/media_probes.py:149`, max 64 MiB).

**What the library accepts** (detected by magic bytes, never by extension):

| Kind | Probe | Asset | `technical` keys |
| --- | --- | --- | --- |
| PNG / JPEG / WEBP | `default_image_probes()` (`image_probes.py:227`) | IMAGE | `format, color_mode, has_alpha, bit_depth` (PNG) |
| wav, mp3, flac, ogg, aac, aiff, mp4/m4a, mov (brand `qt`), mkv/mka, webm/weba, avi | `FfprobeProbe` (needs ffprobe) | AUDIO / VIDEO | `container, audio_stream_count, audio_codec, sample_rate, channels, video_codec, bit_rate` (NO pix_fmt, NO alpha) |
| JSON with a `document_type` | `JsonDocumentProbe` | OTHER | `document_type` |
| HTML, SVG, fonts, subtitles, LUTs, anything else | none | refused (`UnsupportedMediaType`) | n/a |

ProRes 4444 MOV with alpha imports as VIDEO `.mov`. To verify alpha, call
`MediaInspector.execute` (`VideoStream.has_alpha`, `pixel_format`). The library DTO does not say.

**Music/SFX library (recommendation, nothing exists yet):** import the originals with
`import_file(path, tags=("music", "genre:...", "mood:..."), metadata={"license": ..., "bpm": ...,
"key": ...}, source_type=SourceType.LOCAL_FILE | EXTERNAL_SERVICE, source_provider="...",
source_url=...)`. Add them to a group ("Music"/"SFX"). Search with
`MediaQuery(media_types=frozenset({MediaType.AUDIO}), tags=frozenset({"music"}), page_size<=200,
page=n)` (`domain/query.py:35`). `tags` requires ALL listed tags. Tags are casefolded, at most 50
chars and contain no comma. `text` matches only the name and filename (`LIKE`).
**There is NO metadata filter.** Page through the results and filter `metadata` in memory, or
store the measured analysis per track as a derived JSON document. **Rendered outputs**
(MOV/PNG/WAV): use `register_derived(anchor_id, file, operation="<module>_render",
config=<spec>, version=..., metadata={...}, role=DERIVED)`. Identical config returns the existing
asset (`created=False`), which is the cache.

---------------------------------------------------------------------------------------------------

## 4. Upstream contracts Layer 4 consumes

Import ONLY `media_house.modules.<m>.application.contracts`. Every catalog returns
`Result[tuple[<Doc>, MediaAssetDto] | None, MediaError]`, where `Ok(None)` means none is stored.

| Module | Catalog / signature | Document |
| --- | --- | --- |
| media_inspection | `InspectionCatalog.find(asset_id, config=None) -> InspectionResult \| None` (never probes) | `InspectionResult.inspection: MediaInspection` |
| audio_intelligence | `AudioCatalog.latest(asset_id)` | `AudioIntelligenceTimeline` |
| video_intelligence | `VideoCatalog.latest(asset_id, profile=None)`, `ShotIndexCatalog.shot_index(asset_id) -> Result[IndexedAsset \| None, MediaError]`, `VisualTextEncoder` | `VideoAnalysis` |
| content_intelligence | `ContentCatalog.latest(asset_id)`, `.load(id)`, `TextEncoder`, `TextGenerator` | `ContentAnalysis` |
| audio_visual_alignment | `AlignmentCatalog.latest(video_asset_id)` | `AlignmentDocument` |
| story_architecture* | `StoryCatalog.latest(asset_id, *, format=None)`, `history`, `load` | `StoryPlan` |
| editing_intelligence* | `EditCatalog.latest(asset_id, *, profile=None)`, `history`, `load` | `EditPlan` |
| pacing_intelligence* | `PacingCatalog.latest(asset_id, *, profile=None, story_plan_id=None)` | `PacingAnalysis` |
| broll_intelligence* | `BrollCatalog.latest(asset_id, *, profile=None, story_plan_id=None)` | `BrollAnalysis` |
| continuity_intelligence* | `ContinuityCatalog.latest(asset_id, *, profile=None, edit_plan_id=None)` | `ContinuityAnalysis` |
| reframing_intelligence* | `ReframingCatalog.latest(asset_id, *, target=None, profile=None)` | `ReframingAnalysis` |

\* An allowlist test guards these modules (sec. 0, risk 2).

### 4.1 media_inspection (`domain/model.py`)

```python
MediaInspection: asset_id, created_at, depth, observed: ObservedMedia, synchronization, findings, status: InspectionStatus(verdict, usable, ...)
  .container: ContainerInfo(format_names, duration, start_time, bit_rate, size_bytes, stream_count, tags)
  .video_streams / .audio_streams / .primary_video / .audio_stream(position)
VideoStream: index, codec, profile, pixel_format, bit_depth: Sourced[int], chroma_subsampling, has_alpha: bool,
  scan_type, color: ColorInfo(space, primaries, transfer, range, dynamic_range: Sourced[str], ...),
  frame_rate: Sourced[Rational], average_frame_rate: Sourced[Rational], time_base: Rational | None,
  start_time, duration, declared_frame_count, geometry: Geometry, timing: StreamTiming | None
Geometry: width, height (coded), sample_aspect_ratio, display_aspect_ratio: Sourced[Rational], rotation: Sourced[int]
  .display_size -> (w, h) after pixel shape and rotation; .square_pixels
StreamTiming.mode: FrameRateMode (CONSTANT | VARIABLE | UNKNOWN)   # VFR flag; timing may be None at shallow depth
AudioStream: index, codec, sample_rate, channels, channel_layout, channel_class, time_base, start_time, duration, language, is_default
Sourced[T]: value: T | None, provenance (UNKNOWN iff value is None)
ProductionMetadata: creation_time, camera_make/model, lens, iso, ... (observed.production)
```

### 4.2 audio_intelligence

```python
AudioIntelligenceTimeline (domain/analysis/timeline.py:201): metadata: AnalysisMetadata, transcript: Transcript,
  track: AcousticTrack, baseline, events: tuple[AudioEvent], pauses: tuple[Pause], words: tuple[TimelineWord],
  segments: tuple[TimelineSegment], editing_signals: tuple[EditingSignal]
  duration; language; word_at(t); words_between(a, b); segment_at; events_between; pause_after/before(word);
  energy_rhythm(config=None) -> tuple[RhythmWindow(start, end, tempo_bpm, strength, beats: tuple[float], pulses)]
  signals_between; moment_at(t); signal_curve(kind, hop=0.1); frames_between; acoustic_at; to_frame(t, fps, rounding)
TranscriptWord: index, segment_id, raw_word, normalized_word, start, end (float s), confidence, speaker | None, timing, emphasis_hints
TimelineWord: .word + .acoustics + signals; emphasis_score, expressiveness_score, arousal_score, moment_score, pitch, energy, rate
TranscriptMetadata: language, language_detected, model, alignment_method, audio_offset, device, ...
AudioEvent(kind, start, end, strength, confidence=None, source="")  kinds: pitch_rise, pitch_fall, energy_spike,
  energy_drop, silence, pause (laughter/breath constants exist; NO detector ships)
EditingSignal(kind, start, end, score, contributors, anchor, anchor_id)  kinds: moment, cut, broll, music_duck, music_break, zoom_emphasis
```

Speakers are on `TranscriptWord.speaker` only when `AudioIntelligenceConfig.diarization` was set
(off by default; gated pyannote model). Sentences are `content_intelligence` `Unit`s. Language is
`timeline.language`.

### 4.3 video_intelligence

```python
VideoAnalysis (domain/result.py:173): asset_id, asset_checksum, source, history, provenance, inputs_used,
  analyzers: tuple[AnalyzerReport], shots: tuple[Shot], curves: tuple[Curve], warnings, scenes, tracks: tuple[Track],
  identities, retakes, overlays: tuple[Overlay], events: tuple[Event], embeddings;  shot_at(frame=...)
Shot: shot_id, index, range: TimeRange, boundary_in/out, handles, keyframes, camera: CameraObservation, motion,
  quality: QualityAssessment(metrics: QualityMetrics), entities, faces, body, text: TextObservation, scene_id, content,
  meaning, continuity, indicators, framing, composition, lighting: LightingObservation, geometry, crop_safe: tuple[CropSafeRegion], scores
Assessed base: state: AnalyzerState, confidence, evidence, reasons
Track(Assessed): track_id, kind: EntityKind(person|face|animal|vehicle|object), label, shot_id, first, last: FrameTime, points: tuple[TrackPoint(time, box: BBox, confidence)], occluded_frames, identity_cluster
BBox(x0, y0, x1, y1) normalised 0-1, display orientation
TextObservation: items: tuple[TextItem(text, first, last, box, confidence)], screen_kind     # OCR (RapidOCR)
Overlay(kind: WATERMARK|LOWER_THIRD|BURNED_IN_CAPTION|SPLIT_SCREEN, first, last, box, text)  # existing burned-in captions!
Event(event_id, kind: EventKind, shot_id, time: FrameTime, end, strength, detail)
  EventKind: shot_boundary, motion_peak, attention_peak, track_entered/exited, text_appeared/disappeared,
             screen_content_change, scrolling, gesture, flash
CameraObservation: movement (static|handheld|pan|tilt|push_in|pull_out|mixed), intensity, direction_degrees, speed, zoom_rate, shake_residual
QualityMetrics: sharpness, noise_sigma, luma_p1/p50/p99, tonal_spread, clipped, crushed, flicker, sampled_frames
LightingObservation: subject_to_surround, shadow_contrast, color_balance, backlit, harsh_shadows
CropSafeRegion: aspect, window: BBox, margin, subject_inside
edge_state(analysis, seconds) -> EdgeState(shot_id, scene_id, seconds, subject: SubjectState(cx, cy, height, vx, vy, ...), head_yaw,
  eye_contact, visual_speaking, framing, camera_movement, luma, color_balance, subject_to_surround, ...)   # domain/edges.py:74
IndexedShot (shot index): shot_id, index, start/end: Rational, frames, objects, entity_labels, description, environment,
  indoor_outdoor, content_label, texts, screen_kind, scores, sharpness, motion_energy, camera_movement, framing, subject_kind, ...; vector()
VisualTextEncoder: identity(); unavailable() -> str | None; embed(texts, cancellation) -> list[tuple[float, ...]]  (CLIP text tower)
```

### 4.4 content_intelligence

`ContentAnalysis` (`domain/model.py:322`) has: `analysis_id, asset_id, state, source, inputs,
provenance, stages, units: tuple[Unit] (sentences: unit_id, index, text, span: Span(start, end,
start_basis, end_basis), speaker, gap_before, starts_clean, ends_clean), segments:
tuple[SemanticSegment], topics, key_items: tuple[KeyItem(kind: KeyKind, text, unit_ids, span,
confidence, qualified_by)], relationships, summary, key_takeaways, global_observations`.
`SemanticSegment` has `.graphics: tuple[Opportunity]` and `.broll`. `Opportunity` has `kind`
(broll|graphic), `category`, `concept` (verbatim), `span`, `strength` and `already_covered`.
`GraphicKind = key_number | definition | list | process | comparison | name_and_role | quote |
call_to_action | term_clarification`. These are ready-made motion-graphics and caption-emphasis
candidates. Helpers exported from contracts: `designations(text) ->
tuple[Designation(family, value, text)]` (product-like names), `resolve_references`, `stance_of`.
`TextEncoder.embed(texts, cancellation) -> list[list[float]]` (multilingual MiniLM).
`TextGenerator.generate(system, user, cancellation) -> str` (deterministic Qwen2.5-1.5B). Both have
`identity()` and `unavailable()`. Never load a second LLM or embedder.

### 4.5 audio_visual_alignment

`AlignmentDocument` (`domain/model.py:276`) has `alignment_id, asset_id, inputs, sync:
SyncAssessment, speech_sources, speaker_links, text_matches: tuple[TextMatch(start, end, text,
shot, grade, terms, similarity)], sound_actions: tuple[SoundAction(audio_kind, audio_start,
audio_end, video_kind, shot_id, video_time: FrameTime, delta_seconds, ...)], boundaries,
opportunities, flags, stages, provenance`. `.video_correction` is a property (float seconds). A
`SoundAction` pairs an audio event (energy_spike) with a visual one (flash/motion_peak). It is the
closest thing to "bang + flash" sync.

### 4.6 story_architecture

`StoryPlan` (`domain/model.py:424`) has `story_plan_id, plan_version, supersedes, project_id,
created_at, state, source_assets, target_format (story format name), audience_intent,
narrative_pattern, main_message, premise, candidate_openings, recommended_opening, sections:
tuple[Section], beats: tuple[Beat], key_information, ..., visual_opportunities:
tuple[VisualOpportunity], pacing_notes, estimated_duration, ...`. `Beat` has `beat_id,
section_id, function: BeatFunction, source: SourceRef(asset_id, segment_ids, unit_ids, start, end,
exact_time), basis, summary, confidence`. `BeatFunction` covers `hook, setup, context, claim,
explanation, evidence, example, demonstration, counterpoint, caveat, transition, question, answer,
turning_point, payoff, recap, call_to_action, unclassified` (hooks and CTAs live here). `Section`
has `section_id, order, label, purpose, priority, beat_ids, duration, pacing`. Story formats are
`generic, top_n, product_introduction, review, unboxing, talking_head, tutorial, interview,
podcast, documentary, short`.

### 4.7 editing_intelligence EditPlan: the main hand-off

```python
EditPlan (domain/model.py:457): edit_plan_id, plan_version, supersedes, project_id, created_at, state, story: StoryRef,
  profile, target_format, timebase: Timebase(frame_rate: Rational, assumed, note), sources: tuple[SourceRecord],
  decisions, placements: tuple[Placement], audio_bridges: tuple[AudioBridge], coverage: tuple[CoverageRequest],
  effect_requests: tuple[EffectRequest], constraints, issues, checks, structural_proposals, pacing_notes,
  duration: EditDuration(estimated: Rational, ...), confidence, inputs, stages, provenance, warnings, limitations,
  overrides, schema_version, metadata;  track(TrackKind, layer=0), placement(id), decision(id)
Placement (:193): placement_id, track: TrackKind(VIDEO|AUDIO), layer: int (0 main, 1 cover), role: PlacementRole(MAIN|COLD_OPEN|COVER),
  source: SourceRange, timeline_start: Rational, timeline_duration: Rational, start_frame, duration_frames, section_id, beat_id,
  decision_ids, audio: AudioBehavior(SOURCE_SYNC|VOICEOVER|MUTED), fade_in, fade_out: Rational, cut_in/cut_out: CutPoint | None,
  linked, reuse_of, continuity_risk, confidence, warnings, framing: tuple[FramingRef];  timeline_end
SourceRange (:78): asset_id, start, end: Rational (container seconds), start_precision, end_precision: Precision, frame_rate, start_frame, end_frame, sample_rate, ...
FramingRef (:172): recommendation_id, analysis_id, target ("9:16"), strategy, feasibility, executable, start, end: Rational
AudioBridge (:237): bridge_id, kind: BridgeKind(J_CUT|L_CUT|BRIDGE), audio_changes_at, picture_changes_at: Rational, placement_ids, reason
CoverageRequest (:262): request_id, section_id, purpose, necessity, concept, span: SourceRange, need: Signal, chosen, alternatives, placement_id, executability, notes, decision_id

@dataclass(frozen=True, slots=True, kw_only=True)
class EffectRequest:                                     # domain/model.py:284
    request_id: str
    kind: EffectKind
    intent: EffectIntent
    at: Rational                                         # TIMELINE seconds
    duration: Rational | None                            # None = instantaneous or the later stage decides (always None today)
    placement_id: str | None                             # always None today
    section_id: str | None
    priority: float                                      # [0, 1]
    reason: str
    decision_id: str | None = None
```

| EffectKind (`values.py:193`) | Generated? | Where | intent |
| --- | --- | --- | --- |
| `TRANSITION` | yes, when `profile.section_transitions` | `domain/effects.py:65` at the first kept sentence of each new section | `SECTION_CHANGE`, priority 0.5 |
| `MUSIC_CUE` | yes, when `profile.reveal_cue` and the beat function is payoff/turning_point | `effects.py:77` | `REVEAL`, 0.8 |
| `REFRAME` | yes, when `profile.emphasis_reframe` and engagement >= 0.8 | `effects.py:89` | `EMPHASIS` |
| `GRAPHIC` | yes, from story visual opportunities with purpose `explainer_graphic` / `text_on_screen` (NOT capped) | `domain/coverage.py:593` | `EXPLAIN` / `SHOW_TEXT` |
| `SOUND_CUE` | **never generated** | n/a | n/a |
| (intent `COVERED_CUT`) | **never generated** | n/a | n/a |

The budget is `effects_per_minute` (default 4.0) and the strongest requests win. The section
transitions, reveal cues and emphasis reframes are all off in the base profile
(`EditingProfile` in `domain/profile.py`; editing profiles are `generic, top_n,
product_introduction, review, unboxing, talking_head, tutorial, interview, podcast, documentary,
short, montage`). So a default plan carries only GRAPHIC requests. Layer 4 must therefore also
derive its own opportunities (captions from the transcript, transitions at section boundaries).
It cannot rely on `effect_requests` alone.

### 4.8 pacing_intelligence

`PacingAnalysis` (`domain/model.py:334`) has `profile, strategy, story, passages, pauses, shots,
sections, timeline: tuple[TimelineAnalysis(cuts_per_minute, mean_speech_rate_wpm, attention:
tuple[AttentionBin], ...)], rhythm: tuple[RhythmFinding(span, tempo_bpm, strength, beat_count,
cuts_on_beat, cut_count)], recommendations: tuple[Recommendation(type, level, target: TimeSpan,
section_id, beat_id, proposal, confidence, safety, priority, ...)]`. `RecommendationType` includes
`align_cuts_to_beats`, `add_visual_change`, `restore_attention`, `hold_reveal` and
`add_section_breath`, which inform transitions, music and motion graphics.

### 4.9 broll_intelligence

`BrollAnalysis` (`domain/model.py:307`) has `recommendations, gaps: tuple[CoverageGap], pool,
...`. A `CoverageGap` is a passage nothing qualifies for. It is a candidate for a generated
graphic or visual asset.

### 4.10 continuity_intelligence

`Repair(repair_id, kind: RepairKind, owner: Owner, availability: Availability, description,
difficulty, tradeoffs, rank, at_source_seconds, alternative, conflicts)`
(`domain/model.py:89`). `Owner` (`domain/values.py:132`) is one of `editing_intelligence,
broll_intelligence, reframing_intelligence, video_improvement, audio_improvement, person`.
**No Layer 4 module is an owner.** `COLOR_CORRECTION` goes to video_improvement. `AUDIO_CROSSFADE`,
`ROOM_TONE` and `AMBIENCE_BRIDGE` go to audio_improvement. Adding `transition_engine`,
`visual_style_engine` or `music_and_sound_design` as owners would edit continuity (another
module). Leave it as is, or coordinate the edit.
**Input precedent:** continuity owns `ProposedSequence(edit_plan_id, edit_plan_version, clips:
tuple[ProposedClip(clip_id, asset_id, source_start, source_end, timeline_start, timeline_end:
Fraction, section_id, beat_id, layer, audio_bridge_in/out, reuse_of, ...)], project_id,
story_format)` (`domain/facts.py:21,43`). `media_pipeline/application/sequence.py:sequence_of(plan)`
is "the one place that knows both shapes". Use this for every Layer 4 module that needs the edit:
your own `Proposed<X>` DTO in your contracts, and a mapper in `media_pipeline/application/`.

### 4.11 reframing_intelligence

`TargetFormat(name, aspect: Rational, width, height)`. Use `TargetFormat.of("9:16")`, which gives
1080x1920 (`STANDARD_RESOLUTIONS` `geometry.py:23`: 16:9 is 1920x1080, 9:16 is 1080x1920, 1:1 is
1080x1080). A custom aspect gets a long side of 1920. `Crop(x, y, w, h)` is the normalised
top-left window of the display-oriented source. `CropKeyframe(time: Rational, crop,
interpolation: hold|linear)`. `Recommendation` (`model.py:156`) has `recommendation_id,
segment_key, asset_id, shot_id, start, end, strategy, feasibility, executable, keyframes,
elements, scores, ...` and `crop_at(seconds)`. `ReframingAnalysis.covering(asset_id, start, end)`
and `.unreframeable()`. `SafeArea(name, x0, y0, x1, y1)` (`profile.py:221`) is OUTPUT-relative.
Reframing only knows the safe areas the caller passes (`ReframingConfig.safe_areas`). Captions
and motion graphics should state their reserved rectangles in their plan, so a later reframing run
can take them as config. There is no automatic feedback loop.

---------------------------------------------------------------------------------------------------

## 5. image_adjustment (complete) and how visual_asset_generation extends it

Files (981 lines): `domain/values.py` (199), `domain/errors.py`, `application/adjust_image.py`
(193), `application/contracts.py`, `application/ports.py`, `infrastructure/pillow_renderer.py`
(154), `infrastructure/image_ops.py` (134), `infrastructure/rembg_segmenter.py` (63), `module.py`.

```python
ADJUSTMENT_VERSION = 1; ADJUSTMENT_OPERATION = "image_adjustment"; DEFAULT_MODEL = "birefnet-general-lite"
_MAX_CANVAS = 4096; _MIN_CANVAS = 64; _MAX_EFFECT = 500.0; _MIN_SUBJECT_AREA = 16
class Centering(StrEnum): CENTER; CENTER_OF_MASS
HexColor(value "#RRGGBB"); HexColor.of(raw); .rgb
BackgroundRemoval(enabled=True, model=DEFAULT_MODEL)
Glow(enabled=False, color=HexColor("#FF2A5F"), opacity=0.85, radius=30.0, spread=12.0); .sigma=radius/2; .extent
Canvas(width=1080, height=1080, padding=60, centering=CENTER)          # NO background colour: output is transparent
MaskStats(foreground_ratio, bbox); MaskLimits(min_foreground_ratio=0.005, max_foreground_ratio=0.95).check(stats)
AdjustmentSettings(background_removal, glow, canvas, mask_limits).to_config()   # identity; disabled effects collapse; mask_limits excluded
AdjustImageCommand(source_asset_id, settings=AdjustmentSettings(), group_ids=(), inherit_source_groups=True)
class ImageAdjuster(Protocol): execute(cmd, ctx) -> Result[DerivedAssetResultDto, AdjustError]   # AdjustError = MediaError | AdjustmentFailed
errors: AdjustmentFailed(DomainError) <- ImageUnreadable, SegmentationRejected
ports: ImageRenderer.render(source, destination, settings) -> RenderReport(mask: MaskStats, scale, subject_box)
infrastructure: Segmenter(Protocol).matte(image, model) -> float32 [0, 1]  (declared in pillow_renderer.py:25)
```

- Flow (`adjust_image.py:57`): the source must be IMAGE. The use case calls
  `library.fingerprint(source, "image_adjustment", to_config(), 1)`, then `find_derived_asset`. On
  a hit it reuses the asset. Otherwise it renders `adjusted.png` in a temp dir and calls
  `register_derived(...)` with flat metadata (`processing_type`, `background_removal_model`,
  `glow_*`, `canvas_*`, `mask_foreground_ratio`, `subject_box`, `subject_scale`). The result joins
  the source's groups.
- Rembg (`rembg_segmenter.py`): `RembgSegmenter(allow_download=False)` loads the session lazily
  once per model (CPUExecutionProvider) and calls
  `remove(image, only_mask=True, post_process_mask=False)`. A model that is not on disk raises
  `ExternalSystemError` with the fetch command. `_on_disk` uses
  `rembg.sessions.sessions[model].resolve_existing(...)`. Fetch:
  `uv run python scripts/fetch_models.py --background-removal birefnet-general-lite` (into
  rembg's `~/.u2net`; nothing is there now).
- Pillow pipeline (`pillow_renderer.py`, `image_ops.py`): `clean_matte`, `mask_bbox`,
  `visible_bbox`, `decontaminate` (edge colour), `resize`, `make_glow(alpha, spread, sigma,
  opacity)`, `composite_over_glow`, centring by box or centre of mass, `_fill_transparent`, and
  save as RGBA PNG.
- Tests: `tests/unit/image_adjustment/test_domain.py` (11 tests: colour, glow, canvas, identity,
  mask limits); `tests/integration/image_adjustment/test_adjust_image.py` (13 tests with a real
  library and `ColourSegmenter` as a fake model);
  `tests/integration/image_adjustment/test_background_model_policy.py` (3 tests: offline policy).
- Importers: only `bootstrap/modules.py` and its own tests. Architecture tests also NAME it as a
  module others must not depend on (`test_asset_relationships_boundaries.py:34`,
  `test_audio_visual_alignment_boundaries.py:28`, `test_content_intelligence_boundaries.py:30,108`,
  `test_media_inspection_boundaries.py:19`, `test_proxy_manager_boundaries.py:23`,
  `test_video_improvement_boundaries.py:21`), and `test_application_lifecycle.py` lists it.
- Doc defects: the README example uses `Canvas(background_color=...)`, which does not exist. The
  README says "ISNet" but the default is BiRefNet.

**Recommendation for visual_asset_generation.** Rename in place: `git mv
modules/image_adjustment modules/visual_asset_generation`, keeping
`ADJUSTMENT_OPERATION = "image_adjustment"` and `ADJUSTMENT_VERSION = 1` so every stored cutout
remains a cache hit. Then add shadow, outline, variants, asset documents and validation as new
optional settings. A disabled feature must collapse out of `to_config()`, so existing identities
stay unchanged.

| Option | For | Against |
| --- | --- | --- |
| A. Rename in place (recommended) | One owner for image pixels, no duplication, the owner's "extend, not duplicate" kept. Only bootstrap imports it. The test dirs move with it. | It touches `bootstrap/modules.py`, `test_application_lifecycle.py`, the 6 architecture tests (stale names would silently weaken them, so update them) and the README. The module name changes (`name: str = "visual_asset_generation"`). The operation string must NOT change. Merge-conflict risk with other agents editing the same architecture tests. |
| B. Wrap: new `visual_asset_generation` depends on `image_adjustment.application.contracts.ImageAdjuster` | No rename churn. image_adjustment stays stable. | Two modules for one concern. New pixel features need either edits inside image_adjustment (another module's internals, against the rules unless the same agent owns both) or a second Pillow pipeline (duplication). |

Either way, image generation has no model in the repo (no diffusion, no generator). That part can
only be a stub or `not_available`.

---------------------------------------------------------------------------------------------------

## 6. audio_improvement: what music must hand off to

- `AudioMixing.execute(MixAudioCommand(voice_asset_id, music_asset_id, profile=DEFAULT_PROFILE,
  overrides: Mapping[str, JsonValue] = {}), ctx) -> Result[MixResult, ImproveError]`
  (`application/mix_audio.py`). `MixResult` has `voice_asset, music_asset, asset` (a derived
  asset of the VOICE), `profile, provenance, analysis_after, music_gain_db, checks, warnings,
  created`.
- The mixer exists: `FfmpegMixer` (`infrastructure/ffmpeg_mixer.py`) builds one filter graph. The
  music is looped (`-stream_loop -1`), gained to `music_offset_lu` below the voice, faded out at
  the voice end, ducked by a `sidechaincompress` keyed on the voice, then
  `amix duration=first normalize=0`. The output is `pcm_f32le` WAV at the voice rate and channel
  count, then mastered. It mixes ONE music asset over the whole voice. There are no per-section
  cues, no SFX, no speech-aware ducking map and no music start or end offsets. The docstring says
  speech-aware ducking "plugs in by producing a different AudioMixer configuration, not by this
  module importing Audio Intelligence".
- `MixSettings` (`domain/settings.py:247`): `music_offset_lu=-18.0` (-40..0),
  `duck_threshold_db=-40.0`, `duck_ratio=6.0`, `duck_attack_ms=20.0`, `duck_release_ms=500.0`,
  `music_fade_out_s=2.0`.
- Loudness (`MasteringSettings`, `settings.py:200`): `target_lufs=-14.0`,
  `true_peak_ceiling_dbtp=-1.0` (default `youtube`). Profiles: `youtube` (default),
  `social_video` (-14/-1), `podcast` (-16/-1), `cinematic` (-23/-2), `custom`. Overrides use
  dotted paths, for example `{"mix.music_offset_lu": -20.0}`.
- `read_provenance(asset.metadata) -> ProcessingProvenance | None` tells what was done to an
  asset.
- Hand-off: `music_and_sound_design` should produce a stored music/SFX PLAN (cues with exact
  times, gains, ducking ranges) and, when rendering, a stem (WAV). It can call `AudioMixing` for
  the simple "one bed under the voice" case. Anything richer needs a new `AudioMixer`
  configuration in audio_improvement (that module's change) or its own FFmpeg adapter that renders
  a music stem which `AudioMixing` then mixes. Do not re-master: mastering belongs to
  audio_improvement.

---------------------------------------------------------------------------------------------------

## 7. media_pipeline: adding opt-in Layer 4 tasks after edit_plan

The CLI is still `scratch/run_pipeline.py` (542 lines, present after `927a843`, which only removed
the demo scripts). `bootstrap/cli.py` has no pipeline command. Run it with
`uv run python scratch/run_pipeline.py --input <file> --stages edit_plan,<new> [--dry-run]
[--cpu-only]`. Its flags: `--input --asset-id --home --output --report-json --config --stages
--all --content-uses-video --cpu-only --story-format --story-llm --edit-profile --edit-target
--edit-target-hard --pacing-profile --pacing-strategy --broll-profile --continuity-profile
--continuity-strictness --reframe-target --reframe-resolution --reframe-profile
--reframe-safe-area --gpu-slots --cpu-slots --light-slots --max-workers --dry-run --verbose`.
`--config` takes JSON keys that mirror `StageSettings`.

Model: tasks are PER ASSET (`TaskId(asset_id, kind)`). Lanes are `LIGHT=4, CPU=2, GPU=1`
(`application/runner.py:46`). `needs` means skip if the upstream did not deliver. `wants` means
wait for it, then run regardless. Results flow downstream as typed payloads:
`job.payload_as(TaskId(asset_id, kind), Type)` through `self._upstream(job, TaskKind.X, Type)`
(`executors.py:865`). `DEFAULT_STAGES` (`application/run_pipeline.py:47`) is
`inspect, thumbnail, audio_analysis, video_analysis, content_analysis, alignment, story, pacing`.
Layer 4 stays opt-in.

Exact steps. Commit `8f52a39` (reframing) is the template: 9 files.

1. `domain/values.py`: add `TaskKind.<NEW> = "<new>"` (the CLI parses `--stages` by value).
2. `domain/plan.py`: add a `STAGES[TaskKind.<NEW>] = Stage(Lane.LIGHT|CPU|GPU,
   needs=_k(TaskKind.EDIT_PLAN), wants=_k(...), needs_video=..., needs_audio=...)` entry and a
   docstring bullet that says why. Analysis or plan tasks use LIGHT. FFmpeg/HyperFrames renders
   use CPU. Torch inference uses GPU (the lane only limits concurrency; CPU torch still runs).
   Example: `caption -> needs EDIT_PLAN, wants AUDIO_ANALYSIS, CONTENT_ANALYSIS, REFRAMING,
   INSPECT`.
3. `application/executors.py`: import from `<module>.application.contracts`. Add the
   `StageSettings` fields (`None` = the owner's default; no default when a choice is required,
   like `reframing_target`). Add the constructor parameter and attribute, the `table()` entry
   (`:286`) and a method `def <new>(self, job: TaskJob) -> TaskResult`. In that method: build the
   command from in-memory upstream payloads (`self._upstream(job, TaskKind.EDIT_PLAN, EditPlan)`
   mapped through your mapper). `match` on the result: `Ok` gives
   `_outcome(result.created, **facts)` plus `payload=...`. `Err` goes to `_from_error(error)`. Add
   the "nothing to do" code to `_UNAVAILABLE_CODES` (`:139`).
4. `application/<new>_input.py` (like `sequence.py`): map `EditPlan` to your own input DTO. This
   is the only place that knows both shapes.
5. `module.py`: add `<new>_analyzer=c.resolve(<Producer Protocol>)` to `ModuleExecutors(...)`.
6. Tests: `tests/unit/media_pipeline/test_<new>_task.py` (copy `test_reframing_task.py`, 151
   lines). Add `<new>_analyzer=owners.get("<new>_analyzer", fail)` in
   `tests/unit/media_pipeline/test_executors.py:executors()`. The existing
   `set(executors().table()) == set(TaskKind)` assertions then pass.
7. `scratch/run_pipeline.py`: add flags and `--config` keys, and pass them into `StageSettings`.
8. `media_pipeline/README.md` and the `AI_CONTEXT.md` §11 paragraph.
9. If edit_plan should WAIT for a Layer 4 result, do not do it: Layer 4 runs after the edit plan.
   A cycle (edit_plan wants X, X needs edit_plan) breaks the graph.

All seven agents edit `values.py`, `plan.py`, `executors.py`, `module.py` and `test_executors.py`.
Serialize these edits or let one integrator add every task after the modules land.

---------------------------------------------------------------------------------------------------

## 8. Model and environment inventory (this machine, 2026-11-02)

| Capability | Library / model | Fetched by | Stored at | Present now |
| --- | --- | --- | --- | --- |
| Speech recognition | whisperx 3.8.6 (faster-whisper 1.2.1, ctranslate2 4.8.2), model `large-v3` (`audio_intelligence/domain/values.py:77`) | `fetch_models.py --speech large-v3` | HF hub cache | no |
| Word alignment | whisperx alignment (wav2vec2 per language) | `--speech-language en` / `de` | HF / torch cache | no |
| Diarization (opt-in, gated) | pyannote.audio 4.0.7, `pyannote/speaker-diarization-community-1` | `--diarization` (HF token) | `<cache>/audio_intelligence/models/` | no |
| Prosody | praat-parselmouth 0.4.7 (no model) | n/a | n/a | yes (library) |
| Sentence embedder | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` via transformers 4.57.6 | `fetch_models.py` (default) | `<cache>/content_intelligence/models/<org>--<name>` | no |
| LLM | `Qwen/Qwen2.5-1.5B-Instruct` (default; `--llm` for others) | `fetch_models.py` | same folder | no |
| CLIP (picture + text embeddings) | `openai/clip-vit-base-patch32` (`clip_engines.py:61`) | NOT in fetch_models: video analysis with `RuntimeConfig(allow_model_download=True)` | HF hub cache | no |
| VLM descriptions | `HuggingFaceTB/SmolVLM-256M-Instruct` (`vlm_descriptions.py:37`) | same as CLIP | HF hub cache | no |
| Object detector | torchvision 0.23 `ssdlite320_mobilenet_v3_large` (COCO, `profiles.py:81`) | `allow_model_download` (pinned checksum) | `<cache>/video_intelligence/models` or `~/.cache/torch/hub/checkpoints` | no |
| Face / pose / hands | mediapipe 1.1.0: `face_landmarker.task`, `blaze_face_short_range.tflite`, `pose_landmarker_lite.task`, `hand_landmarker.task` | `allow_model_download` | `<cache>/video_intelligence/models` | no |
| OCR | rapidocr 3.10.0 + onnxruntime 1.30.0 (models ship in the wheel) | n/a | wheel | yes |
| Background removal | rembg 2.0.85, `birefnet-general-lite` default (u2net/isnet available by name) | `--background-removal <model>` | `~/.u2net` (rembg) | no |
| Other installed (transitive) | numpy 2.5.3, scipy 1.18.1, pillow 12.3.0, opencv-python-headless and opencv-contrib 5.0.0.93, scikit-image 0.26, scikit-learn 1.9.1, av 19.0.1, torchaudio 2.8.0, torchcodec, numba 0.68, matplotlib 3.11, fonttools 4.66, jinja2 3.1.6, pymatting 1.1.16, lightning 2.6.6, torch-audiomentations | n/a | `.venv` | yes |
| NOT installed | librosa, soundfile, audioread, pyloudnorm, essentia, madmom, aubio, demucs, PANNs/CLAP/AST audio classifiers, OpenColorIO/colour-science, sentence-transformers package, open_clip, diffusers | n/a | n/a | n/a |
| GPU | AMD Radeon (integrated), no NVIDIA. `torch 2.8.0+cu128`, `cuda.is_available() == False`. Device selection: `video_intelligence/infrastructure/torch_support.py:resolve_device` | n/a | n/a | CPU only |
| External tools | FFmpeg/FFprobe: **not on PATH**. Node v24.21.0, npm 11.19.0, npx: present. HyperFrames: not cached. | n/a | n/a | n/a |

`<cache>` is `AppPaths.for_user().cache_dir` = `C:\Users\L14\AppData\Local\media-house\Cache`.
`video_intelligence/module.py:84` defaults its model store to `Path("models")` when no dir is
given. Music, beat or audio classification: **none exists.** Audio-to-audio similarity: none.
Text-to-music search: only via `TextEncoder` on titles and tags. OpenColorIO, which CLAUDE.md asks
for, is not installed. `video_improvement` grades with FFmpeg `colorspace=`/`lut3d=`/`hqdn3d=`/
`unsharp=`. Rules (AI_CONTEXT §11): libraries load lazily. A missing library or model makes only
that capability `not_available`. Models load once per run. Nothing downloads unless the run allows
it. GPU never changes a result beyond tolerance.

---------------------------------------------------------------------------------------------------

## 9. Tests and architecture checks

Layout (mirror the source):

```text
tests/unit/<module>/test_*.py                 pure domain + use cases with fakes (no files, no Qt)
tests/integration/<module>/conftest.py        `app` fixture: real Application under tmp_path (pytestmark = pytest.mark.integration)
tests/integration/<module>/test_*.py
tests/architecture/test_<module>_boundaries.py   your module's own rules (copy reframing's 169 lines)
tests/support/<prefix>_fakes.py               builders for your inputs (refr_fakes.py, pace_fakes.py, edit_fakes.py, ...)
```

- Real app fixture (`tests/integration/reframing_intelligence/conftest.py`):
  `Application.start(StartupOptions(env={}, home=tmp_path / "home",
  environment=Environment.TEST))`, then `application.shutdown()`. Resolve contracts with
  `app.container.resolve(MediaLibrary)`.
- Storing a fake upstream result in a real library (`tests/support/cont_library.py:115`):
  `DerivedDocuments(library, app.container.resolve(AppPaths)).store(asset, analysis_to_json(a),
  filename=..., spec=DocumentSpec(RESULT_OPERATION, 1, {"case": ...}), display_name=...,
  metadata={})`. Importing a clip (`cont_library.py:41`) uses `make_video` (FFmpeg; unavailable
  here).
- Without FFmpeg: write PNG/JPEG with Pillow, WAV with `tests/support/media_factories.py:make_wav`
  (pure Python), and JSON documents with `document_type`. Those import without ffprobe. Audio WAV
  import still runs ffprobe, so it cannot work here. Gate FFmpeg tests with
  `pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, ...)`
  (pattern: `tests/integration/audio_improvement/conftest.py:132`).
- Fakes (`tests/support/fakes.py`): `FixedClock`, `T0`, `RecordingPublisher`,
  `ImmediateJobScheduler`, `InMemoryWorkspaceRepository`. `tests/support/media_fakes.py` has
  `InMemoryMediaStore`, which implements media_library's repository ports. Tests may import
  internals. `tests/support/asset_dto.py:make_asset(asset_id="clip", **changes)` builds a
  `MediaAssetDto`. `tests/support/refr_fakes.py`, `edit_fakes.py`, `story_fakes.py`,
  `pace_fakes.py`, `video_fakes.py` and `audio_fakes.py` build upstream documents.
- Test-file rules (`pyproject.toml`): `filterwarnings = ["error"]`, `--strict-markers`, the
  markers `integration` and `presentation`. mypy strict covers `tests/` too.
- Architecture (`tests/architecture/analysis.py`): `scan(SRC)` parses every file and is NOT
  cached, so every test re-scans. `test_dependency_rules.py` covers the layer rules, third parties
  per layer (none in domain or application), forbidden stdlib (`os`, `subprocess`, `logging`, ...
  in domain; `os`, `shutil`, `subprocess`, `sqlite3`, ... in application), Qt only in
  presentation, contracts-only cross-module imports, and no cycles. The per-module tests add: the
  domain imports no other module; no producer Protocols; no `datetime.now`/`get_logger`/`random`/
  `print(` in the domain; no timeline-backend names (`davinci|resolve_script|fusionscript`); and
  the consumer allowlists (sec. 0).
- Speed: the full architecture suite takes about 4 min. `uv run python -m pytest
  tests/architecture/test_dependency_rules.py -q` took **12.3 s** here. Run that plus your own
  `test_<module>_boundaries.py` and the allowlist files you touched. Run the full suite once
  before handing back.
- Gates: `uv run ruff format --check .`, `uv run ruff check .`, `uv run python -m mypy`,
  `uv run python -m pytest tests/unit tests/architecture -q`, then
  `uv run python -m pytest tests/integration -q`. `scripts/check.py` runs these with bare `pytest`
  and `mypy`, which this machine's app-control policy blocks.

---------------------------------------------------------------------------------------------------

## 10. Creative constants today, and ONE proposed config-schema convention

How settings are exposed today:

| Module | Where | Exposure |
| --- | --- | --- |
| audio_improvement | `domain/settings.py` groups (`MixSettings`, `MasteringSettings`, Eq, Dynamics, DeEsser) with `__post_init__` range checks; `domain/profiles.py` built-ins + `resolve_profile(name, overrides)` with dotted paths "for a future settings UI" | profile name + `Mapping[str, JsonValue]` overrides |
| reframing / pacing / editing / story | `domain/profile.py`: nested frozen dataclasses with `#:` comments, `problems()`, `PROFILES`, `DEFAULT_PROFILE`, `STORY_FORMAT_PROFILE`, `<X>Config.resolve()` | profile name or custom instance; no overrides by path |
| video_intelligence | `domain/profiles.py` `ProcessingProfile` (triage/fast/standard/deep) + `RuntimeConfig` | name or instance |
| image_adjustment | `domain/values.py` (Glow `#FF2A5F`, opacity 0.85, radius 30, spread 12; Canvas 1080x1080, padding 60; limits as `_MAX_*` module constants) | settings dataclasses only, no profiles |
| AppSettings | pydantic `Field(ge=, le=)` | app-level only |

There is no field metadata anywhere (`grep "field(.*metadata="` finds nothing). Descriptions live
in `#:` comments, ranges live in `problems()` and `__post_init__`, and versions live in
`PROFILE_VERSION` and `profile.version`.

**Proposed convention.** No new package, no new file outside each module. Every Layer 4 module
keeps its knobs in `domain/profile.py` as nested frozen dataclasses (the existing pattern) and
describes each user-facing knob with standard-library `dataclasses.field` metadata, using a fixed
set of plain keys:

```python
#: Where captions sit, as a share of the output height from the top.
baseline_y: float = field(
    default=0.82,
    metadata={
        "label": "Caption baseline",
        "help": "Share of the output height from the top.",
        "unit": "fraction",
        "min": 0.5,
        "max": 0.95,
        "step": 0.01,  # numbers: min/max(/step)
    },
)
style: str = field(
    default="pop", metadata={"label": "Animation", "choices": ("pop", "word", "none")}
)
color: str = field(default="#FFFFFF", metadata={"label": "Text colour", "kind": "color"})
```

- Keys: `label` (required), `help`, `unit`, `min`, `max`, `step`, `choices`, `kind`
  (`"color" | "font" | "asset_id" | "seconds"` when the Python type is not enough), and
  `advanced: bool`. A field without `label` is internal and not shown.
- Validation stays where it is: `problems()` must enforce the same `min`/`max`/`choices`. A unit
  test per module walks `dataclasses.fields()` recursively and asserts `problems()` rejects
  `min - step` and `max + step`, so metadata and checks cannot drift.
- Versioning: `PROFILE_VERSION` (schema of the knobs) plus `profile.version` (values). The
  metadata never enters the cache key, because `dataclasses.asdict` ignores it. Renaming or
  removing a knob bumps `PROFILE_VERSION`.
- Overrides: adopt audio_improvement's dotted-path resolver
  (`audio_improvement/domain/profiles.py:62-113`, about 50 lines, a pure domain function) per
  module as `<X>Config(profile="name", overrides={"captions.baseline_y": 0.8})`. A future
  settings UI then needs only `contracts.<X>Config`, `PROFILES` and `dataclasses.fields()`. No
  shared registry is needed. A UI discovers modules through their contracts.
- Where: `modules/<m>/domain/profile.py` (pure stdlib, allowed in the domain), re-exported from
  `modules/<m>/application/contracts.py`. If a shared typed helper is ever wanted (a `Setting`
  value class plus `describe(cls)`), its home is `core/domain/` (pure value types every domain may
  import), not `shared/`. That needs one owner-approved file, so do not add it now.
- visual_style_engine owns the channel identity (colours, fonts, safe areas, motion feel). The
  other Layer 4 modules take a `style_ref` (the visual style document's library id plus version)
  in their config, read it through `VisualStyleCatalog` in their own application layer, and put
  the style's identity in their cache key. Every other module must then list
  `visual_style_engine` in its own allowlist test.

---------------------------------------------------------------------------------------------------

## 11. Layers 1-3 for a fireworks channel (night footage, bangs, product/price OCR, 9:16 Shorts)

Gaps observed in passing. A separate reassessment follows.

- **Bangs:** no sound-event detector ships. A bang becomes an `energy_spike` acoustic event at
  best, or noise in the speech pipeline. WhisperX on loud non-speech may hallucinate words. Its
  `AudioEvent.kind` has no `explosion` or `crackle`. `audio_improvement` denoising and declipping
  run on the whole asset and may flatten the bangs, so "do not destroy good media" matters here.
- **Flash sync:** `EventKind.FLASH` exists (`video_intelligence/domain/events.py:111`, confidence
  0.7). Alignment's `SoundAction` pairs audio and visual events. This is the best existing hook for
  "bang on flash", but it is unvalidated on fireworks.
- **Night picture:** `QualityMetrics` has `luma_p1/p50/p99`, `crushed`, `clipped`, `noise_sigma`
  and `flicker`. Fireworks are clipped highlights on a crushed black. Thresholds tuned for daylight
  may flag every shot as bad quality, and `flicker` will be high by nature. The detector is COCO
  `ssdlite`: it has no class for fireworks, smoke, sparks or rockets. A dark scene with no person
  gives reframing no track, so its shots become `unreframeable` or keep the original framing.
  Saliency and motion are the only cues.
- **Product and price OCR:** RapidOCR (default models: Chinese and English) reads packaging. German
  umlauts and euro prices are untested. `content_intelligence` `designations()` and key info
  recognise `€` and `EUR` in SPEECH, but nothing links OCR text to a product or a price as a
  structured fact. Price graphics need that link.
- **Shorts 9:16:** `TargetFormat.of("9:16")` exists, and the profiles `shorts_reels` (reframing,
  pacing) and `short` (story, editing) exist. Safe areas are not assumed: captions and the
  platform UI must be supplied as `SafeArea`s.
- **Music:** nothing analyses music (tempo, sections, mood, loudness per track). The whole library
  of music and SFX has to be built by `music_and_sound_design` (sec. 3, sec. 6).

---------------------------------------------------------------------------------------------------

## 12. Coordination checklist for the seven parallel agents

Files more than one agent must touch. Assign one owner, or serialize.

| File | Why |
| --- | --- |
| `bootstrap/modules.py` | import + one tuple entry per module |
| `tests/integration/test_application_lifecycle.py:86` | exact set of module names |
| `tests/architecture/test_editing_intelligence_boundaries.py:24,166` | `FUTURE_MODULES` blocks four of the names (sec. 0, risk 1) |
| `tests/architecture/test_{story,pacing,reframing,continuity,broll}_*_boundaries.py` | consumer allowlists when a Layer 4 module reads those catalogs |
| `media_pipeline/{domain/values.py,domain/plan.py,application/executors.py,module.py}`, `tests/unit/media_pipeline/test_executors.py`, `scratch/run_pipeline.py` | one task per module |
| `image_adjustment/**` and 6 architecture tests | the rename, if option A is chosen |
| `AI_CONTEXT.md` §11, `ARCHITECTURE.md` | one paragraph per module |

Per-module definition of done: `uv run python -m pytest tests/architecture/test_dependency_rules.py
tests/architecture/test_<m>_boundaries.py -q` passes. Unit tests use fakes. Integration tests skip
cleanly without FFmpeg and models. Nothing downloads during a run. Results are stored only through
`DerivedDocuments` and `register_derived`, with a version, `supersedes` and `inputs`.
