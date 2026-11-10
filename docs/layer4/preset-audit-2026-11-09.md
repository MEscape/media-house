# Motion graphics preset audit (2026-11-09)

Owner's goal: presets so good that a weak planner only *puzzles and orchestrates*. Design
quality lives in the preset system; the choice surface is small, typed and cannot produce an ugly
graphic.

## Method

* Every `GraphicKind` (31) was built with German product-ranking data (product names, prices
  `79,99 €`, dates `09.11.`, a `Werbung` label) on the MEsc identity v2, format profile
  `ranking`, 1920x1080, 25 fps.
* Each graphic was rendered at three key frames (entry at 1/4, middle of the hold, middle of the
  exit) in the pinned HyperFrames Chrome shell with GSAP. The frames were composited over a
  synthetic dark, busy fireworks plate (bursts, sparks, smoke, a bright ground fire).
* Every frame was inspected by eye. The harness is outside the repo
  (`<scratchpad>/audit.py`, `frames.cjs`). It drives one headless Chrome for all compositions.
* Frame paths: `<scratchpad>` is
  `C:\Users\L14\AppData\Local\Temp\claude\C--Users-L14-Documents-Programming-media-house\49f452fd-c983-416a-8d38-a0397fda2c59\scratchpad`.
  The before round is in `before\<kind>_{in,hold,out}.png` and `before\<kind>_sheet.png`. The
  after round is in `after\...`. New presets are named `o_<preset>`.
* A permanent before/after mosaic is in the repo:
  [`previews/presets/before_after_2026-11-09.jpg`](previews/presets/before_after_2026-11-09.jpg).

## Findings (before), by severity

| # | Severity | Kind(s) | Finding | Frame |
| --- | --- | --- | --- | --- |
| 1 | critical | callout, text_panel | **A bare number is drawn without context**: `79,99` as 40 px white text with a thin outline and no panel, floating in the callout zone. On real footage it appeared up to 43 s before the spoken mention (baseline). | `before\callout_bare_hold.png` |
| 2 | critical | highlight_panel | Black text with a **black 4 px outline** on yellow: the letters close up ("Bester Preis im Test" cannot be read). | `before\highlight_panel_hold.png` |
| 3 | critical | membership_plug | **Default label refused**: "KANALMITGLIED WERDEN" (20 characters) is over the look's 16 characters on 1 line, so the plug is never drawn. | before run log |
| 4 | high | chart (counter) | **Unit printed twice** (`0,00 € €`). The counter sat in the middle of the frame, over the subject. | `before\chart_counter_hold.png` |
| 5 | high | chart (bar/line/pie) | **Full-screen charcoal slab** (1728x972 at 90 %) for 4 numbers. Inverted hierarchy: 96 px values, 30 px labels. One colour for every bar, no winner. In the line chart, value labels collide with each other and with the title. The pie leaves half the card empty. | `before\chart_bar_hold.png`, `chart_line_hold.png`, `chart_pie_hold.png` |
| 6 | high | chart (progress) | The data label ("Ausverkauft") is **not shown**. The bar spans the whole card zone at the top of the frame. | `before\chart_progress_hold.png` |
| 7 | high | comparison | Two **full-height colour slabs** with only the names at the top: decoration with no content. The "VS" is small. | `before\comparison_hold.png` |
| 8 | high | sponsor_label | "WERBUNG" is 30 px with a 3 px outline straight on the footage: about 2 % of the frame height, borderline on a phone. | `before\sponsor_label_hold.png` |
| 9 | high | timeline | 30 px outlined uppercase labels with no panel, a 4 px rail and the date merged into the label. Weak hierarchy, poor contrast on fireworks. | `before\timeline_hold.png` |
| 10 | medium | callout, tracking_label, lists | Body text (40-44 px) on busy video **without a panel** or only a thin outline. Feature/step list titles are barely bigger than the items. | `before\callout_hold.png`, `feature_list_hold.png` |
| 11 | medium | price_tag | The product name is a 30 px outlined uppercase label floating over the footage, wider than the tag. | `before\price_tag_hold.png` |
| 12 | medium | spec_card | Labels are 30 px yellow and values about 48 px, in a card sized by the headline with a large empty right half. | `before\spec_card_hold.png` |
| 13 | medium | product_sticker | The identity's sticker look is missing: **no white outer glow and no shadow**. The cut-out fills the whole zone. | `before\product_sticker_hold.png` |
| 14 | medium | all text | Greedy line breaks leave widows ("Schusszahl pro / Euro"). | `before\feature_list_hold.png` |
| 15 | medium | all | Entry and exit are not paired: a `slide_in` card leaves with the style's `pop_out`. Text inside a panel slides 72 px, in from the right when the card is in the right half. The stagger follows element ids, not reading order. | sheets |
| 16 | perf | still graphics | Every frame is rendered, even when the picture never changes (84.7 s sponsor label: about 5 min, 1 unique frame in 2118). The identical end screen is re-rendered for every clip, because the cache is per anchor. | baseline |
| 17 | missing | – | Presets needed for a ranking or brochure video are missing: comparison matrix, struck-through value, URL card, deadline chip, two-lane timeline, comparison bars, cart counter in a card. | – |

Kinds that are fine (only checked): title/intro (gold glow), section title, rank card
("PLATZ 3"), silhouette teaser, arrow, CTA, end screen (still waiting for its fire plate), PIP
frame, caption group, transition overlays.

## What changed (system level)

