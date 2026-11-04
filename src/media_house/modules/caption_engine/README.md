# caption_engine

Decides the captions of one edit: whether captions are burned in (mode), which spoken words
are shown (selection), how they are grouped, timed and placed (segmentation, layout), what is
highlighted or decorated (emphasis, emoji), which animation is REQUESTED, and the subtitle text
for YouTube closed captions. It never transcribes (`audio_intelligence` owns the transcript) and
never draws (`motion_graphics_engine` executes each caption group as a `GraphicKind.CAPTION`
request). Ownership: `docs/layer4/contracts.md` section 1.

## Contract (`application/contracts.py`)

```python
result = planner.execute(                       # CaptionPlanner (blocking: run as a job)
    PlanCaptionsCommand(
        timeline=CaptionTimeline(...),          # the module's own input DTO (below)
        style=style_ref,                        # StyleRef of the stored style, or a ResolvedStyle
        config=CaptionConfig(profile=None, overrides={}, section_overrides=()),
        render=True,                            # also draw every group through motion graphics
        decisions=(CaptionDecision(item_id, Action.REJECT | NO_EMPHASIS | NO_EMOJI | CORRECT),),
    ), ctx)
plan = result.value.plan                        # stored caption_plan of the anchor asset
plan.subtitles.srt / plan.subtitles.vtt         # text inside the plan (the library refuses .srt)
to_srt(cues) / to_vtt(cues, speaker_labels=)    # the pure exporters
catalog.latest(anchor_id, project_id=) / history(anchor_id) / load(plan_asset_id)   # CaptionCatalog
```

Errors: `InvalidCaptionTimeline` (no clips, empty ranges, bad size/fps), `InvalidCaptionConfig`,
`StyleNotAvailable` (unreadable style or a style resolved for another output size),
`InvalidCaptionPlan` (a CRITICAL QC finding: nothing stored), library `MediaError`.

## Input: `CaptionTimeline` and the `EditPlan` mapping

`CaptionTimeline(edit: EditRef | None, project_id, frame_rate, width, height, clips,
story_format=None, frame_rate_assumed=False)`; `CaptionClip(clip_id, asset_id, source_start,
source_end, timeline_start, picture=True, audio=True, section_id=None, beat_function=None)`.
All times are exact `Rational`, speed 1. The orchestrator's mapper in `media_pipeline/application/`
fills it from the `EditPlan` (this module never imports it):

| CaptionTimeline | EditPlan |
| --- | --- |
| `edit` | `EditRef(plan.edit_plan_id, plan.plan_version)` |
| `project_id`, `story_format` | `plan.project_id`, `plan.target_format` |
| `frame_rate`, `frame_rate_assumed` | `plan.timebase.frame_rate`, `plan.timebase.assumed` |
| `width`, `height` | the output size of the resolved style (`1080x1920` for 9:16) |
| one `CaptionClip` per placement | `placement_id`, `source.asset_id`, `source.start/end`, `timeline_start`, `section_id` |
| `picture` | `track is VIDEO and layer == 0` (main picture; cover clips also `True` on layer 1) |
| `audio` | `audio is SOURCE_SYNC` for video placements, `True` for AUDIO-track placements (`VOICEOVER`), `False` for `MUTED` |
| `beat_function` | the story beat of `placement.beat_id` (`StoryPlan.beats[*].function`), when known |

No-speech path (`edit=None`): one clip per source asset, timeline = source, `project_id=None`,
no sections. The plan is then `disabled` ("no reliable speech", or by profile) and never fails.
Anchor = the first clip's asset.

Upstream results are READ, never recomputed: `AudioCatalog` (words on the source clock,
confidence, speaker, `suspect`, delivery emphasis; required for captions, missing ->
`not_available` + a `ReprocessRequest` to audio_intelligence), `ContentCatalog` (sentences,
key items), `commerce_facts()` (content's pure recogniser: prices, ranks, specs, retailers,
products), `VideoCatalog` (face/object tracks), `ReframingCatalog` (crop windows when the output
crops the source), `InspectionCatalog` (display size), `GraphicsCatalog` (facts a planned price
tag / rank card / spec card already shows), `VisualStyleCatalog` + `StyleValidator`.

