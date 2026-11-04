# transition_engine

Decides the transition between adjacent clips of an edit, and the audio transition of every cut.
A hard cut is the default and always available; a transition is used only when it adds meaning
(a section, time or scene change, a reveal, an editorial request, a brand bookend) and fits the
channel's restraint, the sources and the picture. Layer 4 module (docs/layer4/contracts.md).

Owns: transition choice (kind, intensity 0-4, duration in frames and exact seconds, alignment on
the cut, required vs available handles, fallback), the audio transition plan (J/L kept, speech-
safe crossfades in samples and ms, head/tail fades), transition QC, `TransitionEvent`s.
Does NOT own: cut placement (editing_intelligence), audio processing (audio_improvement), music
crossfades and ambience beds (music_and_sound_design), drawing/rendering (motion_graphics_engine
renders the graphic overlays it is asked for), visual identity (visual_style_engine).

## Contract (`application/contracts.py`)

```python
planner = container.resolve(TransitionPlanner)
result = planner.execute(
    PlanTransitionsCommand(
        sequence,
        style=style_ref_or_resolved_style,
        config=TransitionConfig(profile=None, overrides={...}),
        render=True,
        decisions=(TransitionDecision(...),),
    ),
    ctx,
)
plan = result.value.plan  # TransitionPlan (stored; created=False on a cache hit)
plan.between("p_3", "p_4")  # the Transition at one cut
plan.events  # TransitionEvent(transition_id, kind, timeline_at, intensity)
container.resolve(TransitionCatalog).latest(anchor_asset_id, edit_plan_id=..., profile=...)
```

* `TransitionSequence` (own DTO): `edit: EditRef | None`, `project_id`, `frame_rate`, `clips`
  (`TransitionClip(clip_id, asset_id, source_start, source_end, timeline_start, layer,
  section_id, beat_id, beat_function)`), `sections` (`SectionMark`), `requests`
  (`TransitionRequest`), `bridges` (`AudioBridgeMark`), `bookends` (`Bookend(kind,
  timeline_at)`), `format` (story format / editing profile), `frame_rate_assumed`.
* No-speech path: `TransitionSequence.no_speech(((asset_id, duration),), frame_rate=...,
  bookends=...)`: one clip per recording, timeline = source, `edit=None`. One clip gives only
  the start and end bookends (a fade by profile, the end-screen dissolve if one is supplied).