* **Design tokens** (`domain/design.py`): a single grid (unit = style spacing / 3, steps xs-xl),
  card type sizes taken from the style roles (eyebrow 30, body 44, title 58, value 72, hero 96 at
  1080p), radii, accent spine, hairline, soft card shadow and a 3 % legibility floor for text on
  footage. Card colourways are style colour names only (`dark`, `black`, `accent`, `highlight`),
  and every pair is an identity combination.
* **One card primitive** for every informational overlay: a panel, the accent spine on the
  reading edge, a soft shadow and stacked rows (eyebrow, title, body, value). Text on a panel
  never has an outline. Callout, text/highlight panel, lower third, lists, data card, charts,
  timeline, matrix, struck value, URL card and counter all use it.
* **Preset catalogue and orchestration surface** (`domain/presets.py`,
  `application/orders.py`, exported from `application/contracts.py`). For each kind the preset
  defines typed slots (TEXT / MONEY cents / DATE / URL / RANK / IMAGE / LIST / TABLE, each with a
  max length and a max number of items), 1-6 variants, emphasis 0-2, one choreography pair and
  the allowed zones. `GraphicOrder` → `order_request` / `compile_order`. It rejects an unknown
  slot, an over-long text, a wrong format (cents, `dd.mm.`, address, German number), an unknown
  variant, emphasis or zone, a wrong table shape and a bare number without a label, and it lists
  every reason (`InvalidGraphicOrder.problems`). The graphic is fully visible at `at` (the
  spoken moment). The hold grows with the text to read. `catalogue()` describes the surface for
  a prompt.
* **Fitting**: lines are balanced at the same line count (no widows); captions keep the greedy
  breaks. Text shrinks only inside the bounds and is never clipped. If a preset at hero emphasis
  does not fit its zone, it is laid out again one level smaller (`preset.emphasis_lowered`), so an
  emphasis choice can never push a graphic out of its zone.
* **Choreography**: each preset has one entry/exit pair (cards: panel wipes open, rows slide 24 px
  in the reading direction, everything fades out; tags and stickers: pop; titles: fade; brand:
  cut). An entry without an exit gets its natural pair. The stagger follows the reading rows.
  The strike line of a struck value is drawn after the value is readable (an emphasis event).
* **Zones**: compact charts (counter, progress) sit in the callout zone. The two-lane timeline
  uses the card zone. The existing density limit (3 overlays) and the overlap resolution are
  unchanged and are now tested with five cards ordered at the same time.
* **New and reworked presets**: data card (tiles with the price marked; `rows` variant),
  comparison bars (horizontal, shared scale, best bar red, others silver; `columns` variant),
  comparison matrix (up to 5 products x 3 facts, best row on red), counter card (cart total
  counts up, unit drawn once), two-lane timeline (same day aligned across both years), struck
  value (`04:30` struck through, then the replacement), URL card, deadline chip (`NUR BIS
  29.12.`), product sticker (white glow and soft shadow, size by emphasis), versus (two cards
  and a VS badge), price tag (product on a black chip above the tilted yellow tag), sponsor label
  on a black chip at 35 px.
* **Planner** (PLANNER_VERSION 7): a bare figure (digits, no word of 3 or more letters) without a
  `label` is not drawn (`editorial.bare_figure` WARNING). With `EditorialGraphic.spoken_at`, the
  card is fully in when the figure is spoken. The layout also warns (`text.bare_figure`) when an
  executor request draws a bare figure.
* **Still fast path** (RENDER_VERSION 2): `changing_frames` finds the frames whose picture
  changes (from the keyframe segments). Only those frames are drawn, with the animation
  resampled onto them (`resample`). FFmpeg encodes each drawn frame once, exactly like HyperFrames
  (same filter, `prores_ks` 4444, `yuva444p10le`). The full MOV is then joined by stream copy
  (ProRes is intra-only, so a repeated frame is the same packet). The result was verified
  **pixel-identical** to a full render (all 75 frames, max diff 0). Timings on this busy CPU:
  84.7 s sponsor label, 1 drawn frame of 2118, **11.8 s** (baseline about 300 s); 3 s callout,
  21 of 75 frames drawn, 17.5 s against 22.3 s.
* **Reuse across clips**: each render is tagged with a content tag (`mgc:<digest>` of the
  composition digest, fps, frames, size and renderer, without the anchor or the style document).
  When another clip already has the same graphic, its stored file is registered for the new
  anchor and nothing is drawn. The end screen is rendered once per project.
* Versions: COMPILER 2, VOCABULARY 2 (four new kinds), SCHEMA 2 (`GraphicContent.eyebrow,
  variant, emphasis, rows`), PLANNER 7 (6 kept: end on the last frame), RENDER 2.

## After

Every issue in the table above is fixed (see the mosaic and `after\*_sheet.png`). Unit tests
build every orderable preset × each of its variants × emphasis 0 and 2 on the real v2 style. Each
must be usable, inside its zone, at or above the readability floor, without a style drift ERROR,
and with its preset choreography.

## Remaining (not fixed here)

* The pipeline does not yet fill `EditorialGraphic.label` / `spoken_at` (media_pipeline mapper),
  so on real footage bare figures are now dropped instead of being shown with context. The
  mapper should pass the commerce-fact kind as the label and the word time as `spoken_at`.
* Brand plates (fire, membership) still do not exist, so the end screen and the plug are drawn
  over the footage.
* The title glow (identity v1/v2 token) softens the gold title over very bright bursts. It is an
  identity decision and was not changed.
* The audit frames use the pinned Chrome and GSAP through puppeteer (same engine, not the
  HyperFrames CLI). The fast path and every kind in the real-render integration test were
  checked with HyperFrames itself.
