# Visual Style Engine

The single source of truth for how the MEsc channel looks. It defines the channel identity as
versioned data (colours, fonts, type scale, graphic language, layout zones, caption and title
looks, motion feel, logo and brand-asset slots), resolves it per project and output into an
immutable `ResolvedStyle`, stores that as a pinned `visual_style` document in the Media Library
and validates what other modules drew against it.

It renders nothing, animates nothing, grades no footage and decides no caption, transition or
edit. Owners of those: `motion_graphics_engine`, `caption_engine`, `transition_engine`,
`vfx_engine`, `visual_asset_generation`, `video_improvement`.

Human-readable identity guide (every token, its evidence and the design decisions):
`docs/layer4/visual-identity.md`.

## Contract (`application/contracts.py`)

| Type | Signature / purpose |
| --- | --- |
| `StyleResolver` | `execute(ResolveStyleCommand) -> Result[ResolveStyleResult, MediaError \| VisualStyleError]` (blocking library I/O: run as a job) |
| `ResolveStyleCommand` | `anchor_asset_id` (required), `project_id=None`, `width=None`, `height=None`, `config=StyleConfig()`, `story_format=None`, `identity_version=None`, `brand_assets=()` |
| `StyleConfig` | `profile=None`, `overrides: tuple[Override(path, value, reason)]`, `section_overrides: tuple[SectionOverride(section_id, overrides)]`, `sponsored=False` |
| `ResolveStyleResult` | `style: ResolvedStyle`, `document: MediaAssetDto`, `ref: StyleRef`, `created: bool` |
| `StyleRef` | `document_id`, `fingerprint`, `identity_version`, `profile`: what a plan keeps |
| `VisualStyleCatalog` | `latest(anchor, *, project_id, profile, output)` (= the pinned style), `history(anchor)`, `load(document_id)`, `load_ref(StyleRef)` (refuses a fingerprint mismatch), `affected_by(identity_version)` |
| `StyleValidator` | `validate(style, StyleUsage) -> tuple[QcIssue, ...]` (drift check) |
| `FontFiles` | `locate(FontFace) -> Result[FontFile(face, path, sha256), FontUnavailable]`, `missing_glyphs(face, text)` |
| `resolve_style(identity, config, *, created_at, width, height, ...)` | the pure resolution, for consumers' unit tests (nothing stored) |
| `PROFILES`, `STORY_FORMAT_PROFILE`, `ASPECT_PROFILE`, `DEFAULT_OUTPUT`, `knob_fields()` | format profiles and the settings-UI description of every knob |

`ResolvedStyle` fields: `identity_id`, `identity_version`, `profile`, `profile_version`,
`style_version`, `supersedes`, `project_id` (None on the no-speech path), `anchor_asset_id`,
`output: OutputFormat(width, height)`, `sponsored`, `tokens: StyleTokens` (the resolved token
tree), `faces`, `types` (per type role: face + size in output px), `easings`, `zones` (output
px + fractions), `combinations` (computed WCAG ratios), `contrast`, `thumbnail_only`,
`brand_assets`, `token_rules`, `brand_rules`, `prohibited`, `overrides` (every applied
override, its layer, previous value and reason), `sections`, `checks`, `fingerprint`.
Helpers: `px(ref_px)`, `hex(name_or_meaning, section_id=None)`, `palette()` (HEX/RGB/HSL),
`zone(name)`, `type_style(role)`, `face(key)`, `easing(name).css()`, `tokens_for(section_id)`,
`safe_areas()`, `mandatory_rules`, `preferences`, `locked_tokens`, `missing_brand_assets`,
`format_price(cents)`.

## How a consumer uses it

