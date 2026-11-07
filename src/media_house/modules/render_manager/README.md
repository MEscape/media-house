# render_manager (Layer 6, module 32)

Renders a timeline for review or delivery through one backend port, keeps the **versioned render
profiles**, queues and controls render jobs, **checks every finished file**, and keeps a report of
every attempt. It decides nothing creative, approves nothing, and **never starts a final render
without a person's export authorization**. It judges no delivered file (that is `export_validation`).

> **Status.** Verified with fakes for the backend (no DaVinci Resolve was available) and with real
> FFmpeg, the real inspector and the real library for the file checks and the section cutter
> (`tests/integration/test_render_and_export_validation.py`). The Resolve backend adapter
> (`infrastructure/resolve_backend.py`) is only as real as `resolve_adapter` is (see its README):
> it has never rendered on a live Resolve.

## Contract (`application/contracts.py`)

```python
outcome = renders.render(
    RenderRequest(anchor_asset_id=..., render_type=RenderType.REVIEW, profile_id="review_fast"), ctx
)
outcome.value.succeeded  # the job completed AND the file was looked at and passed
outcome.value.report  # RenderReport: profile@version, backend@version, checks, limits...

profiles.config_hash("youtube_16_9", anchor_asset_id=...)  # the output_config_hash (see below)
renders.render(
    RenderRequest(
        ...,
        render_type=RenderType.FINAL,
        authorization=RenderAuthorization(session_id, authorization_id, ref),
    ),
    ctx,
)
queue.enqueue(request)
queue.run_next(ctx)  # priority queue, one at a time
renders.cancel(render_id)
renders.retry(anchor, render_id, ctx)
renders.pause / resume
RenderCatalog.history / latest / for_output / get  # stored reports
```

Blocking: run as jobs. A completed job whose file fails its checks is a `RenderOutcome` with
`succeeded == False` (not an exception); a request that must not run is `Err(RenderRefused)`.

## Profiles (`domain/profile.py`) and `output_config_hash`

Versioned data: `youtube_16_9`, `shorts_9_16`, `reels_9_16`, `square_1_1` (delivery),
`review_fast`, `review_proxy` (previews, proxies allowed), `intermediate` (ProRes 422),
`master_hq` (ProRes 422 HQ). Visual (`VideoSettings`) and audio (`AudioSettings`) are separate; a
custom `RenderProfile` is validated on construction. A size/rate a profile leaves open is the
timeline's. `output_config_hash(resolve_settings(profile, width, height, frame_rate))` is the
identity of an OUTPUT: `RenderProfiles.config_hash(profile_id, anchor_asset_id=...)` computes it from
the latest timeline. **This is the hash an export authorization is given for and bound to.**

## Rules (what a request may do)

| Render type | Profile purpose | Notes |
| --- | --- | --- |
| `REVIEW`, `SECTION_PREVIEW`, `COMPARISON` | preview | a preview never spends a master's time |
| `INTERMEDIATE` | intermediate | |
| `FINAL` | delivery / master | needs an `RenderAuthorization`; no `overwrite`; no proxy profile; the profile must fit the timeline's aspect; whole timeline only |

The authorization is confirmed with `ReviewSessions.verify_export` (exact timeline version, profile,
output hash, a current final approval) **when the request is planned and again when it runs**, so
an approval withdrawn while the render waited stops it. A backend that cannot realise a profile
(`resolve_profile` not in its `profiles`) is never asked.

## Reuse, naming, overwrite protection

`job_key = digest(timeline id, timeline CONTENT hash, type, profile@version, output hash, range,
backend name)`. The same request is the same render (`render_id = "r-" + key[:20]`) and a stored
successful render is served (`reused`): a changed video is a new render, a rebuild with identical
content is not (this is the dependency-aware invalidation). `overwrite=True` renders again for
non-FINAL types and keeps both. Output names are unique and versioned
(`{timeline}_v{version}_{type}_{profile}_{n:03d}`); nothing is ever overwritten or deleted here.

## Section previews

The Resolve backend renders the whole timeline only (`range_render=False`), so a section preview is
cut out of a whole-timeline review render with FFmpeg (`infrastructure/ffmpeg_cutter.py`, through
`ProcessRunner`, re-encoded so it starts on the frame) with `padding_seconds` (default 2 s) of
context each side; the whole review render is made once and reused by later previews.
`COMPARISON` is a review render that records `reference_render_id`; it does not render an older
version (Resolve holds only the current timeline).

## What a render is checked against (`domain/verify.py`)

exists, readable and inspected, size plausible, video stream, audio stream (when the timeline has
sound), duration, audio duration, resolution, frame rate, video codec, pixel format, container, audio
codec. A setting the backend cannot apply is a **limitation** in the report (never dropped silently)
and its check is only a warning. A file that cannot be inspected is `not_verified` and not a success.
This is a probe-level look; `export_validation` decodes and measures the delivered file.

## Resources

Wall time and disk free before/after are measured. CPU, GPU, RAM and VRAM are recorded as
`unmeasured` (the backend exposes none of them).

## Stored documents

`render_report` (one per attempt; library documents of the recording, no storage of its own). Fields
per the Layer 6 spec plus module/version/inputs/content hash. Reports are recorded in the review
(`ArtifactKind.RENDER_REPORT`) when a `review_session_id` is given.

## Not done / limits

* The Resolve backend has not run against a real Resolve; Resolve's adapter applies only the
  `youtube` and `master` profiles, so bitrate, sample rate, channels, colour and metadata are
  reported as limitations.
* No pause/resume with Resolve (`pause_resume=False`): pausing a running render is a clear `Err`;
  a queued render can be held and released.
* No real CPU/GPU/RAM/VRAM measurement; no render time estimate.
* A section preview needs the whole review render first (one more render the first time).
* The queue is in-process and not persisted: it is driven by whoever calls `run_next`.