## Decisions

**Selection (mode per section).** `full_captions` (all reliable words), `selective_captions`
(sentences with facts, content key items, story beats, warnings, strong claims, strongest first,
up to `selective_share` of the speech time), `emphasis_only` (price/rank/spec/warning phrases
alone), `key_information` (spoken facts with `key_context_words` before them; a fact a planned
graphic of the same edit already shows at that time is not repeated), `disabled`. A word cut out
of the edit is not heard; a word split by a cut is shown only when `cut_word_min_share` of it is
heard (times clamped, `clipped=True`); `suspect` words and words under `min_confidence` are never
burned in; a transcript in another language than `transcript.language` is not burned in (a
re-transcription is requested; subtitles keep it, labelled with its language).

**Presentation.** Atoms are never split: content's facts ("19,99 €", "Platz 3", "25 Schuss",
product names) and a number with its unit. A dynamic programme picks group boundaries under hard
limits (words per caption, characters per line x lines at the look's case — `ß` counts as `SS` in
capitals —, longest caption; pause >= `pause_split`, sentence end, speaker change, clip change
with a gap always break) and soft costs (line ending on an article/preposition/conjunction,
breaks inside a phrase, uneven groups, too-short captions). Timing: `lead_in` before the first
word, `linger` + the style's exit time after the last, stretched to `min_duration` and the reading
speed where the next caption leaves room, never overlapping (`trim` / `drop_later` for overlapping
speech). A caption followed straight by the next one cuts out (no exit animation over a spoken
word). Layout: only the look's zone (`caption_band`; the meme `line` look uses `title_band`),
centred; a face/object overlapping the block moves it to the zone's bottom or top edge; 9:16
platform keep-outs and the readability floor (`typography.min_size` at the real output size) are
checked; the style's drift validator checks every placement.

**Expression.** Emphasis combines meaning, delivery and narrative: price/rank `critical`, spec /
warning `high`, product/retailer/claims `medium`; delivery adds weight to a meaningful word and
alone never exceeds `medium`. Bounded: `max_per_caption` atoms, `max_share` of shown words,
`min_word_gap` words apart; a fact lights up without its function words ("PLATZ 3"). Emoji come
only from the profile's controlled vocabulary, never on a negated word, only warnings inside a
caveat, `max_per_minute`, `min_spacing`, `repeat_spacing`, never twice in a row; off for serious
formats. Animation requests use the style's motion tokens (entry/exit pattern, intensity <= the
style's ceiling, `calm` -> 1); per-word reveal and active-word highlight are passed to motion's
`captions.*` knobs.

**Rendering.** Each planned emoji travels with its group as a `TimedEmoji` (after the last
run of its word, at its planned `local_start`, entry `pop_in`/`fade_in`) and every word as a
`TimedWord` with its `emphasis_level` (0 normal .. 3 critical); the emoji's `render` follows its
group's (`rendered`/`reused`), stays `not_available` when the style has no emoji face, and is
`not_requested` while nothing was rendered. Restraint rules are unchanged. One motion request per caption GROUP: motion lays out one text block per request
with one run per timed word, so a group is exactly what appears and leaves together; motion's
library cache re-renders only changed groups on a replan. ProRes 4444 MOV with alpha per group,
placed at its timeline start (`RenderRef.asset_id`, `start_frame`, `end_frame`).

## Format profiles (`domain/profile.py`, evidence in `docs/layer4/channel-discovery.md`)

| Profile | Mode | Evidence |
| --- | --- | --- |
| generic | key_information | fallback; recent long videos show no captions, 2023 ones condensed key words |
| ranking, brochure_review, shopping_tour, unboxing | disabled | 6 of 6 recent long videos and both owner favourites have no burned-in captions; facts are rank/spec cards and price tags (motion) |
| compilation | key_information | 2023 assortment video (ZUSKEXTAuKI) condensed caps captions |
| nye_uncut | disabled | y_mj09m0XB4 uncut ambience |
| shorts_haul | full, 2-4 word groups, word reveal, active word, emoji | nsIVE20ixMM (591k), VO 220 wpm; emoji punchlines |
| shorts_product_test | full, word groups, no emoji | sponsored tests xCoOYkqzgDo, y1q4lpEEFcI |
| shorts_versus | full, word groups, emoji | NEfxufh6YLw caps with yellow keyword |
| shorts_meme | full, `line` look (upper third, static), emoji | zIxejdVAa0E (986k), P_L7KHcB-_Y |

