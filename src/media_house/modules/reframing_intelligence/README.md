# Reframing Intelligence

Recommends how each shot of a recording should be FRAMED for an output format: a crop window and, where the
subject moves, keyframes that move it; or the original framing; or an explicit statement that no crop can keep what
the shot needs. It answers *what should the window be, why, what does it cost, and what else could serve*. It
recommends only: it never modifies media, renders, touches a timeline backend or decides the edit.
`editing_intelligence` decides what to apply; a later timeline stage executes it.

## Public API (`application/contracts.py` is the only importable file)

```python
command = AnalyzeReframingCommand((asset_id,), TargetFormat.of("9:16"), ReframingConfig(profile="talking_head"))
result = analyzer.execute(command, ctx)  # blocking (library reads): run it as a job
analysis = result.value.analysis  # ReframingAnalysis; result.value.created is False on a cache hit
for rec in analysis.covering(asset_id, start, end):  # what applies to a source range
    rec.strategy, rec.feasibility, rec.executable, rec.camera_motion
    rec.keyframes, rec.crop_at(seconds)  # the window, as keyframes in source time
    rec.scores, rec.confidence, rec.resolution, rec.reasons, rec.tradeoffs, rec.preferred_over
    rec.elements, rec.intervals, rec.issues, rec.alternatives, rec.evidence
analysis.unreframeable(), analysis.pairs, analysis.metrics, analysis.limitations
ReframingCatalog.latest(asset_id, target="9:16")  # read a stored analysis
```

Inputs are READ, never produced (architecture test): the stored picture analysis (`VideoCatalog`: tracks, quality), the
stored inspection (`InspectionCatalog.find`, never probes), the alignment (`AlignmentCatalog`: the clock), the story plan
(`StoryCatalog`: beats and visual needs) and the continuity analysis (`ContinuityCatalog`). Only the picture analysis is
required (`MissingPrerequisite` without it); everything else degrades to a recorded `not_available`. No model is run:
`metrics.model_calls` is 0.

## Coordinates and time (the contract a timeline stage relies on)

* A position is a fraction (0-1) of the source picture in DISPLAY orientation (rotation applied, pixel shape corrected),
  x to the right, y downwards, origin top-left: the same convention `video_intelligence` uses for its boxes.
* A crop is `Crop(x, y, w, h)`: the window's TOP-LEFT corner and size, in that source frame. It is scaled uniformly to the
  output resolution; nothing is stretched. It lies inside the picture (enforced by the constructor).
* A crop is valid when its aspect in display pixels, `(w x display_width) / (h x display_height)`, equals the target's within
  `ASPECT_TOLERANCE` (0.2%, the half-pixel of an integer grid). `crop_problems` checks it; the analysis is never stored
  with an invalid crop (`validate_analysis`).
* The widest crop of a shape (zoom 1) uses a full side of the picture: landscape to vertical keeps the full height, portrait
  to landscape the full width, a source already of the target's shape is the whole frame (`ORIGINAL`). Nothing is cropped
  away that the shape does not force.
* Times are exact `Rational` seconds on the recording's common clock: `pts x time base` of the analysis plus the alignment's
  video correction (0 and `clock_corrected=False` without one). The frame index is never the address, so a variable frame
  rate cannot skew a time. Timeline places never appear here: the editing stage places a source range, a timeline
  builder maps source time to timeline time.
* Keyframes: the first lies at the start of the shot's range, none outside `[start, end]`, strictly increasing. Between two
  keyframes the FIRST one's interpolation applies: `hold` keeps its crop until the next keyframe, `linear` moves x, y, w
  and h linearly (the aspect stays exact). After the last keyframe its crop holds. No movement crosses a cut: every shot
  has its own keyframes. `Recommendation.crop_at(seconds)` implements exactly this.

## Effective resolution (`domain/geometry.py`)

The crop covers `w x sample_width` by `h x sample_height` STORED samples (rotation applied, pixel shape not); the output has
`target.width x target.height` pixels. `scale = max(target.width / samples_across, target.height / samples_down)`: output
pixels per stored sample on the worse axis (>1 enlarges, 1 is one to one). A pixel shape other than square therefore shows
up as an enlargement on one axis even for the whole frame, which is true of the footage. The stored size and pixel shape
come from the stored inspection when it describes the same display size; otherwise the display size stands in and the
recommendation says so (`geometry_exact=False`).

Policy (profile `quality`): up to `upscale_ok` nothing is said; up to `upscale_max` it is a stated trade-off; beyond, or
enlarged footage whose usability is below `min_usability`, the crop is `low_quality`. Enlargement of footage with no
quality measurement is `uncertain`. Nothing is sharpened, denoised or stabilised here.

## What a shot is judged against (`domain/elements.py`)

