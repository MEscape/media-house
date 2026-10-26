# asset_relationships — Slice 3 (reconciliation) design

Status: **approved 2026-10-26 with three corrections (A, B, C), recorded in "Corrections after approval" at
the end and applied in the text below; the nine policy decisions are approved as proposed.** Slices 1 and 2
are built and approved. Foundation: the main design
(`2026-10-26-asset-relationships-design.md`, especially sections 8, 10 and the Slice 1-2 amendments),
`UNIVERSAL_BASE_PROMPT.md`, `AI_CONTEXT.md`.

Labels: **[V]** verified in the repository; **[D]** already approved; **[P]** proposed here; **[U]**
unverified or to be measured.

## 1. Purpose and scope

Reconciliation keeps the module's STORED state truthful against the live library, without events and
without ever copying lineage: it notices claims whose assets are gone, applies the approved retention,
and marks claims whose basis is out of date. It never decides anything a person decides.

In scope for Slice 3 [P]:

- **R1, the claim-health sweep**: a resumable, bounded, idempotent pass over the stored claims.
- Invalidation of claims by detector version (a port; no detector exists yet).
- Retention and purge of claims whose endpoints are missing, with a small audit record.
- Structured outcomes, state in `scan_state`, one new use case and one additive maintenance contract.

Specified now but implemented with Slice 4, because it has no consumer before a detector exists [P]:

- **R2, the library change feed** (find assets that are new or changed). Section 8.

Explicitly out: detectors, similarity, any upstream contract change, any scheduler, any durable event
delivery, any change to `MediaRelations`, the Qt presentation crash.

## 2. Verified facts the design rests on

- [V] `JobScheduler.submit(name, work(JobContext), callbacks=...)` is a flat thread pool (default 4
  workers, `ConcurrencySettings.max_workers`), with cooperative cancellation: `JobContext.raise_if_cancelled()`,
  `ctx.progress.report(...)`, `ctx.cancellation.wait(seconds)`. No timers, no priority, no retry, no dedupe,
  no persistence. `JobContext.detached()` is the unit-test context. There is no startup hook.
- [V] `MediaLibrary.get_many(ids)` skips unknown ids silently (absence is "not in the result"); a database fault
  raises a typed `PersistenceError`. `get(id)` returns `Err(MediaNotFound)` for an unknown id. So *absent* and
  *library failed* are already different signals.
- [V] `MediaLibrary.search` can filter `created_from` (inclusive, `>=`) and `created_to` (exclusive, `<`),
  sorts by `created_at` then `id`, and stores timestamps as fixed-width UTC microsecond text (lexicographic order
  is chronological). `created_at` of an asset never changes. There is **no** `updated_from` filter. Paging is by
  offset.
- [V] Asset ids are never reused: a re-import after deletion is a new id (`new_id()`); archive keeps the id.
- [V] The module's own store: `relationships` (PK `relationship_id`), indexes `ix_relationships_a` / `_b`
  `(asset, type, status)`, `ix_relationships_status (status, updated_at)`; every write is an atomic read-modify-write
  (`RelationshipRepository.update`); migrations are append-only (`user_version` 1 today).
- [V] A claim can be created only for assets that exist (`ProposeRelationship` checks `get_many`), so a claim's
  endpoints existed when it was stored.
- [V] Reads never depend on reconciliation: every read checks endpoint state against the live library
  (`endpoint_states`), and recorded lineage is projected, not stored.

## 3. Invariants (each one is tested)

1. **Reads never wait for, or trust, reconciliation.** Endpoint state on a read stays live; reconciliation only
   maintains stored state and cleanup.
2. **Lineage is never copied.** Reconciliation has no code path to `LineageProjection` or the proxy contract
   (architecture test) and writes no lineage row (integration test).
3. **A human decision is never changed by reconciliation.** `CONFIRMED` / `REJECTED` change only by a person. The
   only things reconciliation may do to a decided claim are: set or clear `endpoint_missing_since`, set
   `outdated_since`, and delete it after retention (with an audit record).
4. **Absence needs proof, failure is not absence.** An endpoint is missing only when the library answered and
   did not have it. Any library failure changes nothing.
5. **Every step is idempotent and resumable.** Running again with the same inputs writes nothing; a crash at any
   point loses at most the batch in flight.
6. **Bounded work.** A job processes at most a configured number of claims; a library call covers at most one
   batch.
7. **Transitions go through the domain.** Rules live in `AssetRelationship` methods, not in the use case or SQL.

## 4. State model

A new attribute, `endpoint_missing_since: datetime | None`, is added to the claim. It is **not a status**: status
(`PROPOSED`, `CONFIRMED`, `REJECTED`, `STALE`) keeps its Slice 1 meaning and transitions. A claim is *healthy*
when the attribute is `None`. A claim is *undecided* when `PROPOSED` or `STALE`, *decided* otherwise.

