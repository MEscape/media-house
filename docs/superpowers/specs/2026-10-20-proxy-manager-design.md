# proxy_manager — design

Status: decisions approved in chat 2026-10-20; implementation in vertical slices.
Foundation: `UNIVERSAL_BASE_PROMPT.md`, `AI_CONTEXT.md`, `CLAUDE.md`.

## Intent

A proxy is a replaceable, verifiable derivative of an original asset. `proxy_manager` creates,
identifies, validates, tracks, replaces and safely removes proxies so analysis and editing are
faster, without changing source identity, timing semantics or the original media.

Goals: stable source identity; trustworthy reuse; never publish an incomplete or invalid file;
never lose the last known-good proxy; bounded resource use; honest reporting of what is verified.

Non-goals: timeline construction, editorial decisions, a second media library, cache manager,
job scheduler or fingerprint scheme, a general transcoding framework, Resolve automation (see
"Resolve").

Anti-goals: a proxy becoming the canonical source; silent frame-rate, rotation, stream or
timestamp changes; a "ready" proxy that was only checked by exit code.

## Verified repository facts this design rests on

- Source identity: `MediaAssetId` (opaque, stable). Content identity: library `Checksum`
  (SHA-256 of the whole file, computed once at import). Sources live in library storage.
- The library is the cache: `fingerprint` / `find_derived_asset` / `register_derived` /
  `list_derived`. The fingerprint (source checksum + operation + version + canonical config)
  is unique in the database, so racing registrations converge. Deleting a source cascades.
- The library has no active-derivative concept, ownership, retention or attempt history.
  `register_derived` copies and re-hashes the file and probes it with the library inspector.
- Public `MediaLibrary.register_derived` does not expose `role`; default search includes
  `DERIVED` and excludes `PREVIEW`.
- `InspectionCatalog.find` returns stored inspections without probing: codec, geometry with
  rotation, SAR/DAR, frame rate and average rate, time base, start time, `StreamTiming`.
- `JobScheduler` is a flat thread pool: cooperative cancel, no priority, retry, dedupe or
  persistence. `ProcessRunner` kills the process on cancel or timeout.
- No shared FFmpeg adapter exists; each module keeps a private one.
- No DaVinci Resolve integration exists.

## Decisions (approved)

1. Add an optional `role: AssetRole = DERIVED` parameter to `MediaLibrary.register_derived`
   (additive). Proxies register as `PREVIEW` and stay out of default searches.
2. `proxy_manager` owns `proxy_manager.db` (SQLite, `PRAGMA user_version` migrations) for the
   active slot assignment and attempt history. Artifact bytes and derivation stay in the library.
3. A small module-level admission queue (priority with aging, bounded concurrent encodes,
   bounded retries) sits in front of the unchanged `JobScheduler`.
4. A module-private FFmpeg/ffprobe adapter, as in the four existing precedents.

## Model

- **Slot**: `(source_asset_id, profile_name)`. Stable. Points at one active artifact and, for
  rollback, the previous one.
- **Artifact**: one library derived asset (operation `proxy`) per distinct `ResolvedSpec`.
  Replacing a proxy means a new artifact and a pointer flip; the old artifact stays until pruned.
- **ProxyProfile** (versioned, schema-versioned) is the request. **ResolvedSpec** is every
  output-affecting value: display-derived dimensions, codec and profile, pixel format and
  depth, frame-rate policy, stream map, audio parameters, rotation policy, container, adapter
  revision. Its canonical JSON is the library derivation config, so the library fingerprint is
  the generation-specification fingerprint.
- Not in the fingerprint, recorded in provenance: concrete encoder, fallback, FFmpeg full
  version, machine details. FFmpeg drift is reported as `toolchain_drift` (advisory); an adapter
  revision bump invalidates. A validator-version change triggers re-validation, never re-encode.
- Source facts come from `InspectionCatalog.find`; if absent, one probe through the module's
  adapter. How they were obtained is recorded (`inputs_used`).

## Lifecycle

