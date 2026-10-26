# asset_relationships — design

Status: Phase 0 audit and decisions A-F accepted in chat 2026-10-26. This document is the design
for approval. No code has been written. Implementation starts only after approval.
Revision 2026-10-26: owner approved the direction and the six items in section 15; one correction
requested and applied (`exact_duplicate_of` made directed with an explicitly partial reverse query,
sections 4, 5, 9, 12, 13, 14). Slice 1 starts only after the owner approves that correction.
Foundation: `UNIVERSAL_BASE_PROMPT.md`, `AI_CONTEXT.md`, `CLAUDE.md`, `ARCHITECTURE.md`.

Labels used in this document:

- **[V]** verified in the repository (path and symbol given).
- **[D]** decision approved by the owner in chat on 2026-10-26.
- **[P]** proposed by this design; needs approval with the rest of the document.
- **[U]** unverified assumption; to be settled by an experiment or further inspection.

## 1. Purpose, goals, non-goals, ownership

`asset_relationships` is the shared, explainable relationship layer over stable library asset ids.
It answers "how does asset A relate to asset B, why do we think so, and has a person decided?"

Goals: one query that returns factual lineage and inferred claims side by side, each with its
evidence; inferred claims are proposals until a person (or an explicit, later-approved type policy)
confirms them; a human decision is never overwritten by automation; a failed or interrupted run never
damages stored claims; no second source of truth for anything another module already owns.

Non-goals: a second asset registry, checksum authority, cache, scheduler, embedding store or similarity
index; a replacement for `media_inspection` or any intelligence module; an alignment engine; asset
merging; best-take selection or any editorial decision (`editing_intelligence`, which does not exist
yet, owns those); project, sequence or timeline membership (no such entities exist).

Anti-goals: a similarity score presented as identity; a stored copy of lineage that can go stale; a
confidence number that is not a probability but looks like one; claims about clocks that were never
verified.

Ownership:

| Responsibility | Owner |
| --- | --- |
| Asset identity, checksum, derivation records, deletion | `media_library` |
| Proxy provenance and which proxy a slot serves | `proxy_manager` |
| Technical facts, production tags, timecode | `media_inspection` |
| Inferred claims, evidence, statuses, human decisions | `asset_relationships` (own `asset_relationships.db`) |
| Merged read view of both kinds | `asset_relationships` (read-time projection, section 5) |

## 2. Confirmed repository facts and reusable capabilities

### Architecture and conventions

- [V] A feature module is `domain/ application/ infrastructure/ presentation/ module.py`; the only
  cross-module import allowed is `<module>.application.contracts`. Enforced by
  `tests/architecture/analysis.py` (`check_all`, `find_cycles`) via `test_dependency_rules.py`.
- [V] Each module owns its SQLite file with `PRAGMA user_version` migrations (ADR-0007;
  `media_library/infrastructure/migrations.py`: `Migration(version, name, statements)`).
- [V] `bootstrap/modules.py:installed_modules` lists modules, one line each.
- [V] `tests/integration/test_application_lifecycle.py` asserts the exact set of installed module
  names (lines 74-86). Adding a module requires adding its name there: a one-line edit to an existing
  test, unavoidable.
- [V] `EventBus` is synchronous, in-process and non-durable. The only subscriber is a debug logger in
  `bootstrap/composition.py`. No module subscribes to `MediaAssetImported`, `MediaAssetDeleted` or
  `DerivedAssetRegistered`. The proxy_manager amendments record that there is no startup hook.
- [V] `JobScheduler` / `JobContext` (`shared/concurrency`) is a flat cooperative thread pool.
- [V] Three modules already define a `RelationKind` enum with different meaning (`media_inspection`,
  `content_intelligence`, `video_intelligence`). This module therefore names its enum `RelationshipType`.

### media_library (`application/contracts.py:MediaLibrary`)

- [V] Original assets are unique by whole-file checksum: partial unique index
  `ux_media_assets_original_checksum ... WHERE source_asset_id IS NULL`; `import_file` of identical
  content returns the existing asset with `created=False`. Two original assets can never share bytes.
- [V] `find_by_checksum` returns an original only (`find_original_by_checksum`: `source_asset_id IS NULL`).
  Derived assets are unique by `processing_fingerprint`, not by checksum, and `checksum` has no index
  usable for derived assets.
- [V] `MediaAssetDto.derivation: DerivationDto(source_asset_id, operation, version, config, fingerprint)`;
  `role` is `ORIGINAL | DERIVED | PREVIEW`; `status` is `ACTIVE | ARCHIVED`; `media_type` is
  `IMAGE | AUDIO | VIDEO | OTHER`. The database forbids an `ORIGINAL` with a source and a derived
  asset without one (`CHECK` constraints in the initial migration).
- [V] JSON analysis documents become `OTHER` assets: `JsonDocumentProbe` accepts a JSON object only if
  it declares `document_type` and assigns `MediaType.OTHER`, `technical={"document_type": ...}`.
  Inspection, transcription, analysis, alignment and content documents are all registered this way
  (`DerivedDocuments`, `InspectionStore`).
- [V] `search(MediaQuery)` supports `source_asset_id`, `roles`, `statuses`, `media_types`,
  `created_from/created_to`, `sort_by` (`CREATED_AT`, `UPDATED_AT`, ...), `page`, `page_size`
  (max 200) and returns `Page(items, total, page, page_size)`. Empty `roles`/`statuses` mean "no
  restriction"; the defaults hide archived assets and `PREVIEW` assets.
- [V] `list_derived(source_id)` is `search` with no role/status restriction, oldest first, **first 200
  only, with no total and no truncation signal** (`queries.py:list_derived`). `search` itself reports
  `total` and pages, so the projection uses `search`, not `list_derived`.
- [V] `get_many(ids)` returns existing assets in request order and silently skips unknown ids.
- [V] Deleting a source deletes its descendants by default (`DeleteMedia`, recursive
  `descendant_ids`; `source_asset_id ... ON DELETE CASCADE`), and publishes one `MediaAssetDeleted`
  per removed asset. `update_metadata(asset_id, patch)` is public: asset metadata can be edited after
  registration.
- [V] Derived operation names in use: documents `media_inspection`, `acoustic_measurements`,
  `audio_intelligence`, `transcription`, `diarization`, `audio_visual_alignment`,
  `content_intelligence`, `content_intelligence_llm_chunk`, `content_intelligence_embeddings`,
  `video_intelligence`; media-bearing `video_improvement`, `audio_improvement`, `audio_mix`,
  `image_adjustment`, `audio_extraction` (WAV of a source's audio, role `DERIVED`), `proxy` (role
  `PREVIEW`), `thumbnail` (image, role `PREVIEW`).
- [U] The cost of `Page.total` (a `COUNT(*)`) on a very large library has not been measured.

### proxy_manager (`application/contracts.py`)

- [V] `read_proxy_provenance(metadata) -> ProxyProvenance | None` parses the self-describing record in
  the proxy asset's metadata (`METADATA_KEY`, schema 1). It returns `None` for anything missing or
  invalid. `ProxyProvenance` carries `source_asset_id`, `profile`, `verdict`, `evidence`,
  `transformations`, `spec_fingerprint`, `attempt_id`. It is written once at registration.