| Observation during the sweep | PROPOSED | STALE | CONFIRMED | REJECTED |
| --- | --- | --- | --- | --- |
| an endpoint is confirmed missing (first time) | set `endpoint_missing_since` | same | same | same |
| every endpoint present again | clear it | clear | clear | clear |
| missing for at least `undecided_orphan_grace` | purge | purge | n/a | n/a |
| missing for at least `decided_retention` | n/a | n/a | purge | purge |
| the claim's basis is not current (version or input identity) | -> `STALE` | stays `STALE` | set `outdated_since` | set `outdated_since` |
| an endpoint is archived or restored | nothing | nothing | nothing | nothing |

Rules behind the table:

- "Missing" is judged on **both** endpoints at once: any missing endpoint makes the claim missing; it is healthy
  again only when both are present. Archived is present.
- `endpoint_missing_since` keeps the FIRST confirmed absence, so retention counts from the first time the asset
  was found gone, not from the latest sweep.
- A purge never happens on the sweep that first sees the absence (the grace and the retention are both > 0).
- `mark_stale` still only accepts `PROPOSED` (Slice 1). A `STALE` claim returns to `PROPOSED` only through a
  re-assertion (`ProposeRelationship`), exactly as built; reconciliation never re-detects.
- New domain methods (additive, pure): `mark_endpoint_missing(now)`, `clear_endpoint_missing(now)`,
  `mark_outdated(now)` (sets `outdated_since` if unset; refused when not decided). A change to
  `endpoint_missing_since` moves `updated_at`; an unchanged claim does not.

### Human decisions survive re-detection and reconciliation

- A decided claim is never written by a refresh except through the Slice 1 rule (evidence added, old evidence
  superseded, `outdated_since` set). Reconciliation adds no new way to change status.
- A rejection stays attached to the pair and type for as long as the claim exists. It is removed only by the
  retention purge after the endpoint has been missing for the full retention, and then an audit record remains
  (section 6).
- Purge re-checks the **claim-owned** preconditions inside its transaction against the claim as it is stored at that
  moment (status class, `endpoint_missing_since` set, grace or retention reached); a person who decides between the
  sweep's read and its write wins, because the rules are evaluated on the current claim. The state of an endpoint in
  the Media Library cannot be checked in that transaction (separate database): see "Corrections after approval", B.

## 5. R1 - the claim-health sweep

### Why a claims-centred sweep

What can go wrong with a stored claim depends only on its two endpoints and its basis. Both are reachable from the
claims themselves: the sweep walks the module's OWN table by primary key and asks the library about the endpoints it
meets. Consequences:

- The cursor is `relationship_id`: unique, immutable, totally ordered. **Equal-timestamp ties cannot occur.**
- The walk is a keyset over the module's own database, so it is stable under inserts, updates and deletes by other
  writers and independent of the library's offset paging.
- It does not use the derivative scan, the 5-page cap or the projection at all (section 9).

### Algorithm (one job)

```
state   = store.load_state()                       # claim_cursor, generation, failures, next_attempt_after, ...
if not due(state, now, force):           return NOT_DUE
budget  = settings.max_claims_per_job
loop:
    ctx.raise_if_cancelled()
    batch = store.claims_after(state.claim_cursor, settings.batch_claims)   # light rows: id, endpoints, status,
    if batch is empty:                                                      #   missing_since, basis (no evidence)
        store.complete_sweep(expected=cursor, now)     # generation += 1, cursor = none, last_sweep_completed_at = now
        return COMPLETED
    present = library.get_many(distinct endpoints of batch)                 # one call, <= 2 * batch_claims ids
        (library failure -> retry policy, section 7; exhausted -> PARTIAL(transient_library), no writes)
    absent  = endpoints - present
    if suspicious(batch, absent): re-verify once after a delay; if still suspicious -> PARTIAL(suspicious_library):
                                  nothing is written for THIS batch and its cursor is NOT advanced; batches that
                                  were already committed earlier in the job stay committed
    for claim in batch:                                  # each is its own atomic read-modify-write
        store.apply(claim.id, evaluate(claim, absent, now, checks))   # re-reads the claim inside the transaction
    store.advance_cursor(expected=cursor, new=batch.last_id)          # compare-and-set; lost race -> SUPERSEDED
    budget -= len(batch); if budget <= 0: return PARTIAL(budget)
```

`evaluate` is a pure domain/application function: it applies the table in section 4 to the **current** claim object
(read inside the transaction) with the batch's lookup facts. A healthy claim whose endpoints are present and whose
basis is current produces no write. Per-claim read-modify-write (not one bulk write of objects read earlier) is what
keeps a human decision made mid-batch from being overwritten.