```python
from media_house.modules.visual_style_engine.application.contracts import (
    ElementKind,
    PlacementUse,
    ResolveStyleCommand,
    StyleConfig,
    StyleResolver,
    StyleUsage,
    StyleValidator,
    TextUse,
    VisualStyleCatalog,
)

# 1. Get the style (pinned: an existing project keeps its identity version).
result = resolver.execute(
    ResolveStyleCommand(
        anchor_asset_id=anchor,
        project_id=project,
        width=1080,
        height=1920,
        story_format=story_format,
    )
)  # or config=StyleConfig(profile="shorts_haul")
style, ref = result.value.style, result.value.ref
# later / elsewhere: style = catalog.load_ref(ref).value

# 2. Use only its tokens.
cap = style.tokens.captions.word_group  # look of word-group captions
face = style.type_style(cap.role).face  # FontFace(family="Anton", resource=..., sha256=...)
path = fonts.locate(face).value.path  # local TTF, verified by sha256
size = style.px(cap.size)  # 84 ref px on shorts_haul -> output px
fill, keyword = style.hex(cap.fill), style.hex(cap.highlight)  # '#FDFEFE', '#F4DE19'
band = style.zone(cap.zone)  # caption_band: left, top, width, height
ease = style.easing(style.tokens.motion.entry_easing).css()
price = style.format_price(1599)  # '15,99 €'

# 3. Cache key and plan: put style.fingerprint in the key and `ref` in the plan.
# 4. Declare what you drew and record the findings in your plan's checks.
issues = validator.validate(
    style,
    StyleUsage(
        module="caption_engine",
        fingerprint=style.fingerprint,
        width=1080,
        height=1920,
        texts=(
            TextUse(
                element_id="cap:1",
                role="caption",
                family=face.family,
                weight=face.weight,
                size_px=size,
                fill_hex=fill,
                stroke_hex=style.hex(cap.stroke),
                stroke_px=style.px(cap.stroke_width),
                text="NUR 15,99 €",
            ),
        ),
        placements=(
            PlacementUse(
                element_id="cap:1",
                kind=ElementKind.CAPTION,
                zone="caption_band",
                left=band.left,
                top=band.top,
                width=band.width,
                height=200,
            ),
        ),
    ),
)
```

Reframing receives `style.safe_areas()` as its `SafeArea`s (`name, x0, y0, x1, y1` are the
same fractions). Section-specific looks: `style.tokens_for(section_id)`.

## Hierarchy, overrides, locking

Precedence (each step changes only the dotted paths it names): **identity** (`MESC_IDENTITY_V2`;
`MESC_IDENTITY_V1` for projects pinned to it)
-> **format profile** (`PROFILES[name].overrides`) -> **project** (`StyleConfig.overrides`) ->
**section** (`StyleConfig.section_overrides`). Profile choice: explicit `config.profile` ->
`STORY_FORMAT_PROFILE[story_format]` (story formats and editing profiles share names) ->
`ASPECT_PROFILE[output aspect]` (`9:16` -> `shorts_meme`) -> `generic`. No size given -> the
profile's native aspect at a 1080 short side (`DEFAULT_OUTPUT`).

Every override needs a non-empty reason, names an existing token, has the token's type, may
appear once per layer, and is recorded (`AppliedOverride`: layer, path, previous and new value,
reason). A token whose rule is MANDATORY is **locked**: no format profile, project or section
may change it (`InvalidStyleConfig`); only a new identity version can. An override that breaks a
range, makes text illegible (contrast or no outline/panel) or moves a text style into a zone
that does not take it is refused, never stored.

## Storage, pinning, versioning

* Document: `document_type = processing_type = "visual_style"`, derived JSON of the anchor
  asset via `DerivedDocuments`, tagged `visual_style`, metadata `project_id, profile, output,
  identity_version, fingerprint, style_version, supersedes`.
* Cache key: project, identity id/version/digest, profile id/version, output, the style
  fingerprint, overrides digest, schema versions. Same key -> the stored document
  (`created=False`); new key -> `style_version + 1` and `supersedes` (same project, profile,
  output). History is never changed.
* Pinning: `identity_version=None` uses the version of the anchor's newest stored style (else
  the current one). A newer identity is used only when the command names it.
* Identity versions are code (`domain/identity.py`): released versions are never edited;
  `IDENTITY_DIGESTS` pins each version's content and the identity QC turns an in-place edit into
  a CRITICAL (nothing is stored). `VisualStyleCatalog.affected_by(v)` re-resolves every stored
  style of another version with the same request under `v` and reports which would change.
* Released: v1 and v2 (`CURRENT_IDENTITY_VERSION` = 2). v2 = v1 unchanged + `emoji` and
  `transitions` token groups + the emoji face. Fields a later version adds are marked
  `ADDED_IN` (`domain/values.py`) and are left out of the stored/canonical form while absent,
  so v1 documents, the v1 digest and every stored v1 fingerprint are byte-identical. A format
  profile override of a group the identity lacks (`transitions.*` under v1) is skipped.
* `fingerprint` = sha256 of the canonical visual content (tokens, zones, faces, easings, rules,
  brand assets, sections, output); bookkeeping (dates, versions, ids) never changes it.

## QC