`queued -> generating -> validating -> publishing -> ready`, plus `failed(category)`,
`cancelled`, `interrupted`. Slot-level conditions (`stale`, `missing`) are computed on lookup.

1. Validate request and profile before any work. 2. Resolve spec, fingerprint. 3. Reuse check:
   artifact exists, blob present, validation current. 4. Disk estimate and reservation.
5. Encode into `temp_dir/proxy-<attempt_id>`. 6. Independent validation. 7. `register_derived`.
8. One SQLite transaction flips the slot. 9. Previous artifact retained (one generation).
10. Temp directory removed on every exit path.

Crash windows: after register and before flip, the next request finds the artifact by
fingerprint, re-validates and adopts it. Flip done but blob missing: slot becomes `missing`,
previous artifact stays usable, regeneration is scheduled by the next request. Startup marks
stranded attempts `interrupted` and removes only temp directories that an attempt row owns;
unknown files are reported, never deleted. `MediaAssetDeleted` purges dangling slots.

## Validation

Pure domain policy over typed probe facts of the output. Verdicts: `VALID`,
`VALID_WITH_TRANSFORMS`, `INVALID`, `UNVERIFIABLE`. Checks: expected streams, codec, display
size and aspect, rotation, frame-rate policy, duration, truncation (last pts, packet count),
plausible size. Tolerances derive from timing facts (source frame, output frame, audio encoder
delay). Default is packet-level; full decode-to-end is opt-in until its cost is measured.
Frame-rate default is source timing; constant rate is explicit and yields
`VALID_WITH_TRANSFORMS`. Rotation is preserved as display metadata, never applied twice.
Nonzero-start behaviour is determined empirically with fixtures and reported.

## Profiles and selection

Built-ins: `analysis` (small H.264) and `edit` (ProRes Proxy in MOV). Parameters are drafts
until benchmarked. Overrides use dotted paths like `video_improvement`. Software encoding is
the default; `auto` hardware is opt-in with a trial encode and a logged fallback that must
pass the same validation. Selection (later slice): pure function of source facts, workload
and constraints; returns profile plus rationale; may return `not_needed`.

## Resources

`DiskSpace` port. Estimate = bitrate x duration x margin; reserve about 2x (temp plus the
library copy). Prune removes only artifacts no slot references. Never touches originals.

## Contracts

`application/contracts.py`: `ProxyCatalog` (read-only `find`, `list`, `status`) and
`ProxyManager` (`ensure`, `remove`, `prune`, `select`, `submit`). `read_proxy_provenance(metadata)`
for other modules. DTOs always carry `source_asset_id`.

## Resolve

Not implemented: no integration exists and Resolve's linking rules are unverified. Library blobs
are hash-named, so an eventual integration needs a named export of the proxy. Documented as a gap;
no claim of relinking or of final-render media mode.

## Slices

1. Domain + store + encoder/probe + `ensure` (generate, validate, register, reuse) with tests.
2. Failure-safe publication, recovery, prune, cancellation.
3. Admission queue: batch, priority, retry, dedupe.
4. Adaptive selection.
5. Docs, architecture test, benchmark script, final audit.

## Open / unverified

Nonzero-start and VFR behaviour of the chosen FFmpeg flags; hardware encoder runtime
availability; default profile parameters; validation overhead; real-footage performance
(only synthetic fixtures available).

## Amendments after risk closure (2026-10-20)

These replace the corresponding statements above. They come from tracing the library and from
FFmpeg 9.0.2 measurements; the module README records the verified behaviour.

1. **Fingerprint collision and corrupt artifacts.** `RegisterDerivedAsset` looks the fingerprint up
   first and returns the existing asset (`created=False`) without inspecting or storing the new
   file, and nothing in the library checks that an existing asset's blob is present. A corrupt or
   missing artifact therefore blocks a same-spec regeneration until it is deleted. Rule: an asset is
   replaced only if this module provably created it (an `artifacts` row, or an attempt row for that
   exact derivation); the new file is encoded and validated first; the old asset is deleted with
   `include_derived=False`, which refuses when anything is derived from it; archived or
   not-provably-ours assets fail with `ArtifactUnrepairable`. If the library returns an asset other
   than the validated file, the bytes it serves are decoded and judged before activation. `force`
   is dropped: a valid artifact is never regenerated for an identical spec. Known limit: the
   library's content-addressed store treats a same-size existing blob as identical, so same-size
   bit rot of a blob shared by identical content cannot be repaired through the library contract.
