# Motion Graphics Engine

Builds, animates, renders and validates every graphic of a MEsc video: rank cards ("PLATZ N"),
price tags, spec cards, lower thirds, callouts, panels, charts, counters, CTAs, the sponsor
label, the fire end screen, and — as an executor — caption groups (for `caption_engine`) and
graphic transition overlays (for `transition_engine`).

It decides HOW a graphic is built, never WHETHER/WHERE (editing) and never what a caption says
(caption_engine). The look comes only from `visual_style_engine` (`ResolvedStyle`): no colour,
font, easing, duration range or zone is defined here.

```text
GraphicsBrief ──► planner (kind, facts, timing; + format templates + brand bookends)
                     │ GraphicRequest (one shared model for every kind)
RenderGraphicsCommand┘
   ▼ layout engine (style zones, real font widths, readability floor, no overlap, density)
   ▼ animation compiler (style easings/durations/stagger/overshoot/ceiling -> keyframes, events)
   ▼ style drift check (StyleValidator)
   ▼ HyperFrames composition (deterministic HTML + local GSAP + local fonts)
   ▼ library cache (identical composition + style + renderer = reuse, nothing rendered)
   ▼ HyperFrames render -> ProRes 4444 MOV (yuva444p, true alpha) | preview PNG still
   ▼ render QC (FFprobe + decoded alpha) -> derived asset "motion_graphic_render" of the anchor
graphics_plan document (contracts.md §5 envelope) of the anchor
```

## Contracts (`application/contracts.py`)

| Type | Signature |
| --- | --- |
| `GraphicsRenderer` | `render(RenderGraphicsCommand, ctx) -> Result[RenderedGraphics, RenderError]` |
| `RenderGraphicsCommand` | `anchor_asset_id`, `requests: tuple[GraphicRequest]`, `style: ResolvedStyle \| StyleRef`, `fps: Rational`, `quality=RenderQuality.FINAL`, `config=MotionGraphicsConfig()`, `story_format=None` |
| `RenderedGraphics` | `items: tuple[RenderedGraphic]`, `style: StyleBinding`, `renderer: (name, version)`; `item(request_id)` |
| `RenderedGraphic` | `request_id`, `status: ItemStatus (ok/fallback/not_available/rejected)`, `asset_id`, `created`, `frames: FrameSpan`, `events: tuple[AnimationEvent]`, `qc: tuple[QcIssue]`, `render: RenderRecord`, `detail` |
| `MotionGraphicsPlanner` | `execute(PlanGraphicsCommand(brief, style, config=..., render=True, quality=None), ctx) -> Result[PlanGraphicsResult(plan, document, created), PlanError]` |
| `GraphicsCatalog` | `latest(anchor, *, project_id=None)`, `history(anchor)`, `load(plan_asset_id)` |
| helpers | `frame_span(timing, fps)`, `product_facts(product_id, commerce_facts, name=, image_asset_ids=)`, `price_cents(Fraction)`, `rank_of(commerce_facts)`, `knob_fields(MotionGraphicsProfile)` |

`RenderError = MediaError | RendererNotAvailable | NothingToRender | InvalidGraphicsConfig |
InvalidGraphicRequest | StyleMismatch`. `RendererNotAvailable` carries the setup command.
Every call is blocking (Chrome renders): run it as a job.

### The graphic model