### Crash, repeat and concurrency behaviour

- **Crash** at any point: claim changes already committed stay (each is idempotent); the cursor only moves after the
  batch's claims were applied, so a restart re-evaluates at most one batch, which finds nothing new to write.
- **Repeated runs**: same library, same claims -> zero writes; the outcome says `claims_changed=0`.
- **Two jobs at once** (two threads or processes): both evaluate the same batch idempotently; the cursor
  compare-and-set lets one proceed and the other end as `SUPERSEDED`. No lease, no heartbeat: correctness does not
  depend on exclusivity, so a dead owner cannot block anyone (the lease machinery in `proxy_manager` exists because
  its work is not idempotent).
- **Claims added or decided during a sweep**: new claims behind the cursor are picked up by the next sweep (they
  were validated against the library when created); claims ahead of the cursor are seen with their current state.
- **A corrupt claim row** is skipped, counted and logged (`claims_skipped_corrupt`), never allowed to fail the whole
  job: the batch is read as light rows and each claim is hydrated only inside its own transaction.

### Due logic

A sweep **in progress** is always continued (so it finishes); a new sweep starts only when
`now - last_sweep_completed_at >= min_sweep_interval`; both are gated by `next_attempt_after` (failure backoff).
`force=True` skips the interval, never the backoff of a failing library.

## 6. Deletion, archive, restore and retention

| Event | Behaviour |
| --- | --- |
| Endpoint **deleted** (library asset gone; includes cascade) | claim gets `endpoint_missing_since` at the first sweep that confirms it; reads already show `endpoint_states[...] == missing` before that |
| Endpoint **archived** | nothing is stored; reads show `archived`; the claim is never purged for this |
| Endpoint **restored** from archive | nothing to undo |
| A deleted asset **comes back with the same id** (for example a library database restored from a backup) | the next sweep clears `endpoint_missing_since`; before expiry the claim carries on unchanged |
| **Undecided** claim missing for `undecided_orphan_grace` | purged, audit record `undecided_orphan` |
| **Decided** claim missing for `decided_retention` (365 days, approved, configurable) | purged, audit record `retention_expired` |
| Claim whose endpoint reappears **after** the purge | gone; only the audit record remains (ids are never reused, so there is nothing to re-attach) |

What a purge does [P]:

1. One transaction: re-check the claim-owned preconditions (status class, `endpoint_missing_since` set before this
   evaluation, grace or retention reached); delete the claim (its evidence and history rows go with it by
   `ON DELETE CASCADE`); insert one `purge_log` row.
2. `purge_log` keeps, per purged claim: relationship id, type, both asset ids, final status, `decided_at` (from the
   history; `NULL` for undecided), `endpoint_missing_since`, `purged_at`, reason. It keeps **no evidence**. It is
   small, append-only and never purged by this module. This is the audit trail for human decisions that would
   otherwise vanish silently.
3. Log one structured line per purge batch (counts and reasons), never asset paths.

Why an undecided grace exists (a deviation from main design section 8, which said "deleted by reconciliation"): a
library that is temporarily wrong (restored from an older backup, pointed at another data directory) would
otherwise erase proposals in one sweep. Seven days is long enough to notice and short enough that orphans do not
accumulate. [P], configurable.

## 7. Failure handling

### Missing versus failed

- **Failed** = the library raised (`PersistenceError` or any `InfrastructureError`). Nothing is changed. The batch is
  retried with the policy below; if it still fails the job ends `PARTIAL(transient_library)` with the cursor at the
  last committed batch.
- **Missing** = the library answered and the id is not in the `get_many` result.
- **Suspicious library guard**: if a batch has at least `suspicious_min_claims` (20) claims and at least
  `suspicious_ratio` (0.5) of their endpoints are absent, wait `suspicious_recheck_seconds` (5, cancellable) and look
  again once. Still suspicious -> `PARTIAL(suspicious_library)`: **no write for that batch and its cursor is not
  advanced**; earlier batches of the same job are already committed and stay (there is no whole-sweep transaction).
  A warning carries the counts. The next attempt, gated by `next_attempt_after` (backoff), resumes from the cursor,
  that is from the suspicious batch, and judges it again. See "Corrections after approval", A.
- Any other exception is a defect: the job ends `FAILED` and the exception propagates (typed error chain), never
  swallowed.

### Retry, backoff, cancellation

- Per batch: 3 attempts with delays 1 s, 4 s, 16 s, each wait via `ctx.cancellation.wait(delay)` so cancellation
  interrupts it.
- Per job: a failed or partial-for-failure job increments `consecutive_failures` and sets
  `next_attempt_after = now + min(30 min * 2^(failures-1), 24 h)`. Success resets both.