- [V] `ProxyCatalog.find(source_asset_id, profile) -> ProxyDto | None` answers which proxy a slot
  serves now (`ProxyDto.proxy_asset_id`, `condition`, `verdict`); `profile_names()` lists profiles. There
  is no reverse lookup (proxy id to source) and no per-source listing.
- [V] A replaced proxy's previous artifact is kept for one generation, so a source can have two proxy
  assets in the library for one profile. `ProxyDto.proxy_asset_id` changes on replacement; the
  contract tells consumers to store the source id.
- [V] `PROXY_OPERATION = "proxy"` is **not** exported through the contract (only `METADATA_KEY` is).
- [U] Whether a bare `ProxyCatalog.find` is cheap (the condition is computed against the live library).

### media_inspection (`application/contracts.py`)

- [V] `InspectionCatalog.find(asset_id, config=None) -> InspectionResult | None` never probes.
  `InspectionResult(inspection: MediaInspection, asset: MediaAssetDto, created: bool)`.
- [V] Accessor paths: `result.inspection.observed.production: ProductionMetadata` (`creation_time:
  datetime | None`, `camera_make`, `camera_model`, `device`, `reel_id`, `clip_id`, `scene`, `take`,
  `lens`, ... and every raw tag in `tags`); `result.inspection.observed.timecode: TimecodeInfo | None`
  (`start: Timecode`, `source`, `start_seconds: float | None`); `...observed.container.start_time` and
  `.duration` (`float | None`); `...synchronization` (stream offsets inside one container).
- [V] `Timecode.frame_number(frame_rate: Rational) -> int` is exact (drop-frame aware); `seconds_at`
  returns a float and is not used here.
- [V] `creation_time` comes from `datetime.fromisoformat` on a container tag (`production.py:_parse_time`).
  A tag without an offset yields a **naive** datetime; one with `Z` or an offset yields an aware one.
  Production tags are vendor-specific and consumer files rarely carry them (module docstring).
- [U] Whether the serialised inspection document preserves tz-awareness of `creation_time`.
- [U] Which `Finding.code` values indicate timestamp discontinuities.

### Time types

- [V] `core.domain.FrameTime(frame, pts, timebase)` and `TimeRange` (half-open frames, same time base)
  are the canonical video position.
- [V] `core.domain.Rational` is exact but requires a **non-negative numerator** (`__post_init__`
  raises otherwise); container start times and `FrameTime.pts` can be negative (AAC priming, pre-roll).
- [V] `audio_visual_alignment` convention: `common = upstream_seconds + correction`; the common clock
  is the container timeline.
- [V] `media_inspection` and the alignment document store seconds as floats.

### Existing relationship-like code

- [V] `media_inspection.MediaRelations` / `FindRelatedMedia` (section 3).
- [V] `video_intelligence.RetakeGroup`, `IdentityCluster`, `Scene`, `Embedding` and
  `content_intelligence.Relationship` are scoped to one analysed recording, not across assets.
  `audio_visual_alignment` aligns audio to video inside one recording.
- [V] No audio fingerprint, perceptual hash or cross-asset similarity index exists (searched `src`,
  `tests`, `scratch`, `scripts`, `docs`). No `editing_intelligence`, project, sequence or timeline entity
  exists. `MediaGroup` (library folders) is the closest membership concept and is not a project.

## 3. The fate of `MediaRelations` [D]

Decision: leave it untouched through Phase 2. It is not maintained as a second relationship store and
is not extended. No file of `media_inspection` changes in Phases 1-2.

Current behaviour [V]: `FindRelatedMedia(library).execute(asset_id)` compares one asset with up to
`SEARCH_LIMIT = 200` video and 200 audio assets (`truncated` flag) on duration, size, container, codec
and checksum. It returns `RelationCandidate(kind, asset_id, certainty, confidence, evidence)` where
`exact_duplicate`, `source` and `derivative` are facts (confidence 1.0) and `possible_duplicate`,
`possible_proxy`, `possible_companion` are heuristic guesses whose confidence is a hand-summed score
capped at 0.9. Nothing is stored. Documents are excluded only because they are `OTHER` media.

Call sites [V], from a search of `*.py`, `*.md`, `*.toml` in the repository:

| Where | What |
| --- | --- |
| `media_inspection/module.py` | registers `FindRelatedMedia` and `MediaRelations` |
| `media_inspection/application/contracts.py` | exports `MediaRelations`, `RelatedMedia`, `RelationCandidate`, `RelationKind` |
| `media_inspection/README.md` (lines ~75, ~110) | documents it |
| `tests/integration/media_inspection/test_related_media.py` | 5 behaviour tests (re-encode is `possible_duplicate` with 0.5 < c <= 0.9; smaller copy is `possible_proxy`; derived asset is a fact both ways; unrelated media yields nothing; inspection documents are not candidates) and 1 unknown-asset error test |
| `tests/integration/test_application_lifecycle.py:93` | asserts `MediaRelations` resolves to `FindRelatedMedia` |
| `tests/unit/media_inspection/test_serialization_config_relations.py` | exercises `relations.py` ([U]: file not opened in this audit) |

No other module and no scratch script imports it. [U] Consumers outside this repository cannot be
checked.

Why not decide now: removing it is only safe after its tests and every call site are reviewed against
what replaces it, and `asset_relationships` cannot replace it before Phase 2. A thin adapter has a
dependency problem: `media_inspection` cannot import `asset_relationships` if `asset_relationships`
later reads `media_inspection` (cycle); an adapter would have to live in `bootstrap` or in
`asset_relationships`.

Decision gate (after Phase 2 verification) [P]:

1. Re-grep call sites and open every test above.
2. Compare each `RelationKind` value with what `RelationshipCatalog` now returns. `exact_duplicate`,
   `source`, `derivative` map to projected lineage. `possible_duplicate`, `possible_proxy`,
   `possible_companion` have no equivalent: they are metadata-only guesses that this design refuses to
   store as trusted claims.
3. Choose one: (a) keep it as is, documented as a heuristic helper; (b) reimplement it over the new
   catalog as a compatibility adapter outside `media_inspection`, keeping its tests green; (c)
   deprecate it per repository conventions, removing wiring, tests and README text in one change.
   `UNIVERSAL_BASE_PROMPT.md` section 20 forbids leaving two competing architectures without a
   documented transitional reason; until the gate the transitional reason is this section.

## 4. Taxonomy, directionality, initial subset

Naming: `RelationshipType` (not `RelationKind`).

### Supported in Phases 1-2 (read-time projections, never stored) [P]

| Type | Direction | Meaning | Authority |
| --- | --- | --- | --- |
| `exact_duplicate_of` | **directed**: non-original asset -> original | the non-original asset has the same whole-file SHA-256 as that original | library checksum |
| `proxy_of` | proxy -> source | a proxy that `proxy_manager` made from the source | library derivation and proxy provenance, which must agree |
| `derived_from` | derivative -> source | a media-bearing asset a library operation produced from the source; the operation name is carried as a label | library derivation record |