* Output: `transition_plan` document of the anchor (first clip's asset), contracts sec. 5
  envelope: `plan_id, plan_version, supersedes, project_id, edit, style (StyleBinding),
  created_at, state, profile, frame_rate, transitions, events, metrics, decisions, checks,
  inputs, stages, provenance, warnings, limitations, schema_version`.
* `Transition`: `transition_id` (`start:<clip>`, `cut:<out>><in>`, `end:<clip>`: stable from the
  adjacent clip ids), boundary, outgoing/incoming clip, section, `cut_at`/`cut_frame`, contexts,
  `status` (`not_needed | recommended | requested | brand | fallback | overridden`), `required`,
  kind, family, intensity, `preset_id` (`<profile>.<kind>`), alignment, `duration_frames`,
  `duration`, `timeline_start/end`, `start_frame/end_frame`, direction, `handles`
  (required/available out/in), `audio` (`AudioTransition`), reasons, confidence, evidence,
  `rejected` (compatibility results), `fallback`, `overlay` (graphic request id, overlay kind,
  render state, rendered asset id), `decision_note`.
* music_and_sound_design: map each `TransitionEvent` to `SyncEvent(event_id=transition_id,
  source=TRANSITION, kind=kind.value, timeline_at, strength=event.strength)`.

## Edit plan -> TransitionSequence (the pipeline's mapper)

| TransitionSequence | from the edit plan |
| --- | --- |
| `edit` | `EditRef(edit_plan_id, plan_version)` |
| `project_id`, `frame_rate`, `frame_rate_assumed` | `project_id`, `timebase.frame_rate`, `timebase.assumed` |
| `clips` | VIDEO placements: `clip_id=placement_id` (the SAME id continuity's `ProposedClip` gets, so its judgement matches), `source.asset_id/start/end`, `timeline_start`, `layer`, `section_id`, `beat_id`; `beat_function` from the story plan's beat |
| `sections` | story plan sections (`section_id, order, label, purpose` text) |
| `requests` | `effect_requests` with kind `TRANSITION` (`request_id, at, section_id, priority, reason, decision_id`) |
| `bridges` | `audio_bridges` (`bridge_id, kind, audio_changes_at, picture_changes_at`) |
| `bookends` | membership plug position (if the person placed one) and the end screen (`duration.estimated`, appended after the edit) |
| `format` | `target_format` / editing profile name |

## Decision rules (in this order, per boundary of the main picture layer)

1. A person's decision (`USE kind [duration]` or `HARD_CUT`) wins; only technical limits can
   refuse it (recorded as fallback). Decisions are stored in the plan and carried to the next
   plan of the same edit.
2. Continuous material (one recording, adjacent frames) and gaps stay hard cuts.
3. Brand bookends: membership plug -> `zoom_tunnel`, end screen -> `pixel_dissolve` (the style's
   `end_screen.*_transition` tokens when a style is given, else the profile). Identity rules
   are exempt from budget and ceiling; the drift check reports them as intentional (INFO).
4. Contexts -> candidates from the profile, most important first: editorial request, section
   change, reveal (beat `payoff`/`turning_point` or pacing `hold_reveal`), time change (same
   recording jumps more than `detection.time_jump`), scene change (video scene / environment
   differs), montage (both clips shorter than `detection.montage_max_clip`), jump cut. No
   candidate = hard cut, labelled `match_cut` (continuity compatibility >= 0.75),
   `action_cut` (subjects move on together), `jump_cut` or `hard_cut`. A new camera angle alone
   never gets an effect.
5. Each candidate must pass: approved vocabulary (the profile's, and under a visual identity v2+
   style also the identity's `transitions` tokens: `approved`, never `prohibited`, and the
   `bookend_only` kinds only at their brand bookend; a v1 style or no style leaves the profile
   alone); intensity <= min(profile max, style ceiling of
   the incoming section); budget (whole-edit cap `per_minute`, `min_spacing`, expressive (3-4)
   spacing and count, never two expressive boundaries in a row; editorial requests skip the
   budget); no dip/flash/cover while a sentence runs across the cut (words of the stored audio
   timeline); picture compatibility (whip pan needs a pan, directions within tolerance; motion
   match needs both sides moving; slide/push/wipe/whip never move on-screen text; a dissolve
   never ghosts subjects moving apart; a zoom needs similar compositions); timing (handles and
   room, below). First passing candidate wins; otherwise hard cut with the fallback recorded.

Pacing cues (`add_section_breath`) lengthen a transition by `durations.breath_factor`, montages
shorten it by `durations.fast_factor`. Continuity's `AUDIO_CROSSFADE` repairs owned by
`transition_engine` (F15) become a 300 ms audio crossfade at that cut.

## Vocabulary (intensity)

0 `hard_cut match_cut action_cut jump_cut`; 1 `cross_dissolve fade_in fade_out dip_to_black
motion_match`; 2 `dip_to_white slide push zoom blur wipe* pixel_dissolve*`; 3 `whip_pan flash
shape* mask_reveal* zoom_tunnel*`; 4 `glitch`. `*` = graphic overlay rendered by
motion_graphics_engine (`GraphicRequest(kind=TRANSITION_OVERLAY, origin=TRANSITION,
content.overlay=<OverlayKind>, content.direction=<the wipe's direction left|right>)`, timed so the overlay fully covers the cut at its midpoint; no
handles needed).

## Handle math

A transition of `D` frames on the cut frame `c` (`c = round(t * fps)`, half to even):
centred = `D // 2` before and `D - D // 2` after (an odd D puts the extra frame after the cut),
`start` = 0/D, `end` = D/0. An overlap needs `after` frames of the OUTGOING recording past its
out point and `before` frames of the INCOMING recording before its in point. Available =
`floor((recording_end - source_end) * fps)` and `floor((source_start - recording_start) * fps)`
in EDIT frames (another source rate is fine: seconds are converted once), minus
`detection.vfr_safety_frames` for a VFR source. Recording length: stored inspection (container
start + duration), else the library's `duration_seconds`, never a re-probe; unknown -> overlap
transitions fall back to a cut. A transition also never covers more than the outgoing clip's
free tail or half of the incoming clip. Fitting tries the profile alignment, then the other
two, then shortens frame by frame down to `durations.min_frames`; each step is recorded.

## Audio transitions

Equal-power (default) crossfades centred on the audio edit point, in seconds, samples (the
incoming recording's sample rate, else 48 kHz) and ms: click guard at a cut (10 ms), with the
picture for an overlap (<= 500 ms), 300 ms for a continuity repair, 80 ms at a J/L cut whose
audio point is taken from the edit (the picture never moves). Continuous material has no audio
edit. Head fade-in / tail fade-out by profile (none for looping Shorts). Every fade is shortened
so that no word of the stored transcript is faded or leaked from a handle (`speech_guard_ms`
from a word; never below `min_ms`); `protected` lists the word edges that limited it.

## Configuration (`domain/profile.py`)

`TransitionConfig(profile, custom, overrides: {dotted path: value}, section_overrides)`; every
knob carries `label/help/unit/min/max/step/choices/kind/advanced` metadata enforced by
`problems()` (unit-tested). Precedence: profile defaults -> channel identity (style ceiling,
bookend tokens) -> format profile -> project overrides -> section overrides (the incoming clip's
section) -> person decisions. Profile choice: explicit -> story format (`STORY_FORMAT_PROFILE`)
-> style format profile (`STYLE_PROFILE`) -> `clean_default`.

| Profile | Character | Evidence |
| --- | --- | --- |
| `clean_default` | hard cuts, dissolve on request / time change, max 1 | spec |
| `modern_youtube` | push at sections, zoom reveals, max 3, 2/min | spec |
| `shorts_energetic` | zoom/slide/whip in montages, 6/min, 3 s spacing | spec |
| `product_review` | dissolve at sections, slide on request, zoom reveal, max 2 | spec |
| `tutorial` | dissolve at sections, max 1 | spec |
| `documentary` | dissolves at scenes, dips at chapters, fades at both ends | spec |
| `cinematic` | dissolves, dips, motion match, long fades | spec |
| `ranking` | carousel slide (or whip) between ranks, max 3, 1.5/min, 20 s spacing | t_BT, d2Ch, bw7 (4.2-5.6 cuts/min) |
| `brochure_review` | cuts only (page changes), max 1 | 9etWVDZnyOs |
| `shopping_tour` | jump/hard cuts, whip between stores when panning, max 3 | dru73YpbQ4Y (7/min) |
| `unboxing` | natural/jump cuts, dissolve into a reveal, max 2 | OkjnKFjRFt4 (4.1/min) |
| `compilation` | hard cuts; source flashes are footage | _Ka5c, ZUSK, tDi6 (~3/min) |
| `nye_uncut` | no transition inside the take | y_mj09m0XB4 |
| `shorts_meme` | cuts only, no fades (loops) | zIxe, P_L7 |
| `shorts_haul` | zoom/slide/whip in the montage, max 4, 6/min | nsIVE20ixMM (31.5/min) |
| `shorts_product_test`, `shorts_versus` | cuts only | xCoO, y1q4 / r8A5 |

## QC (`domain/validation.py`, contracts sec. 6)

CRITICAL (plan never stored): frames and times disagree, insufficient handles, two transitions
on the same frames, a word inside an audio fade beyond the click guard. ERROR: a graphic overlay
failed to render (that boundary falls back to a hard cut, plan PARTIAL). WARNING: cut between
frames (snapped), VFR source under an overlap, overlay not rendered (renderer missing), a
bookend not at a cut, repetition (same expressive kind more than `max_repeats` in a row,
expressive on adjacent boundaries, budget exceeded by decisions; CREATIVE), style drift from
`StyleValidator` (transitions are declared as `MotionPhase.TRANSITION`: checked against the
identity's `transitions.duration_*` under v2, no duration range under v1). INFO: other source frame rate, assumed timebase, intentional style deviations.

## Caching and versions

Cache key = edit ref, sequence digest, digests of every fact read (lengths, words, picture
edges, continuity, pacing), upstream document ids, style fingerprint, `digest(config
.fingerprint(profile))`, person decisions, `render`, versions. A hit returns `created=False` and
computes nothing. A new key -> `plan_version + 1` and `supersedes` (per anchor, edit plan and
profile). Planning is deterministic and fast (milliseconds), so any upstream change replans the
whole edit; unchanged boundaries keep their ids and person decisions.

## Limits

See `LIMITATIONS` in `domain/engine.py`: main picture layer only (cover edges stay cuts); no
move to music beats (cuts are editing's; music syncs to these events); video facts on the
analysis clock without the alignment's correction; composition checks use the main subject box
and on-screen text only; motion transitions need a stored picture analysis; only graphic
overlays are rendered here (dissolves, slides... are specifications for the later timeline
builder).

Validated on synthetic sequences, a still-image anchor, a 6 s FFmpeg test clip (inspection
handles) and one real HyperFrames render of a zoom-tunnel overlay only. Not validated on real
channel edits.