- Cancellation is checked before every batch and every wait. A cancelled job ends `INTERRUPTED` (the outcome is
  recorded in a `finally`, best effort) with the cursor at the last committed batch.

### Library or proxy catalog unavailable

- **Library**: as above; reads against it fail by themselves (they raise), reconciliation writes nothing.
- **Proxy catalog**: reconciliation does not use it. On the read path it can still raise today (section 9, R-C).

## 8. R2 - the library change feed (specified now, implemented with Slice 4)

Its only consumer is a detector that must look at new or changed assets, so building it in Slice 3 would be dead code.
The design is fixed here so Slice 4 does not have to reopen it. No upstream contract is needed [V].

- **New assets**: an exact keyset over `(created_at, id)` using `search(created_from=cursor_ts, sort CREATED_AT ASC,
  page_size=200)`. Because `created_from` is inclusive and rows sort by `(created_at, id)`, resuming at
  `created_from = last.created_at` and skipping rows with `created_at == last.created_at and id <= last.id` is an
  exact cursor for ties **of at most one page**. Every batch is a fresh query (no offset carried between batches), and
  `created_at` of an asset never changes, so inserts at the end and deletes of rows already passed do not disturb the
  cursor. Persisted as `feed_cursor = "<created_at>|<id>"` after every batch. Known limits, not eliminated (see
  "Corrections after approval", C): (1) if more than one page of assets shares a single `created_at`, moving past that
  tie group needs offset paging (`page=2...`) inside it, which can shift under concurrent changes; (2) an asset whose
  insert commits late can carry a `created_at` older than the cursor and is missed by the feed. Both are repaired by
  idempotent processing plus the periodic full asset sweep, i.e. eventually, not immediately.
- **Changed assets** (an inspection or other derived document appearing later): there is no `updated_from`. Detectors
  read derived documents, which are themselves assets with their own `created_at`, so the same keyset over derived
  assets (`roles` filter) finds them. A true "metadata edited" feed would need the unapproved `updated_from` (design
  item C1); until then edits are caught by the periodic full asset sweep below.
- **Full asset sweep** (compensates for anything missed, including a feed cursor lost to corruption): the same keyset
  from `(min, min)` to the end, at most `max_assets_per_job` per job, `min_asset_sweep_interval` apart. Deletions need
  no handling here (R1 owns them).
- **Crash**: cursor committed per batch; re-processing a batch is idempotent because `ProposeRelationship` is.
- **Equal timestamps**: covered by the `(created_at, id)` tie rule, with a property test.
- Defaults to be set in the Slice 4 design; this section only fixes the mechanism.

## 9. The derivative-scan cap, non-snapshot pagination, and the read path

Facts [V]: the projection scans at most 5 library pages (1000 derivatives) per query and recomputes on every page; the
library pages by offset; `truncated` and `EXACT_DUPLICATE_REVERSE_PARTIAL` are reported; `source_missing` and
`untrusted_provenance` are only logged.

- **Reconciliation is independent of all of this.** It never calls the projection, so the cap, the offset paging and
  the per-page recomputation affect only what a *query* returns, never what reconciliation stores or concludes. A
  truncated or shifted read has no persistent effect, so there is nothing to retry or recover on the server side; the
  caller re-asks.
- **Rescanning per page** cannot be avoided safely with what exists: a cache would have to be validated against the
  library, and there is no change token (that would be an upstream contract, item C1 or similar). A time-based cache
  would serve stale lineage after a proxy is replaced, against the rule that a cache hit must be trustworthy
  (`UNIVERSAL_BASE_PROMPT.md` section 12). **Proposal: no cache.** The mitigation is documentation: ask for lineage
  with `page_size=200` (one scan for up to 200 items).
- Small read-path follow-ups, each **separately approvable** because each touches the Slice 2 surface [P]:
  - **R-A (log levels)**: expected skips (`not_media`, `preview`, `excluded_operation`) log at debug; only
    `untrusted_provenance`, `source_missing` and truncation log at info/warning. Today every query on an inspected asset
    logs at info.
  - **R-B (report the findings)**: an additive `RelationshipPage.findings: tuple[LineageFinding, ...]` with
    `LineageFinding(kind: source_missing | untrusted_provenance, asset_id)`, at most 20 listed plus a count, so a
    caller can tell "no proxy" from "a proxy was rejected as untrusted". `truncated` stays as is (the incomplete scan
    is already reported).
  - **R-C (proxy catalog failure)**: an `InfrastructureError` from `ProxyCatalog.find` degrades `serving` to `None`
    plus an additive `Limitation.PROXY_STATE_UNAVAILABLE`, instead of failing the whole query.
  - **R-D (detect non-snapshot paging)**: the cursor becomes an opaque token carrying the last id and the
    first scan page's `Page.total`; if the total differs on the next page the answer says
    `Limitation.LINEAGE_CHANGED_WHILE_PAGING` (restart). It is already an opaque `str` for callers. **It detects some
    concurrent changes, not all**: an insertion plus a deletion leaves the total unchanged, so an unchanged total does
    NOT guarantee a stable snapshot (C).
  I recommend A, B, C and D; none is needed for R1.

