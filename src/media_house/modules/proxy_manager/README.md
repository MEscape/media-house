# Proxy Manager

A proxy is a **replaceable, verifiable derivative** of an original video asset: smaller, cheaper to
decode, never the source. This module creates proxies, proves they keep their promises, tracks
which one a source is served from, and replaces it without ever losing the last good one.

It does not edit, build timelines or decide anything editorial, and it is not a transcoding
framework: one profile in, one verified file out.

Status: **Slice 2** (Slice 1: generate, validate, register, activate, reuse, repair, recover; Slice 2:
waiting for identical requests, lease takeover, removing and pruning). What is not built yet is
listed under [Not built yet](#not-built-yet); nothing below claims more than the tests show.

## What owns what

| Concern | Owner |
| --- | --- |
| Asset identity, content checksum, file bytes, derivation fingerprint (the cache) | Media Library |
| Source facts (streams, timing, rotation) | Media Inspection, read through `InspectionCatalog`; probed once if absent |
| FFmpeg process execution | the shared `ProcessRunner` port |
| Which proxy each `(source, profile)` slot serves; attempt history; the verdict of each proxy | this module (`proxy_manager.db`) |
| Timelines, DaVinci Resolve, final export | none of this module's business |

Nothing here duplicates the library: the database stores **no** checksum, size or path, only library
asset ids and the module's own verdicts.

## Identity

* The **source** is a library asset. Its id never changes and it is never modified.
* A **slot** is `(source_asset_id, profile)`. It is stable and always points at the artifact being
  served, plus one rollback artifact.
* An **artifact** is one library asset (`operation="proxy"`, role `PREVIEW`, so default searches do
  not list it). Replacing a proxy registers a new artifact and moves the slot; the old one stays
  until it is pruned. **The proxy's asset id can change; the source id cannot.** Consumers store the
  source id and ask `ProxyCatalog`.
* The proxy describes itself in its library metadata (`read_proxy_provenance`).

## Profiles and the resolved spec

A profile (`analysis`, `edit`; overrides are flat, e.g. `{"max_long_edge": 480}`) is a *request*.
`resolve_spec(profile, source facts)` turns it into the concrete, deterministic `ResolvedSpec`:
output size (displayed shape, square pixels, even, never upscaled), codec, pixel format, stream map,
rotation, audio, and the deviations from the source timeline that apply to this source. Its
canonical JSON is the library derivation config, so **the library's processing fingerprint is the
generation fingerprint**.

| Profile | Output | Audio |
| --- | --- | --- |
| `analysis` | H.264 in MP4, long edge 640, CRF 28 with a 1500 kbps ceiling | first track, AAC 96 kbps |
| `edit` | ProRes Proxy in MOV, long edge 960 | every track, PCM 16-bit |

The parameters are drafts until benchmarked on real footage (see below).

**Not** in the fingerprint, recorded as provenance: the concrete encoder, the FFmpeg version, the
machine. Whether a stronger check was requested (`full_decode_validation`) does not change what is
generated either.

## Reuse and invalidation

A stored proxy is reused, without encoding and without any database write, only when **all** hold:
the slot's artifact has this exact fingerprint, its verdict is `valid` or `valid_with_transforms`, it
was judged by the current validator version, its evidence is at least what the request asks for, the
library asset exists and is not archived, and its file is present at the size the library recorded.

| Change | Effect |
| --- | --- |
| Different source content (a new import is a new asset) | different fingerprint: its own proxies |
| Profile name, version or any override that changes output | different fingerprint: a new artifact; the old one is the rollback |
| `ADAPTER_REVISION` or `SPEC_VERSION` bump | different fingerprint: regenerated on next request |
| `VALIDATOR_VERSION` bump | not regenerated: the stored file is re-checked, decoded to the end |
| Stronger evidence requested (`full_decode_validation`) | the stored file is decoded and its verdict upgraded |
| FFmpeg upgrade | **no invalidation**; the toolchain is recorded per proxy |
| File missing or wrong size, or asset archived | condition `missing`; the next request repairs it |

Condition is computed against the live library on every call, never stored: `none`, `ready`,
`stale`, `needs_revalidation`, `missing`, `invalid`.

Limit: the source's identity is the checksum taken at import. A file edited in library storage
behind the library's back is not detected, and a same-size silent corruption of a stored proxy is
only caught when it is decoded.

## Lifecycle of a request

`queued -> generating -> validating -> publishing -> ready`, or `superseded`, `failed`,
`cancelled`, `interrupted`. The database refuses any other move.

1. Validate the profile; read source facts (stored inspection, else one probe).
2. Resolve the spec and its library fingerprint; serve a valid stored proxy if there is one.
3. Record the attempt and **reserve disk** in one transaction.
4. Encode into the attempt's own scratch directory `proxy-<attempt id>`.
5. Judge the finished file independently (below). Nothing invalid is ever registered.
6. Register it as a library `PREVIEW` asset and, in one transaction, make it the slot's active
   artifact. The previous artifact becomes the rollback target if it is still usable.
7. Remove the scratch directory on every exit path.

**Fencing.** Each attempt gets a sequence number when it is *submitted*. A slot accepts an
activation only from an attempt newer than the one that produced its active proxy. If an older
request finishes after a newer one it ends `superseded`: its result is kept but not served. If the
newer request fails or is cancelled, the older one still activates. The last good proxy is served
throughout.

**Identical requests in flight.** A request identical to one that is running (same source, profile
and exact spec) does not encode again. It waits, cancellably, polling the running attempt's row,
and then asks again, so it is answered from storage:

| The attempt it waited for | The waiter |
| --- | --- |
| `ready` | is served the stored proxy (`reused=True`) |
| `superseded` | reuses the stored result and activates it as a newer request |
| `interrupted` (owner stopped responding) | takes over and generates |
| `failed` / `cancelled` | gets `SharedAttemptFailed`; it does not encode the same thing again |

Cancelling a waiter cancels only the waiter. A waiter gives up with `WaitTimedOut` after
`wait_timeout_seconds` (default 3600); the running request is untouched. Waiting is bounded to a few
chained attempts per call.

**Lease.** A running attempt proves it is alive on a timer thread (every `heartbeat_seconds`, default
15) that does not depend on progress, because validating by decoding or copying a large file into
the library can run longer than the lease without reporting anything; every state change also
writes `heartbeat_at`. An attempt of *another* process
whose last sign of life is older than `lease_seconds` (default 120) is dead. A new identical request
retires it (`interrupted`, "lease expired") and proceeds, and its disk reservation stops counting.
One that keeps its heartbeat is respected. An attempt of the *same* process is never taken over: its
thread is alive or ended the attempt itself. A process that crashed therefore blocks identical
requests for at most one lease. `remove` and `prune` retire dead owners first, so a crashed process
does not protect its slot from maintenance forever.

**A retired owner stops.** An attempt retired while its owner is still running (a stall longer than
the lease) cannot publish, move to another state, or touch the slot: the store refuses with
`LeaseLost` inside the same transaction, nothing it did leaks, and the heartbeat tells it to stop
before it registers or deletes anything. Whatever else breaks in a retired attempt (its scratch
directory may have been swept by the request that replaced it) is reported as `LeaseLost`, not as
the incidental error. Lease comparisons use each process's wall clock, so processes must share a
clock (one machine); skewed clocks are not handled.

## Validation: what a verdict means

Verdicts: `valid`, `valid_with_transforms`, `invalid`, `unverifiable`. `unverifiable` (a required
check could not be evaluated) is never published as ready.

Every verdict carries its **evidence**:

* `packets`: every packet of every stream was listed and measured. The file is complete and
  consistent with its spec. **This does not prove it decodes.**
* `decoded`: it was also decoded to the end with no decoder complaint (`full_decode_validation`).

Checked on every output: size within the reservation, exact stream set (one video, the mapped
audio, nothing else), codec and pixel format, exact size with square pixels, rotation, **frame
count equal to the frames a player shows in the source**, video duration, start time, audio format,
audio duration and audio/video sync, and the source timecode. Tolerances come from the media's own
timing (one source frame plus one output frame; two audio codec frames), not from fixed seconds.
Language tags the container could not carry are *warnings*, not failures.

`valid_with_transforms` means the source timeline was deliberately departed from. Today that is one
case: a source that does not start at zero is rebased to start at zero (`start_time_rebased`).

## Measured behaviour (FFmpeg 9.0.2; each point has a test)

* Constant and **variable frame rate**: every frame keeps its own time (max difference < 1 ms); the
  rate is not made constant.
* **Rotation** stays display metadata (`-noautorotate` is an input option); pixels are not turned
  twice. Verified for H.264/MP4 and ProRes/MOV.
* **Non-square pixels** are baked into square ones at the displayed shape.
* **Nonzero start**: FFmpeg's default output starts at zero. That is recorded, validated and
  reported; relative audio/video offset is checked.
* A clip **cut without re-encoding** has hidden pre-roll packets; only frames from time zero count.
* **Timecode** survives as a tag, and the MP4 muxer's `tmcd` track is recognised as its carrier.
* Per-stream tags are copied explicitly. The **MOV muxer dropped the language of the second audio
  track** (AAC and PCM alike; MP4 kept it; more than two tracks were not tried), so `edit` proxies
  of multi-language sources carry a warning.
* `-fs` stops the encode near the reservation but **overshoots** (about 8% at 1 MB). It is a
  runaway guard, not a limit: the finished size is checked again.

## Disk

The reservation for an encode is a conservative estimate, not a proven bound: H.264 uses the rate
ceiling times duration times 1.5 (white noise measured 1.13x), ProRes uses 375 bits per macroblock
per frame (white noise measured 250.5) plus PCM audio, plus 4 MiB. When the library shares the
volume the reservation doubles, because registering copies the file. Reservations are summed per
volume inside the attempt transaction (safe across threads and processes) and released by the
attempt reaching any terminal state; there is no release call to forget. A file that outgrows its
reservation is discarded. A full disk during encoding or registering is a transient failure that
leaves the previous proxy active and removes the scratch directory. Free space is not guaranteed
against writers outside this module.

## Repair and recovery

* A library asset already owns the fingerprint but is unusable (file missing, wrong size, failed
  re-check): it is replaced **only if this module provably created it** (an artifact record or an
  attempt for that exact derivation). The new file is encoded and validated first; the unusable
  asset is deleted without cascading (`include_derived=False`, so anything derived from it blocks
  the repair); then the new one is registered and the slot moves. Anything not provably ours, or
  archived, fails with `ArtifactUnrepairable` and nothing is deleted.
* If the library hands back an asset other than the file just validated, the bytes it serves are
  judged, decoded to the end, before they can be activated.
* Crash after registering but before activating: the next identical request finds the asset by
  fingerprint, proves ownership from the attempt, decodes it, and adopts it. Nothing is re-encoded.
* On first use, attempts of another process whose lease ran out are marked `interrupted` and only
  scratch directories named in an attempt row are removed. Unknown directories are left alone.

Several processes may share a data directory: each attempt records its owner and heartbeat, and
reservations, fencing and duplicate detection all run inside database transactions.

**Database resilience.** Transient SQLite failures (another process switching the journal mode,
write-ahead-log recovery after a process was killed, which Windows reports as "disk I/O error") are
retried for at most 5 seconds: always for reads, and for writes only before the transaction has
begun. A write whose work has run is never repeated, because an error at COMMIT may or may not have
taken effect.

## Removing and pruning

`ProxyMaintenance.remove(source, profile)` takes a slot's proxy out of service and deletes its
files (active and rollback). It is refused with `AttemptInFlight` while a request for that slot is
running. `ProxyMaintenance.prune(dry_run=True)` deletes artifacts no slot serves or keeps (results of
superseded requests, proxies pushed out of the rollback position). A dry run, the default, deletes
nothing.

Deletion is two-phase so a crash between phases is repaired by the next call:

1. in one transaction: mark the artifact `pruning`, after checking no slot references it and its
   slot has no attempt in flight (remove: dissolve the slot);
2. delete the library asset (`include_derived=False`);
3. forget the record.

A `pruning` artifact can be neither adopted nor activated: a request racing the delete fails with
`ArtifactBeingRemoved` (transient) and succeeds when asked again. A request that meets an artifact
left `pruning` by a crash does not wait for somebody to prune: it finishes the removal itself (an
asset that is already gone is success) and carries on.

**Stale records.** If the library asset behind a record is gone (deleted directly, or cascaded)
the record would collide with the fingerprint of its replacement. The replacement attempt
therefore names the stale record, and removes it in the same transaction that activates the new
artifact. If something else was derived from
the proxy (a thumbnail, an analysis) it is kept, returned to `live` and reported. An infrastructure
failure leaves it `pruning` and the next prune finishes it.

Never deleted: originals, assets of other modules, anything the module cannot prove it created,
and artifacts of a slot with a request in flight. Prune also **reports, without deleting**, proxy
assets in the library with no record here (`orphan_assets`) and `proxy-*` scratch directories no
attempt names (`orphan_scratch`), and forgets records whose source or proxy file the library no
longer has (it cascades deletes without telling anybody).

## Not built yet

Priority queue, batch and retry policy; adaptive profile selection; a sweep that adopts stranded
assets nobody asks for again; hardware encoders (software only, by design for
now); a UI; DaVinci Resolve (no integration exists in this repository, and Resolve's linking rules
are unverified: library files are hash-named, so an integration needs a named export of the proxy).
Colour is not managed: proxies are for editing and analysis, not for judging colour, and HDR or log
sources are not tone-mapped.

## Not verified

Machines with skewed clocks, a network file system, a worker running the real FFmpeg encoder
(the cross-process tests use a scripted encoder), real camera footage, long or very large files, 10-bit/HDR/interlaced/alpha sources, performance and
the cost of `full_decode_validation` (no benchmark has been run, and only generated media was
available), hardware encoders, and any FFmpeg version other than 9.0.2.

## Tests

```text
uv run pytest tests/unit/proxy_manager tests/architecture/test_proxy_manager_boundaries.py
uv run pytest tests/integration/proxy_manager tests/integration/media_library/test_register_derived_role.py
```

Integration tests need `ffmpeg` and `ffprobe` on `PATH` and skip with that reason otherwise. The
real-media tests inspect outputs with ffprobe called directly by the test.