Only observed evidence decides. The main subject is the shot's main subject track (else the largest person); a person who
does not fit the window is replaced by their face when one was tracked inside them; other people are PARTICIPANTS (required)
when tall enough and present long enough; an object is required only when the story's visual need names it (word match
between the track label and the need's concept); everything else is context. No hands are tracked upstream: a
demonstration that needs hands says it cannot protect them (`not_tracked`), it does not guess a box. Semantic importance
is never invented.

## Strategies

`original_framing` (no change: the source already has the target's shape and nothing forces a change), `static_crop`,
`static_crop_repositioned` (one still crop with the subject placed off-centre), `dynamic_crop`, `push_in`, and `pull_out`
(only the CLASSIFICATION of a zoom that widens, which happens when a push-in must widen because required elements grow: the
planner never plans a pull-out for its own sake);
and, when nothing carries the shot, `unreframeable` (or `fill_treatment` when the profile allows the proposal). Alternatives
are listed with what each would need and are never executable here: `wider_source` and `alternative_range` (other shots of
the SAME picture analysis: same scene and wider, or another take, each evaluated), `alternating_crops` (one crop per person,
needs per-moment speaker timing the analysis does not state), the main subject alone (names what it omits and needs a
decision to drop it), and `fill_treatment`. A split screen is never proposed: no stage can carry it.

## Planning (`domain/planner.py`, `domain/camera.py`)

1. The window starts as wide as the shape allows. A tighter zoom exists only for a slow push-in a story beat the profile
   wants emphasised earns, and only while the quality policy allows it.
2. One still window is preferred: used when one position keeps every required element inside (with the profile's margin)
   and the subject never leaves the dead zone. Otherwise the window follows.
3. Following (deterministic): noisy positions are smoothed (triangular average over `smoothing` seconds); the window waits
   inside a dead zone, then follows at the subject's own pace keeping it at the zone's edge, eased by `smoothing` and moved
   ahead by the subject's observed velocity (anticipation from motion that was OBSERVED, not predicted); it never moves
   faster than `max_speed` (crop widths per second) or zooms faster than `max_zoom_rate`; a change of direction waits
   `min_dwell` seconds (that is what suppresses jitter). Limits are propagated backwards at the speed limit so the window
   starts early enough to be where a required element must be held.
4. Containment beats the limits: if a required element would leave the window the window moves faster than the limit and
   the recommendation says so (`speed_limit`).
5. A gap (no observation for longer than `max_gap`) is never interpolated across: the window holds and the interval is
   reported (`TrackingInterval`, `tracking_gap`). Observations below `min_confidence` are not used. Between observations the
   path is interpolated only across gaps no longer than `max_gap`; the window is never claimed to follow frame by frame.
6. Keyframes are the fewest points whose straight lines stay within `key_tolerance` of the path.

## Composition, safe areas, neighbours

Composition is a PREFERENCE (profile `composition`: where the subject sits across the output, headroom, lead room): the
`composition_score` is a heuristic, never proof of quality, and an already-suitable frame is left alone. Lead room
comes from the subject's travel, else from head yaw only when a profile asks for it (the sign convention is assumed, not
validated upstream; the reason says so).

Safe areas are CONFIGURATION (`ReframingConfig.safe_areas`: reserved rectangles of the OUTPUT picture), never a profile and
never assumed: with none, `caption_safe_area_score` is `None` and the stage says so. With some, the window slides VERTICALLY where the
picture leaves room to keep required elements out of them (nine candidate shifts, least overlap wins), else the overlap is
reported.

Neighbours: two shots that may meet in a cut are compared by the main subject's size and place and by headroom. The pairs
are the cuts a stored continuity analysis judged (real) and, where none covers them, recording order (an ASSUMPTION the pair
records in `origin`). A size step across a cut the analysis judged a jump cut `hides` it; unchanged crops across one are a
`continuity_risk`; an abrupt step across an ordinary cut, two push-ins in a row are warnings. Different people are not
compared. Continuity detection itself is not repeated here.

## Scores and confidence

Separate, each in [0, 1] or `None` with a reason; definitions and `basis` (`observed` / `measured` / `estimated` /
`heuristic`) in `domain/values.py`: `subject_visibility_score`, `composition_score`, `crop_feasibility_score`,
`effective_resolution_score`, `tracking_confidence`, `edge_clipping_risk` (lower is better), `multi_subject_visibility_score`,
`caption_safe_area_score`, `continuity_compatibility_score`, `motion_smoothness_score`, `reframing_need_score` (higher =
more need). `recommended_crop` is `rec.keyframes[0].crop`, `recommended_camera_motion` is `rec.camera_motion`. `confidence`
is how well the evidence covers the shot (coverage x tracker confidence x quality known), ordinal, never a probability, and
`None` without subject evidence. Feasibility keeps four things apart: `impossible` (nothing of the shape keeps what is
required), `low_quality` (valid, too soft), `feasible_with_tradeoffs` (a stated cost) and `uncertain` (missing evidence: a
placeholder that is NOT executable).

## Profiles and configuration (`domain/profile.py`)

`generic talking_head interview_podcast product_review tutorial shorts_reels cinematic` are data (composition, motion,
emphasis, quality policy, participants, adjacency); the story format picks one. A profile changes the recommendation, never a
measurement. `ReframingConfig(profile, custom, safe_areas)` and `TargetFormat.of("9:16", width=, height=)` are the whole
configuration (standard 16:9, 9:16 and 1:1 have standard sizes; a custom aspect names its own).

## Storage, cache, incremental analysis, decisions

A JSON document `reframing_analysis` (schema 1), a derived asset of the first recording, found by target and profile through
`ReframingCatalog`. Cache key = every shot's fingerprint (its tracks, quality evidence, range, the source geometry, the target
format and resolution, the configuration incl. safe areas, the beats and needs overlapping it, versions) + story and
continuity identity + decisions: an identical request returns the stored analysis (`created` False); anything that changes the
answer (target, resolution, safe area, profile, a track, the story) makes a NEW analysis (`analysis_version`, `supersedes`;
earlier ones are kept). A shot whose own fingerprint is unchanged is carried over WITHOUT planning again (same id).
A person's accept/reject (`SegmentDecision`, keyed `asset_id:shot_id`) follows the segment through every reanalysis and
never changes the proposal.