## 10. Scheduling and resource bounds

Reuse `JobScheduler` / `JobContext`; nothing else is created. The use case is
`ReconcileRelationships.execute(command, ctx) -> ReconcileOutcome`, blocking and cooperative like the other
long-running use cases in the repository.

| Setting (`ReconciliationSettings`, frozen) | Default | Rationale |
| --- | --- | --- |
| `batch_claims` | 250 | at most 500 endpoints, one `get_many` (the library chunks at 500) |
| `max_claims_per_job` | 10 000 | 40 batches; healthy claims write nothing, so a job is dominated by 40 indexed lookups (to be measured [U]); bounds how long one pool worker is held |
| `min_sweep_interval` | 24 h | endpoint loss is visible on reads at once, so freshness of stored cleanup is not urgent |
| `undecided_orphan_grace` | 7 days | section 6 |
| `decided_retention` | 365 days | approved provisional default |
| `retry_delays_seconds` | 1, 4, 16 | three attempts per batch |
| `failure_backoff` | 30 min * 2^(n-1), capped 24 h | stops a down library being hammered |
| `suspicious_min_claims` / `suspicious_ratio` / `suspicious_recheck_seconds` | 20 / 0.5 / 5 | section 7 |

All defaults are provisional and configurable; none is applied to a real library until measured.

**Trigger (needs your decision).** There is no timer facility and no startup hook, and I am not to build a scheduler.
The interval above is therefore a *policy the caller consults*, not something that fires by itself. Options:

- **A (recommended for Slice 3)**: caller-driven only. The use case and a `RelationshipMaintenance` contract exist;
  nothing calls them automatically. A later UI action, CLI command or startup submission uses them.
- **B**: a one-shot job submitted from bootstrap after startup, which touches `bootstrap`.
- Lazy triggering from the query path is rejected: background work from a read is a surprise.

A sweep is spread across jobs when the budget runs out, so the caller invokes the use case repeatedly until the outcome
is `COMPLETED`; it never needs a long-held worker.

## 11. Observability

`ReconcileOutcome` (returned, logged, and persisted as `scan_state['last_run']`):

- `status`: `COMPLETED` (sweep finished), `PARTIAL`, `INTERRUPTED`, `FAILED`, `NOT_DUE`, `SUPERSEDED`.
- `reason` for `PARTIAL`: `budget`, `transient_library`, `suspicious_library`.
- counters: `claims_checked`, `claims_changed`, `marked_missing`, `restored`, `purged_undecided`, `purged_decided`,
  `marked_stale`, `marked_outdated`, `skipped_corrupt`, `batches`, `retries`.
- `sweep_generation`, `cursor` (opaque), `started_at`, `finished_at`, `next_attempt_after`.
- Logging: one structured info line per job with the outcome; warnings for `suspicious_library` and for exhausted
  retries; debug per batch; no asset paths, no evidence.
- Reporting of the Slice 2 findings: section 9 (R-B for `source_missing` / `untrusted_provenance`; `truncated` already
  covers an incomplete scan). Reconciliation itself has no derivative scan, so it can neither hit the cap nor emit those
  findings; claims whose assets are gone are counted as `marked_missing`.

## 12. Schema and code impact (exact; nothing applied)

### Migration 2 (append-only; user_version 1 -> 2)

```
ALTER TABLE relationships ADD COLUMN endpoint_missing_since TEXT;
CREATE INDEX ix_relationships_missing ON relationships (endpoint_missing_since)
    WHERE endpoint_missing_since IS NOT NULL;
CREATE TABLE scan_state (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
    -- claim_cursor, sweep_generation, sweep_started_at, last_sweep_completed_at,
    -- consecutive_failures, next_attempt_after, last_run (JSON)
CREATE TABLE purge_log (
    seq INTEGER PRIMARY KEY,
    relationship_id TEXT NOT NULL, type TEXT NOT NULL, asset_a TEXT NOT NULL, asset_b TEXT NOT NULL,
    final_status TEXT NOT NULL CHECK (final_status IN ('proposed','confirmed','rejected','stale')),
    decided_at TEXT, endpoint_missing_since TEXT NOT NULL, purged_at TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('undecided_orphan','retention_expired')));
CREATE INDEX ix_purge_log_assets ON purge_log (asset_a, asset_b);
```

- `ALTER ... ADD COLUMN` of a nullable column rewrites no rows; every existing claim starts healthy.
- **`analysis_marks` is not in this migration**: main design section 8 put it here, but nothing writes it before a
  detector exists. It moves to the Slice 4 migration (a documented deviation).