```python
GraphicRequest(request_id, kind: GraphicKind, content: GraphicContent, timing: Timing,
               placement=Placement(), animation=AnimationRequest(), sync=(), priority=0.5,
               reason="", origin=Origin.EDITORIAL, section_id=None)
GraphicContent(headline="", body=(), highlight=(), rank=None, price_cents=None, price_text="",
               specs=(SpecLine(label, value),), data=(DataPoint(label, value: Rational, unit),),
               chart: ChartKind | None, image_asset_ids=(), words=(TimedWord,), look="",
               emojis=(TimedEmoji,), overlay: OverlayKind | None,
               direction: OverlayDirection | None = None, source="")
Timing(local_duration: Rational, timeline_start: Rational | None = None)
Placement(zone="" (kind default), anchor: Anchor | None, track=(TrackPoint(local_at, x, y),), track_ref="")
AnimationRequest(entry=None, exit=None, emphasis_at=(Rational,), intensity: int | None)  # 0-4
SyncPoint(ref, local_at, label="")
TimedWord(text, local_start: Rational, local_end: Rational, emphasis=False,
          emphasis_level: int | None = None)  # 0 normal .. 3 critical (caption_engine's scale)
TimedEmoji(emoji, after_word: int, local_start: Rational, entry="")  # entry pop_in | fade_in
AnimationEvent(graphic_id, kind: EventKind, local_at, timeline_at, strength, ref="")
```

`GraphicKind`: `title, section_title, lower_third, callout, text_panel, highlight_panel,
price_tag, rank_card, silhouette_teaser, spec_card, comparison, product_sticker, feature_list,
step_list, chart (bar|line|pie|progress|counter), timeline, arrow, tracking_label, cta,
sponsor_label, intro, end_screen, logo_sting, membership_plug, pip_frame, caption,
transition_overlay, comparison_matrix, strike_value, url_card, deadline_chip`. `Origin`: `editorial | caption | transition | brand`.
`EventKind`: `enter_start | enter_peak | emphasis | exit_start | exit_end`.
Entry patterns `pop_in | slide_in | fade_in | wipe_in | cut`, exits `pop_out | slide_out |
fade_out | cut` (the style's vocabulary; `None` = the style's choice).

### Time domains (contracts.md §3)

* `local_*` = seconds from the first frame of the graphic (`TimedWord.local_start/end`,
  `AnimationRequest.emphasis_at`, `SyncPoint.local_at`, `TrackPoint.local_at`,
  `Timing.local_duration`, event `local_at`).
* `timeline_*` = seconds from the start of the edit (`Timing.timeline_start`, event
  `timeline_at`).
* No source time is accepted: the caller maps source -> timeline through its clip.
* Frames are computed once (`frame_span`): placed graphics `start_frame = round(timeline_start
  * fps)`, `end_frame = round((timeline_start + duration) * fps)` (half-to-even on exact
  values), `frames = end_frame - start_frame`; the MOV has exactly `frames` frames and its
  local 0 is `FrameSpan.timeline_start` (the frame-snapped start). Unplaced: `round(duration *
  fps)` local frames. Events carry `timeline_at = FrameSpan.timeline_start + local_at`.

### Example: a caption group (caption_engine)

caption_engine decides the words, the grouping, the timing and the emphasis; motion draws them
in the style's caption look (`word_group`: Anton caps, white, black outline, keyword yellow;
`line`: the static meme line) inside the caption zone.

```python
group_start = Rational(1253, 100)  # timeline seconds of the group's first frame
words = (
    TimedWord(text="DIESE", local_start=Rational(0), local_end=Rational(2, 5)),
    TimedWord(
        text="BATTERIE", local_start=Rational(2, 5), local_end=Rational(22, 25), emphasis=True
    ),
    TimedWord(text="KNALLT", local_start=Rational(22, 25), local_end=Rational(7, 5)),
)  # local_* = seconds from the group start; one word per TimedWord (no spaces)
request = GraphicRequest(
    request_id="cap:3f2a9c",  # stable id (e.g. hash of the speech span)
    kind=GraphicKind.CAPTION,
    origin=Origin.CAPTION,
    content=GraphicContent(words=words, look="word_group"),  # look "" = style default
    timing=Timing(local_duration=Rational(8, 5), timeline_start=group_start),
)
result = renderer.render(
    RenderGraphicsCommand(
        anchor_asset_id=anchor, requests=(request,), style=style_ref, fps=Rational(25)
    ),
    ctx,
)
item = result.value.item("cap:3f2a9c")  # item.asset_id = ProRes 4444 MOV, item.frames, item.events
```

