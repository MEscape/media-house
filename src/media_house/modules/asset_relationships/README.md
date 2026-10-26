# Asset Relationships

A relationship is a **claim about two library assets, with the evidence that supports it**, or a
**recorded fact** the library or the proxy manager already holds. This module stores claims and the
decisions people make about them, projects recorded lineage at read time, and answers "how are these
related and why do we think so?". It never merges assets, never edits media and never decides anything
editorial.

Design: `docs/superpowers/specs/2026-10-26-asset-relationships-design.md` and, for reconciliation,
`docs/superpowers/specs/2026-10-26-asset-relationships-slice3-reconciliation-design.md`.

Status: **Slice 3** (Slice 1: domain, store, claims and decisions; Slice 2: recorded lineage projection;
Slice 3: reconciliation). What is not built yet is listed under [Not built yet](#not-built-yet); nothing below claims more than the
tests show.

## What owns what

| Concern | Owner |
| --- | --- |
| Asset identity, checksum, derivation records, deletion | Media Library, read through `MediaLibrary` |
| Proxy provenance, which proxy a slot serves now | Proxy Manager, read through `read_proxy_provenance` and `ProxyCatalog` |
| Stored claims, their evidence, statuses and the history of status changes | this module (`asset_relationships.db`) |
| Technical facts, production tags | `media_inspection` (not read yet) |

The database stores **no** checksum, path or copy of upstream data, and **no recorded lineage**: only
library asset ids, the claim, and the few measured values that explain it. Endpoints are checked against
the live library on every read (`active`, `archived` or `missing`).

## Vocabulary

`RelationshipType` (not `RelationKind`: three other modules already use that name).

| Type | Direction | Kind | Origin |
| --- | --- | --- | --- |
| `temporally_overlaps_with` | symmetric, stored once in canonical order | stored claim | `detector`, `user` |
| `alternate_take_of` | symmetric, stored once in canonical order | stored claim | `detector`, `user` |
| `exact_duplicate_of` | directed, non-original -> original | recorded lineage | `library_record` |
| `proxy_of` | directed, proxy -> source | recorded lineage | `proxy_record` |
| `derived_from` | directed, derivative -> source | recorded lineage | `library_record` |

`transcode_of` and `version_of` do not exist: no operation in the repository is a transcode and nothing
models versions.

## Recorded lineage (read-time projection)

Lineage is **never stored**: it is asked from its authority on every query, so a replaced, removed or
cascade-deleted derivative cannot leave a stale relationship behind. A recorded item has `stored=False`,
`status=None` (a fact cannot be confirmed or rejected), `grade=recorded`, and evidence naming who recorded
it (`media_library` or `proxy_manager`) and when the library recorded the asset, so the same query gives
the same answer.

What counts, in this order (`domain/lineage.py`):

1. A document (`MediaType.OTHER`: inspection, transcript, analysis, alignment) is **not** lineage.
2. A **proxy** needs the library's derivation record AND `read_proxy_provenance` to name the same source.
   A provenance record naming another source is not trusted (asset metadata is editable) and the asset is
   dropped, not demoted to `derived_from`. A malformed record counts as no provenance.
3. A preview that is not a proxy (a thumbnail) is not lineage.
4. An operation in `LineageSettings.excluded_operations` is not lineage. The default is
   `{"audio_extraction"}`, the internal WAV that audio intelligence cuts from a source; a test pins it to
   the real constant.
5. Anything else that is audio, video or an image is `derived_from`, with the operation as a label.

`proxy_of` items carry `serving`: `True` when the proxy's slot serves it now (`ProxyCatalog.find`, asked
once per source and profile), `False` for a proxy that is kept but not served (the rollback of a replaced
proxy), `None` when the catalog is not available. Nothing guesses.

### `exact_duplicate_of` is directed, and its reverse is partial

* From a **non-original** asset the matching original is found **exactly** (`find_by_checksum`; the
  library allows one original per checksum). The answer carries no limitation.
* From an **original** only duplicates among its **own derivatives** are found: the library cannot list
  every asset sharing its bytes, and this module does not scan. A duplicate derived from another source is
  not found.
* Two originals are never duplicates (the library stores identical content once). Derived-to-derived
  duplicates are not reported.

### Limitations and bounds on a page

`RelationshipPage.limitations` is a stable list of `Limitation` values:

| Value | Meaning | When |
| --- | --- | --- |
| `exact_duplicate_reverse_partial` | duplicates of this original are found only among its own derivatives; an empty answer is not proof that there are none | the queried asset is an **original** AND `exact_duplicate_of` is in scope, **even when nothing was found**, on every page |
| `proxy_state_unavailable` | `proxy_manager` failed, so `serving` of the proxies on this page is `None`; the rest of the answer is complete | a lineage query that reached the proxy catalog and got an infrastructure failure (asked once per source and profile) |
| `lineage_changed_while_paging` | the number of derivatives differs from what the previous page saw, so the pages may not fit together; restart the query | the cursor's total differs from this page's scan total. **An unchanged total proves nothing**: an insertion plus a deletion keeps it, so no limitation is not a stable snapshot |