`exact_duplicate_of` is directed, not symmetric (revised 2026-10-26). The library guarantees at most one
original per checksum, so the original is the single holder of those bytes and the duplicate is always a
non-original asset. Two originals can never be duplicates of each other [V]. Current contracts can find
the original from a duplicate (`find_by_checksum`) but cannot enumerate the non-original assets that share
an original's bytes (no checksum lookup for derived assets; scanning the library is rejected). So:

- Forward query ("what is this asset a duplicate of?") from a non-original asset is **complete**: at
  most one original exists, and `find_by_checksum` returns it.
- Reverse query ("what duplicates this original?") is **partial and says so**: it finds only duplicates
  among the original's own derivatives, which the derivative scan already reads (section 5). A duplicate
  derived from a different source is not found.
- Derived-versus-derived duplicates are not reported in either direction.
- Complete reverse discovery needs an upstream checksum lookup across roles (C2, section 13), deferred
  until separately approved. No stored copy of duplicate facts is created as a workaround.

`transcode_of` and `version_of` are **not supported**: no operation in the repository is a transcode
and nothing models versions (`video_improvement` output is `derived_from` with its operation label).
Adding them would invent semantics the library does not establish.

### Supported as stored claims from Phase 1 (domain and store); automatic detectors only in Phase 4 [P]

| Type | Direction | Meaning | Admissible evidence in Phase 1 |
| --- | --- | --- | --- |
| `temporally_overlaps_with` | symmetric, canonical pair | the recorded time spans intersect on a stated clock; says nothing about content | wall-clock interval overlap, timecode interval overlap, user assertion |
| `alternate_take_of` | symmetric, canonical pair | the clips are different takes of the same scene | production-tag match (same `scene`, different `take`), user assertion |

A stored claim can also come from a person (origin `USER`). No stored type is confirmed automatically in
any phase of this design; confirmation is a human decision. [P]

### Reserved, not representable yet

`near_duplicate_of`, `multicam_of`, `audio_companion_of`, `same_recording_session_as`, `same_scene_as`,
`same_location_as`, `contains_same_subject_as`, `used_in_project`, `visually_similar_to`,
`semantically_related_to`, `transcode_of`, `version_of`. They are not enum members. A type is added
only together with its semantics, admissible evidence and tests (section 14).

### Per-type contract (domain `taxonomy.py`, pure data plus rules) [P]

Each type declares: directed or symmetric; admissible `EvidenceKind` set; whether intervals are allowed;
the grade rule (section 6). A claim whose evidence is not admissible for its type is rejected by the
domain (`InvariantViolation`), so filename, duration, size, visual or transcript similarity cannot
create lineage claims: no such evidence kinds exist in Phases 1-3.

## 5. Factual lineage projections versus stored claims [D][P]

Rule [D]: authoritative lineage lives in `media_library` derivation records and `proxy_manager`
provenance; `asset_relationships` projects it at read time and stores nothing of it.

`LineageProjection` (application) is built on `MediaLibrary` and an optional `ProxyCatalog`. For an
asset it:

1. `get(asset_id)`; unknown -> `Err(NotFoundError)`.
2. Upward: if the asset has a derivation, fetch the source (`get`); if missing, emit nothing and report
   `source_missing`.
3. Downward: `search(MediaQuery(source_asset_id=asset_id, roles=frozenset(), statuses=frozenset(),
   page_size=200, page=n))` over all pages; the `Page.total` makes truncation visible. (`list_derived`
   is not used: it caps at 200 silently.)
4. Exact duplicate (directed, duplicate -> original; see section 4):
   - asset is non-original: `find_by_checksum(asset.checksum)`; a hit with a different id yields
     `exact_duplicate_of(asset -> hit)` with evidence `CHECKSUM_MATCH`. Complete.
   - asset is an original: while scanning its derivatives (step 3), every derivative whose `checksum`
     equals the original's yields `exact_duplicate_of(derivative -> asset)`. No extra call. Partial: the
     result page carries the limitation `exact_duplicate_reverse_partial` whenever this type is in
     scope for an original, **even when no derivative matches** (approved 2026-10-26): the flag depends
     on the query, not on what was found, so an empty answer never reads as "no duplicates". The rule is
     the pure function `exact_duplicate_limitations` in the domain.
5. Classify each derivative with one pure function (domain `lineage.py`), in this order:
   1. `media_type is OTHER` -> not a media relationship (documents). Basis: verified probe behaviour.
   2. `read_proxy_provenance(metadata)` present and `provenance.source_asset_id ==
      derivation.source_asset_id` -> `proxy_of`. Present but disagreeing -> not projected, counted as
      `untrusted_provenance` (metadata is editable through `update_metadata`; derivation record and
      provenance must agree). The proxy operation string is not needed.
   3. role `PREVIEW` and not a proxy (thumbnails) -> not projected.
   4. role `DERIVED`, `media_type` in `VIDEO | AUDIO | IMAGE`, operation not in `excluded_operations`
      -> `derived_from`, operation and version as labels.
6. Proxy liveness: for `proxy_of`, if a `ProxyCatalog` is wired, `find(source, provenance.profile)`;
   `serving = (dto.proxy_asset_id == asset.id)`; without it `serving` is unknown. A retained previous
   proxy therefore appears as `proxy_of` with `serving=false`, never as a stale stored edge.

`excluded_operations` is module configuration, default `{"audio_extraction"}`: it is a media-bearing
internal intermediate rather than a user-meaningful derivative. Configuration lives in one frozen
settings object, not in the query. A test pins the default to the real constant
`audio_intelligence.domain.values.EXTRACTION_OPERATION` (tests may import it; the architecture scan covers
`src` only), so a rename fails loudly. [P]

Distinguishing media derivatives from documents uses two documented facts (`MediaType.OTHER` for
`document_type` JSON; role `PREVIEW` for previews), not an operation allow-list of other modules'
private strings. The one string used is the exclusion above. [P]

Deleted, replaced or inaccessible assets: a cascade delete removes derivatives from the library, so the
projection cannot dangle; a replaced proxy is a new asset id and appears as a new projected item; the
old one carries `serving=false` while retained; an archived derivative is hidden unless the query asks
for archived endpoints; an unreadable library call is a typed exception that propagates (no partial
silent result).

Limits [V]/[U]: derived-versus-derived byte-identical assets, and byte-identical derivatives of a
different source than the queried original, are not discoverable without a scan and are not reported
(flagged by `exact_duplicate_reverse_partial`); the fact that a user re-imported an identical file is not recorded anywhere, so it
cannot be projected.

Stored claims: only inferred types and human decisions (section 8).

## 6. Domain model, evidence, status, grade [P]

Pure Python (stdlib only; `hashlib` and `fractions` are allowed in the domain layer).

