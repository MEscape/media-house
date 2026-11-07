# review_orchestrator (Layer 6, shared component)

The one place where a review is kept. `quality_control`, `revision_engine`, `final_polish`,
`render_manager`, `export_validation` and `resolve_export` all report to it and ask it; none keeps a
second copy of the state, the approvals, the locks or the history. It owns no timeline, produces no
report, renders and edits nothing, and knows no editing application.

## What it keeps

A `ReviewSession` (a frozen value; every step returns the next one and the audit entries that
explain it) is bound to one exact timeline version: `TimelineRef(timeline_id, version, content_hash,
build_asset_id)`. The version is the timeline builder's own `build_version` and `build_hash`; nothing
else is a second timeline model. A rebuild with the same content moves only the reference.

| Kept | Where |
| --- | --- |
| state (25 states, a table of allowed moves) | `domain/workflow.py` (`ALLOWED`, `GUARDED`) |
| approvals, each bound to the version it was made on | `Approval`, `ReviewSession.is_current` |
| section status: unreviewed, under review, approved, locked, revision requested | `SectionRecord` |
| feedback exactly as said, until a plan takes it up | `FeedbackItem` |
| checkpoints (a stored build is read back and its hash compared) | `CheckpointRecord` |
| the authorization of the final export | `ExportAuthorization` |
| the project's permissions (automatic / suggested / manual only) | `PermissionPolicy` |
| the history of every step | append-only `audit` table |

## Rules it enforces

* **A decision is for the version the person looked at.** `decide` takes the `TimelineRef` they saw;
  another version raises `StaleTimeline`.
* **A change voids what depended on it.** `bind_timeline` compares sections by fingerprint: an
  approved section that changed loses its approval; a locked section stays locked and is marked
  `drift` (a person changed it); everything decided about the final video (final approval, export
  authorization, open proposals, reports) is withdrawn and the review goes to `review_again_required`.
  An unchanged rebuild voids nothing.
* **Who may decide.** The system never decides for a person. Automation may approve a revision only
  where the project granted `AUTOMATIC` in advance. Floors are not configurable: the final export,
  delivery approval, unlocking a section and overriding a blocker are always `MANUAL_ONLY`.
* **Guarded states.** `FINAL_APPROVED`, `EXPORT_AUTHORIZED`, the export states and `DELIVERY_READY`
  are reachable only through their own operation; `advance` refuses them.
* **Export authorization** (`authorize_export`) needs a person, the current final approval, the
  reports of this version, and an override on record for every blocking issue. It is bound to the
  profile and the hash of the output settings. `verify_export` looks at the stored timeline first, so
  an edit made after the authorization is noticed before anything renders. One authorization starts
  one export at a time; a failed export may be retried on it while it stands.
* **A validation that cannot decide** waits for a person and is never a pass.
* **Writes are optimistic**: a step that loses a race is applied again to the newer session.

## Contract (`application/contracts.py`)

`ReviewSessions` (every operation that changes a review), `ReviewCatalog` (`get`, `find`, `history`),
the value types, `Actor` (`human` / `system` / `preconfigured`) and the events
(`ReviewStateChanged`, `TimelineVersionBound`, `DecisionRecorded`, `ApprovalInvalidated`,
`ExportAuthorized`). Events are published once per stored step; a repeated step publishes nothing.

## Storage

Its own `review.db` (SQLite behind the `ReviewStore` port): one row per session (the session as a
strict JSON document, a revision counter) and an append-only audit table. Reports are not stored here:
they are Media Library documents of their owners; the session keeps only their ids.

## Not done / limits

* A session keeps ids of reports and plans; it does not verify they still exist in the library.
* No UI and no notification: events are in-process (the shared `EventBus`).
* Section fingerprints cover the clips of a section; a change to transitions or mix instructions
  outside the clips makes the whole version differ (and voids the final decisions) without naming a
  section.