"In scope" means the query asks for lineage (`include_projected`), allows the type (`types`), allows its
origin (`origins`) and does not filter by `statuses` (a status filter asks for claims only). The rule is
`domain/limitations.py`.

`RelationshipPage.findings` lists problems found in the library's own records while projecting (at most 20,
sorted; `findings_omitted` counts the rest): `source_missing` (a derivative whose source cannot be found) and
`untrusted_provenance` (a proxy whose provenance names another source than its derivation record; it is not
reported as lineage). Routine skips (documents, thumbnails, excluded operations) are not findings and are
logged at debug only.

`RelationshipPage.truncated` is `True` when the derivative scan reached `LineageSettings.max_derivative_pages`
(5 pages of 200, provisional): some derivatives may be missing. Below the cap nothing is dropped silently:
the scan reads the library's own pages (`Page.total`) and pages are deterministic (oldest first).

## Querying

`relationships_of(asset_id, RelationshipQuery(...))` answers stored claims and recorded lineage **together**,
in relationship-id order (one cursor pages through both). `origin` and `stored` say which is which.

* `types`, `origins`, `min_grade`, `cursor`, `page_size` (1-200) filter as named; empty means no restriction.
* `statuses` asks for **claims only**; recorded lineage has no status.
* `min_grade` keeps recorded facts (`recorded` is the strongest grade) and filters claims.
* `include_projected=False` answers claims only and reads nothing from the library.
* `include_archived=True` shows recorded lineage whose *other* end is archived. Stored claims always come
  back, with `endpoint_states`.
* `get(relationship_id)` finds a **stored** claim only; ask about an asset to get its lineage.
* The cursor is opaque. For a lineage query it also carries the derivative total the scan saw, so the next
  page can notice a change (`lineage_changed_while_paging`). Paging is **not a snapshot**: every page
  recomputes the lineage (at most 5 library pages), and the library pages by offset, so a derivative added or
  deleted between two pages can shift or skip an item. Ask with `page_size=200` for lineage-heavy assets.

## Claims, evidence and grades

* A claim is a **proposal** until a person confirms it. No stored type is confirmed by automation.
* `grade` is ordinal (`corroborated` > `indicated` > `suggestive`; `recorded` is for lineage). It says how
  well the evidence supports the claim *for its type*. It is never a probability and never makes a claim
  trusted lineage.
* Evidence is typed and admissible per type. There is no evidence kind for filename, duration, size,
  visual or transcript similarity, so those cannot create a claim.
* Wall-clock evidence is `suggestive` unless the clock is trusted (same device, or a person says so).
  Timecode alone is `suggestive`. A repeated `scene` with different `take` tags is `indicated`, and
  `corroborated` when the reel or device is shared.

## Decisions are protected

* `confirm` and `reject` are the only ways a decided relationship changes, and only a person calls them.
* Asserting an existing pair again is a **refresh**: undecided claims take the new evidence; a decided
  claim keeps its status, intervals and offset, adds the new evidence (superseding the old, never
  deleting it) and is marked `outdated_since` so a person can look again.
* A rejection applies to the pair and type and survives re-detection.
* The same assertion again changes nothing, `updated_at` included.
* Projection never touches stored claims or decisions.

## Reconciliation (caller-driven)

`RelationshipMaintenance.reconcile(ReconcileCommand(force=False), ctx)` keeps the STORED claims truthful
against the live library. Nothing calls it by itself: there is no timer and no startup hook, so the caller
(a UI action, a command, a startup submission) runs it as a job on the existing `JobScheduler` and repeats
it until the outcome is `COMPLETED`. Reads never wait for it: endpoint state on every read is live.

A job walks the module's own claims by id in batches of 250 (a keyset, so ties cannot occur), asks the
library once per batch about the endpoints of those claims, and applies the rules to each claim in its own
atomic step, but only for claims that need attention: a missing endpoint, an existing marker, or a basis a
check calls out of date. A healthy claim opens no transaction at all (a sweep of 2 000 healthy claims took
0.3 s on SQLite; judging every claim cost 8.9 s). A healthy claim whose row is unreadable is therefore not
noticed until it needs attention. At most 10 000 claims per job; a longer sweep continues in the next job.

| Observation | Undecided (`proposed`, `stale`) | Decided (`confirmed`, `rejected`) |
| --- | --- | --- |
| an endpoint is confirmed missing, first time | `endpoint_missing_since` is set | same |
| every endpoint present again | marker cleared | marker cleared |
| missing for 7 days (`undecided_orphan_grace`) | purged | kept |
| missing for 365 days (`decided_retention`) | n/a | purged |
| basis out of date (a `BasisCheck` says so) | becomes `stale` | `outdated_since` is set, status kept |
| endpoint archived or restored | nothing | nothing |

* Missing means the library answered and did not have the id. A library **failure** is never an absence: it is
  retried (1, 4, 16 s, cancellable) and then the job ends `PARTIAL(transient_library)` with nothing written for
  that batch; the next attempt waits `min(30 min * 2^(n-1), 24 h)`.