- Tests: migrate a populated v1 database and keep every row; refuse a newer version; concurrent first use applies it
  once; the CHECKs reject bad values.

### Code (additive)

- Domain: `AssetRelationship.endpoint_missing_since`, `mark_endpoint_missing`, `clear_endpoint_missing`,
  `mark_outdated`; `domain/reconciliation.py` (pure `evaluate` rules and the suspicious-library test).
- Port: `ReconciliationStore` (`claims_after`, `apply`, `advance_cursor`, `complete_sweep`, `purge`, `load_state`,
  `record_run`), implemented by the SQLite adapter beside the existing repository; the existing
  `RelationshipRepository` is not changed.
- Application: `ReconcileRelationships`, `ReconciliationSettings`, `ReconcileOutcome`, and the `BasisCheck` port:
  `is_current(basis) -> bool | None` (`None` = "this check does not know that detector"). Slice 3 wires an empty
  list, so nothing is invalidated in production; tests use a fake. Slice 4 detectors contribute real checks.
- Contract (additive): `RelationshipMaintenance` (`reconcile`, `last_run`), `ReconcileOutcome` and its enums,
  `RelationshipDto.endpoint_missing_since`.
- Module wiring only; **no bootstrap change** under option A.
- Architecture test: reconciliation modules do not import `lineage_projection` or the proxy contract.

## 13. Acceptance tests

Fast tests use fakes, a fixed clock and zero-length delays (the settings carry them); integration tests use the real
library and SQLite; two use real processes.

| Scenario | Test |
| --- | --- |
| crash recovery | exception injected after batch k commits and before the cursor advances; a new job resumes and ends in the same state as an uninterrupted run; a real worker process killed mid-sweep (`os._exit`), then a second process completes |
| repeated runs | two runs in a row: the second has `claims_changed == 0`, every `updated_at` unchanged |
| timestamp ties | many claims created at the same instant sort and sweep by id, each visited once; (R2, in Slice 4) a property test of the `(created_at, id)` keyset with ties at page boundaries |
| concurrent changes | an endpoint deleted, archived, restored, or a claim added, decided or deleted between batches: the human decision wins, the claim is visited or picked up next sweep, nothing is lost or doubled |
| archive / restore | archived endpoint: no mark, never purged; restore: nothing to undo |
| endpoint deletion | marked at first confirmed absence; kept through the grace; purged after it with a `purge_log` row; decided claim kept until retention; reappearing endpoint clears the mark before expiry |
| transient failures | library raising `PersistenceError`: no write, retries with the configured delays, `PARTIAL(transient_library)`, cursor unchanged, `next_attempt_after` set, later job resumes; a non-infrastructure exception ends `FAILED` and propagates |
| suspicious library | a batch with most endpoints absent: re-verified once, then `PARTIAL(suspicious_library)` with no write for that batch, its cursor not advanced, earlier committed batches kept, and the next job resuming from that cursor after the backoff |
| decision preservation | a matrix over the four statuses: no status change by reconciliation; purge preconditions re-checked inside the transaction (a decision or a reappearance between read and write wins) |
| algorithm-version change | fake `BasisCheck`: `PROPOSED` -> `STALE`, decided -> `outdated_since`, manual basis and unknown detector untouched, a re-assertion returns `STALE` to `PROPOSED` |
| bounded work | with 12 000 claims and `max_claims_per_job=10 000` the first job stops `PARTIAL(budget)` after 40 batches and 40 `get_many` calls; the second completes the sweep; `NOT_DUE` inside the interval; an in-progress sweep ignores the interval |
| cancellation | cancelled mid-job: `INTERRUPTED`, cursor at the last committed batch, no half-applied claim |
| two jobs at once | threads and processes: one advances, the other ends `SUPERSEDED`, final state equals a single run |
| corrupt row | a corrupt claim is skipped and counted; the rest of the batch is applied |
| lineage independence | after reconciling a library with derivatives and proxies the `relationships` table is unchanged; the architecture test holds |
| migration | v1 -> v2 as above |

Static checks as before (ruff, ruff format, mypy on explicit paths). The final verification runs the focused tests, the
architecture tests, the lifecycle test and the **full** integration suite to completion; the presentation suite still
aborts on the pre-existing Qt-lifetime problem (reproduced on pristine HEAD without this module) and is reported as such,
not as passing.

## 14. Slice 2 limitations that remain documented

- The derivative scan is capped at 5 library pages per query; beyond 1000 derivatives the rest is unreachable and
  `truncated` says so (archived derivatives are fetched before they are hidden, so they also use the cap).
- Reverse duplicate discovery from an original is partial and always flagged; derived-to-derived duplicates are not
  found (design item C2, not approved).