Identity QC (every resolve; CRITICAL = refused): token ranges/choices/colours, every token has a
rule, every rule matches a token, faces/fallbacks/easings referenced exist, all layouts define
the same named zones inside the frame with no overlap (keep-outs included; `card_safe` is for
full-screen cards and only must avoid keep-outs), approved colour pairs meet their WCAG minimum
(computed), each text style's zone takes its element kind in every layout, every format profile
applies cleanly and touches only preferences, released-version digest. Style checks (recorded):
font file present with the pinned sha256 and covering the required German glyphs
(`font.unavailable`, `font.glyphs_missing` = ERROR), missing brand assets (`brand.asset_missing`
WARNING if required, INFO otherwise), profile made for another aspect (INFO).

Drift validation (`StyleValidator`): a `MotionPhase.TRANSITION` is checked against
`transitions.duration_*` (no range under v1); stale fingerprint / other output size (ERROR); off-palette
colour, thumbnail-only colour in video, unknown easing, zone violations (unknown, keep-out,
wrong kind, outside, reaching into a keep-out), wrong face (fallback face = INFO
`font.substituted`), text below the readability floor, no outline/panel, low contrast, a price
with a decimal dot -> ERROR (mandatory rules). Semantic colour, duration, intensity ceiling and
other preference deviations -> WARNING creative, INFO with a recorded reason. A reason never
unlocks a mandatory rule.

## Fonts (bundled, SIL Open Font License 1.1, from github.com/google/fonts)

| Key | Family / weight | Role | Matches |
| --- | --- | --- | --- |
| `bangers_400` | Bangers 400 (v2.100) | display | T1 comic heavy display (wordmark, end screen, thumbnails) |
| `poppins_800` / `poppins_700` / `poppins_400` | Poppins (v4.004) | headline, number / body, label / fallback | T2 geometric bold (PLATZ N, spec cards, titles, "Werbung") |
| `anton_400` | Anton 400 (v2.116) | caption | T3 condensed caps (burned-in captions) |

| `noto_color_emoji` | Noto Color Emoji 2.051 (COLRv1, no flags), identity v2+ | `emoji.face` | emoji punchlines 😍 🤯 😱 🤔 👀 + caption_engine's vocabulary |

Files: `infrastructure/fonts/<family>/` with `OFL.txt`; tokens reference `resource` + `sha256`.
The emoji face (github.com/googlefonts/noto-emoji, tag v2.051, `fonts/Noto-COLRv1-noflags.ttf`)
is checked against `FontFace.coverage` = `EMOJI_GLYPHS` instead of the German text glyphs;
variation selectors and the joiner are not glyphs. COLRv1 over CBDT: the pinned headless Chrome
renders both, but COLRv1 is vector (sharp at any size) and 3 MB instead of 10.7 MB.
All cover `A-Z a-z 0-9 ÄÖÜ äöü ß €` plus German quotes, dash and ellipsis (checked at every
resolve with a stdlib cmap reader; a unit test cross-checks it with fontTools). Poppins has no
capital ẞ: upper case writes SS (brand rule `text.german`). Commercial use is allowed by the
OFL; the fonts may not be sold on their own.

## Format profiles

| Profile | Native | Overrides (each with a reason) | Evidence |
| --- | --- | --- | --- |
| `generic` | 16:9 | none | recurring patterns |
| `ranking` (v2) | 16:9 | intensity 3, max 3 graphics, transitions + slide/whip | t_BTkcPy5sw, d2ChnPz3FRg, bw7fkdSK1Vk |
| `brochure_review` | 16:9 | intensity 1, calm | 9etWVDZnyOs |
| `shopping_tour` | 16:9 | intensity 3 | dru73YpbQ4Y |
| `unboxing` | 16:9 | none | OkjnKFjRFt4 |
| `compilation` | 16:9 | none | _Ka5c6jMYRg, ZUSKEXTAuKI, tDi6cJFBP0s |
| `nye_uncut` | 16:9 | intensity 1, max 1 graphic, calm | y_mj09m0XB4 |
| `shorts_meme` | 9:16 | caption look `line` | zIxejdVAa0E, P_L7KHcB-_Y |
| `shorts_haul` (v2) | 9:16 | intensity 4, captions 84 ref px, transitions + slide/whip | nsIVE20ixMM, hPWgWyzYtq8 |
| `shorts_product_test` | 9:16 | captions 80 ref px | xCoOYkqzgDo, y1q4lpEEFcI |
| `shorts_versus` | 9:16 | intensity 3 | r8A57tnbyKA |