* **Suspicious library**: a batch of at least 20 claims with at least half of its endpoints absent is looked at
  once more after 5 s; if it still looks emptied the job ends `PARTIAL(suspicious_library)`: no write for that
  batch and its cursor does not move. Earlier batches of the job stay committed (there is no whole-sweep
  transaction); the next attempt resumes from that batch after the backoff.
* A purge deletes the claim and writes one `purge_log` row (type, both asset ids, final status, decision time,
  first-missing time, reason; **no evidence**) in one transaction. The sweep that first sees an absence never
  purges.
* Decisions: reconciliation never changes `confirmed` or `rejected`. A purge evaluates the claim as it is stored
  at that moment, so a decision made while the job runs wins.
* **Documented race**: the library and this store are separate databases, so one transaction cannot check an
  endpoint's state in the library. An endpoint that reappears after the batch's lookup but before the purge
  commits is purged anyway; the next sweep can clear a marker on a claim that still exists but cannot restore a
  purged one. Not eliminated.
* Cancelling records `INTERRUPTED` and raises `OperationCancelledError`; a crash (even a hard kill) loses at
  most the batch in flight, and the next job resumes from the committed cursor to the same end state (tested
  with real processes). Two jobs at once converge: the one that moved the cursor second ends `SUPERSEDED`.
* A claim row that cannot be read is skipped and counted (`skipped_corrupt`), never fatal.

`ReconcileOutcome` (also kept as the last run, `last_run()`): `status` (`completed`, `partial`, `interrupted`,
`failed`, `not_due`, `superseded`), `reason` (`budget`, `transient_library`, `suspicious_library`, `backoff`,
`interval`, `cancelled`, `unexpected`), and counters `claims_checked`, `claims_changed`, `marked_missing`,
`restored`, `purged_undecided`, `purged_decided`, `marked_stale`, `marked_outdated`, `skipped_corrupt`,
`batches`, `retries`, plus `sweep_generation`, `cursor`, times and `next_attempt_after`. `not_due` answers are
not recorded. A new sweep waits `min_sweep_interval` (24 h) after the last one; `force=True` skips that wait,
never the failure backoff. All numbers are provisional and configurable (`ReconciliationSettings`).

Reconciliation never reads or stores recorded lineage (an architecture test forbids it). Version invalidation
takes `BasisCheck` ports; production wires none yet, so nothing is invalidated by version until a detector
exists.

## Time

Exact seconds (`fractions.Fraction`, signed) on a named clock (`container`, `timecode`, `wall_clock`),
half-open `[start, end)`. Intervals on different clocks are never compared; an offset
(`target = source + seconds`) records its origin and its uncertainty (`None` means unknown, not
zero). No interval is made up when the evidence does not support one.

## Using it

```python
from media_house.modules.asset_relationships.application.contracts import (
    RelationshipCatalog,
    RelationshipEditor,
    RelationshipQuery,
    RelationshipType,
)

page = catalog.relationships_of(clip_id)  # claims and lineage, id order, cursor paging
proxies = catalog.relationships_of(
    clip_id, RelationshipQuery(types=frozenset({RelationshipType.PROXY_OF}))
)
for item in proxies.value.items:
    if item.serving:  # the proxy its slot serves now
        use(item.source_asset_id)  # the proxy's id; do not store it
if proxies.value.limitations:  # never read an empty answer as exhaustive
    ...
editor.confirm(relationship_id, "same scene")  # a stored claim
```

## Storage

`asset_relationships.db` (SQLite, `PRAGMA user_version` migrations, one short-lived connection per
operation, writes in `BEGIN IMMEDIATE`). Tables: `relationships` (one row per type and pair, never both
directions), `relationship_evidence`, `decision_history` (append-only), `scan_state` (the sweep's cursor,
generation, backoff and last outcome) and `purge_log` (one small append-only row per purged claim). Lookup from either end uses
`ix_relationships_a` / `ix_relationships_b`. Every write is an atomic read-modify-write through
`RelationshipRepository.update`, so racing writers (threads or processes) converge on one row.

## Not built yet

* Detectors (temporal overlap, production-tag takes), audio fingerprints, visual or semantic similarity,
  subject identity, scenes, locations, projects - none exist; the design lists what each needs first.
* The library change feed for detectors (find new or changed assets) is specified in the Slice 3 design and
  built with the first detector, as is the "analysed, found nothing" marker table.
* Anything that triggers reconciliation automatically, and any real `BasisCheck`.
* Complete reverse discovery of duplicates, and derived-to-derived duplicates: they need a checksum lookup
  across roles in the library (design item C2), which is not approved.
* No UI and no domain events: nothing consumes them yet.
* `MediaRelations` in `media_inspection` is untouched; its fate is a decision gate.

Known limitations that remain: the derivative scan is capped at 5 library pages per query (the rest of a
bigger set is unreachable and `truncated` says so; archived derivatives are fetched before being hidden, so
they use the cap too); paging is not a snapshot; `serving=true` means the slot points at the proxy, not that it
is usable; the cross-database purge race above.