- `RelationshipType` (section 4), with a `taxonomy` table.
- `RelationshipId`: deterministic, `sha256(type | endpoint_a | endpoint_b)` truncated to 32 hex
  characters. Symmetric types store endpoints in lexicographic order; directed types keep
  source/target. Same inputs give the same id: upserts are idempotent and ids are stable across runs.
- Endpoints are library asset ids (opaque strings). Self-relations are invalid. Paths, names, checksums,
  camera identifiers and timecodes are evidence attributes, never endpoints.
- `AssetRelationship` (aggregate): id, type, endpoints, `status`, `grade`, `origin`, `basis`,
  optional `source_interval`, `target_interval`, `offset`, evidence, decision record, timestamps
  (passed in; the domain has no clock).
- `RelationshipOrigin`: `LIBRARY_RECORD`, `PROXY_RECORD` (projected only), `DETECTOR`, `USER`.
- `Basis`: detector id and version plus the identities of the inputs it read (for example inspection
  asset ids and versions). It defines "stale": a claim is stale when its detector version differs from
  the current one or an input identity changed. `USER` claims have basis `manual`.
- `RelationshipEvidence`: `kind`, `provider` and `provider_version`, `observed_at`, and `detail`
  (a flat, JSON-safe mapping validated per kind). Kinds in Phases 1-3: `CHECKSUM_MATCH`,
  `LIBRARY_DERIVATION`, `PROXY_PROVENANCE` (projection only); `WALL_CLOCK_OVERLAP`,
  `TIMECODE_OVERLAP`, `PRODUCTION_TAG_MATCH`, `USER_ASSERTION` (storable). Evidence stores references
  and the few measured values that explain the claim (for example the two tag values or the overlap
  bounds), never embeddings, transcripts or whole analyses. It answers "why linked" without loading an
  upstream document.
- `Grade` (ordinal, strongest first): `RECORDED` (authoritative store states it; projections only),
  `CORROBORATED` (two or more independent evidence kinds agree and none contradicts),
  `INDICATED` (one evidence kind whose semantics are defined for the type), `SUGGESTIVE`
  (circumstantial: the evidence is consistent with the claim but unverified assumptions remain). A grade
  is a deterministic pure function of (type, evidence) in `assessment.py`. It is not a probability and
  is never averaged or weighted. A grade says how well the evidence supports the claim as defined for
  its type; it never makes a claim trusted lineage.
- `RelationshipStatus`: `PROPOSED`, `CONFIRMED`, `REJECTED`, `STALE`.

Per-type grade rules (Phase 1) [P]:

| Type | Evidence | Grade |
| --- | --- | --- |
| `temporally_overlaps_with` | wall-clock overlap, **aware** timestamps, same `device` on both | `INDICATED` |
| | wall-clock overlap, different or unknown devices (clock sync unverified) | `SUGGESTIVE` |
| | timecode overlap, same reel/jam source not established | `SUGGESTIVE` |
| | wall-clock and timecode overlap both present and agreeing | `CORROBORATED` only if the offsets they imply agree within their stated uncertainty, else `SUGGESTIVE` and flagged contradictory |
| `alternate_take_of` | same non-empty `scene`, different non-empty `take` | `INDICATED` |
| | plus same `device` or `reel_id` | `CORROBORATED` |

Naive timestamps are not admissible wall-clock evidence (no timezone is invented). A `USER_ASSERTION`
does not raise the grade; it drives status.

Status transitions (domain methods, each raises `InvariantViolation` otherwise):

- `PROPOSED -> CONFIRMED | REJECTED | STALE`
- `STALE -> PROPOSED` (re-detected on the current basis) `| REJECTED`; a stale claim cannot be
  confirmed before it is re-evaluated.
- `CONFIRMED -> REJECTED` and `REJECTED -> CONFIRMED` (a person changes their mind; every change is
  appended to the decision history).
- Automation never changes a `CONFIRMED` or `REJECTED` claim. A refresh may append or supersede
  evidence and set `outdated_since` on it; the status stays.

Rejection scope [P]: a rejection applies to the pair and type, not to one evidence version. A later
detection of the same pair and type adds evidence and shows the reviewer the change, but cannot revive
the claim. Only a person can reopen it. The decision record keeps the evidence that existed when it was
made.

Contradictory evidence: opposed evidence of defined kinds (for example differing `scene` tags, or
timecode and wall-clock offsets that disagree beyond uncertainty) is stored, caps the grade at
`SUGGESTIVE` and is flagged `contradicted`; it does not delete the claim.

Ambiguity: every candidate pair is its own claim, so alternatives are preserved side by side. No type in
Phases 1-3 requires a unique counterpart, so "ambiguous" is not a status yet. A type that needs a unique
counterpart (for example `audio_companion_of`) must define its contention rule when it is added.

No claim publishes domain events in Phases 1-3 (no consumer exists; `ApplicationEvent` has one user and
the architecture review warns against speculative events). [P]

## 7. Time representation [D][P]

Decision [D]: rational seconds on an explicitly identified clock, half-open intervals, independent
source and target ranges, recorded origin and uncertainty of any offset, no invented ranges.

- Exact numbers: `core.domain.Rational` cannot be used for signed seconds [V]. Proposed: stdlib
  `fractions.Fraction` inside the domain for signed exact seconds, converting from a
  `core.domain.Rational` time base with `Fraction(n, d)`. `FrameTime` and `TimeRange` stay the canonical
  type for frame-addressed work and are not migrated. Alternative (not proposed): make
  `core.domain.Rational` signed, which touches a core invariant used for aspect ratios and frame rates.
  This is item 1 of the approval list in section 15.
- `ClockDomain`: `CONTAINER` (seconds on the container timeline whose zero is the media time origin,
  the audio-visual-alignment common clock), `TIMECODE` (position computed as
  `Timecode.frame_number(frame_rate) / frame_rate`, zero at 00:00:00:00, declared source recorded),
  `WALL_CLOCK` (seconds since the Unix epoch, UTC, from an **aware** timestamp only).
- `TimeCoordinates(domain, origin_note)` names what zero means. `TimeInterval(start, end,
  coordinates)` is `[start, end)`; `start < end` strictly (an instant is not an interval); `Fraction`
  values only. `overlaps` and `intersection` raise if the coordinates differ: nothing is compared across
  clock domains silently.
- A claim has an optional `source_interval` and `target_interval`, each in its own asset's coordinates.
  The two may use different domains.
- `ClockOffset(seconds: Fraction, uncertainty: Fraction | None, origin)` maps source time to target time
  (`target = source + seconds`). `uncertainty=None` means unknown, not zero. `origin` is
  `USER_ASSERTED`, `TIMECODE_DIFFERENCE` or `WALL_CLOCK_DIFFERENCE`; a measured-sync origin is added only
  when a sync producer is approved. An offset exists only when evidence supplies it.
- Inspection stores floats [V]. Conversion is explicit: a float second value is quantised to
  microseconds (`Fraction(round(x * 10**6), 10**6)`) and the interval's uncertainty is at least 1 us;
  timecode is converted exactly through `Timecode.frame_number` and the frame-rate `Rational`.
