# export_validation (Layer 6, module 33)

Independently validates the file that was **actually exported**. It never trusts a render's success
status: it inspects the file, decodes it to the end, measures black frames, frozen frames, silence,
loudness and peaks, samples pictures and compares them with the same moments of the approved review
render, and checks the file against the **approved** timeline version and the delivery profile.

It changes nothing, re-edits nothing, re-renders nothing and advances no review: it returns a
report with one of four verdicts and, for every problem, who owns the fix. `resolve_export` reads
the verdict and records it in the review.

> **Status.** Verified on real FFmpeg-made files (a good export passes; a truncated file, a file
> without sound, a wrong size, a wrong frame rate, black frames and a wrong length each fail) through
> the real inspector and `MediaAnalyzer`, and with fakes for everything else
> (`tests/unit/export_validation`, `tests/integration/test_render_and_export_validation.py`). Never
> run on a file rendered by a real DaVinci Resolve.

## Contract (`application/contracts.py`)

```python
outcome = validator.execute(
    ValidateExportCommand(
        output_asset_id=...,
        approved=ref,  # the version a person approved (has a build_asset_id)
        delivery_id="youtube_16_9",
        review_session_id=...,
        export_id=...,
        authorized_config_hash=...,  # what the export was authorized for
        reference_render_asset_id=...,  # the approved review render
        intentional=(IntentionalSpan("black", 1.9, 3.1),),
    ),
    ctx,
)
outcome.value.status  # PASS / WARNING / FAIL / MANUAL_REVIEW_REQUIRED
outcome.value.review_status  # the same verdict as the orchestrator's ValidationStatus
outcome.value.report  # detected_problems, not_checked, checks, area_status, measurements
ExportValidationCatalog.history / get / latest_for_export
```

## The verdict (`domain/rules.py`, deterministic)

* **FAIL** a CRITICAL finding, or a HIGH one that is CONFIRMED.
* **MANUAL_REVIEW_REQUIRED** else, anything that needs a person: a finding that says so, a HIGH one
  that is not confirmed, a POSSIBLE one of MEDIUM or above, or a **mandatory check that was not
  made** (no full decode, no loudness, no reference render to compare pictures with, no stream
  inspection). It is never turned into PASS.
* **WARNING** else, any finding. **PASS** nothing found and everything mandatory was checked.

Every finding has severity (critical/high/medium/low), certainty (confirmed/likely/possible), the
module that owns the fix (`route_to`) and a next action (`rerender`, `revise_timeline`,
`remix_audio`, `investigate`, `reexport`, `manual_review`). A render-setting problem (size, rate,
codec, container, audio format, drops) goes back to `render_manager`; a loudness or peak problem to
`audio_mix_engine` (via `revision_engine`); black frames or silence the plan did not declare to
`quality_control` with `investigate` (the cause is not known from the file); a delivery length limit
to `revision_engine`; a wrong timeline version / other output settings to `resolve_export`.

## What is checked

* **Integrity**: file exists, size, readable, **a full decode to the end** (a container probe is not a
  decode; a file whose decoder ends before its stated duration is `truncated`).
* **Video**: resolution, frame rate (declared and measured), codec, pixel format, aspect, dropped
  frames, discontinuities, duplicate timestamps, frame count, colour tags, black stretches, frozen
  stretches (unless declared intentional).
* **Audio**: stream, codec, rate, channels, duration against the picture (an early end is an abrupt
  cut), start offset (sync), integrated loudness against the delivery's target and tolerance, true
  peak, clipping, loudness range, unexpected silence.
* **Pictures**: frames of the file at the opening, the ending and the middle of each section, compared
  with the same moments of the approved review render.
* **Delivery**: container, maximum duration and size, required metadata, naming rule.
* **Approved timeline**: the render report of the file (which timeline version made it; a render of
  another version, another timeline, other output settings or another review session fails), and the
  duration against the approved build. The approved version is read from its **stored build**
  (`TimelineRef.build_asset_id`), never from "the latest".

## Delivery profiles (`domain/profile.py`)

Versioned data (`youtube_16_9`, `youtube_shorts`, `instagram_reels`, `square`, `master`). Each names a
render manager profile (and version); what the output IS comes from that profile, so the two cannot
disagree. A delivery adds loudness/peak/length/size/metadata/name rules and the thresholds the
measurements are judged by. Platform limits are data, not code; the shipped numbers are examples to
adjust, not claims about a platform's current rules.

## Reuse and storage

The report is a `export_validation_report` library document of the recording. Its cache key is the
file checksum + delivery profile + approved timeline ref + analyzer identity + validator version +
declared spans + reference render + authorized hash: an unchanged file validated again is not
measured again (`reused`); `force=True` measures again. The report names the file, never an absolute
path. The report is recorded in the review (`ArtifactKind.EXPORT_VALIDATION_REPORT`).

## Not done / limits

* **Text clipping, safe areas, debug elements, missing graphics and captions** cannot be judged from
  sampled pictures; the report says so (`not_checked`, optional) and the review in Resolve covers them.
  Picture comparison is a coarse luma similarity at a few moments, not a frame-exact diff; a
  duplicated or missing section that happens to look like the approved one at every sampled moment is
  not found (the duration check usually is).
* The picture comparison needs an approved review render; without one the verdict is
  `MANUAL_REVIEW_REQUIRED` by design.
* Intentional black/silence/freezes must be passed in (`intentional`): nothing here knows the plan.
* A file whose streams cannot be inspected (e.g. an inspection already stored for identical bytes
  under another asset: the library refuses a second one) is `not checked` for its stream facts and
  needs a person; the analyzer still catches missing sound and decoding errors.
* Loudness is measured once over the whole file (no short-term/momentary series).
