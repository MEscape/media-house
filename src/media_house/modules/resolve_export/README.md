# resolve_export (Layer 6, module 34)

The controlled end of Media House: the final review, a person's approval and authorization, the export
through Resolve, the independent check of the file, and its delivery. **Media House prepares
everything; a person decides when the final video leaves Resolve.** It never exports because
automated checks passed, and it never publishes or uploads anything.

```text
present ──► (person) approve_final ──► (person) authorize ──► run export ──► validation ──► (person) approve_delivery ──► deliver
 final review         the version          this output           FINAL render      PASS / WARNING /          copy, never replace
 + readiness           they watched        of this version       (render_manager)  FAIL / MANUAL REVIEW
```

## Who does what

| Step | Owner |
| --- | --- |
| final review package, readiness | this module (reads quality control, final polish, the mix, the Resolve binding, the render profile) |
| approval, override, authorization, state | `review_orchestrator` (this module only calls it) |
| the render | `render_manager` through `resolve_adapter` (the FINAL render needs the authorization and checks it again) |
| the check of the file | `export_validation` (independent of the render's own success) |
| the delivered copy | this module's folder adapter: unique, versioned names, never replaces a file |

## Readiness (`domain/readiness.py`)

* **Hard blockers** are never overridden: a critical issue, a report of another version, a missing
  quality or polish report, an unfinished element, a mix that is not finished, a file that is offline, a
  timeline never carried out in Resolve (or an older version there, or open conflicts), an output
  profile the render manager does not know or that does not fit.
* **Soft blockers** (a high issue, a problem the polish found) stop the export until resolved or a
  person overrides them on record (`ExportApprovals.override`, an `OVERRIDE` decision with their name).
* A good quality report, a ready polish and a good review render are never an authorization.

## Running the export (`ExportRunner`)

`verify_export` (the review checks, now, that this authorization is valid for this version, this
profile and this output hash, after looking at the stored timeline) → `begin_export` → FINAL render →
`export_rendered` → validate → `record_export_validation`. A render that fails, or a file that fails
its check, is a failed export with its reason; the review is told and the authorization stands for a
retry; nothing failed is delivered. A validation that could not decide waits for a person
(`settle_manual_review`). Every export has an `ExportReport` (a library document) with the exact
Resolve project and timeline, the authorization, the render, the validation and the delivery.

`deliver` needs the validated state and, when the project requires it (the default), a person's
delivery approval. It writes `<project>_v<version>_<profile>_<number>.<ext>` plus a details file into
the delivery folder (`<data dir>/deliveries`); a name that exists is never reused.

## Not done / limits

* **Not run against a real DaVinci Resolve**; everything above that touches Resolve is as real as
  `resolve_adapter` (see its README). The render backend stand-in and fake measurements are what the
  unit tests use; the file checks are verified with real FFmpeg in `export_validation`'s tests.
* No platform delivery: nothing is uploaded or published.
* Resolve's scripting cannot pause a render, set bitrate, sample rate or colour per export, or apply a
  mix; those show as limitations in the render report.