- Missing, nonzero-start, discontinuous, unverified:
  - Container interval requires a known `container.start_time` (nonzero starts are represented as
    `[start_time, start_time + duration)`) and a known `duration`; otherwise no interval, and the claim
    stays clip-level.
  - A recording known to have timestamp discontinuities gets no single interval ([U]: finding codes to be
    confirmed before the detector phase).
  - Timecode needs a parsed `Timecode` and a known frame rate; wall clock needs an aware timestamp and a
    known duration.
  - A clock's trust (`unverified`, `same_device`, `user_asserted`) is evidence detail and drives the
    grade rules above. Container timestamps, camera timecode, wall-clock creation time and
    external-recorder timestamps are never assumed to share a clock.

## 8. Persistence [P]

One SQLite file, `asset_relationships.db` under `AppPaths.data_dir`, own module only, no foreign keys or
reads into the library database, per-operation short-lived connections, `PRAGMA user_version`
migrations, same style as `media_library/infrastructure/migrations.py`. The module keeps its own small
connection helper (each module keeps its adapters private; see the proxy_manager precedent). Its
lessons apply: set a busy timeout, retry transient open errors, create the database safely when several
processes start at once. [U] cross-process behaviour is covered by a test.

Migration 1 (Phase 1):

```
relationships(
  relationship_id TEXT PRIMARY KEY,
  type            TEXT NOT NULL,
  symmetric       INTEGER NOT NULL CHECK (symmetric IN (0, 1)),
  asset_a         TEXT NOT NULL,    -- directed: source; symmetric: lower id
  asset_b         TEXT NOT NULL,    -- directed: target; symmetric: higher id
  status          TEXT NOT NULL CHECK (status IN ('proposed','confirmed','rejected','stale')),
  grade           TEXT NOT NULL,
  origin          TEXT NOT NULL,
  basis           TEXT NOT NULL,    -- canonical JSON: detector id/version + input identities
  contradicted    INTEGER NOT NULL DEFAULT 0,
  a_domain TEXT, a_origin TEXT, a_start_num INTEGER, a_start_den INTEGER, a_end_num INTEGER, a_end_den INTEGER,
  b_domain TEXT, b_origin TEXT, b_start_num INTEGER, b_start_den INTEGER, b_end_num INTEGER, b_end_den INTEGER,
  offset_num INTEGER, offset_den INTEGER, offset_unc_num INTEGER, offset_unc_den INTEGER, offset_origin TEXT,
  outdated_since  TEXT,
  endpoint_missing_since TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK (asset_a <> asset_b),
  CHECK (symmetric = 0 OR asset_a < asset_b),
  -- interval column groups are all NULL or all set (checks per group)
  UNIQUE (type, asset_a, asset_b))
relationship_evidence(
  evidence_id INTEGER PRIMARY KEY,
  relationship_id TEXT NOT NULL REFERENCES relationships ON DELETE CASCADE,
  kind TEXT NOT NULL, provider TEXT NOT NULL, provider_version TEXT NOT NULL,
  detail TEXT NOT NULL,             -- JSON, validated per kind
  observed_at TEXT NOT NULL, superseded INTEGER NOT NULL DEFAULT 0)
decision_history(
  seq INTEGER PRIMARY KEY, relationship_id TEXT NOT NULL REFERENCES relationships ON DELETE CASCADE,
  status_from TEXT, status_to TEXT NOT NULL, at TEXT NOT NULL, note TEXT, evidence_ids TEXT NOT NULL)
```

Indexes: `ix_rel_a (asset_a, type, status)` and `ix_rel_b (asset_b, type, status)` give efficient forward
and reverse lookup without storing both directions; `ix_rel_status (status, updated_at)` for
reconciliation; `ix_evidence_rel (relationship_id, kind)`. A per-asset query reads both indexes and
merges.

Writes and uniqueness:

- Deterministic id plus `UNIQUE (type, asset_a, asset_b)`; symmetric canonical order enforced by a
  `CHECK`. Racing writers converge on one row.
- One `BEGIN IMMEDIATE` transaction per command: upsert claim, replace or append evidence, append a
  decision-history row. An error rolls everything back.
- Upsert is idempotent: identical inputs and versions leave every column, including `updated_at`,
  unchanged. For a `PROPOSED` or `STALE` claim, evidence from the same provider is replaced and the
  grade recomputed; for a decided claim only evidence is appended (and `outdated_since` set); status
  and decision history are never touched by automation.
- Evidence of a superseded provider version is kept (`superseded=1`) only while the claim is decided;
  undecided claims drop it. Decision history is kept as long as the claim.

Deletion and stale references:

- The library has no foreign key into this database and events are not durable, so endpoints are checked
  against the live library: on every read (one `get_many` per page; unknown ids mean the endpoint is
  missing, archived assets are flagged) and during reconciliation (section 10), the same approach
  proxy_manager adopted.
- A claim with a missing endpoint and no decision is deleted by reconciliation. A decided claim is kept
  with `endpoint_missing_since` set and removed after a retention period, default 365 days [P] (asset
  ids are not reused, so the decision can never apply again; it is kept only as history).
- Archived endpoints are not an error; results flag them.

Migration 2 (Phase 3): `analysis_marks(asset_id, detector, detector_version, input_identity, outcome
CHECK (outcome IN ('found','none','unavailable')), at, PRIMARY KEY (asset_id, detector))` and
`scan_state(key PRIMARY KEY, value)`. This is how "no relationship found" is told apart from "not yet
analysed" without recomputing. Migrations are append-only and transactional.

## 9. Public contracts and dependency direction [P]

`application/contracts.py` is the only file other modules may import. Follow repository conventions:
frozen dataclass DTOs, `Result` for expected failures, typed exceptions for infrastructure faults.

```
class RelationshipCatalog(Protocol):          # read-only; never computes, never writes
    def relationships_of(self, asset_id: str, query: RelationshipQuery | None = None)
        -> Result[RelationshipPage, NotFoundError]: ...
    def get(self, relationship_id: str) -> Result[RelationshipDto, NotFoundError]: ...

class RelationshipEditor(Protocol):           # writes claims and decisions
    def propose(self, command: ProposeRelationshipCommand) -> Result[RelationshipDto, RelationshipError]: ...
    def confirm(self, relationship_id: str, note: str = "") -> Result[RelationshipDto, RelationshipError]: ...
    def reject(self, relationship_id: str, note: str = "") -> Result[RelationshipDto, RelationshipError]: ...
```

- `RelationshipQuery`: `types`, `statuses`, `min_grade`, `origins`, `include_projected=True`,
  `include_archived=False`, `page`, `page_size` (bounded). Stored results page by keyset on
  `relationship_id`; projected results are bounded by the library pages. The page reports `truncated`
  whenever any underlying source was capped, and carries `limitations: tuple[Limitation, ...]`
  (`Limitation.EXACT_DUPLICATE_REVERSE_PARTIAL`, section 4). In Slice 1 the tuple is always empty.
