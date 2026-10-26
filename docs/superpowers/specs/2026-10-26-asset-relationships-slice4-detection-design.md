# Asset Relationships - Slice 4: change feed, analysis marks and rule-based detectors

Status: **draft for review, 2026-10-26. Nothing in this document is built, migrated or committed.** Slice 4 is not
authorized. This document asks for decisions (section 12); it does not make them silently.

Evidence tags used throughout: **[V]** verified in the repository while writing this document; **[P]** a proposal of
this design; **[U]** unverified, to be settled before or during the slice; **[A]** needs your explicit approval.

Related documents: `2026-10-26-asset-relationships-design.md` (main design, Slice 1-2 amendments) and
`2026-10-26-asset-relationships-slice3-reconciliation-design.md` (Slice 3, as built; its section 8 fixed the R2 mechanism
that this document now details and refines).

## 1. Purpose, scope and boundaries

### Purpose

Let the module itself *propose* two kinds of claims, `temporally_overlaps_with` and `alternate_take_of`, from facts that
Media Inspection already recorded, without re-probing any media and without ever confirming, rejecting or reviving a
claim on a person's behalf. Everything a detector produces is a `PROPOSED` claim with evidence and a deterministic
grade; a person decides.

### What Slice 4 delivers [P]

1. **R2, the change feed and the full asset sweep**: an incremental walk over new assets and a periodic complete walk
   that repairs everything the incremental walk can miss (section 4).
2. **`analysis_marks`**: a derived ledger answering "has detector D looked at asset A on these inputs, and what came
   out" (section 5), plus **`observed_facts`**, a derived index of the few values the detectors need to find
   candidates without scanning the whole library (section 5, decision D2).
3. **Two rule-based detectors**: `wall_clock_overlap` and `production_tag_takes`, with a pure domain contract
   (section 7).
4. **`DetectRelationships`**, a use case that runs under the existing `JobScheduler` / `JobContext`, and a
   `RelationshipDetection` contract in this module's own `application/contracts.py` (section 8).
5. **A real `BasisCheck`** (detector versions), replacing the empty list wired in Slice 3 (section 7).
6. **Migration 3**, tests, README and `AI_CONTEXT.md` text (sections 6 and 9).
7. **Detectors ship disabled** (empty `enabled_detectors`): the code and tests exist, nothing runs against a real
   library until you approve enabling it after the evaluation in section 9.7 (decision D10).

### Already done (Slices 1-3, accepted, uncommitted) - not reopened here

Domain, store, claims and decisions; recorded lineage projection and its findings; reconciliation of claims against the
library (`RelationshipMaintenance`), purge audit, endpoint-missing marker, `scan_state` table, CAS cursor pattern.

### Not in Slice 4 (later work, each with its own approval)

Timecode-based detection (section 7.5), near-duplicate or visual similarity, multicam or audio sync, transcript
matching, any other relationship type, any upstream contract change (section 3.2), `used_in_project` or editing
integration, an automatic trigger (timer or startup hook), UI, events or notifications, any change to
`media_inspection.MediaRelations`, and the unresolved presentation-suite Qt crash.

### Goals, non-goals, anti-goals

- Goals: stable, deterministic, bounded, resumable detection; reuse of inspection data (never re-probing);
  every proposal explainable from its evidence alone; human decisions never overridden.
- Non-goals: finding every relationship; real-time detection; ranking claims by a score.
- Anti-goals (a failure even if it "works"): a claim built from filename, duration, size or visual similarity; a
  detector that confirms, rejects, revives or deletes a decided claim; unbounded fan-out (one asset linked to hundreds
  of others); work that grows quadratically with library size; an upstream change slipped in as a convenience.

## 2. Verified facts the design rests on

Library [V]:

- `MediaLibrary.search` orders by `(<sort column>, id)`; `created_from` is inclusive (`>=`), `created_to` exclusive
  (`<`); timestamps are stored as fixed-width UTC microsecond text, so text order equals time order; `page_size` is at
  most 200; paging is by offset; `Page.total` comes from a separate `COUNT` statement; defaults hide archived assets
  (`statuses={ACTIVE}`); `AssetStatus` has only `ACTIVE` and `ARCHIVED`; there is no `updated_from` filter and no filter
  on derivation operation or on capture time.
- `MediaAssetDto` carries `created_at`, `updated_at`, `role`, `status`, `media_type` and `derivation`
  (`source_asset_id`, `operation`, `version`, `config`, `fingerprint`). A fingerprint is computed from the source
  checksum, the operation, the version and the configuration, so it identifies *which inspection of which bytes*.

Media Inspection [V]:

- `InspectionCatalog.find(asset_id, config=None)` returns the stored `InspectionResult` or `None`; it never probes and
  "never fails" (so `None` can mean "never inspected" or "stored document unreadable"; the two cannot be told apart).
- An inspection is stored as a derived JSON document of its source, operation `media_inspection`; `InspectionResult`
  exposes the document asset (`asset.id`, `asset.derivation.fingerprint`) and `MediaInspection`.
- Inspection is possible only for `AUDIO` and `VIDEO` assets.
- Facts a detector can read: `observed.container.start_time` and `.duration` (floats, may be `None`),
  `observed.production.creation_time` (`datetime | None`), `.device`, `.camera_make`, `.camera_model`, `.reel_id`,
  `.scene`, `.take`, and `observed.timecode` (`TimecodeInfo`).
- `media_inspection.application.contracts` exports `MediaInspection`, `ContainerInfo`, `Timecode`, `StreamTiming` and
  others, but **not** `ProductionMetadata`, `TimecodeInfo` or `ObservedMedia`. Those are reachable only as attribute
  types, never importable by name from outside the module.

This module [V]:

- `ProposeRelationship` is idempotent; undecided refresh replaces evidence of the same providers and moves `STALE` back
  to `PROPOSED`; decided refresh keeps status, intervals and offset, supersedes differing evidence and sets
  `outdated_since`; `mark_stale` applies only to `PROPOSED`, `mark_outdated` only to decided claims.
- Admissible evidence: `temporally_overlaps_with` accepts `WALL_CLOCK_OVERLAP` (needs `clock_trust`),
  `TIMECODE_OVERLAP`, `USER_ASSERTION`; `alternate_take_of` accepts `PRODUCTION_TAG_MATCH` (needs `scene_a`, `scene_b`,
  `take_a`, `take_b`; optional boolean `same_device`, `same_reel`) and `USER_ASSERTION`. Extra flat JSON keys in
  `detail` are allowed.
- Grades come only from `assess(type, evidence)`; wall-clock overlap with `clock_trust` other than `unverified` is
  `INDICATED`, otherwise `SUGGESTIVE`; scene equal plus take different is `INDICATED`, `CORROBORATED` with the same
  device or reel.
- `BasisCheck.is_current(basis)` is called for **every** claim of a sweep once at least one check is wired, outside
  any transaction, and again inside the claim's own transaction when it answers "out of date". It must be quick and
  must not read this module's store.
- SQLite: WAL, `BEGIN IMMEDIATE` for writes, busy timeout, `PRAGMA user_version` migrations, current version 2;
  `scan_state(key, value)` exists and is generic.

Not yet verified [U]: see section 12.

## 3. Upstream dependencies

### 3.1 Contracts reused, no change [P]

| Dependency | Used for | Contract file | Change |
| --- | --- | --- | --- |
| `MediaLibrary.search`, `get_many` | the feed and the full sweep, existence checks | `media_library.application.contracts` | none |
| `InspectionCatalog.find` | the only source of inspection facts | `media_inspection.application.contracts` | none; **first import of `media_inspection` by this module** (decision D13) |
| `Clock`, `JobScheduler`, `JobContext` | time, cooperative cancellation, progress | `core.application.ports`, `shared.concurrency` | none |
| `ProposeRelationship` | every proposal (own module) | - | none |
| `BasisCheck` port | version invalidation (own module) | - | none |

Inspection values are read as attributes of `MediaInspection` inside one application mapper that copies them into this
module's own value object (`InspectionFacts`); the domain never sees a media-inspection type. Accessing attributes whose
class is not exported is allowed by the architecture rule (only importing by name is restricted) [U: confirm
`mypy --strict` accepts it without a cast].

### 3.2 Contract changes considered and NOT proposed (each would need separate approval)

| Candidate | What it would buy | Without it |
| --- | --- | --- |
| `MediaQuery.updated_from` (item C1) | a true "edited since" feed | edits are only caught by the full sweep |
| export `ProductionMetadata`, `TimecodeInfo` from `media_inspection` contracts | typed names in the mapper | attribute access through `MediaInspection`; no loss of function |
| a library filter on derivation operation | a feed of new *inspections* only | such a feed scans every new derived asset and filters client-side (decision D4) |
| `find_by_checksum(..., roles=...)` (item C2) | complete reverse duplicate discovery | unchanged from Slice 2; unrelated to this slice |
| embedding view in `video_intelligence` (item C5) | similarity detectors | no similarity detector in Slice 4 |

**Slice 4 needs no upstream contract change [P].** If any item above is later wanted it comes back as its own proposal.

### 3.3 `MediaRelations` is untouched

`media_inspection.MediaRelations` guesses from duration, size, container and codec; none of that is admissible evidence
here (main design section 4). Slice 4 neither calls nor changes it.

## 4. R2 - the change feed and the full sweep

This refines section 8 of the Slice 3 design. The mechanism is the same; the details below are what Slice 4 builds.

### 4.1 What is walked

Eligible assets: `roles={ORIGINAL}`, `media_types={VIDEO, AUDIO}`, `statuses={ACTIVE}` (decision D5), because only
those can have an inspection and the library hides archived assets by default. Documents, proxies and other derived
assets are never analyzed.

Two walks use the same keyset:

- **Feed walk** (incremental): starts at the stored `feed_cursor`, visits assets created after it, advances it.
- **Full sweep** (complete): starts at the minimum, visits every eligible asset, at most `max_assets_per_job` per job,
  at least `min_full_sweep_interval` apart; its cursor, generation and timestamps are stored separately.

A job does the feed walk first, then continues the full sweep if one is due or in progress, within one shared
`max_assets_per_job` budget. An asset visited by both is handled by both (the second visit finds an unchanged mark and
does almost nothing, section 5.4).

### 4.2 The cursor

`feed_cursor = "<created_at as fixed-width UTC microseconds>|<asset id>"`, committed after every batch with a
compare-and-set (`expected -> new`), exactly like the Slice 3 claim cursor. A value, not a row reference: deleting the
row it names changes nothing.

One batch is a fresh query, never an offset carried from the previous batch:

1. `MediaQuery(created_from=cursor.created_at, roles={ORIGINAL}, media_types={VIDEO, AUDIO}, statuses={ACTIVE},
   sort_by=CREATED_AT, sort_order=ASC, page=1, page_size=feed_batch_assets)`.
2. Drop leading rows with `(created_at, id) <= cursor`. Because `created_from` is inclusive and the library breaks ties
   by id [V], what remains is exactly the next rows, **provided the tie group at the cursor's timestamp fits in the
   page**.
3. If the page contained only rows at or before the cursor (the tie group at that microsecond is larger than a page),
   request `page=2, 3, ...` until a row beyond the cursor appears, at most `max_tie_pages` pages (default 10, so 2 000
   assets sharing one microsecond). Beyond that the job ends `PARTIAL(tie_group_too_large)` and logs the timestamp.