Profile choice: `config.profile` -> the style's format profile -> story format (`top_n` ->
ranking, `review`/`talking_head` -> brochure_review, `unboxing`, `product_introduction`/`montage`
-> compilation, `short` -> shorts_haul) -> aspect (`9:16` -> shorts_meme) -> generic.

## Configuration

`CaptionConfig(profile, overrides={"selection.mode": "full_captions", ...}, section_overrides=
(SectionOverride(section_id, overrides),))`. Every user-facing knob has `field(metadata=label,
help, unit, min, max, step, choices, advanced)`; `problems()` enforces the same ranges
(`tests/unit/caption_engine/test_expression_layout_export.py` walks every knob). Groups:
`selection`, `transcript`, `segmentation` (`0 = the style's look` for characters/lines),
`layout`, `emphasis`, `emoji`, `animation`, `subtitles` (its own segmentation: 42 characters x 2
lines, 17 chars/s, 7 s). `PROFILE_VERSION` versions the knob schema, `profile.version` the values.

## Plan (`caption_plan`, contracts.md section 5 envelope)

`plan_id, plan_version, supersedes, project_id, edit, style (StyleBinding), created_at, state,
anchor_asset_id, width, height, fps, mode, mode_reason, transcripts (TranscriptRef: document id,
language, alignment, words heard/shown/suspect/uncertain/split), segments (CaptionSegment: id
`cap:<sha of word ids>`, clips, section, speaker, mode, reasons, text, words with source /
timeline / local times + emphasis + cues, emojis, layout, animation request, render ref, frames),
subtitles (cues + SRT + WebVTT), decisions, reprocess requests, checks, inputs, stages, provenance
(schema/planner/vocabulary/profile versions, profile evidence, configuration fingerprint,
engines incl. the renderer), warnings, limitations, schema_version`. Cache key: timeline, transcript
documents + digests, subjects, covered facts, style fingerprint + caption look, configuration,
decisions, render flag, versions (digests only). Identical inputs return the stored plan
(`created=False`); a plan whose requested render was not possible is retried as a new version.

## QC (`domain/qc.py`)

CRITICAL (never stored): a word that is not what was said (`text.integrity`, NFC-compared, only a
final full stop may be dropped), a word outside its caption, an empty caption. ERROR (that
caption is not rendered, plan PARTIAL): overlapping captions, zero frames, a zone that takes no
captions, a platform keep-out, word timing drift beyond `tolerance`, a failed render. WARNING:
too short / too long, reading speed, line length, zone overflow, text below the readability
floor, a covered face, low-confidence words, language mismatch, estimated word timing.
INFO: suspect words dropped, split words, rejected captions, facts already shown by a graphic.
Render QC (dims, fps, alpha, frames) is motion's and is copied into the plan as `render.*`.

## Limits

* Emoji are drawn only under a visual identity v2+ style (bundled Noto Color Emoji); under a
  v1 style they stay planned (`not_available`, `EMOJI_NOT_RENDERED`). An installed emoji font
  is never relied on.
* Subject avoidance uses video tracks on the video clock (no audio/video sync correction,
  sub-second) and only where the output shows the source uncropped or a stored reframing crop
  maps it; the block size is an estimate (motion measures the real font).
* Segmentation and layout knobs apply per project; section overrides change selection,
  emphasis, emoji and animation.
* Speakers only when audio_intelligence ran diarization; no translation.
* Validated on synthetic German transcripts (real audio_intelligence fusion and content's
  commerce facts), the MEsc v1 style and a real HyperFrames render of three 9:16 caption groups
  only. No real ASR output on fireworks footage was available on this machine.