- `RelationshipDto`: id (deterministic for projected items too), type, `source_asset_id`,
  `target_asset_id`, `symmetric`, `status`, `grade`, `origin`, `stored: bool`, evidence tuple,
  intervals, offset, basis, `contradicted`, `outdated_since`, `endpoint_states`, `serving` (proxy only),
  timestamps. Projected items have status `CONFIRMED`-equivalent semantics expressed as grade
  `RECORDED`; they have no status transitions and cannot be rejected (a fact is not a claim).
- Dependencies: `media_library.application.contracts` (Phases 1-3),
  `proxy_manager.application.contracts` (Phase 2, `read_proxy_provenance` required, `ProxyCatalog`
  optional), `media_inspection.application.contracts` (Phase 4 only). Nothing else. No other module
  depends on `asset_relationships` except through its contracts; `media_library`, `media_inspection`,
  `proxy_manager` and the intelligence modules do not import it (architecture test).
- No import cycle: `asset_relationships -> proxy_manager -> media_library`.
- Layers: domain (taxonomy, values, intervals, claim, assessment, lineage classifier, repository port,
  errors), application (queries, projection, propose, decide, DTOs, commands, contracts),
  infrastructure (SQLite repository, migrations), `module.py`. No presentation layer in Phases 1-3.
  Packages are created only when a file exists for them.
- Wiring: one line in `bootstrap/modules.py`; one added name in the lifecycle test; one paragraph in
  `AI_CONTEXT.md` section 11 and a module `README.md`, as `UNIVERSAL_BASE_PROMPT.md` section 27 requires.

## 10. Incremental discovery, reconciliation, invalidation, recovery [P]

Phases 1-2 need none: queries project at read time and stored claims are explicit. From Phase 3:

- No reliance on events. They are in-process and lost on a crash, and nothing subscribes today. An
  optional subscription may later *hint* a scan; reconciliation is the source of truth.
- `ReconcileRelationships.execute(ctx: JobContext)` runs on the existing `JobScheduler`: no new
  scheduler.
  1. Library scan with the existing contract: `search(sort UPDATED_AT desc, statuses ACTIVE|ARCHIVED,
     roles ORIGINAL|DERIVED, page_size 200)`, paging until it meets the stored watermark (minus an
     overlap margin). Offset paging can repeat items when assets arrive during the scan (harmless:
     everything is idempotent) and can skip an item when one is deleted mid-scan; a bounded full sweep
     every N runs closes that gap. [U] sweep period to be set from measurements.
  2. Endpoint check for stored claims in batches with `get_many`: set or clear `endpoint_missing_since`,
     delete undecided orphans, apply retention.
  3. Invalidation: a detector version bump or changed input identity marks undecided claims `STALE`;
     decided claims only get `outdated_since`.
  4. Detectors (Phase 4) run only for assets whose `analysis_marks` basis changed.
- The watermark advances only after the batch it covers is committed. An interrupted or failed run
  leaves earlier rows intact, never deletes decided claims, and resumes from the committed watermark.
  Cancellation is cooperative (`ctx.raise_if_cancelled()` between batches).
- Queries stay responsive during a run: reads are independent transactions.
- There is no startup hook [V per proxy_manager amendments]; the run is started by an explicit command
  or a caller's job. A run is a function of (library state, stored rows), so repeated runs with the same
  inputs write nothing new.

## 11. Performance, reuse, cache policy [P]

- Phase 1-2 queries cost one library `get`, one paged `search` for derivatives, optionally one
  `find_by_checksum` and one `ProxyCatalog.find` per proxy, and one `get_many` per page for endpoint
  state. No all-pairs work, no media reads, no model loading.
- Nothing is cached in memory across calls (the library changes). The stored claims table is the only
  state, and it holds claims, not copies of upstream data.
- Phase 4 candidate generation must not compare all pairs. Overlap detection needs a time-sorted index
  of per-asset timing keys; reading every inspection JSON per run is O(n). If that index is built it is
  a small table keyed by inspection asset id and version, rebuildable, holding only keys needed to
  generate candidates (never served as inspection data). It is a proposal to be evaluated in Phase 4,
  not part of Phases 1-3.
- Reuse: asset identity, checksum, derivation, proxy provenance, inspection documents all come through
  contracts; no new fingerprinting, hashing, embedding, cache or scheduling code.
- Observability: structured log lines for query counts, projection skips (`untrusted_provenance`,
  `source_missing`, `truncated`), reconciliation batch counts and failures (`get_logger`, fields as
  kwargs, no media paths).

## 12. Testing strategy and acceptance criteria

### Conventions [V]

`tests/unit/<module>/test_domain.py`, `test_use_cases.py` with in-memory fakes, `FixedClock` and `T0`
from `tests/support/fakes.py`; `tests/integration/<module>/` for the real adapter and real media
(`pytestmark = pytest.mark.integration`), repository contract tests parametrised over fake and SQLite;
`tests/architecture/test_<module>_boundaries.py` per module. `filterwarnings = ["error"]`, so every
connection must be closed. Real-media fixtures come from `tests/support` (for example
`media_factories.ffmpeg`, `proxy_media.py`, `proxy_fakes.py`). [U] How existing fakes implement a
`MediaLibrary` (`tests/support/media_fakes.py`) was not read; reuse it if it fits, else add the smallest
fake beside it.

Architecture test (smallest suitable), `tests/architecture/test_asset_relationships_boundaries.py`,
modelled on `test_proxy_manager_boundaries.py`:

- it imports the library, proxy_manager (and later inspection) only through their `application.contracts`,
  only from application code and `module.py`;
- it imports no other processing module;
- nobody imports it except through `application.contracts`; `media_library`, `media_inspection`,
  `proxy_manager` do not import it;
- `sqlite3`, `os`, `shutil` stay out of its domain and application layers.

`test_dependency_rules.py` already scans every module automatically, and `bootstrap` is exempt.

### Commands (taken from `scripts/check.py` and `pyproject.toml`) — none run in this task

```
uv run pytest tests/unit/asset_relationships tests/architecture -q
uv run pytest tests/integration/asset_relationships -q
uv run pytest tests/integration/test_application_lifecycle.py -q
uv run mypy
uv run ruff check <explicit new paths>      # never repo-wide; pre-existing findings exist
uv run ruff format --check <explicit new paths>
```

`uv run python scripts/check.py` reported pre-existing lint/format failures when last recorded
(2026-10-07 note); [U] re-verify before using it as a gate. Judge new code with ruff and mypy on its own
paths, and check `git status --short` after any wide command.

### Acceptance criteria by phase

Phase 1 (domain, store, query contract):

- Domain: taxonomy table (directed/symmetric, admissible evidence), deterministic ids, canonical
  endpoint order, `TimeInterval` validation and cross-clock refusal, grade function for every rule in
  section 6, every legal and illegal status transition, decided claims survive refresh, evidence
  admissibility rejects forbidden kinds.
- Store: contract tests pass on fake and SQLite; migration from empty; idempotent upsert leaves
  `updated_at` unchanged; reverse lookup uses both indexes; transaction rollback on failure; corrupt row
  raises `PersistenceError`; two concurrent proposers converge; decided claim untouched by a refresh.
