# MEsc Visual Identity (identity `mesc` v1, v2)

The human-readable guide to the channel identity implemented by
`src/media_house/modules/visual_style_engine` (`domain/identity.py`: `MESC_IDENTITY_V1`,
`MESC_IDENTITY_V2` = v1 unchanged plus emoji and the transition language, section 8a).
Evidence: `docs/layer4/channel-discovery.md` and `docs/layer4/channel-evidence.json` (19 sampled
videos, 218 thumbnails, measured colours). Every token below carries its rule kind and evidence
in code; the token table at the end is generated from the same data.

Legend: **mandatory** = brand-defining and locked (only a new identity version changes it);
**preference** = the default look (format profiles, projects and sections may change it with a
reason). Evidence: **measured** (HEX measured on channel frames), **observed** (recurring
pattern, value by eye), **proposed** (design decision made here). Confidence: high / medium /
low.

## 1. Global identity

* **Channel:** MEsc (`@MEscapYT`), German fireworks channel: retail intelligence (brochures,
  ranges, rankings: what, where, how much), shopping tours, unboxings, midnight footage; Shorts
  for memes and hauls.
* **Personality:** loud, playful, comic, hands-on, trustworthy on prices.
* **Audience / emotions:** German-speaking fireworks fans preparing for New Year's Eve;
  anticipation, excitement, fun, confidence in the buying decision.
* **Look in one line:** black + fire red/orange + white, with yellow/gold highlights; comic caps
  for brand moments, geometric bold for information, condensed caps for captions; cut-out
  stickers with a white glow over fireworks or fire; the fire end screen.
* **Reference look:** the owner favourites `t_BTkcPy5sw` (ranking: defines graphics) and
  `dru73YpbQ4Y` (tour: defines real-world footage handling).

## 2. Colours