## Pipeline and editing

Task `reframing` (`--stages reframing --reframe-target 9:16 [--reframe-resolution WxH] [--reframe-profile P]
[--reframe-safe-area NAME=x0,y0,x1,y1 ...]`): light lane, needs `video_analysis`, wants story, alignment and inspection.
**Opt-in**, and skipped (and says so) until a target is given: a crop means nothing without a format. `edit_plan` wants it, so
within a run the editor is handed the analysis; otherwise it reads the stored one through `ReframingCatalog`
(`PlanEditCommand.reframing_target` picks the format). The editor attaches to every picture clip a `FramingRef` per overlapping
recommendation (id, analysis, target, strategy, feasibility, executable, the part of the range it covers) and raises an issue
(`IssueKind.FRAMING`) plus a note (`level="framing"`) for a clip no crop serves, a soft crop or an uncertain one, with the best
alternative the analysis named. The crop and keyframes stay HERE: a timeline stage fetches them by `recommendation_id`.
Nothing is cropped or decided in the edit plan.

## Cost

Reads stored results; no model, no decoding. About 3 ms per shot of 50 observations; a ten-minute shot with 6000
observations about 0.4 s; an unchanged library is not planned again (about 0.6 ms per shot to fingerprint).

## Evaluation and known limitations

* `tests/support/refr_eval.py` (`uv run python -m tests.support.refr_eval`): 15 labelled SYNTHETIC situations, an
  independent recomputation of validity, clipping, enlargement and speed, and a stability check under a tiny perturbation of the
  tracks. Outcome agreement, valid crops and stability are 1.0 by construction: this guards regressions and says NOTHING about
  real footage, a real tracker or how a picture looks. **No real footage was evaluated; every threshold is an uncalibrated
  design choice.**
* Positions are only as good as the tracks: sampled at the analysis' rate, whole-person boxes, no hands, no per-person speaking.
* Story times are used as given (the audio clock is not shifted by the alignment); the picture clock is.
* Alternatives come from the same picture analysis only; other recordings and B-roll are not searched. Split screen, fill and
  alternating crops are proposals no stage can render.
* Neighbour pairs by recording order are an assumption; the editing stage may join shots differently.
* A recommendation is planned for one target at a time; a vertical and a landscape deliverable are two analyses.

## Testing

```
uv run pytest tests/unit/reframing_intelligence
uv run pytest tests/unit/editing_intelligence/test_framing_input.py tests/unit/media_pipeline/test_reframing_task.py
uv run pytest tests/integration/reframing_intelligence   # real library, FFmpeg
uv run pytest tests/architecture/test_reframing_intelligence_boundaries.py
```

Builders: `tests/support/refr_fakes.py` (geometry, tracks, one-call plans), `tests/support/refr_eval.py` (the labelled set).

## Adding things

* **A profile**: add it to `PROFILES` (and `STORY_FORMAT_PROFILE`), bump `ANALYZER_VERSION`.
* **A strategy or issue kind**: add it to `values.py`, bump `VOCABULARY_VERSION` and `ANALYZER_VERSION`.
* **A threshold**: it is a named field of a profile or a constant in `planner.py`/`verdict.py`; bump `ANALYZER_VERSION`.
* **Hand tracking, speaker timing**: extend `video_intelligence` / `audio_visual_alignment`, map it in `application/views.py`
  into `domain/facts.py`; the planner needs no change to protect a new kind of element.