Per-word timing: each word appears (opacity + pop) at its `local_start`; while it is spoken
(`local_start <= t < local_end`) it takes the highlight colour (`config.captions.active_word =
"highlight"`, or `"none"`); `emphasis=True` words keep the highlight colour the whole group.
Each word yields an `emphasis` event (`ref` = the word's run id `"<request_id>.text.w<i>"`);
its strength is `0.25 x (1 + emphasis_level)` when the level is given (else 0.5 emphasised,
0.25 normal).

**Emoji** (identity v2+ styles, which bundle the emoji face `style.tokens.emoji.face`): each
`TimedEmoji` is its own run `"<request_id>.text.e<k>"` right after word `after_word`, measured
with the emoji face's real advance (so lines wrap correctly), drawn in the emoji face without
the text outline, and enters at its own `local_start` (`pop_in`: scale 0.3 -> 1 with the style's
entry easing over `motion.entry_max`; `fade_in`: opacity only), with an `emphasis` event. It
leaves with its group. An emoji anywhere in other text (a callout or punchline headline) is
measured and drawn the same way (`TextRun.emoji`, `TextBlock.emoji_face`). Under an identity v1
style the group is drawn without its emoji and the item carries `emoji.not_available`
(WARNING); no system emoji font is ever used. The emoji font file is copied into a render only
when its composition draws an emoji (other renders are byte-identical to before).
A word outside `[0, local_duration]` is an ERROR (the group is not drawn). Text that does not
fit the look's lines/characters inside the zone at or above the readability floor is refused
(`text.overflow`), never clipped.

### Example: transition overlays (transition_engine)

transition_engine decides kind, duration and alignment; motion draws a full-frame overlay
whose middle (`local_duration / 2`, the `enter_peak` event) is the moment of full cover — align
it to the cut.

```python
cut = Rational(9301, 25)  # timeline seconds of the cut
dur = Rational(16, 25)  # 16 frames at 25 fps
overlay = GraphicRequest(
    request_id="tr:s3>s4",
    kind=GraphicKind.TRANSITION_OVERLAY,
    origin=Origin.TRANSITION,
    content=GraphicContent(overlay=OverlayKind.ZOOM_TUNNEL),  # PIXEL_DISSOLVE | WIPE | SHAPE | MASK
    timing=Timing(local_duration=dur, timeline_start=Rational(9301, 25) - dur / 2),
)
```

| `OverlayKind` | Look (style colours) | Motion |
| --- | --- | --- |
| `wipe` | accent red panel + charcoal panel | travels in `content.direction` (`right` = default: in from the left to full cover at the middle, out to the right; `left`, `up`, `down`), staggered |
| `shape` | accent red circle | grows to cover by the middle, fades |
| `mask` | black circle | iris to black by the middle, fades |
| `zoom_tunnel` | concentric fire-colour rings | rings fly outward, staggered (intro only, per the identity) |
| `pixel_dissolve` | grid of fire/black cells | cells appear in a seeded order to full cover at the middle, then disappear (into the end screen) |

(`timeline_start` arithmetic above is illustrative: `Rational` has no operators; use
`Fraction`.) Overlays are full frame (no zone) and may be opaque at the middle.

## Presets and the orchestration surface

A planner (rules or a weak language model) only puzzles. It sends
`GraphicOrder(kind, at, slots, variant="", emphasis=None, duration=None, request_id="",
section_id=None, priority=0.5, zone="")` to `order_request(order, style)`, which returns
`Result[GraphicRequest, InvalidGraphicOrder]`. `at` is the timeline second when the fact is
spoken: the graphic is fully visible then. Each kind's preset (`PRESETS`, `catalogue()` as plain
data) fixes:

* typed **slots**: `text` / `money` (cents) / `date` (`dd.mm.`) / `url` / `rank` / `image`
  (cut-out id) / `list` / `table`, each with a max length and a max item count;