| Token | HEX | Role | Evidence |
| --- | --- | --- | --- |
| `brand_red` | `#C4131A` | primary accent, rank-label stroke | measured (d2ChnPz3FRg); **proposed canonical, owner to confirm** (observed range #B60F00-#DE243C) |
| `brand_red_neon` | `#DE243C` | studio neon sign | measured, high |
| `fire_dark` / `fire_mid` / `fire_hot` / `fire_glow` | `#3F0807` / `#7C2A0F` / `#D0410A` / `#F3BC19` | fire plates, warning | measured (end screen), high |
| `black` | `#000000` | outlines, hard shadows, spec cards | observed look, value proposed |
| `white` | `#FDFEFE` | text | measured, high |
| `silver_light` / `silver_mid` / `silver_dark` | `#ECECEC` / `#C1C1C1` / `#9B9B9B` | wordmark chrome | measured (banner), high |
| `accent_yellow` | `#F4DE19` | caption keywords, price tag | measured, high |
| `accent_gold` | `#D5A50A` | opening titles | measured, medium (one card) |
| `charcoal` | `#302F2F` | panels, grid plates | measured, medium (2025 sponsored work) |
| `thumbnail_blue` / `thumbnail_blue_light` | `#245FA7` / `#3F9AE3` | **thumbnails only** | measured, high |
| `mascot_cyan` | `#3ADCEE` | wolf eyes; tiny accents | measured (avatar) |
| `positive_green` | `#01D402` | positive values | measured, low (one Short) |
| `versus_red` / `versus_blue` | `#B60F00` / `#055183` | polls, charts, negative | measured, medium |

Semantic meanings (preference, proposed): highlight = accent_yellow, accent = brand_red,
title_accent = accent_gold, panel = charcoal, glow = white, price = black on accent_yellow,
positive = positive_green, negative = versus_red, warning = fire_glow, charts = versus_red,
versus_blue, accent_yellow, silver_mid.

Rules: graphics use palette colours only (mandatory); the thumbnail blues never appear in
in-video graphics (mandatory, proposed from the observation that blue appears only in
thumbnails); graphics never tint footage (footage belongs to `video_improvement`).

Approved combinations with the WCAG 2.x contrast the engine computes (minimum 4.5 for text, 3.0
for large text of 48 ref px or more):

| Foreground on background | Ratio | Use |
| --- | --- | --- |
| white on black | 20.78 | text |
| white on charcoal | 13.21 | text |
| white on brand_red | 6.02 | text |
| white on brand_red_neon | 4.70 | large text |
| white on fire_dark | 16.64 | text |
| white on fire_hot | 4.68 | large text |
| black on accent_yellow, accent_yellow on black | 15.31 | text |
| accent_gold on black | 9.22 | large text |
| accent_gold on charcoal | 5.86 | large text |
| black on fire_glow | 12.02 | text |
| positive_green on black | 10.42 | large text |
| white on versus_red | 6.80 | large text |
| white on versus_blue | 8.28 | large text |
| white on thumbnail_blue | 6.36 | large text (thumbnails) |
| silver_light on black | 17.78 | large text |

## 3. Typography

| Role | Face (bundled, SIL OFL 1.1) | Size (ref px) | Case | Observed as |
| --- | --- | --- | --- | --- |
| display | Bangers 400 | 120 | UPPER (locked) | T1 comic heavy: wordmark, end screen "ABONNIEREN", thumbnails |
| headline | Poppins 800 | 88 | as written | T2 geometric bold: "PLATZ 9", "Einkaufstour 2025/26" |
| body | Poppins 700 | 44 | as written | spec cards, lower thirds |
| caption | Anton 400 | 72 | UPPER (locked) | T3 condensed caps captions (2023, Shorts) |
| number | Poppins 800 | 96 | as written | prices, rank numbers |
| label | Poppins 700 | 30 | UPPER | "WERBUNG", credits |

Faces are locked per role. The looks are observed; the families are look-alikes chosen here
(the channel's real fonts are unknown). Readability floor: 28 ref px (locked). Fallbacks are
bundled faces only (Bangers -> Anton -> Poppins 800 -> 700 -> 400) and every substitution is
reported (`font.substituted`). German: all faces cover ÄÖÜ äöü ß €, German quotes, en dash and
ellipsis (checked at every resolve); Poppins has no capital ẞ, so upper case writes SS. Sizes
are reference pixels at a 1080 px short side: a 1080x1920 Short and a 1920x1080 video use the
same pixel sizes, 4K doubles them.

## 4. Graphic language

* **Stickers:** product cut-outs with a white outer glow (8 ref px, 90%) and a 5-10 degree
  tilt, over fireworks footage or a fire plate, never over a flat colour (mandatory, observed in
  8 samples).
* **Rank card "PLATZ N":** headline caps, white with a 10 ref px brand_red stroke, in the
  lower-third band; the next-rank black silhouette teaser is a motion-graphics template.
* **Spec card:** body text on a black panel, four lines Schuss / Höhe / Brenndauer / Kosten,
  full screen (card_safe).
* **Price tag:** number face, black on an accent_yellow panel, German decimal comma `15,99 €`
  (or `15,99 EUR`, the 2021-24 spelling); thousands with a dot (`1.299,00 €`). A dot decimal is
  a QC error.
* **Text treatment:** text over footage always has a black outline (6 ref px, locked) or a
  panel.
* **Panels:** charcoal at 90%, 12 ref px radius, 28 ref px padding; spacing unit 24 ref px.
* **Shadows and glow:** hard comic shadow (6 ref px offset, 85%) for T1; soft shadow (24 blur,
  55%) for PiP and brochure pages; title glow 28 ref px at 60%.
* **Density:** at most 2 overlays at once (ranking 3, NYE uncut 1), captions and labels
  excluded.
* **Emojis:** single punchline stickers or full-screen pops (preference).

## 5. Layout zones

Named, non-overlapping zones; captions and graphics are forced into them (a request outside its
zone is a QC error). Pixel rectangles as left,top width x height.

16:9 (1920x1080):

| Zone | Rectangle | Takes |
| --- | --- | --- |
| corner_badge | 67,54 317x130 | logo |
| title_band | 422,54 1076x184 | title, static meme line |
| sponsor_label | 1536,54 317x65 | "Werbung" |
| credit_label | 1536,130 317x54 | third-party credit |
| callout_left / callout_right | 67,259 547x432 / 1306,259 547x432 | callout, price tag, sticker, CTA |
| lower_third_band | 67,713 1786x151 | lower third, rank card, CTA, price tag |
| caption_band | 192,886 1536x140 | speech captions only |
| card_safe | 96,54 1728x972 | full-screen cards (they replace the picture) |

9:16 (1080x1920) adds platform keep-outs where nothing may be drawn: platform_top 0,0 1080x134,
platform_right 929,730 151x883 (action buttons), platform_bottom 0,1613 1080x307 (title and
channel row). Zones: corner_badge 54,154 216x96; credit_label 292,154 421x76; sponsor_label
734,154 292x76; title_band 54,269 972x307 (meme line in the upper third); callout_left 54,614
421x346; callout_right 497,614 410x346; caption_band 54,1114 853x249 (lower middle);
lower_third_band 54,1382 853x212; card_safe 54,154 853x1440.

1:1 (1080x1080): corner_badge 43,43 195x108; title_band 259,43 562x151; sponsor_label 842,43
195x54; credit_label 842,108 195x54; callout_left 43,216 281x410; callout_right 756,216 281x410;
lower_third_band 43,648 994x151; caption_band 86,821 908x194; card_safe 43,43 994x994.

The centre of every format stays free for the subject (product, burst). The rectangles are
proposed from the observed placements ("Werbung" and credits top right, rank card in the lower
third, captions lower middle or centre, meme line in the upper third); the zone rule itself is
mandatory. Reframing receives the zones as safe areas (`ResolvedStyle.safe_areas()`).

## 6. Captions

* **Long form default: no burned-in speech captions** (6 of 6 recent long samples;
  `caption_engine` decides).
* **Word-group look** (Shorts, caption-style long videos): Anton caps, 72 ref px (Shorts
  80-84), white with a thin 3 ref px black outline per letter (tracking 0.05 em) and a soft shadow, keyword in accent_yellow, at
  most 22 characters per line and 2 lines, caption_band, no trailing full stop.
* **Line look** (meme Shorts): Poppins 800 sentence case, 56 ref px, white with a 5 ref px dark
  outline, one static line in the title_band (upper third) from frame 1.
* Text is German with correct umlauts; prices use a decimal comma.

## 7. Titles, CTA, end screen

* Opening title: gold headline with glow and a thin outline, full screen ("Einkaufstour" in gold
  above the white season "2025/26").
* Lower third: body text on a charcoal panel. Callout: body text with outline in a callout zone.
  CTA: display caps.
* Sponsor label: "WERBUNG" in the sponsor_label zone for the whole sponsored duration
  (mandatory, 6 sponsored samples). Credits: label caps in the credit_label zone while
  third-party footage is visible (mandatory).
* **End screen** (mandatory, 8 of 11 long samples): about 20 s fire/lava plate with the wolf,
  "ABONNIEREN" and "WEITER SCHAUEN" in display caps above the YouTube end-screen slots, entered
  by a pixel dissolve. The spoken catchphrase "abonnieren, liken und Schwarzpulver inhalieren"
  belongs to the edit, not to graphics.
* **Membership plug:** "Kanalmitglied werden" on the fire/wolf plate after the hook, entered by
  a zoom tunnel (observed in 6 samples; preference).

## 8. Motion

* Character **punchy**: comic pop-ins with overshoot (calm for brochure reviews and NYE uncut).
* Easings (cubic-bezier control points; consumers may use only these): `pop` (0.34, 1.56,
  0.64, 1) entries with overshoot; `enter` (0.16, 1, 0.3, 1) expo out; `exit` (0.7, 0, 0.84,
  0) expo in; `move` (0.65, 0, 0.35, 1) in-out; `linear` for counters.
* Durations (seconds): entry 0.20-0.45, exit 0.15-0.35, emphasis 0.10-0.30; stagger 0.06;
  overshoot 12%; shortest hold of a text graphic 1.2 s; motion blur on fast moves.
* Intensity ceiling (0-4, the transition scale): generic, unboxing, compilation 2; ranking and
  tour 3; brochure review and NYE uncut 1; Shorts haul 4; versus 3. Transitions mark structure
  (intro, rank change, end), not every cut. The values are proposals derived from the observed
  pop-ins and cut rates; `transition_engine` owns the transition vocabulary.

## 8a. Emoji and transitions (identity v2)

v2 is v1 byte-for-byte plus two token groups (in v1 both are absent, `None`, and not stored):

* `emoji.face` (**mandatory**, proposed, medium): `noto_color_emoji` = Google Noto Color Emoji
  2.051, COLRv1 vector colour glyphs without flags (`fonts/noto_emoji/Noto-COLRv1-noflags.ttf`,
  2.99 MB, sha256 `469e3b92...`, SIL OFL 1.1: commercial use allowed, not sold on its own;
  from github.com/googlefonts/noto-emoji tag v2.051). COLRv1 was chosen over the 10.7 MB CBDT
  bitmap font: the pinned headless Chrome renders both, vectors stay sharp at every size, and
  the text outline is not drawn around colour glyphs. The face must cover `EMOJI_GLYPHS`: the
  channel's punchlines 😍 🤯 😱 🤔 👀 (discovery 6.5) and caption_engine's vocabulary 💶 🏆 💥
  🔥 ⚡ 🔊 💸 ⚠ ❌ ❤ (checked at every resolve; FE0F selects the emoji form and has no glyph).
  Brand rule `emoji.bundled_face`: never a system emoji font. Emoji stay single punchlines
  (`emoji.punchline`).
* `transitions` (discovery 6.6, 11): `default` hard_cut (**mandatory**); `approved` hard_cut,
  match/action/jump cut, cross_dissolve, fade_in/out, flash, zoom_tunnel, pixel_dissolve
  (preference; rankings and haul Shorts add slide and whip_pan); `prohibited` dip_to_white,
  glitch, wipe, shape, mask_reveal (**mandatory**); `bookend_only` zoom_tunnel (membership
  plug) and pixel_dissolve (end screen) (**mandatory**); `duration_min/max` 0.1-1.2 s
  (preference, proposed). Brand rule `transitions.closed`. The drift validator checks a
  `MotionPhase.TRANSITION` against the duration range (a v1 style has none).

## 9. Logo and brand assets

No logo file exists. The identity models a `logo` slot (variants full_color / white / black,
corner_badge zone, min height 56 ref px, clear space 16 ref px, opacity 90%, no persistent
watermark) and reports it as missing; nothing invents a logo. Further slots, all missing today
and bound to Media Library assets per project: wolf_mascot (required), fire_plate (required),
wordmark, neon_script, fire_blur_plate, charcoal_grid, membership_plate, thumbnail_background,
corner_stripes. `visual_asset_generation` prepares them.

## 10. Format profiles

| Profile | Native | Overrides | Evidence |
| --- | --- | --- | --- |
| generic | 16:9 | none | recurring patterns |
| ranking | 16:9 | intensity 3, 3 graphics at once | t_BTkcPy5sw, d2ChnPz3FRg, bw7fkdSK1Vk |
| brochure_review | 16:9 | intensity 1, calm | 9etWVDZnyOs |
| shopping_tour | 16:9 | intensity 3 | dru73YpbQ4Y |
| unboxing | 16:9 | none | OkjnKFjRFt4 |
| compilation | 16:9 | none | _Ka5c6jMYRg, ZUSKEXTAuKI, tDi6cJFBP0s |
| nye_uncut | 16:9 | intensity 1, 1 graphic, calm | y_mj09m0XB4 |
| shorts_meme | 9:16 | caption look `line` | zIxejdVAa0E, P_L7KHcB-_Y |
| shorts_haul | 9:16 | intensity 4, captions 84 ref px | nsIVE20ixMM, hPWgWyzYtq8 |
| shorts_product_test | 9:16 | captions 80 ref px | xCoOYkqzgDo, y1q4lpEEFcI |
| shorts_versus | 9:16 | intensity 3 | r8A57tnbyKA |

Story formats: top_n -> ranking; review, talking_head -> brochure_review; unboxing ->
unboxing; product_introduction, montage -> compilation; short -> shorts_haul; generic,
tutorial, interview, podcast, documentary -> generic. Without a story format (the no-speech
path) a 9:16 output gets shorts_meme, everything else generic.

## 11. Versioning and overrides

* Identity versions live in code and are never edited after release; each version's content is
  pinned by a sha256 (`IDENTITY_DIGESTS`). A change is `MESC_IDENTITY_V2`.
* A project's resolved style is stored in the Media Library and pinned: old projects keep their
  identity, a newer one is used only on request. `VisualStyleCatalog.affected_by(version)` lists
  stored styles whose look would change.
* Overrides (dotted paths such as `captions.word_group.size`) need a reason, can change only
  preferences and are recorded with their previous value. Precedence: identity -> format
  profile -> project -> section.

## 12. Evidence-based vs proposed

**Evidence-based (observed or measured):** the palette values; the black/red/fire/white/yellow
look; the three type looks (comic caps, geometric bold, condensed caps); white text with a black
outline; stickers with a white glow and tilt over fireworks or fire; the PLATZ N rank card with
a red stroke; the spec card on black; German comma prices; "Werbung" and credits top right; the
caption looks (word groups with a yellow keyword, meme line in the upper third); no burned-in
captions in recent long videos; the fire end screen with ABONNIEREN / WEITER SCHAUEN entered by a
pixel dissolve; the membership plug with a zoom tunnel; thumbnail blue only in thumbnails; the
per-format cut rates behind the intensity ceilings.

**Proposed here (design decisions, to confirm with the owner):** the canonical brand red
`#C4131A`; the font families (Bangers, Poppins, Anton); the type scale and readability floor;
the exact zone rectangles and 9:16 keep-outs; the semantic colour mapping
(positive/negative/warning/charts); spacing, radius, shadow and glow values; easing curves and
duration ranges; the logo slot placement; the contrast minimums (WCAG AA); the rule that
thumbnail blue stays out of video graphics.

## 13. Changelog

* **v1, 2026-11-02 (changed in place before any project stored a style):** the word-group
  caption outline was 7 ref px with 0.01 em tracking. Rendered through motion_graphics_engine
  (HyperFrames), Anton's narrow letter gaps filled up and the caption became a solid black box,
  which is not the channel look (white condensed caps, each letter outlined). Now: outline
  3 ref px, caption tracking 0.05 em, soft shadow kept; callout outline 6 -> 4 ref px. New
  locked rule `text.open_counters` with a per-face maximum outline/size ratio (Anton 5%,
  Bangers 9%, Poppins 11%), checked in the identity QC and by the drift validator
  (`text.stroke_heavy`). Rank card (10 on 96 px Poppins), price tag, title and sponsor label
  were rendered too and keep open counters. The v1 digest was re-recorded. From now on v1 is
  frozen: further changes need v2.

* **v2, 2026-11-02 (new version, v1 untouched):** emoji face + `emoji` tokens and the
  `transitions` tokens (section 8a); format profiles `ranking` and `shorts_haul` (version 2)
  widen `transitions.approved` by slide and whip_pan (skipped under v1, which has no such
  token). Why a new version and not v1 data: v1 styles are already stored and pinned in
  project libraries and their fingerprints key every downstream cache. Fields a later version
  adds are marked `ADDED_IN` and left out while absent, so the v1 digest
  (`a3942361...`) and every stored v1 fingerprint stay identical. New projects resolve v2;
  existing projects keep v1 until a command names v2.

## 14. Full token reference

Generated from `knob_fields(StyleTokens)` and `MESC_IDENTITY_V1.token_rules`. Colour values in
`semantic`, `captions` and `titles` are palette names. Unit `ref_px` = pixels at a 1080 px short
side.

| Token | Value | Unit / range | Rule | Evidence / confidence |
| --- | --- | --- | --- | --- |
| `palette.brand_red` | `#C4131A` |  | mandatory | measured/medium |
| `palette.brand_red_neon` | `#DE243C` |  | mandatory | measured/high |
| `palette.fire_dark` | `#3F0807` |  | mandatory | measured/high |
| `palette.fire_mid` | `#7C2A0F` |  | mandatory | measured/high |
| `palette.fire_hot` | `#D0410A` |  | mandatory | measured/high |
| `palette.fire_glow` | `#F3BC19` |  | mandatory | measured/high |
| `palette.black` | `#000000` |  | mandatory | observed/high |
| `palette.white` | `#FDFEFE` |  | mandatory | measured/high |
| `palette.silver_light` | `#ECECEC` |  | mandatory | measured/medium |
| `palette.silver_mid` | `#C1C1C1` |  | mandatory | measured/medium |
| `palette.silver_dark` | `#9B9B9B` |  | mandatory | measured/medium |
| `palette.accent_yellow` | `#F4DE19` |  | mandatory | measured/high |
| `palette.accent_gold` | `#D5A50A` |  | mandatory | measured/medium |
| `palette.charcoal` | `#302F2F` |  | mandatory | measured/medium |
| `palette.thumbnail_blue` | `#245FA7` |  | mandatory | measured/high |
| `palette.thumbnail_blue_light` | `#3F9AE3` |  | mandatory | measured/high |
| `palette.mascot_cyan` | `#3ADCEE` |  | mandatory | measured/medium |
| `palette.positive_green` | `#01D402` |  | mandatory | measured/low |
| `palette.versus_red` | `#B60F00` |  | mandatory | measured/medium |
| `palette.versus_blue` | `#055183` |  | mandatory | measured/medium |
| `semantic.highlight` | `accent_yellow` |  | preference | observed/high |
| `semantic.accent` | `brand_red` |  | preference | observed/high |
| `semantic.title_accent` | `accent_gold` |  | preference | proposed/medium |
| `semantic.panel` | `charcoal` |  | preference | proposed/medium |
| `semantic.glow` | `white` |  | preference | proposed/medium |
| `semantic.price_fill` | `accent_yellow` |  | preference | proposed/medium |
| `semantic.price_text` | `black` |  | preference | proposed/medium |
| `semantic.positive` | `positive_green` |  | preference | proposed/medium |
| `semantic.negative` | `versus_red` |  | preference | proposed/medium |
| `semantic.warning` | `fire_glow` |  | preference | proposed/medium |
| `semantic.chart_a` | `versus_red` |  | preference | proposed/medium |
| `semantic.chart_b` | `versus_blue` |  | preference | proposed/medium |
| `semantic.chart_c` | `accent_yellow` |  | preference | proposed/medium |
| `semantic.chart_d` | `silver_mid` |  | preference | proposed/medium |
| `typography.display.face` | `bangers_400` |  | mandatory | proposed/medium |
| `typography.display.size` | `120.0` | ref_px 16.0-240.0 | preference | proposed/medium |
| `typography.display.tracking` | `0.02` | em -0.05-0.2 | preference | proposed/medium |
| `typography.display.leading` | `1.0` | ratio 0.9-1.6 | preference | proposed/medium |
| `typography.display.case` | `upper` |  | mandatory | observed/high |
| `typography.headline.face` | `poppins_800` |  | mandatory | proposed/medium |
| `typography.headline.size` | `88.0` | ref_px 16.0-240.0 | preference | proposed/medium |
| `typography.headline.tracking` | `0.0` | em -0.05-0.2 | preference | proposed/medium |
| `typography.headline.leading` | `1.05` | ratio 0.9-1.6 | preference | proposed/medium |
| `typography.headline.case` | `as_written` |  | preference | proposed/medium |
| `typography.body.face` | `poppins_700` |  | mandatory | proposed/medium |
| `typography.body.size` | `44.0` | ref_px 16.0-240.0 | preference | proposed/medium |
| `typography.body.tracking` | `0.0` | em -0.05-0.2 | preference | proposed/medium |
| `typography.body.leading` | `1.15` | ratio 0.9-1.6 | preference | proposed/medium |
| `typography.body.case` | `as_written` |  | preference | proposed/medium |
| `typography.caption.face` | `anton_400` |  | mandatory | proposed/medium |
| `typography.caption.size` | `72.0` | ref_px 16.0-240.0 | preference | proposed/medium |
| `typography.caption.tracking` | `0.05` | em -0.05-0.2 | preference | proposed/medium |
| `typography.caption.leading` | `1.05` | ratio 0.9-1.6 | preference | proposed/medium |
| `typography.caption.case` | `upper` |  | mandatory | observed/medium |
| `typography.number.face` | `poppins_800` |  | mandatory | proposed/medium |
| `typography.number.size` | `96.0` | ref_px 16.0-240.0 | preference | proposed/medium |
| `typography.number.tracking` | `0.0` | em -0.05-0.2 | preference | proposed/medium |
| `typography.number.leading` | `1.0` | ratio 0.9-1.6 | preference | proposed/medium |
| `typography.number.case` | `as_written` |  | preference | proposed/medium |
| `typography.label.face` | `poppins_700` |  | mandatory | proposed/medium |
| `typography.label.size` | `30.0` | ref_px 16.0-240.0 | preference | proposed/medium |
| `typography.label.tracking` | `0.04` | em -0.05-0.2 | preference | proposed/medium |
| `typography.label.leading` | `1.15` | ratio 0.9-1.6 | preference | proposed/medium |
| `typography.label.case` | `upper` |  | preference | proposed/medium |
| `typography.min_size` | `28.0` | ref_px 16.0-60.0 | mandatory | proposed/medium |
| `graphics.text_stroke` | `6.0` | ref_px 0.0-20.0 | mandatory | observed/high |
| `graphics.graphic_stroke` | `4.0` | ref_px 0.0-20.0 | preference | proposed/medium |
| `graphics.rank_stroke` | `10.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `graphics.corner_radius` | `12.0` | ref_px 0.0-60.0 | preference | proposed/medium |
| `graphics.panel_opacity` | `0.9` | fraction 0.0-1.0 | preference | proposed/medium |
| `graphics.panel_padding` | `28.0` | ref_px 0.0-120.0 | preference | proposed/medium |
| `graphics.spacing` | `24.0` | ref_px 4.0-96.0 | preference | proposed/medium |
| `graphics.hard_shadow_offset` | `6.0` | ref_px 0.0-30.0 | preference | proposed/medium |
| `graphics.hard_shadow_opacity` | `0.85` | fraction 0.0-1.0 | preference | proposed/medium |
| `graphics.soft_shadow_blur` | `24.0` | ref_px 0.0-120.0 | preference | proposed/medium |
| `graphics.soft_shadow_opacity` | `0.55` | fraction 0.0-1.0 | preference | proposed/medium |
| `graphics.sticker_glow` | `8.0` | ref_px 0.0-40.0 | preference | observed/high |
| `graphics.sticker_glow_opacity` | `0.9` | fraction 0.0-1.0 | preference | observed/high |
| `graphics.sticker_tilt_min` | `5.0` | degrees 0.0-20.0 | preference | observed/medium |
| `graphics.sticker_tilt_max` | `10.0` | degrees 0.0-20.0 | preference | observed/medium |
| `graphics.title_glow` | `28.0` | ref_px 0.0-120.0 | preference | observed/medium |
| `graphics.title_glow_opacity` | `0.6` | fraction 0.0-1.0 | preference | proposed/medium |
| `graphics.pip_border` | `4.0` | ref_px 0.0-20.0 | preference | proposed/medium |
| `graphics.max_simultaneous` | `2` | 1-5 | preference | proposed/medium |
| `graphics.price_format` | `comma_euro_symbol` |  | preference | observed/high |
| `captions.word_group.role` | `caption` |  | preference | observed/medium |
| `captions.word_group.size` | `72.0` | ref_px 28.0-160.0 | preference | observed/medium |
| `captions.word_group.case` | `upper` |  | preference | observed/medium |
| `captions.word_group.fill` | `white` |  | preference | observed/medium |
| `captions.word_group.stroke` | `black` |  | preference | observed/medium |
| `captions.word_group.stroke_width` | `3.0` | ref_px 0.0-20.0 | preference | observed/medium |
| `captions.word_group.shadow` | `True` |  | preference | observed/medium |
| `captions.word_group.highlight` | `accent_yellow` |  | preference | observed/high |
| `captions.word_group.max_chars_per_line` | `22` | 8-42 | preference | observed/medium |
| `captions.word_group.max_lines` | `2` | 1-3 | preference | observed/medium |
| `captions.word_group.zone` | `caption_band` |  | preference | observed/medium |
| `captions.word_group.strip_trailing_period` | `True` |  | preference | observed/medium |
| `captions.line.role` | `headline` |  | preference | observed/medium |
| `captions.line.size` | `56.0` | ref_px 28.0-160.0 | preference | observed/medium |
| `captions.line.case` | `sentence` |  | preference | observed/medium |
| `captions.line.fill` | `white` |  | preference | observed/medium |
| `captions.line.stroke` | `black` |  | preference | observed/medium |
| `captions.line.stroke_width` | `5.0` | ref_px 0.0-20.0 | preference | observed/medium |
| `captions.line.shadow` | `True` |  | preference | observed/medium |
| `captions.line.highlight` | `accent_yellow` |  | preference | observed/medium |
| `captions.line.max_chars_per_line` | `32` | 8-42 | preference | observed/medium |
| `captions.line.max_lines` | `2` | 1-3 | preference | observed/medium |
| `captions.line.zone` | `title_band` |  | preference | observed/medium |
| `captions.line.strip_trailing_period` | `True` |  | preference | observed/medium |
| `captions.default_look` | `word_group` |  | preference | observed/medium |
| `titles.opening_title.role` | `headline` |  | preference | observed/medium |
| `titles.opening_title.size` | `110.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.opening_title.case` | `as_written` |  | preference | observed/medium |
| `titles.opening_title.fill` | `accent_gold` |  | preference | observed/medium |
| `titles.opening_title.stroke` | `black` |  | preference | observed/medium |
| `titles.opening_title.stroke_width` | `4.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.opening_title.background` | `none` |  | preference | observed/medium |
| `titles.opening_title.panel` | `charcoal` |  | preference | observed/medium |
| `titles.opening_title.glow` | `True` |  | preference | observed/medium |
| `titles.opening_title.zone` | `card_safe` |  | preference | observed/medium |
| `titles.opening_title.max_lines` | `2` | 1-6 | preference | observed/medium |
| `titles.opening_title.max_chars_per_line` | `18` | 4-60 | preference | observed/medium |
| `titles.opening_title.entry` | `fade_in` |  | preference | observed/medium |
| `titles.chapter_title.role` | `headline` |  | preference | observed/medium |
| `titles.chapter_title.size` | `96.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.chapter_title.case` | `upper` |  | preference | observed/medium |
| `titles.chapter_title.fill` | `white` |  | preference | observed/medium |
| `titles.chapter_title.stroke` | `brand_red` |  | preference | observed/medium |
| `titles.chapter_title.stroke_width` | `10.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.chapter_title.background` | `none` |  | preference | observed/medium |
| `titles.chapter_title.panel` | `charcoal` |  | preference | observed/medium |
| `titles.chapter_title.glow` | `False` |  | preference | observed/medium |
| `titles.chapter_title.zone` | `lower_third_band` |  | preference | observed/medium |
| `titles.chapter_title.max_lines` | `1` | 1-6 | preference | observed/medium |
| `titles.chapter_title.max_chars_per_line` | `14` | 4-60 | preference | observed/medium |
| `titles.chapter_title.entry` | `pop_in` |  | preference | observed/medium |
| `titles.lower_third.role` | `body` |  | preference | observed/medium |
| `titles.lower_third.size` | `44.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.lower_third.case` | `as_written` |  | preference | observed/medium |
| `titles.lower_third.fill` | `white` |  | preference | observed/medium |
| `titles.lower_third.stroke` | `black` |  | preference | observed/medium |
| `titles.lower_third.stroke_width` | `0.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.lower_third.background` | `panel` |  | preference | observed/medium |
| `titles.lower_third.panel` | `charcoal` |  | preference | observed/medium |
| `titles.lower_third.glow` | `False` |  | preference | observed/medium |
| `titles.lower_third.zone` | `lower_third_band` |  | preference | observed/medium |
| `titles.lower_third.max_lines` | `2` | 1-6 | preference | observed/medium |
| `titles.lower_third.max_chars_per_line` | `28` | 4-60 | preference | observed/medium |
| `titles.lower_third.entry` | `pop_in` |  | preference | observed/medium |
| `titles.callout.role` | `body` |  | preference | observed/medium |
| `titles.callout.size` | `40.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.callout.case` | `as_written` |  | preference | observed/medium |
| `titles.callout.fill` | `white` |  | preference | observed/medium |
| `titles.callout.stroke` | `black` |  | preference | observed/medium |
| `titles.callout.stroke_width` | `4.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.callout.background` | `none` |  | preference | observed/medium |
| `titles.callout.panel` | `charcoal` |  | preference | observed/medium |
| `titles.callout.glow` | `False` |  | preference | observed/medium |
| `titles.callout.zone` | `callout_left` |  | preference | observed/medium |
| `titles.callout.max_lines` | `3` | 1-6 | preference | observed/medium |
| `titles.callout.max_chars_per_line` | `24` | 4-60 | preference | observed/medium |
| `titles.callout.entry` | `pop_in` |  | preference | observed/medium |
| `titles.price_tag.role` | `number` |  | preference | observed/medium |
| `titles.price_tag.size` | `64.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.price_tag.case` | `as_written` |  | preference | observed/medium |
| `titles.price_tag.fill` | `black` |  | preference | observed/medium |
| `titles.price_tag.stroke` | `black` |  | preference | observed/medium |
| `titles.price_tag.stroke_width` | `0.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.price_tag.background` | `panel` |  | preference | observed/medium |
| `titles.price_tag.panel` | `accent_yellow` |  | preference | observed/medium |
| `titles.price_tag.glow` | `False` |  | preference | observed/medium |
| `titles.price_tag.zone` | `callout_right` |  | preference | observed/medium |
| `titles.price_tag.max_lines` | `1` | 1-6 | preference | observed/medium |
| `titles.price_tag.max_chars_per_line` | `12` | 4-60 | preference | observed/medium |
| `titles.price_tag.entry` | `pop_in` |  | preference | observed/medium |
| `titles.spec_card.role` | `body` |  | preference | observed/medium |
| `titles.spec_card.size` | `40.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.spec_card.case` | `as_written` |  | preference | observed/medium |
| `titles.spec_card.fill` | `white` |  | preference | observed/medium |
| `titles.spec_card.stroke` | `black` |  | preference | observed/medium |
| `titles.spec_card.stroke_width` | `0.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.spec_card.background` | `panel` |  | preference | observed/medium |
| `titles.spec_card.panel` | `black` |  | preference | observed/medium |
| `titles.spec_card.glow` | `False` |  | preference | observed/medium |
| `titles.spec_card.zone` | `card_safe` |  | preference | observed/medium |
| `titles.spec_card.max_lines` | `4` | 1-6 | preference | observed/medium |
| `titles.spec_card.max_chars_per_line` | `30` | 4-60 | preference | observed/medium |
| `titles.spec_card.entry` | `slide_in` |  | preference | observed/medium |
| `titles.cta.role` | `display` |  | preference | observed/medium |
| `titles.cta.size` | `72.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.cta.case` | `upper` |  | preference | observed/medium |
| `titles.cta.fill` | `white` |  | preference | observed/medium |
| `titles.cta.stroke` | `black` |  | preference | observed/medium |
| `titles.cta.stroke_width` | `6.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.cta.background` | `none` |  | preference | observed/medium |
| `titles.cta.panel` | `charcoal` |  | preference | observed/medium |
| `titles.cta.glow` | `False` |  | preference | observed/medium |
| `titles.cta.zone` | `lower_third_band` |  | preference | observed/medium |
| `titles.cta.max_lines` | `1` | 1-6 | preference | observed/medium |
| `titles.cta.max_chars_per_line` | `20` | 4-60 | preference | observed/medium |
| `titles.cta.entry` | `pop_in` |  | preference | observed/medium |
| `titles.sponsor_label.role` | `label` |  | preference | observed/medium |
| `titles.sponsor_label.size` | `30.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.sponsor_label.case` | `upper` |  | preference | observed/medium |
| `titles.sponsor_label.fill` | `white` |  | preference | observed/medium |
| `titles.sponsor_label.stroke` | `black` |  | preference | observed/medium |
| `titles.sponsor_label.stroke_width` | `3.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.sponsor_label.background` | `none` |  | preference | observed/medium |
| `titles.sponsor_label.panel` | `charcoal` |  | preference | observed/medium |
| `titles.sponsor_label.glow` | `False` |  | preference | observed/medium |
| `titles.sponsor_label.zone` | `sponsor_label` |  | mandatory | observed/high |
| `titles.sponsor_label.max_lines` | `1` | 1-6 | preference | observed/medium |
| `titles.sponsor_label.max_chars_per_line` | `12` | 4-60 | preference | observed/medium |
| `titles.sponsor_label.entry` | `cut` |  | preference | observed/medium |
| `titles.credit.role` | `label` |  | preference | observed/medium |
| `titles.credit.size` | `28.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.credit.case` | `upper` |  | preference | observed/medium |
| `titles.credit.fill` | `white` |  | preference | observed/medium |
| `titles.credit.stroke` | `black` |  | preference | observed/medium |
| `titles.credit.stroke_width` | `3.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.credit.background` | `none` |  | preference | observed/medium |
| `titles.credit.panel` | `charcoal` |  | preference | observed/medium |
| `titles.credit.glow` | `False` |  | preference | observed/medium |
| `titles.credit.zone` | `credit_label` |  | mandatory | observed/high |
| `titles.credit.max_lines` | `1` | 1-6 | preference | observed/medium |
| `titles.credit.max_chars_per_line` | `30` | 4-60 | preference | observed/medium |
| `titles.credit.entry` | `cut` |  | preference | observed/medium |
| `titles.end_screen_label.role` | `display` |  | preference | observed/medium |
| `titles.end_screen_label.size` | `80.0` | ref_px 16.0-240.0 | preference | observed/medium |
| `titles.end_screen_label.case` | `upper` |  | preference | observed/medium |
| `titles.end_screen_label.fill` | `white` |  | preference | observed/medium |
| `titles.end_screen_label.stroke` | `black` |  | preference | observed/medium |
| `titles.end_screen_label.stroke_width` | `6.0` | ref_px 0.0-30.0 | preference | observed/medium |
| `titles.end_screen_label.background` | `none` |  | preference | observed/medium |
| `titles.end_screen_label.panel` | `charcoal` |  | preference | observed/medium |
| `titles.end_screen_label.glow` | `False` |  | preference | observed/medium |
| `titles.end_screen_label.zone` | `card_safe` |  | preference | observed/medium |
| `titles.end_screen_label.max_lines` | `1` | 1-6 | preference | observed/medium |
| `titles.end_screen_label.max_chars_per_line` | `16` | 4-60 | preference | observed/medium |
| `titles.end_screen_label.entry` | `cut` |  | preference | observed/medium |
| `motion.character` | `punchy` |  | preference | proposed/medium |
| `motion.entry_easing` | `pop` |  | preference | proposed/medium |
| `motion.exit_easing` | `exit` |  | preference | proposed/medium |
| `motion.move_easing` | `move` |  | preference | proposed/medium |
| `motion.entry_pattern` | `pop_in` |  | preference | proposed/medium |
| `motion.exit_pattern` | `pop_out` |  | preference | proposed/medium |
| `motion.entry_min` | `0.2` | seconds 0.0-2.0 | preference | proposed/medium |
| `motion.entry_max` | `0.45` | seconds 0.0-2.0 | preference | proposed/medium |
| `motion.exit_min` | `0.15` | seconds 0.0-2.0 | preference | proposed/medium |
| `motion.exit_max` | `0.35` | seconds 0.0-2.0 | preference | proposed/medium |
| `motion.emphasis_min` | `0.1` | seconds 0.0-2.0 | preference | proposed/medium |
| `motion.emphasis_max` | `0.3` | seconds 0.0-2.0 | preference | proposed/medium |
| `motion.stagger` | `0.06` | seconds 0.0-0.5 | preference | proposed/medium |
| `motion.overshoot` | `0.12` | fraction 0.0-0.4 | preference | proposed/medium |
| `motion.min_hold` | `1.2` | seconds 0.3-5.0 | preference | proposed/medium |
| `motion.intensity_ceiling` | `2` | 0-4 | preference | observed/medium |
| `motion.motion_blur` | `True` |  | preference | proposed/medium |
| `branding.logo_zone` | `corner_badge` |  | preference | proposed/low |
| `branding.logo_variant` | `full_color` |  | preference | proposed/low |
| `branding.logo_min_height` | `56.0` | ref_px 24.0-200.0 | preference | proposed/low |
| `branding.logo_safe_margin` | `16.0` | ref_px 0.0-80.0 | preference | proposed/low |
| `branding.logo_opacity` | `0.9` | fraction 0.0-1.0 | preference | proposed/low |
| `branding.watermark` | `False` |  | preference | proposed/low |
| `end_screen.seconds` | `20.0` | seconds 10.0-25.0 | preference | observed/high |
| `end_screen.subscribe_label` | `ABONNIEREN` |  | mandatory | observed/high |
| `end_screen.watch_next_label` | `WEITER SCHAUEN` |  | mandatory | observed/high |
| `end_screen.background` | `fire_plate` |  | preference | observed/high |
| `end_screen.entry_transition` | `pixel_dissolve` |  | mandatory | observed/high |
| `end_screen.membership_label` | `Kanalmitglied werden` |  | preference | observed/high |
| `end_screen.membership_transition` | `zoom_tunnel` |  | preference | observed/high |