- Architecture test passes; `bootstrap` has one added line; lifecycle test lists the module.

Phase 2 (lineage projection):

- Pure classifier tests for every branch in section 5 including provenance/derivation disagreement,
  documents, thumbnails, `audio_extraction`, archived derivatives.
- Real-library integration tests: derived asset projects `derived_from`; a real proxy projects
  `proxy_of`; after a proxy is replaced the new one is `serving=true`, the retained one `serving=false`,
  and nothing is stored; deleting the source removes all projected items; `get_many`-reported missing
  endpoints are flagged; inspection and transcript documents never appear; more than 200 derivatives
  paginate and report `truncated` correctly.
- `exact_duplicate_of`: a non-original byte-identical to an original is found from the duplicate
  (complete, no limitation); a derivative of an original with the original's bytes is found from the
  original; a byte-identical derivative of a different source is not found from the original, and the
  page then carries `exact_duplicate_reverse_partial`; two originals never produce the type; a
  derived-versus-derived pair is never reported.
- Pin test: default `excluded_operations` equals the real extraction constant.
- No file in `media_library`, `media_inspection` or `proxy_manager` changed.

Phase 3 (reconciliation): interrupted-run and cancellation tests leave stored rows intact; watermark
advances only after commit; repeated run writes nothing; deletion handling and retention; version bump
marks undecided claims `STALE` and never touches decided ones; `analysis_marks` distinguish "none" from
"not analysed".

Phase 4 (matching): separate approval, with labelled examples and measured false positives and
negatives before any default threshold is set.

## 13. Proposed upstream contract additions — each requires separate approval

Principle: use existing contracts first. None of the following is needed for Phases 1-3.

| # | Candidate | Consumer use case | Current limitation | Why existing APIs suffice or not | Smallest signature if ever needed | Compat and tests | Needed for |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | `MediaQuery.updated_from: datetime \| None` in `media_library` | cheaper reconciliation scan | no updated-since filter | **Sufficient without it**: `search` supports `sort_by UPDATED_AT` and paging, so a scan stops at the watermark. Costs an offset-paging overlap and a periodic sweep (section 10). | one optional field, default `None` | additive; library search tests | optional, after measuring |
| C2 | find assets by checksum across roles, or expose `storage_key` | complete reverse discovery of `exact_duplicate_of` (what duplicates this original) and derived-vs-derived duplicates | `find_by_checksum` is originals only; no checksum index for derived assets | not needed for Phases 1-3: forward discovery is complete and reverse discovery is partial and flagged (section 4); needed only if a consumer requires complete reverse discovery | `find_by_checksum(checksum, *, roles=...)` or an index on `checksum` | additive; library search and index tests | deferred, separate approval |
| C3 | truncation signal on `list_derived` | projection correctness | silent 200 cap | **Not needed**: `search(source_asset_id=...)` returns `total` | none | none | not needed |
| C4 | export `PROXY_OPERATION` from `proxy_manager.application.contracts` | identify proxies by operation | constant is private | **Not needed**: provenance parsing identifies proxies | none | none | not needed |
| C5 | embedding-only view in `video_intelligence` (`VideoCatalog` reads whole analysis) | cross-asset visual similarity | loading a full analysis to read vectors | no existing alternative, but no approved detector uses it | `embeddings(asset_id) -> Result[tuple[Embedding, ...] \| None, MediaError]` | additive; embedding model identity must be returned | Phase 4+, only if a similarity detector is approved |
| C6 | record that an import hit an existing asset | report "you imported a duplicate" | `created=False` leaves no record | not needed for any listed type | an event or log in the library | event is not durable | not recommended |
| C7 | new library/inspection production-view helpers | overlap/take detectors | `InspectionCatalog.find` already exposes `observed.production` and `observed.timecode` | **Not needed** | none | none | not needed |

Nothing above is implemented or assumed by this design.

## 14. Limitations, unverified behaviour, deferred capabilities

Limitations by design:

- The first release finds nothing automatically: Phases 1-3 provide the store, decisions and lineage
  projection. Automatic detection of overlap and takes arrives only in Phase 4 with its own approval.
- Production tags are rarely present in consumer footage; `alternate_take_of` will often have no
  evidence. Wall-clock overlap between different devices is `SUGGESTIVE` by rule until a clock offset is
  established by a person or a future sync producer.