- `source_missing` and `untrusted_provenance` are log-only until R-B is approved.
- Pagination is not a snapshot; each page recomputes the lineage; R-D (if approved) detects a change during paging, it
  does not prevent it.
- `serving=True` means "the slot points at it", not "usable"; the verdict in the evidence was recorded at registration.

## 15. Risks and unresolved questions

Risks: the numbers in section 10 are unmeasured; a sweep that is never invoked leaves stored cleanup undone (reads stay
correct); the empty `BasisCheck` list means version invalidation is untestable in production until Slice 4;
`purge_log` grows by one small row per purged claim; the suspicious-library thresholds can be wrong for a very small
library (hence the 20-claim minimum).

Decisions I need from you (my recommendation first):

1. **Trigger**: A, caller-driven only (recommended), or B, a bootstrap one-shot.
2. **Undecided grace**: 7 days (recommended) or immediate deletion as main design section 8 said.
3. **Audit**: keep `purge_log` (recommended) or hard-delete decided claims at expiry.
4. **`BasisCheck`**: wire the port now with an empty list (recommended; testable with a fake) or defer version
   invalidation to Slice 4.
5. **R2** (library change feed): specified here, implemented with Slice 4 (recommended), or built now.
6. **Read-path follow-ups** R-A, R-B, R-C, R-D: approve all four as part of Slice 3 (recommended), pick some, or none.
7. **Per-page rescan**: no cache (recommended) or a short time-based cache.
8. **`analysis_marks`** moved to the Slice 4 migration (recommended).
9. **Defaults** in section 10 as provisional starting values.

## 16. Vertical slices after approval

- **3a**: domain attributes and rules, migration 2, SQLite store methods, with tests (including v1 -> v2).
- **3b**: `ReconcileRelationships`, settings, outcome, retry/backoff, suspicious guard, `BasisCheck`, with the crash,
  repeat, concurrency, transient and bounded-work tests.
- **3c**: contract (`RelationshipMaintenance`, `endpoint_missing_since`), module wiring, README and `AI_CONTEXT.md`, the
  approved read-path follow-ups, architecture test.
- **Final**: focused tests, architecture, lifecycle, the full integration suite to completion, static checks, diff review.

## Corrections after approval (2026-10-26)

The three corrections below were required before coding and are binding; they replace anything above that says
otherwise.

### A. The suspicious-library guard commits per batch, so it cannot promise zero writes for a job

Batches are committed incrementally on purpose (no whole-sweep transaction). Therefore, when a batch is still
suspicious after its recheck:

- no write is made for that batch (no mark, no clear, no purge, no stale or outdated change) and its cursor is **not**
  advanced;
- the job ends `PARTIAL(suspicious_library)`;
- batches committed earlier in the same job are preserved;
- the next attempt resumes from the cursor (so from the suspicious batch) once `next_attempt_after` allows, under the
  normal backoff, and judges that batch again.

### B. The cross-database reappearance race is documented, not eliminated

The relationship store and the Media Library are separate SQLite files, so one transaction cannot verify an endpoint's
state in the library. What is guaranteed, and what is not:

- Inside the relationship-store transaction the purge re-checks only what that store owns: the claim's status class,
  that `endpoint_missing_since` was already set before this evaluation, and that the grace or retention has elapsed.
