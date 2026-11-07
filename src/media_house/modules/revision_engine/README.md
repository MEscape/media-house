# revision_engine (Layer 6, module 30)

The feedback loop of Media House. It turns the findings of a quality report and a person's own words
into proposals, each with its owner, the level of approval it needs, what it touches and what it
conflicts with; it carries out **only what a person approved**, through the owner's own contract,
after a checkpoint; and it checks the result on the next report. It decides nothing creative and
never overwrites a person's work, a locked section or a decision.

```text
quality_report + feedback ──► PlanRevisions ──► revision_plan ──► a person decides (DecideRevisions)
                                                                     │ approved
   a person's Resolve edits are read back first                      ▼
   checkpoint ◄── ExecuteRevisions ── begin_revision ── owner contracts (builder, mix engine, Resolve adapter)
                      │                                              │
                      └── record result ── quality control again ── validate (resolved / still there / new)
```

## Levels of approval

`A` automatic (a reversible technical operation the project granted in advance), `B` suggested (a
creative change a person approves), `C` manual only (deleting main content, replacing something
already approved, a locked section, a person's own edit, a rollback, the final export). A level is
never raised or lowered by confidence: it comes from the operation's permission in the review's
`PermissionPolicy`, and a conflict or an approved section makes it `C`.

## Planning (`domain/planner.py`)

A table maps each finding to a change. Concrete changes: close a gap or an overlap by a trim of the
named clip, rebuild the timeline from its specification (a missing file is looked up again), change
the level of a dialogue clip or a bus, mix again, bring Resolve and the timeline together, restore a
checkpoint. Everything else (pacing, captions, B-roll, transitions, graphics, reframing, continuity,
sound design, source repair, a re-render) is a **hand-off**: the owner and the request are named and
the change is passed on as an event; this module does not drive those planners and says so. Findings
that become no change are listed with the reason (`Skipped`). A rejected proposal is remembered by its
stable key (`proposal_key`, which includes the severity), so it is not proposed again unless the
finding got worse or the person asks (`reconsider`).

## Feedback (`domain/feedback.py`)

Deterministic patterns, no model. One instruction per clause, with the original words kept. It tells
a directive from a preference, a question, a rejection, an approval, a request to lock and a "keep".
`this section`, `the introduction`, `the ending`, `the music here`, `the previous shot`, `this
transition`, a timestamp and a named section are resolved from the playhead, the selection and the
timeline; when a reference could mean more than one thing the instruction carries a clarification
with the options and nothing is guessed. A lock is carried out at once (it only protects).

## Execution and the other guards

* Before anything changes, the person's edits in Resolve are read back (they are the baseline) and the
  review is synced: an edit since the approval makes the plan `stale` and nothing runs.
* A checkpoint is made and read back to prove it is usable; an unverified one stops the revision
  unless a person says to go on.
* Changes run in plan order through the executors; the first failure stops the rest. A failed or
  skipped change is never marked as done.
* A change is `resolved` only when the finding it answered is gone from the next report; new or worse
  findings are regressions and send the review to `review_again_required`.
* Provenance: edits made here are recorded in the builder as made by `revision_engine:<revision>` on a
  person's approval, so a later build keeps them and they can be told from a person's own edits.
* A rollback is a revision of its own (level `C`): proposed, approved by a person, carried out by
  restoring the checkpoint's clips (as builder edits) and the mix overrides of that time.

## Not done / limits

* **Hand-offs are not executed here.** A person (or the owner's planner, re-run through the
  pipeline) carries them out; they show as `not_checked` in the validation. No pipeline-stage
  re-run executor is wired.
* Rollback restores clips (position, length, opacity, enabled, gain) and the mix; it does not
  restore transitions, tracks or a specification that the builder takes only from the specification.
* Dependency invalidation is stated (`invalidates`), and the producers' own fingerprints do the
  reuse; this module does not invalidate caches itself.
* Verified through the real composition root with the real mix engine and timeline builder; the
  Resolve read-back is `skipped_unavailable` without a Resolve and has not run against a real one.
