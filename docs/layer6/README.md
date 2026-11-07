# Layer 6: review and refinement (architecture)

Layer 6 inspects the constructed video, finds what is wrong, coordinates the corrections, prepares the
final presentation, manages the renders, checks the file that was really exported and controls the
final export. **A person stays the creative authority: nothing is exported, delivered or approved
because an automated check passed.**

Modules (one README each under `src/media_house/modules/`): `review_orchestrator` (shared state),
`quality_control` (29), `revision_engine` (30), `final_polish` (31), `render_manager` (32),
`export_validation` (33), `resolve_export` (34). Plus one shared capability in `core`:
`MediaAnalyzer` (see below).

```text
Layer 5: built_timeline ── audio_mix ── resolve (project) ─────────────────────────────┐
                                                                                        ▼
                  review_orchestrator (one review state, versions, approvals, locks, checkpoints, history)
                       ▲                ▲                  ▲                 ▲                ▲
 preview ──► quality_control ──► revision_engine ──► final_polish ──► render_manager ──► export_validation
   (render_manager)   report        plan/decide/        final review      FINAL render        file check
                                    execute/rollback    package           (authorized)             │
                                                                                                   ▼
                                                    resolve_export: present → approve → authorize → run → deliver
```

## Architecture assessment (what existed, what was added)

| Needed | Existed | Done |
| --- | --- | --- |
| the constructed timeline and its versions | `timeline_builder` (`build_version`, `build_hash`, stored builds, `HumanEdit`, provenance) | reused as THE timeline version; no second model |
| human edits and what they changed | `HumanEdit`, `EditRecord`, `SyncFromResolve` | reused: revision edits are recorded through `TimelineEditor`, a person's edits read back first |
| audio mix with overrides | `audio_mix_engine` (`MixOverrides`, stems as cache) | reused; **extended**: `MixCatalog.find_spec` (the newest stored specification is not always the one in use) |
| rendering and Resolve | `resolve_adapter` (`RenderTimeline`, binding, sync) | reused through its contract; never a second connection |
| file measurements (black, frozen, silence, loudness, decode, frames) | four private `ebur128` parsers, nothing shared | **new core port** `MediaAnalyzer` + FFmpeg adapter (one implementation for quality control, polish, the render check and export validation) |
| review state, approvals, locks, checkpoints, audit | nothing | `review_orchestrator` (own module, own small `review.db`; `shared/` is capped) |
| quality report | builder checks that need a render were `not_run` | `quality_control` fills them (`ExternalCheck` interface stays the builder's) |
| revisions | nothing | `revision_engine` |
| render profiles, queue, section previews | `resolve_adapter` has two profiles | `render_manager` (versioned profiles, limits stated when Resolve cannot apply a setting) |
| independent check of the exported file | `resolve_adapter` compares a render with the timeline | `export_validation` (full decode, measurements, comparison with the approved render, delivery profile) |
| the human barrier | nothing | `review_orchestrator` (authorization) + `resolve_export` |

Responsibilities that stayed where they were: story, pacing, B-roll, continuity, reframing, captions,
graphics, transitions, VFX, sound design, mixing and source repair remain the owners'. The revision
engine names the owner of a change and, for the owners it cannot drive, hands the request over.

## One review, one version

Every report, plan, render, validation and decision names the exact timeline version it is about. The
orchestrator binds a review to a `TimelineRef`; a decision made on another version is refused; a
change to the timeline withdraws what depended on it (see its README). A `quality_report` has
`approval: "not_approved"`; a `final_polish_report` has `ready_*` and never `approved`; the only
approvals are decisions a person made in the review.

## The loop

1. `quality_control` reports on the timeline (and a preview) → attached to the review.
2. A person reviews in Resolve and speaks (`add_feedback`); `revision_engine` plans (findings +
   words), a person approves, rejects or modifies each proposal.
3. Approved changes run through the owners after a checkpoint; the new version is checked again and
   compared with the report the plan was made from. A rejected proposal is not made again.
4. `final_polish` checks the finished presentation → the review holds a final review version.
5. `resolve_export.present` → a person approves the final video → authorizes the export for one
   output → `run` (FINAL render under the authorization) → `export_validation` → a person approves the
   delivery → `deliver` (copy; never replaces; never uploads).
6. Any timeline change after approval withdraws the approval and the authorization.

There is no score-driven loop: no module changes the video until a number is reached.

## Configuration of the permissions

`PermissionPolicy` (per review): which operations may run unattended (`AUTOMATIC`, default none beyond
render preparation), whether delivery needs a second approval (default yes), whether HIGH issues block
the export (default yes). The floors cannot be lowered: the final export, delivery approval, unlocking
and overriding a blocker are always a person's.

## How to run

```text
uv run python scratch/run_pipeline.py --home H --input clip.mp4 \
    --stages timeline_build,audio_mix,quality_control --output-format 16:9
```

The interactive part (review, feedback, approvals, export) is API: the contracts of
`review_orchestrator`, `revision_engine`, `final_polish`, `resolve_export` (no UI is part of Layer 6).
`tests/integration/revision_engine/test_revision_loop.py` is a runnable example of the loop.

## Tests

`tests/unit/{review_orchestrator,quality_control,revision_engine,final_polish,render_manager,
export_validation,resolve_export}`, `tests/integration/{test_review_store,test_ffmpeg_media_analyzer,
test_layer6_wiring,test_render_and_export_validation}.py`, `tests/integration/{quality_control,
revision_engine,final_polish}` (real composition root, real FFmpeg, real mix engine and builder) and one
architecture test per module (`tests/architecture/test_*_boundaries.py`; among them: only
`resolve_export` may request a FINAL render, the final render always carries the authorization, nothing
in Layer 6 uploads, quality control and the polish change nothing).

## Not done / limits

* **No real DaVinci Resolve.** Everything that touches Resolve (execution, read-back of a person's
  edits, the live look, rendering) is exactly as unverified as `resolve_adapter` (see its README).
  Mocks never stood in for the owners except Resolve itself and, in unit tests, media measurements.
* **No listening or viewing evaluation.** Findings are measurements and rules; thresholds are
  uncalibrated, versioned data. Focus, composition, flicker, caption spelling and text inside rendered
  graphics are not measured.
* **Hand-offs are not executed**: pacing, captions, B-roll, transitions, graphics, reframing,
  continuity, sound design and source repair changes are passed to their owners (an event), not
  carried out by `revision_engine`; they show as `not_checked` until a new report.
* Resolve's scripting cannot pause a render, set bitrate / colour / metadata per export or apply a
  mix; the render report states each as a limitation.
* `media_library` / `media_inspection`: two assets with identical bytes cannot both be inspected (the
  second inspection fails on a content-based fingerprint). Layer 6 survives it (a file that cannot be
  inspected counts as not verified, never as a pass) but the cause is upstream.
* A person's decision is the authority; no part of Layer 6 can be configured to export on its own.
