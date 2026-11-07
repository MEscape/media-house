# quality_control (Layer 6, module 29)

Looks at a built timeline (and, when it is given one, at a preview render of it) and reports what is
wrong, with the evidence. It reads, measures and reports. It never changes a timeline, decides
nothing creative, approves nothing, and does not repeat analysis another module owns.

## What it checks

| Area | From | Examples |
| --- | --- | --- |
| structure | the built timeline | offline or unresolved clips, gaps and overlaps on the main picture, a clip past the end, stretched or heavily enlarged clips, transitions that cannot run, no sound, assumed frame rate; the builder's own errors, carried once |
| intent vs result | the compiled specification, the Resolve binding, optionally the live Resolve timeline | an element the specification lists that is not on the timeline, a binding of an older version, open conflicts, edits made in Resolve and not read back |
| editing | the edit | flash frames, repeated footage, shot lengths, jump cuts, weak opening, abrupt ending, long static stretches (opinions are marked `subjective`) |
| captions, graphics | cues, clips, the creator identity | reading speed, density, overlap, empty cues, missing fonts or brand assets, graphics outside the frame, in no zone or on top of each other, inconsistent styling |
| picture and sound of the render | the preview, through the shared `MediaAnalyzer` | black frames, frozen frames, silence where sound is planned, decode errors, size, rate and duration against the timeline, loudness, true peak, clipping, dialogue level jumps, an abrupt end, exposure, soft focus, colour jumps at cuts, unexpected borders |
| plans upstream | stored results through their contracts | the mix engine's validation, the continuity analysis, the story plan (missing sections, order, setup without payoff) |

A black frame, silence or a freeze that the timeline explains (a dip to black, a freeze frame, a
still image, a stretch with no planned sound) is **not** a finding: what is measured and not
explained is.

## Findings (`QualityIssue`)

`issue_id` (stable per version), `fingerprint` (stable for the same problem in the same place, so a
later report can tell "still there" from "new" and a rejected suggestion is not made again),
category, `severity` (`critical` > `high` > `medium` > `low` > `optional`), `confidence`, `basis`
(`objective` or `subjective`), timeline frames, affected clips/tracks/sections/source assets,
description, why it matters, evidence (words, named `measurements`, and frames kept in the Media
Library for the exact version), a suggested correction and the module that owns it, `blocks_export`.

Two guards hold for every finding (`domain/classify.py`): a subjective finding is never worse than
MEDIUM and never blocks the export; a CRITICAL the check is not sure of (confidence under 0.6) is
reported as HIGH. `blocks_export` is only a sure, objective CRITICAL.

## The report (`QualityReport`)

Immutable, versioned (`report_version`, `supersedes`), bound to one timeline version and one
preview. Its id derives from everything it rests on, so the same inputs give the same stored report
(`created=False`) and a changed input a new one. It has an indicator per area (an area nobody checked
is `not_checked`, never `ok`), the checks that ran and the ones that did not (and why), the severity
summary, the export-blocking ids, and `approval: "not_approved"`. `compare_reports(before, after)`
says what is resolved, still there, worse and new.

## Use

```python
result = analyzer.execute(AnalyzeQualityCommand(anchor_asset_id=..., preview_asset_id=...,
                                                preview_timeline_hash=..., review_session_id=...), ctx)
result.value.report, .asset, .created, .comparison
QualityCatalog.latest(anchor, timeline_id=..., timeline_content_hash=...)
```

* Without a preview only the timeline is checked and the picture and sound checks say `not_run`.
* A preview rendered from another version (`preview_timeline_hash`) is refused: it would be shown
  as evidence of the wrong video.
* With a review session the report is attached to it and the review moves to `review_ready`; the
  session must be at the current version. After a revision (`revalidation_pending`) the report is
  attached and the revision engine decides what follows.
* Pipeline task `quality_control` (opt-in, `--stages quality_control`, needs `timeline_build`).

## Not done / limits

* **Validated** on the builder's real fixtures, synthetic measurements and one real pipeline run
  with FFmpeg-made previews (black frames, missing sound). Not validated on real footage; thresholds
  (`domain/profile.py`) are uncalibrated and versioned data.
* Focus, composition, subject clipping, flicker, spelling of captions and text inside rendered
  graphics are **not measured**. Pictures are judged from a bounded sample of frames.
* Picture checks need a preview; the module does not render one (the render manager does).
* The live Resolve look goes through `resolve_adapter`, which has not run against a real Resolve.