* a few **variants** (the first one is the default) and **emphasis** 0 subtle / 1 standard /
  2 hero (type scale x0.85 / 1 / 1.2; a hero that does not fit its zone is drawn one level
  smaller);
* one **choreography** pair (cards: wipe + slide, fade out; tags and stickers: pop in/out;
  titles: fade; brand: cut) and the allowed **zones**.

`validate_order` rejects anything else and lists every problem: an unknown slot, text that is too
long, a wrong format, an unknown variant, emphasis or zone, a wrong table shape, or a bare number
without its label. Design tokens (`domain/design.py`): one grid, type sizes from the style
roles, radii, accent spine, soft shadow and a legibility floor for text on footage. Every card
uses the same primitive.

| Kind | Slots (required*) | Variants |
| --- | --- | --- |
| callout | text* (60), label | dark, accent, highlight |
| price_tag | price* (cents), product (40), label | tag |
| spec_card (data card) | facts* ((label, value) x4), product, price, image | tiles, rows |
| chart | values* ((label, "1,25 €") x5), title, best | bars, columns, line, pie, progress, counter |
| timeline | events* ((date, text) / (lane, date, text) x6) | steps, two_lane |
| comparison_matrix | columns* (x3), rows* (x5), best, title | table |
| strike_value | label*, old* (10), new | strike |
| url_card | url*, label | dark, accent |
| deadline_chip | date*, label | accent, highlight |
| ... | see `PRESETS` (every orderable kind) | |

Audit and before/after frames: `docs/layer4/preset-audit-2026-11-09.md`.

## Planning (`MotionGraphicsPlanner`)

Input: `GraphicsBrief(anchor_asset_id, project_id, edit: EditRef | None, frame_rate, width,
height, timeline_duration=None, story_format=None, sponsored=False, sponsored_spans=(),
sections=(BriefSection(section_id, title, purpose, timeline_start, timeline_end, rank=None,
product_id=None),), graphics=(EditorialGraphic(request_id, intent: Intent(explain|show_text),
timeline_at, priority, concept, section_id=None, duration=None, product_id=None, data=(),
reason=""),), products=(ProductFacts(product_id, name, price_cents, specs=(SpecFact(kind,
value, unit),), retailer, image_asset_ids, source),), end_screen=None,
membership_plug_at=None)`.

* Editorial request + product with ≥2 specs -> spec card (Schuss / Höhe / Brenndauer / ...
  + Kosten); with a price -> price tag (`style.format_price(cents)`: `15,99 €`); data -> bar
  chart / counter; otherwise `show_text` -> callout, `explain` -> text panel with the concept.
* Format templates (profile `ranking`): a "PLATZ N" card at every ranked section start; the
  black-silhouette teaser of the next rank (cut-out from `visual_asset_generation`
  `VariantName.SILHOUETTE`) ending before the next rank.
* Brand bookends: the end screen (style length, at the end) when the profile mandates it; the
  "Werbung" label over the sponsored spans (mandatory style rule); the membership plug only when
  the brief names a time. Each records its reason.
* No-speech path (`edit=None`, no sections/graphics): bookends only.
* Product pictures are prepared by `VisualAssetPreparer` (stickers/silhouettes, never cut out
  here); no usable cut-out -> the item is `not_available` (the spec card just omits the photo).
* Person decisions: `GraphicDecision(graphic_id, REJECT)` -> status `rejected`, nothing drawn.
* Stored: `graphics_plan` document of the anchor (contracts.md §5 envelope: `plan_id,
  plan_version, supersedes, project_id, edit, style (StyleBinding), created_at, state, graphics,
  events, checks, inputs, stages, provenance, warnings, limitations, schema_version`). Cache key
  = brief digest, style fingerprint, config digest, render flag/quality, renderer identity and
  availability, versions. Same key -> stored plan, nothing computed.

### Mapping from an edit plan (for the pipeline mapper)

