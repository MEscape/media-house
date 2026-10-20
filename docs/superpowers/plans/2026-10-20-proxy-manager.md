# proxy_manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status (2026-10-20, later):** Task 7 is complete as Slice 2 (waiting, lease, remove, prune).
> Tasks 8-9 (queue, selection) are not started.
>
> **Earlier status:** Tasks 1-6 are implemented as Slice 1, with the amendments at the end of
> the spec (artifacts table, ownership-gated repair, no `force`, no event subscription, scratch
> ownership by attempt row). Task 7 is partly done (recovery of dead attempts and scratch sweep,
> cancellation, cleanup); remove/prune, waiting and lease takeover are not. Tasks 8-10 are not done
> except the architecture test, README and `AI_CONTEXT.md`, which exist.

**Goal:** A `proxy_manager` module that creates, validates, tracks, replaces and safely removes proxies as library-derived assets with a stable source identity.

**Architecture:** Hexagonal vertical slice. Library owns bytes and the derivation fingerprint; the module owns a SQLite store (slot assignment, attempts), a pure-domain spec/validation/selection core, a private FFmpeg adapter, and a priority admission queue in front of the unchanged `JobScheduler`.

**Tech Stack:** Python 3.13, stdlib `sqlite3`, FFmpeg/ffprobe via `ProcessRunner`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-20-proxy-manager-design.md`

## Global Constraints

- Python >= 3.13, PEP 695 generics, `uv`; strict `mypy --strict`; `ruff` format/lint run only on explicit module paths (repo has pre-existing findings).
- Absolute imports only. Domain: no I/O, no `os`, `logging`, `sqlite3`, `subprocess`, `threading`, `shutil`, `tempfile`. Application: no infrastructure, `sqlite3`, `subprocess`, `os`, `shutil`, `core.modules`.
- Cross-module imports only `media_house.modules.<other>.application.contracts`.
- `subprocess`, `os.environ`, `print()` banned in library code; tools via `ProcessRunner`; no `shell=True`; no module-level mutable state.
- Expected failures return `Err`; infrastructure failures raise typed errors `from exc`.
- Only `bootstrap/modules.py` (one line), `media_library` contract (Task 1), `AI_CONTEXT.md` and docs change outside `modules/proxy_manager/` and `tests/`.
- Originals are never modified or deleted. A file is deleted only if an attempt row proves this module owns it.
- Gate before any "done": `uv run pytest tests/architecture`, `uv run mypy`, ruff on the touched paths, then the module's tests.

## Review Focus

- Source file removed or changed between request and register: expect `Err`/typed failure, no slot flip, temp dir gone.
- Library hit exists but blob missing or corrupt: expect regeneration, never a "ready" report.
- Two concurrent `ensure` calls for one source+profile: expect one encode, both callers get the same artifact.
- Cancel during validation or publish: expect previous proxy still active, no temp files left.
- Sources with no audio, no video, two audio streams, rotation, VFR, nonzero start: expect explicit result (valid, valid-with-transforms or unsupported), never a silent wrong mapping.

---

### Task 1: Library `register_derived` accepts a role

**Files:**
- Modify: `src/media_house/modules/media_library/application/contracts.py` (`register_derived` signature), `src/media_house/modules/media_library/application/library.py` (pass `role` into `RegisterDerivedCommand`)
- Test: `tests/unit/media_library/` (existing derived-assets test file)

**Interfaces:**
- Produces: `MediaLibrary.register_derived(..., role: AssetRole = AssetRole.DERIVED)`; `AssetRole` already exported.

- [ ] **Step 1:** Test `test_register_derived_with_preview_role_is_hidden_from_default_search`: register with `role=AssetRole.PREVIEW`; assert `asset.role is AssetRole.PREVIEW`, `find_derived_asset` finds it, `search()` default page excludes it. Also `test_register_derived_default_role_unchanged`.
- [ ] **Step 2:** Run `uv run pytest tests/unit/media_library -k register_derived -v`; expect FAIL (unexpected keyword `role`).
- [ ] **Step 3:** Add keyword-only `role` to the Protocol and `library.py`; `ORIGINAL` is already rejected by `RegisterDerivedAsset`.
- [ ] **Step 4:** Run the same command plus `uv run pytest tests/unit/media_library tests/integration/media_library`; expect PASS.
- [ ] **Step 5:** Commit `feat(media_library): optional role when registering a derived asset`.

### Task 2: Domain values, profiles, resolved spec

**Files:**
- Create: `src/media_house/modules/proxy_manager/{__init__,domain/__init__}.py`, `domain/values.py`, `domain/errors.py`, `domain/profile.py`, `domain/source.py`, `domain/spec.py`
- Test: `tests/unit/proxy_manager/test_profile_and_spec.py`

**Interfaces:**
- Produces:
  - `values.py`: `PROXY_OPERATION = "proxy"`, `SPEC_VERSION = 1`, `ADAPTER_REVISION = "1"`, `METADATA_KEY = "proxy"`; `StrEnum`s `FrameRatePolicy{SOURCE_TIMING, CONSTANT}`, `AudioPolicy{NONE, FIRST, ALL}`, `EncoderPolicy{SOFTWARE, AUTO}`, `Codec{H264, PRORES}`, `Workload{ANALYSIS, EDITING}`, `Verdict{VALID, VALID_WITH_TRANSFORMS, INVALID, UNVERIFIABLE}`.
  - `errors.py`: `ProxyError(DomainError)`, `InvalidProfile`, `UnsupportedSource`, `ValidationFailed`.
  - `profile.py`: `@dataclass(frozen=True, slots=True) ProxyProfile(name, version, codec, max_long_edge, quality, frame_rate_policy, constant_rate, audio_policy, audio_bitrate_kbps, pixel_format, container, encoder_policy, full_decode_validation)`; `PROFILES: Mapping[str, ProxyProfile]` with `analysis` (H.264 mp4, long edge 640, audio FIRST) and `edit` (ProRes Proxy mov, long edge 960, audio ALL); `resolve_profile(name: str, overrides: Mapping[str, JsonValue]) -> ProxyProfile` raising `InvalidProfile` for unknown name/key, odd dimensions, `CONSTANT` without rate, `PRORES` with mp4, `max_long_edge < 2`.
  - `source.py`: `SourceStream(index, kind, codec, language, is_attached_pic)`, `SourceFacts(width, height, rotation, sample_aspect_ratio, frame_rate, average_frame_rate, variable_frame_rate, duration, start_time, streams, timecode, origin)`; properties `display_size`, `video_stream`, `audio_streams`; `origin` is `"inspection" | "probe"`.
  - `spec.py`: `ResolvedSpec` (frozen: profile name/version, out width/height, codec, pix_fmt, container, frame-rate policy/rate, mapped stream indexes with roles, audio params, rotation policy, transformations tuple, adapter revision); `resolve_spec(profile, facts) -> ResolvedSpec`; `ResolvedSpec.to_config() -> dict[str, JsonValue]` (canonical, excludes encoder/ffmpeg version).

- [ ] **Step 1:** Tests with the spec's exact values: `test_overrides_change_only_named_fields`; `test_contradictory_profile_is_rejected` (parametrized per rule above); `test_resolve_spec_scales_display_size_and_keeps_aspect` (1920x1080 -> 640x360; 1080x1920 rotation 90 stays portrait; even dimensions only; never upscales a smaller source); `test_vfr_with_constant_policy_records_transformation`; `test_attached_pic_never_selected_as_video`; `test_no_audio_source_maps_no_audio`; `test_to_config_is_deterministic_and_excludes_encoder`; `test_audio_only_source_is_unsupported_for_video_profiles`.
- [ ] **Step 2:** Run `uv run pytest tests/unit/proxy_manager -v`; expect FAIL (module missing).
- [ ] **Step 3:** Implement signatures above; scaling uses display size (SAR then rotation) and maps back to coded orientation.
- [ ] **Step 4:** Run tests; expect PASS. Run `uv run pytest tests/architecture`.
- [ ] **Step 5:** Commit `feat(proxy_manager): profiles and resolved generation spec`.

### Task 3: Domain validation policy and reuse decision

**Files:**
- Create: `domain/validation.py`, `domain/reuse.py`
- Test: `tests/unit/proxy_manager/test_validation.py`, `test_reuse.py`

**Interfaces:**
- Consumes: `ResolvedSpec`, `SourceFacts`.
- Produces:
  - `OutputFacts(width, height, rotation, codec, pix_fmt, container, frame_rate, packet_count_video, duration, start_time, last_pts, streams, size_bytes, decode_errors: int | None)`.
  - `VALIDATOR_VERSION = 1`; `Check(name, passed, detail)`; `ValidationReport(verdict, checks, validator_version, transformations)`; `validate(spec, source, output) -> ValidationReport`; `duration_tolerance(source, spec) -> float` (source frame + output frame + audio delay, no constants in seconds).
  - `reuse.py`: `ReuseDecision(reusable: bool, reasons: tuple[str, ...], revalidate: bool)`; `decide_reuse(*, wanted_fingerprint, artifact_fingerprint | None, blob_present, report | None, current_validator_version, toolchain: str, recorded_toolchain: str | None) -> ReuseDecision`. Reasons are stable strings: `missing_artifact`, `blob_missing`, `never_validated`, `validator_outdated`, `toolchain_drift` (advisory, still reusable), `invalid_report`, `fingerprint_mismatch`.

- [ ] **Step 1:** Tests: truncated output (last_pts well short) is `INVALID`; wrong stream count `INVALID`; duration inside tolerance `VALID`; one tolerance-boundary pair (just inside / just outside); CONSTANT policy on VFR source -> `VALID_WITH_TRANSFORMS`; missing output facts (`None` duration) -> `UNVERIFIABLE`, never `VALID`; rotation mismatch `INVALID`; `decide_reuse` table test over every reason, including that `toolchain_drift` alone stays reusable and `validator_outdated` sets `revalidate`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; PASS. **Step 5:** Commit `feat(proxy_manager): validation policy and reuse decision`.

### Task 4: Attempt lifecycle, ports, SQLite store

**Files:**
- Create: `domain/attempt.py`, `application/ports.py`, `infrastructure/sqlite_store.py`, `infrastructure/migrations.py`
- Test: `tests/unit/proxy_manager/test_attempt.py`, `tests/integration/proxy_manager/test_store.py` (contract tests parametrised over an in-memory fake in `tests/support/proxy_fakes.py` and the real store)

**Interfaces:**
- Produces:
  - `AttemptState` StrEnum: `QUEUED, GENERATING, VALIDATING, PUBLISHING, READY, FAILED, CANCELLED, INTERRUPTED`; `Attempt` aggregate with `advance(to, now)` enforcing a transition table (illegal transition raises `InvariantViolation`); fields: id, source_asset_id, profile, spec_fingerprint, state, error_category (`transient|permanent|cancelled|None`), error_message, encoder, fallback, artifact_asset_id, started/finished, report JSON.
  - `ProxyStore` Protocol: `begin_attempt(attempt) -> None` (raises `AttemptInFlight` when a non-terminal attempt exists for the same source+profile+fingerprint — partial unique index), `save_attempt`, `get_slot(source, profile) -> Slot | None`, `assign(source, profile, artifact_id, expected_active: str | None) -> None` (one transaction; `ConflictError` when `expected_active` differs, previous = old active), `mark_slot_missing`, `list_slots(source)`, `slots_referencing(asset_id)`, `purge_source(source)`, `interrupt_stranded(now) -> list[Attempt]`, `owned_temp_dirs() -> list[str]`.
  - Other ports (application/ports.py): `ProxyEncoder.encode(request: EncodeRequest, cancellation, on_progress) -> EncodeOutcome(encoder, fallback: str | None)`; `OutputProbe.probe(path, cancellation) -> OutputFacts`, `SourceProbe` (same adapter) `probe_source(path, cancellation) -> SourceFacts`; `DiskSpace.free_bytes(path) -> int`; `Toolchain.identity() -> str`.
- DB file `data_dir / "proxy_manager.db"`; migrations via `PRAGMA user_version`, tuple-of-statements pattern from `media_library/infrastructure/migrations.py`.

- [ ] **Step 1:** Tests: transition table (parametrised legal/illegal); store contract: second `begin_attempt` for same key raises `AttemptInFlight`, after terminal it succeeds; `assign` flips active and keeps previous; stale `expected_active` raises; two threads racing `assign` -> exactly one winner; `interrupt_stranded` marks non-terminal rows; corrupt row -> `PersistenceError`; connections closed (ResourceWarning is an error).
- [ ] **Step 2:** Run; FAIL. **Step 3:** Implement (short-lived connection per operation). **Step 4:** PASS. **Step 5:** Commit `feat(proxy_manager): attempt lifecycle and sqlite store`.

### Task 5: FFmpeg adapter (encode, probe, toolchain, disk)

**Files:**
- Create: `infrastructure/ffmpeg_tool.py`, `infrastructure/ffmpeg_encoder.py`, `infrastructure/ffprobe_facts.py`, `infrastructure/disk_space.py`
- Test: `tests/unit/proxy_manager/test_ffmpeg_args.py`, `tests/integration/proxy_manager/test_ffmpeg_real.py` (`pytest.mark.integration`; `skipif` when `ffmpeg`/`ffprobe` not on PATH, reason stated), fixtures in `tests/support/proxy_media.py` (lavfi generators, nothing committed)

**Interfaces:**
- Consumes: `ProcessRunner`, `ResolvedSpec`.
- Produces: `build_encode_args(spec, source: Path, destination: Path, *, encoder: str) -> list[str]` (pure; explicit `-map` per spec stream indexes, `-noautorotate`, `-fps_mode passthrough` or `-r`, `-enc_time_base demux`, `-map_metadata 0`, `-progress pipe:1`); `FfmpegEncoder(ProxyEncoder)` with trial-encode hardware detection only when `EncoderPolicy.AUTO`, CPU fallback recorded in `EncodeOutcome.fallback`; `FfprobeFacts` implementing `OutputProbe` and `SourceProbe` (rotation via side data, attached_pic excluded, `-count_packets`, last packet pts, optional `-v error -f null` decode when `full_decode_validation`); `StatDiskSpace`.

- [ ] **Step 1:** Unit tests on `build_encode_args`: argv list only (no shell string), paths passed verbatim, attached-pic stream never mapped, `AudioPolicy.NONE` adds `-an`, ProRes uses `prores_ks -profile:v 0`, constant rate adds `-r`. Integration tests with fixtures: CFR+audio, video-only, audio-only (unsupported), two audio tracks, rotation 90, VFR, nonzero start, ProRes `edit`. Each asserts via independent `ffprobe` that stream count, display size, rotation and duration meet the spec. Record observed start-time and VFR behaviour in the test docstrings.
- [ ] **Step 2:** Run; FAIL. **Step 3:** Implement. **Step 4:** Run; PASS; run twice to detect flakiness. **Step 5:** Commit `feat(proxy_manager): ffmpeg encoder and output probe adapters`.

### Task 6: `EnsureProxy` use case, contracts, wiring (first vertical slice)

**Files:**
- Create: `application/{dto,commands,ensure_proxy,contracts,provenance}.py`, `module.py`
- Modify: `src/media_house/bootstrap/modules.py` (one import, one tuple entry)
- Test: `tests/unit/proxy_manager/test_ensure_proxy.py`, `tests/integration/proxy_manager/test_ensure_real.py`

**Interfaces:**
- Consumes: `MediaLibrary`, `InspectionCatalog` (contracts), Tasks 2-5 types, `AppPaths`, `Clock`, `EventPublisher`.
- Produces:
  - `EnsureProxyCommand(source_asset_id, profile="edit", overrides=Mapping, force=False)`; `ProxyDto(source_asset_id, proxy_asset_id, profile, profile_version, spec_fingerprint, state, verdict, width, height, codec, duration, size_bytes, encoder, fallback, warnings, reused: bool, toolchain)`.
  - `ProxyCatalog` Protocol (`find(asset_id, profile) -> ProxyDto | None`, `list(asset_id) -> list[ProxyDto]`, `status(asset_id, profile) -> ProxyStatusDto` incl. reuse reasons); `ProxyManager` Protocol (`ensure(command, ctx) -> Result[ProxyDto, ProxyManagerError]`, `remove`, `prune`, later `select`, `submit`); `read_proxy_provenance(metadata) -> ProxyProvenance | None`.
  - Flow: source via `library.get` (video only) -> facts from `InspectionCatalog.find` else probe -> resolve spec -> `library.fingerprint` -> reuse check (`find_derived_asset`, `local_path`, report in artifact metadata) -> disk reserve -> begin attempt -> encode in `paths.temporary_directory` -> validate -> `register_derived(role=PREVIEW, metadata=provenance)` -> `store.assign` -> attempt READY. Adoption path: fingerprint hit with no slot assignment re-validates and assigns.

- [ ] **Step 1:** Unit tests with fakes (no ffmpeg): generate once then second call `reused=True` and `encoder.calls == 1`; source change (new asset checksum) -> new fingerprint; profile override -> new fingerprint; artifact blob missing -> regenerate; `INVALID` report -> `Err`, no `register_derived`, no slot change; `UNVERIFIABLE` -> not published as ready; adoption path after crash between register and assign; source id identical before and after replacement; inspection facts used when present (probe not called) and `origin` recorded.
- [ ] **Step 2:** Run; FAIL. **Step 3:** Implement; module registers factories and the two contracts. **Step 4:** Unit PASS; real-media integration PASS (generate, reuse without encoder call, original checksum unchanged); `uv run pytest tests/architecture`. **Step 5:** Commit `feat(proxy_manager): ensure proxy end to end`.

### Task 7: Failure-safe publication, cancellation, recovery, remove, prune

**Files:**
- Create: `application/{remove_proxy,prune_proxies,recover}.py`; Modify: `module.py` (startup recovery, `MediaAssetDeleted` subscription via the library contract)
- Test: `tests/unit/proxy_manager/test_safety.py`, `tests/integration/proxy_manager/test_safety_real.py`

**Interfaces:**
- Produces: `RecoverStranded.execute() -> RecoveryReport(interrupted, temp_dirs_removed, orphan_candidates)`; `RemoveProxy.execute(source_asset_id, profile, *, include_previous=False)`; `PruneProxies.execute(*, dry_run=True) -> PruneReport` deleting only artifacts no slot references via `library.delete(id, include_derived=False)`.

- [ ] **Step 1:** Tests: replacement failing validation keeps previous active; cancel at each stage (encode, validate, publish) leaves previous active and no temp dir; disk-full `OSError` mid-encode -> failed attempt category `transient`, temp removed; stale job whose `expected_active` no longer matches does not overwrite (`ConflictError` -> attempt FAILED stale); recovery marks stranded attempts and removes only owned temp dirs, reports unknown dirs without deleting; prune never deletes active/previous artifacts nor any original; source deletion event purges slots.
- [ ] **Step 2-5:** Fail, implement, pass, commit `feat(proxy_manager): safe publication, recovery, remove and prune`.

### Task 8: Admission queue (batch, priority, dedupe, retry)

**Files:**
- Create: `application/queue.py`; Modify: `application/contracts.py`, `module.py`
- Test: `tests/unit/proxy_manager/test_queue.py`

**Interfaces:**
- Produces: `ProxyQueue(scheduler: JobScheduler, manager: EnsureProxy, *, max_concurrent_encodes: int = 2, max_retries: int = 2, aging_seconds: float = 30.0, clock: Clock)`; `submit(commands: Sequence[EnsureProxyCommand], *, priority: Priority) -> list[QueueTicket]` (`Priority{INTERACTIVE, NORMAL, BACKGROUND}`); `QueueTicket.cancel()`, `.wait()`, `.outcome`. Identical `(source, profile, spec)` requests share one ticket; transient errors retry up to `max_retries`, permanent never; waiting items gain priority with age so BACKGROUND cannot starve.

- [ ] **Step 1:** Tests with `ImmediateJobScheduler`/fake clock: INTERACTIVE jumps ahead of queued BACKGROUND; aged BACKGROUND eventually runs ahead of fresh NORMAL; never more than `max_concurrent_encodes` running; duplicate submissions share a ticket and encode once; transient error retried exactly `max_retries` times; permanent not retried; cancel queued ticket removes it, cancel running ticket cancels token; shutdown cancels queued tickets.
- [ ] **Step 2-5:** Fail, implement, pass, commit `feat(proxy_manager): priority admission queue`.

### Task 9: Adaptive profile selection

**Files:** Create `domain/selection.py`; Modify `application/contracts.py`, `module.py`; Test `tests/unit/proxy_manager/test_selection.py`

**Interfaces:** `select_profile(facts: SourceFacts, workload: Workload, *, free_bytes: int | None, existing: Sequence[str]) -> Selection(profile: str | None, reasons: tuple[str, ...])`; `profile=None` with reason `not_needed` when the source is already within the target size or intra-coded.

- [ ] **Step 1:** Tests: identical inputs -> identical output; ANALYSIS->`analysis`, EDITING->`edit`; tiny source -> `not_needed`; tight free space downgrades `edit` to `analysis` with a reason; explicit profile request bypasses selection.
- [ ] **Step 2-5:** Fail, implement, pass, commit `feat(proxy_manager): deterministic profile selection`.

### Task 10: Boundaries, docs, benchmark, final audit

**Files:**
- Create: `tests/architecture/test_proxy_manager_boundaries.py` (copy the `video_improvement` pattern: no other processing module imports, library/inspection only via contracts, `subprocess`/Qt/`sqlite3` placement), `src/media_house/modules/proxy_manager/README.md`, `scripts/benchmark_proxies.py` (reports source traits, profile, toolchain, generation/validation/end-to-end seconds, output size, peak RSS; refuses to claim results for synthetic input)
- Modify: `AI_CONTEXT.md` (§11 paragraph and package map), `ARCHITECTURE.md` module list if modules are enumerated

- [ ] **Step 1:** Architecture test; run `uv run pytest tests/architecture`; PASS.
- [ ] **Step 2:** README covers every item in spec "Documentation" scope: responsibility, identity, profiles, selection, lifecycle, fingerprint and invalidation rules, storage, concurrency, recovery, Resolve gap, tools, how to test, what was measured and what was not.
- [ ] **Step 3:** Run module tests sequentially in batches; record exact commands and counts. Run `uv run mypy` and ruff on `src/media_house/modules/proxy_manager tests/unit/proxy_manager tests/integration/proxy_manager`.
- [ ] **Step 4:** Adversarial audit against the spec acceptance list and `UNIVERSAL_BASE_PROMPT.md` §29; fix findings; `git status` shows only intended files.
- [ ] **Step 5:** Commit `docs(proxy_manager): readme, architecture test, benchmark`.
