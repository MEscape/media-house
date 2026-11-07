# final_polish (Layer 6, module 31)

The last look at the finished presentation, on the exact version the review is at. It does not
reopen creative decisions, fix anything, render or export, and it does not approve the video: its
report says how ready the final review is, and a person approves.

It reuses and restates, with the source named, what quality control and the audio mix already found
(visual, graphic and caption consistency, audio refinement, transitions' structure) and adds what only
the finished presentation shows:

| Section | Checks |
| --- | --- |
| opening and ending | picture starts at the first frame and runs to the last, an opening with nothing on screen, an ending with no fade, text that appears a few frames before the end |
| transitions | transitions too close together, a transition that swallows its shot, many different kinds |
| placeholders and leftovers | placeholder text in captions and names, unresolved elements, disabled clips, open review markers |
| final frames | the first and last picture of a preview (black without a planned fade) |
| presentation validation | the report is of the review's version, no export-blocking issue is open, approved revisions are carried out, **no rejected change was applied**, locked sections a person changed (the lock holds), a person's edits counted as kept |

## Report and readiness

`FinalPolishReport` (nine sections, remaining issues, recommendations, the approved revisions it
includes, and a `FinalReviewPackage`: version, review version, Resolve project and timeline, the
quality report, representative frames, what is outstanding). `readiness_status` is
`ready_for_final_review`, `ready_with_warnings` or `not_ready`; it is never `approved`. A section
that could not be checked says `not_checked` and why.

With a review session it needs the quality report of that version (it asks quality control for one if
there is none), moves the review to `final_polish_pending`, attaches both reports (the review then
holds `final_review_ready`) and, when the video is `not_ready`, to `review_again_required`. An
identical polish is not made twice.

Small finishing changes are **not automated**: any fix goes through the revision engine.

## Not done / limits

* Pictures: only the first, the last and the middle of each section are looked at, and only when a
  preview of this exact version is given. Safe-area compliance and text clipping inside rendered
  graphics are not measured.
* Brand colour and typography consistency are what quality control reports from the creator identity;
  no second identity system exists here.
* Validated on synthetic facts and one real pipeline run (no real footage).