| GraphicsBrief | From |
| --- | --- |
| `anchor_asset_id` | first source asset of the edit |
| `project_id`, `edit` | `EditPlan.project_id`, `EditRef(edit_plan_id, plan_version)` |
| `frame_rate` | `EditPlan.timebase.frame_rate` |
| `width`, `height` | delivery size (= the resolved style's output) |
| `timeline_duration` | `EditPlan.duration.estimated` (no-speech: source duration) |
| `story_format` | `EditPlan.target_format` / editing profile |
| `sections` | story sections with their timeline span (via placements); `rank` from `rank_of(commerce_facts(section text))` |
| `graphics` | `EffectRequest` with `kind == GRAPHIC`: `request_id`, `intent` (EXPLAIN/SHOW_TEXT), `at` -> `timeline_at`, `priority`, `section_id`, `reason`; `concept` from the story visual opportunity |
| `products` | `product_facts(id, commerce_facts(text))` per product/section (+ library pictures) |
| `sponsored`, `sponsored_spans` | project flag / sponsored sections |

## Rendering (HyperFrames)

* Pinned: `hyperframes` **0.9.5** (Apache-2.0) and `gsap` **3.15.0** (GreenSock "Standard no
  charge" license: commercial use allowed) in `infrastructure/hyperframes_runtime/package.json`
  + `package-lock.json` (package data). Chrome: the headless shell HyperFrames pins
  (`chrome-headless-shell` 152.0.7977.30 at setup time).
* Setup (the ONLY download): `uv run python scripts/setup_hyperframes.py` -> `npm ci` from the
  lockfile into `AppPaths.cache_dir/motion_graphics/hyperframes`, then `hyperframes browser
  ensure` with HOME/USERPROFILE redirected into that folder. TLS stays verified (Node trusts the
  system store via `--use-system-ca`). `--check` reports without downloading.
* Render: `node <cli> render <project> --format mov --fps <n/d> --workers 4 --quiet --strict
  --no-browser-gpu` through `ProcessRunner`, offline: telemetry, update checks, auto-install and
  skill installs are disabled by environment, the browser path is explicit, GSAP and fonts are
  copied next to the HTML (`@font-face` with the style's bundled OFL fonts, unique family names
  so nothing is fetched). Software GL (SwiftShader) by default: the same pixels on every
  machine. Composition HTML is deterministic (byte-identical for the same scene; unit-tested).
* Output: ProRes 4444 MOV (FFprobe reports `yuva444p12le` for ProRes 4444 decoding; the encoder
  input is 10-bit 4:4:4:4 with alpha), the edit's fps, the style's output size, exactly
  `FrameSpan.frames` frames. Preview quality = one PNG still of the hold at
  `render.preview_scale` (role PREVIEW).
* Still fast path: only the frames whose picture changes are drawn (`changing_frames`, the
  animation `resample`d onto them). Each drawn frame is encoded once, exactly like HyperFrames
  encodes, and the MOV is joined by stream copy (ProRes is intra-only). The result is
  pixel-identical to a full render. An 84.7 s "Werbung" label: 1 frame drawn, about 12 s instead
  of about 5 min.
* Across clips: a render carries a content tag (`mgc:<digest>`, without the anchor or the style
  document). The same graphic of another clip (the end screen) is registered from the stored
  file and not drawn again.
* Cache: `register_derived(anchor, file, operation="motion_graphic_render", config={composition
  digest (HTML + font sha256 + image checksums + GSAP version), fps, frames, quality, renderer +
  version, style fingerprint, size, still frame, software_gl})`; a hit returns the existing
  asset (`created=False`), nothing rendered. Only changed graphics re-render (incremental).
  Metadata: renderer, version, composition digest, style fingerprint, frames, pix_fmt, codec,
  alpha statistics, item fingerprint.

## QC

* Request/layout (ERROR = the graphic is not drawn, the plan is PARTIAL): invalid timing, word
  outside its group, unknown zone or a zone that does not take the kind (`zone.wrong_kind`), a
  rect leaving its zone, text overflow below the readability floor, missing price/specs/data
  (`chart.no_data`: nothing is invented), missing image/cut-out, missing logo asset
  (`brand.asset_missing`), missing track (`track.missing`), overlap with a simultaneous graphic
  after trying the alternate zone (`layout.overlap`). WARNING: density above the style's
  `max_simultaneous` (lowest priority not drawn), intensity above the ceiling (lowered), short
  hold, clamped track, missing plate. INFO: text shrunk, moved zone, rounded number.
* Style drift: every text, colour, zone placement and entry/exit motion is declared to
  `StyleValidator`; its ERRORs (locked rules) drop the graphic, other findings are recorded
  (`style.<code>`).
* Render QC (on the file): size, fps, ProRes 4444, pix_fmt with alpha, exact frame count, alpha
  decoded on hold frames: not fully transparent, not fully opaque (except end screen, membership
  plug and transition overlays).
* Plan CRITICAL (never stored): duplicate ids, OK item without layout/animation/frames,
  inconsistent frames.

## Configuration (`domain/profile.py`, settings-UI metadata on every knob)

`MotionGraphicsConfig(profile=None | name | MotionGraphicsProfile, overrides={"dotted.path":
value}, section_overrides=(SectionOverride(section_id, overrides),), decisions=(...))`.
Profiles: `generic` (end screen), `ranking` (+ rank cards and teaser), `shorts` (no end screen,
shorter holds), `calm` (longer holds). Story format -> profile: `top_n -> ranking`, `short /
shorts_* -> shorts`, `review / brochure_review / nye_uncut -> calm`, else `generic`.
Knobs: `timing.*` (multiples of the style's `motion.min_hold`, reading speed), `bookends.*`,
`templates.*`, `captions.*`, `render.*` (quality, preview scale, workers, timeout, software GL,
alpha samples). Durations of entries/exits, easings, stagger, overshoot and the intensity
ceiling come from the style.

## Module wiring

Registered by `MotionGraphicsEngineModule(hyperframes_dir=None)` (default install folder: the
app cache). `bootstrap/modules.py` needs:

```python
from media_house.modules.motion_graphics_engine.module import MotionGraphicsEngineModule

(MotionGraphicsEngineModule(),)  # in installed_modules(); lifecycle name "motion_graphics_engine"
```

Consumers allowed by the boundary test: `caption_engine`, `transition_engine`,
`music_and_sound_design` (events), `media_pipeline`, `bootstrap`. It reads only contracts of
`visual_style_engine`, `visual_asset_generation`, `media_library`, `content_intelligence`
(+ allowlisted `media_inspection`, `story_architecture`, `reframing_intelligence`).

## Limitations

* Rendering needs the one-time setup; without it renders are `not_available` (plans are still
  stored with layout, animation, events and QC).
* Render speed on this CPU-only machine: roughly 0.3-1 s per 1080p frame (SwiftShader, 4
  workers), so a 20 s end screen takes minutes; renders are cached.
* Brand asset files (logo, wolf, fire plate, membership plate) do not exist yet: the logo sting
  is not drawn, plates are reported missing and the end screen is drawn without its plate.
* Emoji need an identity v2+ style (bundled Noto Color Emoji); single code points and
  FE0F sequences are measured exactly, ZWJ sequences are measured per part (wider than drawn).
* Section-specific style looks are not applied (project look); tracking labels follow supplied
  track points only (no tracking here); no audio-reactive animation beyond supplied sync points.
* Charts take non-negative values; numbers are shown exactly (German format) or rounded to two
  decimals with an INFO.

Validated on: unit tests (layout, fitting with real font widths, animation, events, planning,
config metadata, deterministic HTML), integration tests with a real Application/Media Library
and FFmpeg, and real HyperFrames 0.9.5 renders (rank card, price tag, lower third, caption
group, end screen, a 9:16 caption group with an emoji and a callout with an emoji:
`docs/layer4/previews/motion/caption_emoji_9x16.png`) on Windows 11, CPU only.