- A library failure is a failure, never proof of absence: it changes nothing and follows the retry policy.
- If an endpoint is seen present before the purge decision (the batch's lookup), the missing marker is cleared and
  nothing is purged.
- **Unavoidable race**: an endpoint that reappears after the batch's last library lookup but before the purge commits
  is purged anyway (and gets its `purge_log` row). The next successful sweep can correct a missing *marker* on a claim
  that still exists; it cannot restore a purged claim. The window is one batch's processing time and requires an
  asset that was missing for the full grace or retention to return inside it; ids are never reused, so the only
  realistic source is a library restored from a backup. A characterization test pins this behaviour so it is never
  mistaken for a guarantee.
- If eliminating this window ever becomes essential, it needs a stronger coordination design (for example the library
  owning a tombstone, or a two-phase confirmation), proposed separately; this slice does not claim it.

### C. Pagination guarantees

- R-D compares `Page.total` between pages. That detects some concurrent changes only. An insertion plus a deletion
  between two pages leaves the total unchanged, so **an unchanged total does not guarantee a stable snapshot**; a
  characterization test shows this.
- R2 stays deferred to Slice 4 and no change-feed contract is added upstream. Its `(created_at, id)` cursor handles
  timestamp ties up to one page; moving beyond a tie group larger than a page needs offset paging, which can shift
  under concurrent changes, and a late-committed insert can be missed. Recovery is eventual and comes from two things
  already in the design: every consumer step is idempotent (re-processing a batch changes nothing), and the periodic
  full asset sweep re-walks everything, so anything the feed missed is processed by the next full sweep.

### Approved decisions (all nine, as proposed)

1 caller-driven only; 2 seven-day undecided grace; 3 keep `purge_log`; 4 `BasisCheck` wired with an empty production
list; 5 R2 specified now and implemented in Slice 4; 6 R-A, R-B, R-C and R-D implemented with the limits above; 7 no
time-based lineage cache; 8 `analysis_marks` moves to the Slice 4 migration; 9 the defaults in section 10 are
provisional and configurable, including the 365-day retention.

## Amendments after Slice 3 (as built, 2026-10-26)

Slice 3 is built. These replace the corresponding statements above; the module README records the verified
behaviour.

1. **Retries.** `retry_delays_seconds=(1, 4, 16)` are the waits before each RETRY, so a batch gets one attempt
   plus up to three retries (four calls), not three attempts in total. Waits go through
   `ctx.cancellation.wait`, so cancelling interrupts them.
2. **What a job returns.** `ReconcileRelationships.reconcile(command, ctx)` returns a `ReconcileOutcome` for
   `COMPLETED`, `PARTIAL`, `NOT_DUE` and `SUPERSEDED`. A cancelled job records `INTERRUPTED` and then raises
   `OperationCancelledError`; an unexpected exception records `FAILED` and propagates. `last_run()` answers both.
   `NOT_DUE` is never recorded, so it cannot hide the last real run.
3. **Cancellation granularity.** It is checked before every batch and during every wait. A batch that has
   started is applied and its cursor committed first, so cancelling loses nothing; "at most the batch in flight"
   describes a crash (a hard kill), which the real-process tests cover at batch boundaries and in the middle of
   a batch.
4. **Store port.** `ReconciliationStore` is separate from `RelationshipRepository` (which is unchanged):
   `claims_after` (light references, keyset), `reconcile_claim` (one transaction: load, let the rules decide on
   the stored claim, save or purge), `load_state`, `advance_cursor` and `complete_sweep` (compare-and-set),
   `record_run`. The purge guard (the claim must already carry the marker) is checked by the store as well as by
   the rules.
5. **Corrupt rows.** `CorruptClaim(PersistenceError)` marks a claim row that cannot be rebuilt, so reconciliation
   can skip it (`skipped_corrupt`) while a database fault, which is also a `PersistenceError`, still stops the job.
6. **Failure accounting.** `FAILED`, `PARTIAL(transient_library)` and `PARTIAL(suspicious_library)` raise
   `consecutive_failures` and set `next_attempt_after`; `COMPLETED` and `PARTIAL(budget)` reset them;
   `INTERRUPTED` and `SUPERSEDED` leave them as they were.
7. **`sweep_started_at`** is set by the first cursor advance of a sweep.
8. **Read-path follow-ups as built.** R-A: expected skips log at debug; untrusted provenance and source-missing
   findings at warning; a truncated scan and an unavailable proxy catalog at info and warning. R-B: `findings`
   (at most 20, sorted by kind and asset id) and `findings_omitted` on the page; `source_missing` names the
   derivative whose source cannot be found. R-C: an `InfrastructureError` from `ProxyCatalog.find` sets `serving`
   to `None`, adds `Limitation.PROXY_STATE_UNAVAILABLE`, and is not asked again for that slot in the same query; any
   other exception still propagates. R-D: the cursor is `<last id>` or `<last id>:<derivative total>`; a differing
   total adds `Limitation.LINEAGE_CHANGED_WHILE_PAGING`; a plain id cursor from before still works; and
   characterization tests pin that an insertion plus a deletion is NOT detected.
9. **DTO.** `RelationshipDto.endpoint_missing_since` (claims only), `RelationshipPage.findings` and
   `findings_omitted`.
10. **Not built, as agreed:** R2 and `analysis_marks` (Slice 4), any automatic trigger, any real `BasisCheck`, any
    change to bootstrap, upstream contracts, `MediaRelations`, the workspace or presentation code.
11. **Only claims that need attention open a transaction.** The first build judged every claim in its own
    write transaction; measured on SQLite that cost about 4.5 ms per healthy claim (2 000 claims: 8.9 s). A
    claim reference now carries two hints (`marked`, the stored `basis`), and the job judges a claim only if an
    endpoint is absent, it already carries the marker, or a `BasisCheck` calls its basis out of date (the same
    sweep then took 0.3 s). The hints are only a hint: everything acted on is judged again on the stored claim
    inside its transaction. Consequences: a healthy claim whose row is unreadable is not noticed until it needs
    attention; a `BasisCheck` is asked for every claim, outside any transaction, and again inside the claim's own
    transaction when it says "out of date", so it must be quick and must not call back into this store.