2. **Fencing.** The attempt `seq` orders requests by SUBMISSION. A slot accepts an activation only
   from a higher `seq` than its `activated_seq`, inside one `BEGIN IMMEDIATE` transaction. An older
   attempt finishing after a newer one ends `superseded` (artifact kept, slot unchanged); a failed
   or cancelled newer attempt never blocks an older one.
3. **Schema.** An `artifacts` table (module verdicts and ownership) joins `slots` and `attempts`;
   `slots` references it with foreign keys so an active or previous artifact cannot dangle; the
   allowed attempt transitions are seeded in the database and enforced by a trigger. Activation
   removes a replaced artifact record and repoints the slot in the same transaction. Recovery is
   idempotent and table-driven: foreign-owner attempts become `interrupted`; scratch directories
   named by finished attempts are removed; a registered-but-uncommitted asset is adopted by the next
   identical request after its ownership is proven and its file decoded. There is no startup hook
   and the event bus is not durable, so slots are reconciled against the library on every read
   instead of by a `MediaAssetDeleted` subscription.
4. **Disk.** Estimates are not hard bounds. H.264 (CRF with a rate ceiling) measured 1.13x its
   ceiling on white noise; ProRes Proxy measured 250.5 bits per macroblock; FFmpeg's `-fs`
   overshoots (about 8% at 1 MB) and exits 0 with a truncated file. The reservation is the measured
   worst case with 1.5x headroom plus 4 MiB, summed per volume inside the attempt transaction,
   doubled when the library shares the volume, released by any terminal state. Output beyond the
   reservation is discarded. A full disk is a transient failure that keeps the previous proxy.
5. **Slice 1 scope.** Duplicate in-flight requests returned `AttemptInFlight`. Waiting, lease
   takeover, the admission queue, pruning and selection were NOT built.
6. **Slice 2 scope (2026-10-20).** The sentence above Slice 1 ("Slice 2 polls cancellably and takes
   over via heartbeat lease") and the `Slices` list disagree on whether waiting and lease belong to
   Slice 2; the approved risk review says they do, so Slice 2 is: waiting with result sharing
   (`SharedAttemptFailed`, `WaitTimedOut`), heartbeat lease takeover (migration 2 adds
   `attempts.heartbeat_at`; foreign attempts are dead only past `lease_seconds`), `remove`, and
   two-phase `prune` with reconciliation and orphan reports. NOT in Slice 2: the admission queue
   (batch, priority, retry), adaptive selection, a startup sweep that adopts stranded assets,
   hardware encoders, Resolve.
7. **Slice 2 hardening (2026-10-20).** Review of Slice 2 found and fixed four defects, each with a
   regression test written first: (a) the heartbeat only ran during encode progress, so a long
   decode or library copy could outlast the lease and be taken over - it is now a timer thread;
   (b) a retired attempt could still try to publish (raw `PersistenceError` from the transition
   trigger) - the store now raises `LeaseLost` and nothing it did persists; (c) a record whose
   library asset had vanished collided on `UNIQUE (source, fingerprint)` with its replacement and
   wedged the request - the replacement now supersedes the stale record atomically, and a request
   that meets a half-finished removal completes it; (d) `remove` and `prune` were blocked forever by
   the attempt of a dead process - they retire dead owners first. Cross-process behaviour is
   exercised with real worker processes (`tests/support/proxy_worker.py`), which found two more:
   (e) several processes creating the database at once failed with "database is locked" (the WAL
   switch is not waited for like a write lock), and (f) the first read after a process was hard
   killed could fail with a transient "disk I/O error" (WAL recovery). Transient failures are now
   retried for at most 5 s: reads always, writes only before the transaction has begun - never
   after its work ran, because an error at COMMIT may or may not have taken effect.