Story format / editing profile -> profile: `generic, tutorial, interview, podcast, documentary`
-> generic; `top_n` -> ranking; `review, talking_head` -> brochure_review; `unboxing` ->
unboxing; `product_introduction, montage` -> compilation; `short` -> shorts_haul.

## Token reference (groups; full table with evidence in `docs/layer4/visual-identity.md`)

| Group | Content | Rule |
| --- | --- | --- |
| `palette` | 20 HEX colours (brand red `#C4131A`, neon `#DE243C`, fire `#3F0807/#7C2A0F/#D0410A/#F3BC19`, white `#FDFEFE`, black, silver, yellow `#F4DE19`, gold `#D5A50A`, charcoal `#302F2F`, thumbnail blues, versus red/blue, positive green, mascot cyan) | mandatory (locked) |
| `semantic` | meaning -> palette name (highlight, accent, panel, price, positive/negative/warning, chart A-D) | preference |
| `typography` | display/headline/body/caption/number/label: face, size (ref px), tracking (em), leading, case; `min_size` 28 ref px | faces, display/caption case, min size locked |
| `graphics` | strokes, radius, panel, spacing, hard/soft shadow, sticker glow 8 + tilt 5-10 deg, title glow, PiP border, max simultaneous, price format | text stroke locked |
| `captions` | `word_group` (Anton caps, white, thin black 3 ref px outline + soft shadow, tracking 0.05 em, yellow keywords, 22 chars x 2 lines, caption_band) and `line` (Poppins sentence case, title_band) | preference |
| `titles` | opening title, chapter (PLATZ N), lower third, callout, price tag, spec card, CTA, sponsor label, credit, end-screen label | sponsor/credit zones locked |
| `motion` | character, entry/exit/move easing, entry/exit patterns, duration ranges (s), stagger, overshoot, min hold, intensity ceiling, motion blur | preference |
| `branding` | logo zone/variant/min height/clear space/opacity, watermark off | preference (no logo exists) |
| `end_screen` | 20 s, ABONNIEREN / WEITER SCHAUEN, fire plate, pixel dissolve, membership plug label + zoom tunnel | labels + dissolve locked |
| `emoji` (v2) | `face` = `noto_color_emoji` | locked |
| `transitions` (v2) | `default` hard_cut, `approved` (rankings / haul Shorts add slide, whip_pan), `prohibited` (dip_to_white, glitch, wipe, shape, mask_reveal), `bookend_only` (zoom_tunnel, pixel_dissolve), `duration_min/max` 0.1-1.2 s | default, prohibited, bookend_only locked |

Units: `ref_px` = pixels at a 1080 px short side (`style.px()` scales: 4K x2; 1080x1920 x1).
Sizes scale with the SHORT side, so a vertical Short gets the same text size as a 1080p video.

## Mapping from upstream

No `EditPlan` field is needed. The pipeline passes: the first source asset as
`anchor_asset_id`, `EditPlan.project_id` (or None), the story format / editing profile name as
`story_format`, the delivery size, and `sponsored` from the project. Without an edit plan
(no-speech path) only the anchor is required.

## Limitations

* Brand asset FILES do not exist yet (logo, wolf mascot, wordmark, fire plate, ...): slots are
  modelled and reported (`brand.asset_missing`); bind library assets with `BrandAssetBinding`.
  No logo is invented.
* Colours are measured on 360p H.264 / 320x180 JPEG (about +-5-8 per channel); brand red
  `#C4131A` is a proposal awaiting owner confirmation.
* Font families are look-alikes chosen here (the channel's real fonts are unknown).
* Drift validation judges declared usage (colours, text, rectangles, motion); it does not look
  at rendered pixels, does not know timing (simultaneous-graphic count, sponsor label for the
  whole duration) and cannot see stickers over flat colour: render QC / the consumer checks
  those.
* Zones are proposals for 16:9, 9:16, 1:1 only (other aspects: `UnsupportedOutputFormat`);
  9:16 keep-outs approximate the YouTube Shorts UI, not measured per platform.
* No UI; knobs carry settings-UI metadata only.

Validated on: unit tests (identity QC, precedence, locking, fingerprint, zones, validator,
fonts vs fontTools) and integration tests with a real Application, real Media Library and PNG
anchors, on Windows 11 / Python 3.13 only. Not validated against rendered channel videos.