- `exact_duplicate_of` is directed (duplicate -> original). Its forward query is complete; its reverse
  query is partial (only the original's own derivatives) and flagged. Derived-versus-derived byte
  duplicates and import-duplicate history are not reported (sections 4, 5).
- `transcode_of` and `version_of` do not exist; there is no `used_in_project`, session, scene or
  location relationship because no canonical entity or evidence provider exists.
- No `editing_intelligence`, project, sequence or timeline integration exists or is claimed.

Deferred, with the prerequisite that must be established first:

| Capability | Prerequisite |
| --- | --- |
| external-audio and multicam sync (`audio_companion_of`, `multicam_of`) | an audio fingerprint or cross-correlation producer; none exists |
| near-duplicate and visual similarity | an approved embedding-access contract (C5), model identity rules, an index, labelled evaluation |
| transcript-based take matching | a cross-asset use of `TextEncoder` and a defined false-positive policy |
| subject identity | stable identity across assets; `IdentityCluster` is per-analysis and anonymous |
| scene, location, session, project membership | canonical entities for them |

Unverified [U] (settle before the relevant phase):

1. Whether the stored inspection document keeps tz-awareness of `creation_time`.
2. Which inspection finding codes mark timestamp discontinuities.
3. Cost of `Page.total` and offset paging on very large libraries.
4. Cost of `ProxyCatalog.find` for many proxies in one query.
5. Content of `tests/unit/media_inspection/test_serialization_config_relations.py`.
6. How `tests/support/media_fakes.py` fakes `MediaLibrary`.
7. Whether `mypy` covers `tests/`, and whether `scripts/check.py` is clean today.
8. Whether the app has any user-identity concept; decisions record time and note, no actor, until one
   exists.
9. The retention default (365 days) and the full-sweep period are placeholders until measured or chosen.

## 15. Slices and final verification

Items needing explicit approval with this document (my calls, [P]):

1. `fractions.Fraction` for signed exact seconds, not a signed `core.domain.Rational`.
2. Default `excluded_operations = {"audio_extraction"}`, configurable, pinned by a test.
3. Rejection applies to the pair and type and survives re-detection.
4. 365-day retention of decided claims whose endpoint is gone.
5. Enum name `RelationshipType`; no enum members for unsupported types.
6. Edit to `tests/integration/test_application_lifecycle.py` (one name) and one paragraph in
   `AI_CONTEXT.md`.

Vertical slices, each leaving the repository coherent and testable:

- **Slice 1 / Phase 1 — domain, store, query.** Taxonomy, ids, intervals, evidence, grade, claim and
  transitions; repository port, in-memory fake, SQLite adapter with migration 1; `ProposeRelationship`,
  `DecideRelationship`, stored-only `QueryRelationships`; contracts; `module.py`; bootstrap line;
  lifecycle-test name; boundary test; `AI_CONTEXT.md` paragraph and module README.
- **Slice 2 / Phase 2 — lineage projection.** Classifier, `LineageProjection`, merge into
  `QueryRelationships`, optional `ProxyCatalog` wiring, real-library integration tests, README update.
  `MediaRelations` untouched; decision gate (section 3) written into the plan.
- **Slice 3 / Phase 3 — reconciliation** (needs approval of the section 10 numbers).
- **Slice 4 / Phase 4 — first detector** (temporal overlap or production-tag takes), only after separate
  approval and a labelled evaluation set.
- **Slice 5 — documentation, diff audit and final verification.**

Final verification plan for each slice before it is called done: run the commands in section 12 on the
final code and report their actual output; `git status --short` and `git diff --stat` show only the
module, its tests, the bootstrap line, the lifecycle-test name and the documentation paragraph; the
architecture test passes without editing its rules; the Definition of Done checklist in `AI_CONTEXT.md`
section 9 is answered line by line; the architecture review in `UNIVERSAL_BASE_PROMPT.md` section 29 is
repeated against the diff; and the report states what is not verified.

## Amendments after Slice 1 (2026-10-26)

Slice 1 is built. These replace the corresponding statements above; the module README records the
verified behaviour.

1. **Evidence offsets are columns.** `relationship_evidence` has `offset_num`, `offset_den`,
   `offset_unc_num`, `offset_unc_den`, `offset_origin` (typed, like the claim's own offset) instead of
   hiding the offset in `detail`.
2. **`endpoint_missing_since` is not in migration 1.** Nothing writes it before reconciliation; it is
   added by the Slice 3 migration with its writer. Until then a missing endpoint is reported on read
   (`endpoint_states`) and the claim is left alone.
3. **`decision_history.evidence_snapshot`** (a JSON list of evidence keys `kind|provider|version`)
   replaces `evidence_ids`: evidence rows are rewritten on every save, so row ids are not stable. The
   history records **every** status change, including creation (`status_from` is NULL), not only
   decisions.
4. **The write port is an atomic read-modify-write**, `RelationshipRepository.update(id, change)`
   (load, let `change` edit or create, save in one `BEGIN IMMEDIATE`; `change` returning `None` writes
   nothing, raising writes nothing). This is how "racing writers converge" and "unchanged input writes
   nothing" are guaranteed, and it is tested with threads and with four real processes.
5. **Query surface in Slice 1.** `RelationshipQuery` has `types`, `statuses`, `min_grade`, `origins`,
   `cursor`, `page_size`; asking for a recorded-lineage type is a `ValidationError`, not an empty page.
   `include_projected`, `include_archived`, `RelationshipPage.truncated`, `RelationshipDto.stored` and
   `.serving` arrive with the projection (Slice 2) because they have no meaning before it.
   `RelationshipPage.limitations` exists and is always empty in Slice 1; the rule that fills it is built
   and tested now (`exact_duplicate_limitations`) and is wired in Slice 2.
6. **No settings object yet.** `excluded_operations` (default `{"audio_extraction"}`) and the 365-day
   retention are introduced with the slice that uses them (2 and 3); a field with no reader would have
   no meaning.
7. **Grade rules as built.** Wall-clock evidence carries `clock_trust` (`unverified`, `same_device`,
   `user_asserted`); trusted -> `indicated`, otherwise `suggestive`. Timecode alone and a person's
   assertion alone are `suggestive`. Wall-clock plus timecode corroborate only when both imply an offset
   with a known uncertainty and the offsets agree within the sum of the uncertainties; when they cannot
   be compared the single-kind grade stands; when they disagree the grade is `suggestive` and the claim
   `contradicted`. Tag evidence: same `scene` and different `take` is `indicated`, plus a shared device or
   reel `corroborated`; differing scenes or an identical take number is `suggestive` and `contradicted`.
8. **Test-only finding, not a library defect.** `ManageAsset` saves an archive or restore only when
   `updated_at` moves, so a test with a fixed clock must advance it; with the real clock it always moves.

## Amendments after Slice 2 (2026-10-26)

Slice 2 is built (lineage projection only). These replace the corresponding statements above.

1. **One merged answer.** `relationships_of` returns stored claims and projected lineage together in
   relationship-id order, so one cursor pages through both. The stored side is fetched with the keyset
   (`page_size + 1`), the projected side is computed and filtered by the cursor, and the two are merged.
   Ids cannot collide: the type is part of the hash.
2. **Facts have no status.** `RelationshipDto.status` is `None` for recorded lineage (design section 9
   said "CONFIRMED-equivalent"; that would read as a human decision). Facts have `grade=recorded`,
   `stored=False`, and an `origin` of `library_record` or `proxy_record`. A `statuses` filter therefore asks
   for claims only; `min_grade` keeps facts; `origins` selects the authority.
3. **New fields.** `RelationshipQuery.include_projected` (default `True`) and `include_archived`
   (default `False`); `RelationshipDto.stored` and `serving`; `RelationshipPage.truncated`. `include_archived`
   governs recorded lineage only (its OTHER end); stored claims are not filtered post-fetch, which would
   break page sizes, and always carry `endpoint_states`. The Slice 1 `ValidationError` for lineage types
   is gone: they are now answered.
4. **Settings are built.** `LineageSettings(excluded_operations={"audio_extraction"}, max_derivative_pages=5)`.
   The cap is whole library pages (200 each): beyond it the page says `truncated`. 5 is provisional until
   measured. The 365-day retention stays a Slice 3 setting.
5. **Limitation rule as built.** `EXACT_DUPLICATE_REVERSE_PARTIAL` is attached on every page when the
   queried asset is an original and `exact_duplicate_of` is in the projection's scope (lineage requested,
   type allowed by `types`, origin `library_record` allowed by `origins`, no `statuses` filter), even when
   nothing was found. It is derived from the query, never from the result.
6. **Evidence of projected items** is built from the library record itself: `observed_at` is the creation
   time of the derivative (or duplicate), so the same query gives the same answer; `provider` is
   `media_library` or `proxy_manager`; `provider_version` and the basis version are
   `LINEAGE_PROJECTION_VERSION` ("1"), which changes whenever the classification rules change meaning.
7. **Reads are bounded.** An empty scope reads nothing; a scope of duplicates for a non-original makes one
   `find_by_checksum` call and no search; the derivative scan runs only for derivative types or for
   duplicates of an original; the proxy catalog is asked once per `(source, profile)` per query.
8. **Wiring.** `ProxyCatalog` is resolved from the container (`proxy_manager` is always installed); the
   projection itself accepts `None` and then reports `serving=None`. `QueryRelationships` now requires a
   projection. The architecture test lets only `application/lineage_projection.py` and `module.py` import
   the proxy contract.
9. **`get(relationship_id)` finds stored claims only.** A projected id is a hash and is not reversible;
   ask about an asset to get its lineage.
10. **Not in this slice, as approved:** reconciliation, sweeps, endpoint-retention migration, detectors,
    any new consumer, any change to `MediaRelations` or to an upstream contract.