### 4.3 What the feed does and does not guarantee

| Situation | Behaviour |
| --- | --- |
| An asset is created after the cursor | seen by the next feed walk |
| Rows are deleted behind the cursor | no effect |
| The row the cursor names is deleted | no effect (the cursor is a value) |
| A row ahead of the cursor is deleted | simply not seen |
| An insert commits late and carries a `created_at` older than the cursor (or equal with a smaller id) | **missed by the feed**; found by the next full sweep |
| A tie group larger than one page | resolved by offset paging inside the group (step 3), which can shift if rows in that same group change meanwhile: a row may be skipped (found by the next full sweep) or visited twice (harmless: the work is idempotent) |
| An archived asset is restored | not a new row, so **not seen by the feed**; found by the next full sweep (decision D5) |
| An asset's metadata or inspection changes later | not seen by the feed (no `updated_from`, section 3.2); found by the next full sweep |
| `Page.total` changes between two batches | not used by the feed at all |
| `created_at` of an existing asset changes | treated as a new position; at worst a visit is repeated or skipped until the next sweep [U: no library test pins that `created_at` never changes] |

The feed is an **optimisation for latency, not a source of truth**. Correctness is the full sweep plus idempotent
processing.

### 4.4 The full sweep: eventual recovery without a snapshot

The sweep walks the same keyset from the minimum to the end. Its stated guarantee, and nothing stronger:

> An eligible asset that exists, stays active and keeps its `(created_at, id)` for the whole duration of one complete
> sweep is visited at least once in that sweep, unless it sits in a tie group larger than `max_tie_pages` pages. An
> asset the feed missed is therefore processed within one `min_full_sweep_interval` plus one sweep duration of the
> moment a caller starts running jobs.

It does **not** guarantee a snapshot: assets created or deleted during the sweep may or may not be visited, and the
sweep never compares `Page.total`. There is no timer, so "within" assumes the caller keeps invoking jobs (section 8).

A sweep stamps every mark it visits with the current `sweep generation` (section 5.5). When a sweep completes, marks
and facts that were not stamped are removed (the asset is gone, archived, or no longer eligible). That removal is
guarded the same way as Slice 3's purge: if it would remove at least `suspicious_min_marks` rows and at least
`suspicious_ratio` of all marks, nothing is removed, the outcome says `gc_skipped_suspicious`, and the next sweep tries
again (reuse of `looks_emptied`).

### 4.5 One feed lane, not two (decision D4)

A second lane over new *inspection documents* (so that an asset whose inspection appeared after the asset itself is
picked up quickly) would have to scan every new derived asset of every operation and filter client-side, because the
library cannot filter by operation. Slice 4 does not build it. An asset created before its inspection exists is marked
`unavailable` and revisited (a) at the start of every job, for marks older than `unavailable_retry_after` (default 6 h),
and (b) by the full sweep.

## 5. `analysis_marks` and `observed_facts`

### 5.1 Purpose

`analysis_marks` answers three questions without recomputing:

1. "Was this asset analysed by this detector, on which version and which inputs?" - so "found nothing" differs from
   "not yet analysed" (the main design's reason for the table).
2. "Did anything change since?" - so an unchanged asset costs one comparison instead of an analysis (the
   caching/fingerprinting rule of `CLAUDE.md`).
3. "Which assets could not be analysed and should be retried?"

`observed_facts` stores the handful of values copied from an inspection that are needed to find *other* assets that
match: without it, finding the candidates for one asset means reading every other asset's inspection (quadratic work).
It is an index, not a second copy of the inspection.

### 5.2 Authority: what is derived and what is human-authored

| Thing | Authored by | Authoritative? | Safe to delete? |
| --- | --- | --- | --- |
| `relationships`, `relationship_evidence` (claims) | a detector (`PROPOSED`) or a person (`USER` origin) | the claim and its evidence, yes | no (only reconciliation purges, with audit) |
| `decision_history`, `purge_log` | a person's decisions; reconciliation | yes | no |
| `analysis_marks` | **derived** by the detection job only | **no**: a cache of "what was analysed" | **yes, entirely**: the cost is re-analysis; no claim, decision or evidence changes |
| `observed_facts` | **derived** by the detection job only | **no**: a copy of inspection values | **yes, entirely**: rebuilt by the next sweep |
| `scan_state` | the jobs | cursors and timestamps only | yes (a lost cursor means a full re-walk) |

No person writes to marks or facts. No code path reads a mark to decide a claim's status. A mark never stores a claim id
or a decision. Losing either table can cost time; it can never lose or alter a human decision. This is tested by
deleting both tables in a populated database and checking that decided claims are byte-identical afterwards.

### 5.3 Schema (migration 3, section 6)

```
analysis_marks(
  asset_id           TEXT    NOT NULL,
  detector           TEXT    NOT NULL,
  detector_version   TEXT    NOT NULL,
  input_identity     TEXT    NOT NULL,   -- canonical text of the inputs read (section 5.4); '' for 'unavailable'
  outcome            TEXT    NOT NULL CHECK (outcome IN ('found', 'none', 'unavailable', 'capped')),
  candidates_found   INTEGER NOT NULL DEFAULT 0 CHECK (candidates_found >= 0),
  analyzed_at        TEXT    NOT NULL,
  seen_generation    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (asset_id, detector)
) WITHOUT ROWID

observed_facts(
  asset_id   TEXT    NOT NULL,
  detector   TEXT    NOT NULL,
  kind       TEXT    NOT NULL CHECK (kind IN ('wall_span', 'production_tags')),
  group_key  TEXT    NOT NULL DEFAULT '',  -- normalised scene for production_tags; '' for wall_span
  start_us   INTEGER,                      -- wall_span: UTC microseconds since the epoch, half-open [start, end)
  end_us     INTEGER,                      -- NULL for production_tags
  detail     TEXT    NOT NULL,             -- flat JSON: device, camera, reel, take, raw tag text, ...
  PRIMARY KEY (asset_id, detector, kind),
  CHECK ((start_us IS NULL) = (end_us IS NULL) AND (start_us IS NULL OR start_us < end_us))
) WITHOUT ROWID
-- indexes: (kind, start_us) and (kind, group_key), for candidate lookup
```

`outcome` adds `capped` to the main design's three values (decision D3): candidates beyond
`max_candidates_per_asset` were found but not all proposed (section 8.2). No foreign key to `relationships` or to
anything outside this file (the same rule as every table so far).

### 5.4 Key, input identity and when a mark is "unchanged"

Key: `(asset_id, detector)`; one current row, no history.

`input_identity` is the canonical text of what the detector read: the **inspection document's asset id and derivation
fingerprint** (which already covers the source bytes, the inspection version, its configuration and the probe
identity [V]). A mark is *unchanged* when `detector_version` and `input_identity` equal the current ones and the
outcome is not `unavailable`.

### 5.5 Lifecycle

- **Creation**: the first time an enabled detector analyses an asset.
- **Update** (an upsert, one transaction, written **last**): when the detector version or the input identity changed;
  when an `unavailable` mark is retried; when a re-analysis changes `outcome` or `candidates_found`. Writing the mark
  last makes it the commit record: a crash before it leaves the old (or no) mark, so the asset is simply analysed
  again.
- **No-op visit**: an unchanged mark only gets `seen_generation` stamped during a full sweep.
- **Deletion**: only (a) at the end of a complete full sweep for rows not stamped (section 4.4, guarded);
  (b) never by a person, by a decision, or by claim reconciliation. A claim's purge does not touch marks (they are
  independent, and the asset's own disappearance removes its marks at the next sweep end).
- **Retention**: none beyond "current"; the oldest a mark of a vanished asset can be is one sweep interval plus one
  sweep. Marks hold no history.
- **Detector retirement**: a mark whose `detector` is not registered is ignored and kept (a later build may register it
  again); removal for retired detectors is not automatic (open question Q-7).
- **Relationship to claims**: claims name their detector and input identities in `basis`; marks never name a claim.
  The two meet only inside the detection job (section 7.4).
- **Relationship to detector results**: a detector's *result* is claims with evidence; a mark records that the result
  was computed, and `observed_facts` record the inputs that made candidates findable.
- **Concurrency**: one asset's analysis is idempotent, so two jobs analysing the same asset write the same rows;
  upserts run in `BEGIN IMMEDIATE`; the CAS cursor makes the second job's batch `SUPERSEDED` rather than duplicated
  (section 8.4). Facts are replaced and the mark upserted in separate short transactions; the order below makes a pair
  discoverable even when both ends are analysed at the same instant.

**Order of one asset's analysis (the unit of work):**

1. Look the inspection up (`find`), outside any transaction.
2. `None` -> upsert mark `unavailable`; keep the old facts; touch no claim; stop. (Never `none`: absence of an
   inspection is not absence of relationships.)
3. Extract facts and `input_identity` (pure).
4. Mark unchanged -> stamp generation if sweeping; stop. (Pairs with assets analysed later are found when *those* run:
   the relation is symmetric and the later asset sees this one's facts.)
5. Transaction 1: replace this asset's facts. **Facts are committed before candidates are searched**, so whichever of
   two assets is analysed second finds the other, even if both run concurrently (tested with real processes).
6. Find candidates, apply the pairing rule, order deterministically, cap, and propose each through
   `ProposeRelationship` (each its own idempotent transaction).
7. Transaction 2: for undecided claims of this detector that involve this asset and were *not* re-proposed, and whose
   recorded input identity for this asset differs from the new one, mark them `STALE`; for decided ones set
   `outdated_since` (section 7.4). Then upsert the mark with the outcome (`found` / `none` / `capped`).

## 6. Migration 3 (user_version 2 -> 3)

Append-only and transactional, in `infrastructure/migrations.py`, same runner as migrations 1 and 2 [P]:

- Creates `analysis_marks`, `observed_facts` and their indexes (section 5.3). Nothing else.
- **Existing tables are untouched**: `relationships`, `relationship_evidence`, `decision_history`, `scan_state`
  (reused with new keys, no schema change) and `purge_log`. All rows survive; the migration is DDL only and does not
  scale with data.
- **New `scan_state` keys** (reconciliation's keys are unchanged and independent): `feed_cursor`, `sweep_cursor`,
  `sweep_generation`, `sweep_started_at`, `last_sweep_completed_at`, `detect_consecutive_failures`,
  `detect_next_attempt_after`, `detect_last_run`.
- **Compatibility**: code at version 3 opens version-1 and version-2 databases and upgrades them. Code at version 2
  **refuses** a version-3 database (the existing "newer than supported" rule); there is no down-migration, per the
  append-only convention.
- **Rollback considerations**: a rollback means either restoring a backup of `asset_relationships.db` taken before the
  first start of the new build, or running the old build on a copy. Nothing else references the two new tables, so
  dropping them by hand and setting `user_version` back to 2 is possible, but is an emergency procedure written in the
  README, not shipped code. Because both tables are derived, restoring a backup that predates them loses no human data
  made *before* the backup; decisions made after the backup would be lost, which is the usual backup trade-off and is
  stated in the README.
- **Concurrent initialisation**: several processes opening a version-2 file at once apply migration 3 exactly once
  (the version is re-read inside `BEGIN IMMEDIATE`), the same mechanism and test as the v1 -> v2 step.
- **Feature flag independence**: the schema is created whether or not any detector is enabled.

## 7. Detector contracts

### 7.1 Shape [P]

Pure domain code; no I/O, no clock, no randomness, no library or inspection types.

```
InspectionFacts      value object built by an application mapper from MediaInspection:
                     asset_id, inspection_asset_id, inspection_fingerprint,
                     duration_seconds | None, start_time_seconds | None,
                     creation_time (aware datetime | None), device, camera_make, camera_model,
                     reel_id, scene, take  (each str | None, blank treated as None)

Detector (Protocol)  id: str; version: str
                     facts(subject: InspectionFacts) -> tuple[ObservedFact, ...]
                     input_identity(subject: InspectionFacts) -> str
                     match(subject: Candidate, other: Candidate) -> Detection | None

Detection            relationship_type, asset ids, evidence (kind, detail, offset), optional intervals;
                     NO score, NO probability, NO grade field
```

The use case turns a `Detection` into the existing `ProposeRelationshipCommand` with `origin=DETECTOR`,
`basis=Basis(detector.id, detector.version, inputs={asset_a: identity_a, asset_b: identity_b})`, and evidence whose
`provider` is the detector id and `provider_version` its version. No new proposal path exists.

### 7.2 Evidence provenance

Every evidence item records the detector and its version (as `provider` / `provider_version`), the observation time
(taken from the clock when proposing), the inspection documents it came from (`inspection_a`, `inspection_b`: the
document asset ids, in `detail`), and the measured values that justify the claim (overlap bounds in microseconds, the
margin used, the raw tag text). A reader can answer "why linked" from the claim alone, as Slice 1 requires.

### 7.3 Grade and confidence semantics

- Detectors **never set a grade**. The grade is `assess(type, evidence)` (a pure function of evidence, Slice 1) and is
  ordinal, not a probability, never averaged or weighted.
- There is no numeric confidence anywhere in a detection, in a mark or in a claim.
- Contradiction is detected by `assess` from the evidence kinds it understands; a detector does not decide it.
- A grade never promotes a claim to trusted lineage; only a person's `CONFIRMED` does.

### 7.4 Idempotency and interaction with human decisions

Same inputs and versions give the same `Detection` set in the same order, so repeating a job writes nothing (claim rows
and `updated_at` unchanged). Proposals reuse `ProposeRelationship`, so the interaction with decisions is exactly the
Slice 1 refresh rule, restated:

| Stored claim | A detector proposes the same pair and type | Result |
| --- | --- | --- |
| none | yes | new claim, `PROPOSED`, graded from its evidence |
| `PROPOSED`, same evidence | yes | nothing changes |
| `PROPOSED`, different evidence | yes | evidence of this provider replaced, grade recomputed, still `PROPOSED` |
| `STALE` | yes (re-detected) | back to `PROPOSED` |
| `CONFIRMED` | yes | status never changes; differing evidence is appended (the old kept as superseded), `outdated_since` set so a person can look again |
| `REJECTED` | yes | **not revived**, status unchanged; differing evidence appended and `outdated_since` set; only a person can reopen it |
| any | no longer proposed (pair no longer matches) | undecided: `STALE` **only if** the recorded input identity of an endpoint differs from the current one (otherwise it still holds); decided: `outdated_since` set; **nothing is deleted** by detection |
| any | detector disabled or retired | untouched |

Decided claims are never created, changed or deleted by detection beyond the `outdated_since` flag. A detector cannot
call `DecideRelationship`; no code path from detection reaches it (architecture test, section 9).

**The real `BasisCheck`** (`DetectorVersions`): in-memory, O(1), no store access. For a registered detector id it
returns whether `basis.version` equals the current version; for any other id (including `manual`) it returns `None`.
It knows every *registered* detector whether or not it is enabled, so a version bump invalidates old claims either way.
Input-identity staleness is **not** a `BasisCheck` concern (the check may not read the store); it is applied by the
detection job in step 7 of section 5.5.

One existing behaviour to note, not change (question Q-4): a decided claim's `outdated_since` is set whenever the new
basis differs, even if the evidence is identical (for example after a re-inspection with a new tool version).

### 7.5 The two detectors

**`wall_clock_overlap` v1 -> `temporally_overlaps_with`, evidence `WALL_CLOCK_OVERLAP`.**

- Fact `wall_span`: only when `creation_time` is timezone-aware **and** `duration` is known and positive; the span is
  `[creation_time, creation_time + duration)` in UTC microseconds. Naive timestamps are never used (Slice 1 rule); no
  timezone is invented.
- Pairing rule: the two spans' intersection must be **strictly longer than the combined timestamp uncertainty**
  (`2 * timestamp_margin`, default margin 1 s because container creation times are commonly whole seconds). This is
  what keeps back-to-back clips of one continuous recording, which touch or differ by a rounding, from being reported
  as overlapping.
- `clock_trust`: `same_device` only when both assets have an equal, non-blank `device`; otherwise `unverified`. By
  the Slice 1 grading rule that yields `INDICATED` or `SUGGESTIVE` respectively. Different cameras are therefore
  `SUGGESTIVE` unless a person says otherwise.
- The implied offset (difference of the two creation times, origin `WALL_CLOCK_DIFFERENCE`, uncertainty
  `2 * timestamp_margin`) and both source intervals are attached, so the claim carries where on each clip the overlap
  lies. The sign follows `ClockOffset` (`target = source + seconds`) and is pinned by a test.
- Known weakness [U]: whether `creation_time` is the start or the end of the recording varies by camera and software.
  The detector assumes **start**, says so in the evidence (`creation_time_semantics: "assumed_start"`) and stays
  `SUGGESTIVE` across devices for that reason.

**`production_tag_takes` v1 -> `alternate_take_of`, evidence `PRODUCTION_TAG_MATCH`.**

- Fact `production_tags`: only when `scene` and `take` are both non-blank. `group_key` is the scene normalised exactly
  as `assess` normalises it (trimmed, case-folded). Raw tag text is kept in `detail`.
- Pairing rule: same `group_key`, **different** normalised `take`, and (decision D6, recommended) the **same non-blank
  `device` or the same non-blank `reel_id`**. Equal takes are not an alternate take and produce nothing.
- Evidence: `scene_a`, `scene_b`, `take_a`, `take_b` (raw), `same_device`, `same_reel`. Grade is then `INDICATED` or
  `CORROBORATED` by Slice 1's rule. If D6 is declined (scene and take alone), the same evidence is `INDICATED` and the
  false-positive risk from generic scene labels such as "1" across unrelated shoots goes up.
- Consumer footage rarely carries these tags; for most libraries this detector will propose nothing.

**Timecode is not a detector in Slice 4.** Timecode overlap alone is `SUGGESTIVE` by the Slice 1 rule, and many
cameras start timecode at `00:00:00:00` on every clip, which would make every clip overlap every other clip. A timecode
detector would need a source constraint (same reel or a jam-sync indicator) that the library does not carry today
(decision D7).

## 8. Scheduling and failure handling

### 8.1 Use case, contract and trigger

`DetectRelationships.execute(command, ctx) -> DetectOutcome` runs under `JobScheduler`/`JobContext`, blocking and
cooperative, like `ReconcileRelationships`. **No scheduler, timer or queue is created.** It is exposed through a
`RelationshipDetection` protocol in this module's `application/contracts.py` (`detect(command, ctx)`, `last_run()`);
that is the only public addition and it is this module's own contract. **Caller-driven only** (decision D11, the same
choice as Slice 3): nothing calls it automatically, and `bootstrap` gains nothing. The intended call order for a caller
is reconcile, then detect, then query.

### 8.2 Settings (`DetectionSettings`, frozen; all provisional and unmeasured)

| Setting | Default | Rationale |
| --- | --- | --- |
| `enabled_detectors` | empty | nothing runs until approved after the evaluation (D10) |
| `feed_batch_assets` | 100 | one `search` page; the unit of cursor commit |
| `max_assets_per_job` | 1 000 | bounds one worker's time; the rest continues in the next job |
| `min_full_sweep_interval` | 24 h | recovery latency for what the feed misses |
| `max_tie_pages` | 10 | section 4.2 |
| `max_candidates_per_asset` | 25 | bounds fan-out (anti-goal); beyond it the outcome is `capped` |
| `timestamp_margin` | 1 s | section 7.5 |
| `unavailable_retry_after` | 6 h | section 4.5 |
| `retry_delays_seconds`, `failure_backoff_base`, `failure_backoff_cap` | 1/4/16 s; 30 min; 24 h | same values as reconciliation |
| `suspicious_min_marks`, `suspicious_ratio` | 20; 0.5 | guard on the end-of-sweep removal only |

Candidate ordering, so a cap is deterministic: longest overlap first, then asset id (overlap detector); then asset id
(take detector). A capped asset proposes its first `max_candidates_per_asset`, the outcome is `capped`, and the count
of dropped candidates is logged; the others are *not* silently pretended not to exist.

### 8.3 Bounds, cancellation, retries, checkpoints

- **Bounds**: assets per job, candidates per asset, and one library page (at most 200) per query bound time and memory.
  Candidate lookup is one indexed SQL query per detector with `LIMIT max_candidates_per_asset + 1`.
- **Cancellation**: `ctx.raise_if_cancelled()` between assets (a unit is short). On cancellation the job records
  `INTERRUPTED` best effort, then lets `OperationCancelledError` propagate, as reconciliation does.
- **Retries**: a failing `search` or `get_many` is retried with the configured delays; if it still fails the batch
  writes nothing, the cursor does not move, the job ends `PARTIAL(transient_library)` and a failure backoff is stored.
- **Checkpoints**: the feed and sweep cursors move per batch, after every asset in the batch has finished, by CAS.
  Each asset's work is atomic in the sense of section 5.5 and idempotent.
- **`find` returning `None`** is `unavailable`, never "none" and never a reason to touch a claim (section 5.5).
- **Unexpected exception**: `FAILED` recorded, then propagated.
- Outcomes: `COMPLETED`, `PARTIAL(budget | transient_library | tie_group_too_large)`, `INTERRUPTED`, `FAILED`,
  `NOT_DUE` (interval or backoff), `SUPERSEDED`; counters: assets visited, analysed, unchanged, unavailable, claims
  proposed (new), refreshed, made stale, flagged outdated, capped, marks and facts removed, `gc_skipped_suspicious`.

### 8.4 Recovery

- A crash between assets or batches loses at most the batch in flight; the next job resumes from the committed cursor.
  A crash inside one asset's unit is safe because facts are replaced idempotently, proposals are idempotent, and the
  mark is written last (so the asset is analysed again).
- Rival jobs: the CAS cursor makes the loser `SUPERSEDED`; if two jobs do analyse the same asset, they write identical
  rows.
- Lost or corrupt cursor: the feed re-walks from the start and the sweep restarts; the cost is time, not correctness.
- A deleted marks or facts table's contents: next sweep rebuilds them; claims and decisions are unaffected.

## 9. Testing and acceptance criteria

Tests mirror the layout, use fakes for the library and for `InspectionCatalog`, and real processes for cross-process
claims. Nothing here weakens a Slice 1-3 test.

### 9.1 Unit (domain and application)

- Fact extraction: naive timezone, missing or zero duration, negative start, blank tags, blank device, whitespace and
  case in scene and take.
- Overlap rule: adjacency within the margin is not an overlap; overlap just above `2 * margin` is; containment;
  identical spans; different devices (`unverified`) versus equal devices (`same_device`); offset sign pinned.
- Take rule: equal scene and different take with and without device or reel; equal takes produce nothing; case-fold.
- Determinism: a property test that the same inputs give the same `Detection` set in the same order, and that
  candidate order under the cap is stable.
- Idempotency: running the job twice leaves every claim row and `updated_at` unchanged.
- `DetectorVersions`: current, old, unknown, `manual`.
- `DetectionSettings` validation.

### 9.2 Marks and facts (store contract, over the in-memory fake and SQLite)

Upsert, unchanged detection by version and identity, facts replace, candidate queries (overlap and group), capped
queries, generation stamping, end-of-sweep removal, the suspicious-removal guard, and the "delete both tables, decided
claims are unchanged" test from section 5.2.

### 9.3 Pagination and the feed

- Keyset property test with timestamp ties placed at page boundaries (every asset visited exactly once, in order).
- Tie group larger than a page: offset path, `max_tie_pages` exceeded gives `PARTIAL(tie_group_too_large)`.
- Fake library with hooks that insert, delete, archive and restore between batches: insert at the end is seen; a
  delete behind the cursor and a delete of the cursor row change nothing; a **late insert with an older timestamp is
  missed by the feed and found by the next full sweep** (characterization test); an archived-then-restored asset is
  found by the sweep only.
- A characterization test that an unchanged `Page.total` is not a snapshot (as in Slice 3), to keep the "no snapshot"
  statement honest.

### 9.4 Failure and recovery

Library failure with retries and backoff, cursor unmoved; `find` returning `None` records `unavailable` and touches no
claim; cancellation records `INTERRUPTED` and raises; unexpected exception records `FAILED`; a crash injected between
assets, after facts and before the mark, and after the mark and before the cursor, each resuming to the same end state
as an uninterrupted run.

### 9.5 Concurrency (real processes, as in Slice 3)

Kill between batches and in the middle of an asset; repeated kills; rival jobs; **two processes analysing the two ends
of one matching pair at the same time produce exactly one claim**; concurrent first use applies migration 3 once.

### 9.6 Migration, lifecycle, real library

- Migration: v2 database with claims, decisions, evidence and reconciliation state upgrades with every row and cursor
  intact; a version-3 file is refused by a version-2 runner; v1 upgrades straight to 3; concurrent first use.
- Lifecycle: the application resolves `RelationshipDetection`; the module name set in
  `tests/integration/test_application_lifecycle.py` is unchanged (no new module); `BasisCheck` is wired.
- Real library integration: real `MediaLibrary` with real inspection documents (using the existing integration
  fixtures for media inspection) driving the full path to a stored `PROPOSED` claim and its query result; decided
  claims untouched; a rejected claim not revived.
- Version invalidation end to end: bump a detector version, run reconciliation, undecided claims `STALE`, decided
  claims `outdated_since`, `manual` untouched, re-detection returns `STALE` to `PROPOSED`.

### 9.7 Evaluation gate (before D10, outside CI)

A labelled fixture set: synthetic cases (adjacent, overlapping, nested, naive timestamps, same device and different
devices, generic scene labels, equal takes) and, **if you provide it**, a sample of real footage with your own ground
truth. The harness reports, per detector, proposals, false positives and misses against the labels. No default
threshold or enablement is chosen from a number that was not measured on such a set (main design, Phase 4).

### 9.8 Architecture

Extend `tests/architecture/test_asset_relationships_boundaries.py`: the module imports `media_inspection` only through
`application.contracts` and only `InspectionCatalog` and `MediaInspection`; domain imports no `media_library`,
`media_inspection` or infrastructure; detectors import neither library nor inspection types; the detection use case
never imports `DecideRelationship`, the lineage projection or the proxy catalog; reconciliation never imports
detection; no scheduler class; marks and facts are never imported by the claim aggregate. Then
`uv run pytest tests/architecture`.

### 9.9 Acceptance criteria (all must hold to call Slice 4 built)

1. No change in `media_library`, `media_inspection`, `proxy_manager`, `video_intelligence`, workspace, presentation,
   `shared` or `core`; `git diff --stat` shows only this module, its tests, the lifecycle-test resolution, one
   `AI_CONTEXT.md` paragraph and the docs (plus the `bootstrap` line only if D11 is changed).
2. Migration 3 preserves every existing row and refuses newer versions.
3. Detection never changes the status of a decided claim and never deletes a claim.
4. Detectors are disabled by default and the default test configuration proves nothing runs.
5. A repeated job writes nothing; a killed job resumes to the same end state.
6. `ruff check`, `ruff format --check` and `mypy --strict` clean on explicit paths; unit and architecture suites pass;
   the full `tests/integration` suite is run to completion on the final tree and its real result reported.
7. The presentation suite is reported as it is (it aborts for a pre-existing, unresolved reason) and is never described
   as passing.

## 10. Risks

| Risk | Mitigation or note |
| --- | --- |
| `creation_time` semantics differ by camera (start versus end), and clocks are often wrong | stays `SUGGESTIVE` across devices; evidence says "assumed start"; D8 |
| Back-to-back clips look like overlaps | the `2 * margin` rule; tested |
| Fan-out in multi-camera shoots | `max_candidates_per_asset`, `capped`, deterministic order |
| `observed_facts` is a second copy of inspection values | derived, rebuildable, never authoritative (section 5.2); D2 offers the alternative |
| Detectors disabled by default look like dead code | they are tested and ship disabled on purpose (D10) |
| The feed misses late commits and edits | by design; the full sweep recovers eventually (section 4.4) |
| `find` returning `None` conflates "not inspected" and "unreadable" | recorded as `unavailable`, retried, never changes claims |
| Range query cost on a large `observed_facts` | indexed; `LIMIT`; measured before any default is called final [U] |
| A real `BasisCheck` makes every reconciliation claim call it | O(1) in memory; pinned by a test |
| Whole-sweep removal on a bad library read | the suspicious guard (reuse of `looks_emptied`) |
| Unmeasured defaults | all provisional; no real library is touched before D10 |
| Presentation Qt crash remains | unrelated; not fixed here |

## 11. Limitations that will remain after Slice 4

Pairs are found only between assets that both have an inspection with usable facts; most consumer footage has no
production tags; wall-clock overlap across cameras is only suggestive; no timecode, similarity, audio or multicam
detection; the feed can miss late commits, edits and restores until the next full sweep; there is no automatic trigger,
so "eventually" assumes a caller that keeps running jobs; `serving=true` still does not mean a proxy is usable; reverse
duplicate discovery is still partial; the cross-database purge race is still documented rather than eliminated; the
empty-string `unavailable` identity cannot say why an inspection was missing.

## 12. Decisions requiring approval, open questions, unverified items

### Decisions requiring your approval

| # | Decision | Recommendation |
| --- | --- | --- |
| D1 | Scope of Slice 4: feed, marks and **both** detectors, versus feed and marks only | feed, marks and both detectors, shipped disabled |
| D2 | A derived `observed_facts` index table, versus scanning other assets' inspections per asset (quadratic) | the index table |
| D3 | `analysis_marks.outcome` gains `capped` beyond the main design's three values | yes |
| D4 | One feed lane (new originals) plus age-based retry of `unavailable`; no second lane over new inspection documents | one lane |
| D5 | Sweeps and removal consider `ACTIVE` assets only; archived assets lose their marks and facts at sweep end and are re-analysed after restore by the next full sweep | `ACTIVE` only |
| D6 | `production_tag_takes` proposes only when the device or reel also matches | require device or reel (more precise, less recall) |
| D7 | No timecode detector in Slice 4 | exclude |
| D8 | `timestamp_margin` 1 s, overlap must exceed twice the margin, `same_device` only for equal non-blank `device`, `creation_time` assumed to be the start | as stated |
| D9 | `max_candidates_per_asset` 25 with the `capped` outcome | as stated |
| D10 | Detectors ship disabled; enabling needs a later explicit approval after the section 9.7 evaluation | yes |
| D11 | Caller-driven only; no timer, no startup hook, no `bootstrap` change | yes |
| D12 | Migration 3 as in section 6, rollback by backup restore, no down-migration | yes |
| D13 | First import of `media_inspection.application.contracts` (`InspectionCatalog`, `MediaInspection`) by this module | yes |
| D14 | No upstream contract change in Slice 4; the items in section 3.2 stay unapproved | yes |
| D15 | Every default in section 8.2 is provisional and unmeasured | accepted as provisional |

### Open questions (not silently resolved)

- Q-1: do you have real footage with your own ground truth for the evaluation gate, or should the gate use synthetic
  fixtures only (which proves the rules, not their real-world precision)?
- Q-2: is a candidate cap of 25 acceptable for multi-camera shoots, or should a capped asset instead propose nothing
  and be flagged for a person?
- Q-3: should an `unavailable` asset that stays unavailable across several sweeps be surfaced anywhere (a finding in a
  query, a log only)?
- Q-4: should a decided claim's `outdated_since` be set when only the *input identity* changed and the evidence is
  identical (today it is; changing it touches Slice 1 behaviour)?
- Q-5: should `DetectionSettings` share the retry fields of `ReconciliationSettings` (a small refactor of accepted
  Slice 3 code) or duplicate them (a few lines, no change to Slice 3)? This document assumes duplication.
- Q-6: is 24 h the right default sweep interval, given there is no timer and the caller decides when jobs run?
- Q-7: should marks of a retired detector be removed automatically, or only by an explicit maintenance action?
- Q-8: should reconciliation and detection be invoked together by one caller-facing helper, or stay separate
  contracts (this document assumes separate)?

### Unverified [U] (to be settled before or during the slice)

1. Whether `creation_time` keeps timezone information in stored inspections, and whether it marks the start or the end
   of the recording for the cameras you use.
2. What `ProductionMetadata.device` actually contains for common cameras (a model name, a serial number, a generic
   string), which decides how safe `same_device` is.
3. What `InspectionCatalog.find` returns after the probe tool version changes (the fingerprint includes the probe
   identity), that is, how often a healthy library would show `unavailable` after a tool upgrade.
4. Whether any test pins that a library asset's `created_at` never changes.
5. Whether `RelationshipRepository` already has a read by asset and detector suitable for step 7 of section 5.5, or
   needs one more method in this module.
6. The cost of the overlap range query on a large `observed_facts`.
7. Whether `Page.total` and the page rows come from one read snapshot (they are separate statements).
8. Whether `mypy --strict` accepts attribute access to unexported inspection types without casts.

## 13. Vertical steps after approval (proposed)

- **4a**: domain (facts, detectors, settings, pairing rules) with unit tests; migration 3 and the marks/facts store with
  contract tests over the fake and SQLite.
- **4b**: the feed and sweep, `DetectRelationships`, `DetectorVersions` wired into reconciliation,
  `RelationshipDetection` contract, module wiring, lifecycle resolution.
- **4c**: process and real-library integration tests, architecture tests, the evaluation harness, README and
  `AI_CONTEXT.md`, the final diff audit and a full integration run.

I would stop and report after each step. None of it starts without your explicit approval of this document.
