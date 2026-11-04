# Channel Discovery: MEsc (German fireworks channel)

Status: evidence-based analysis, 2026-11-02. Machine-readable companion: `docs/layer4/channel-evidence.json`.
Confidence scale: **high** = seen repeatedly in several samples or measured; **medium** = seen in 1-2 samples or partly inferred; **low** = inference or assumption.

> Note: All scratchpad paths below are relative to
> `C:\Users\L14\AppData\Local\Temp\claude\C--Users-L14-Documents-Programming-media-house\b2b35f84-7051-4739-8a2a-328ac470981f\scratchpad\`
> (abbreviated as `SCR/`). The downloaded videos, frames and sheets stay there and are not copied into the repo.

---

## 1. Sources inspected

| Source | What | Path / URL |
|---|---|---|
| Channel listing (long) | 218 entries: id, title, duration, views (yt-dlp flat JSON) | `SCR/channel/videos.json` |
| Channel listing (Shorts) | 196 entries: id, title, views (no durations) | `SCR/channel/shorts.json` |
| Channel metadata | description, tags, 15.1k subscribers, handle `@MEscapYT` | https://www.youtube.com/channel/UCqItgDUUug-qhhpc0pfLkDg |
| Banner + avatar | full-res PNG/JPG | `SCR/channel/thumbs/banner.png`, `avatar.jpg` (previews `banner_small.png`, `avatar_small.png`) |
| All 218 long-video thumbnails | 320x180, as 8 contact sheets, newest first | `SCR/channel/thumbs/sheet_long_00.png` … `_07.png` |
| 19 sampled videos | 360p (format 18), info.json, thumbnail | `SCR/channel/videos/` |
| Auto-subtitles (de-orig) | 15 of 19 sampled videos (4 have no speech/captions) | `SCR/channel/subs/` |
| Frames | scene-change frames (gt(scene,0.3)), one frame every 20 s (3 s for Shorts), first 15 s at 1 fps, native-res stills | `SCR/channel/frames/<id>/`, `SCR/channel/frames/hi/` |
| Contact sheets | `sheet_scene.png`, `sheet_interval.png`, `sheet_intro.png` per video; Shorts composites | `SCR/channel/frames/<id>/sheet_*.png`, `SCR/channel/frames/shorts_a.png`, `shorts_b.png` |
| Scripts (reproducible) | taxonomy, frame/audio analysis, colour k-means, records | `SCR/scripts/classify.py`, `analyze.py`, `colors.py`, `summarize.py`, `build_evidence.py` |

## 2. Videos inspected

Selection: the 2 owner favourites, the newest video of each active format, and the most-viewed video of each format.

| id | Original title (from info.json) | Upload | Dur | Views | Format | Why chosen |
|---|---|---|---|---|---|---|
| t_BTkcPy5sw | DIESE 10 FEUERWERK ARTIKEL BRAUCHST DU FÜR SILVESTER! 🔥 | 2024-12-06 | 9:40 | 51.7k | ranking | **owner favourite**; top ranking 2024/25 |
| dru73YpbQ4Y | XXL 800€ Silvester Feuerwerk Einkaufstour 2025/26 🔥🛒 | 2025-12-30 | 22:22 | 8.7k | shopping tour | **owner favourite**; newest long video |
| y_mj09m0XB4 | 0 UHR FEUERWERK ESKALATION 🔥😍 \| UNCUT | 2025-12-26 | 11:05 | 0.8k | midnight uncut | newest of format |
| _Ka5c6jMYRg | ALLE NETTO LESLI FEUERWERK BATTERIEN 2025/26 | 2025-12-25 | 13:53 | 4.0k | assortment | newest compilation |
| OkjnKFjRFt4 | PYROLAND HAT MIR FEUERWERK GESCHICKT! 🛒🔥\| UNBOXING | 2025-12-24 | 11:51 | 0.6k | unboxing | newest unboxing (sponsored) |
| 9etWVDZnyOs | OFFIZIELLES Lidl Feuerwerk Prospekt 2025/26 mit Albert Feuerwerk 😍 | 2025-12-04 | 12:15 | 2.4k | brochure | newest Lidl brochure (sponsored) |
| d2ChnPz3FRg | Top 10 BESTE Lidl Feuerwerk Batterien für Silvester 2025/26!🛒🔥 | 2025-12-17 | 9:09 | 2.2k | ranking | newest ranking, scripted Lidl production |
| bw7fkdSK1Vk | TOP 10 BESTE LIDL BATTERIEN II WECO FEUERWERK | 2021-11-20 | 11:12 | 71.1k | ranking | most-viewed long video of the channel |
| ZUSKEXTAuKI | DAS GESAMMTE LIDL FEUERWERK SORTIMENT 2023/24 + Produktvideos | 2023-12-02 | 17:01 | 44.7k | assortment | most-viewed assortment |
| tDi6cJFBP0s | Dort bekommst du die BESTEN BÖLLER für Silvester! 🔥 | 2024-12-24 | 8:27 | 34.4k | assortment / where-to-buy | most-viewed 2024/25 guide |
| sgUxhOUxqZ0 | 😍🚧 Pyro News zum Feuerwerksverbot II Doch Feuerwerksverkauf erlaubt !? | 2020-12-18 | 4:14 | 30.5k | pyro news | top tier of dormant news format |
| y1q4lpEEFcI | Tip Tap Tube Raketensortiment von @pyroland | 2026-01-15 | 2:41 | 16.5k | Short: Pyroland test | newest Short |
| xCoOYkqzgDo | Gargoyle WECO Feuerwerkbatterie aus dem @Lidl [WERBUNG] | 2025-12-27 | 0:21 | 5.6k | Short: sponsored test | newest sponsored test |
| hPWgWyzYtq8 | So viel Feuerwerk kriegst du für 150€ bei @Lidl [WERBUNG] | 2025-12-18 | 0:54 | 22.7k | Short: sponsored haul | price graphics |
| zIxejdVAa0E | HAHAH Markiere Bro... | 2025-12-19 | 0:07 | 985.9k | Short: meme | most-viewed Short |
| P_L7KHcB-_Y | Gleich gibts Schlägerrei... | 2025-12-26 | 0:07 | 78.1k | Short: meme | newest meme |
| nsIVE20ixMM | WIR HABEN FÜR 600 EURO FEUERWERK GEKAUFT! | 2024-12-08 | 0:31 | 590.7k | Short: haul | most-viewed haul |
| r8A57tnbyKA | Was findest du besser schreibs in die Kommentare! | 2025-12-16 | 0:08 | 106.8k | Short: versus poll | most-viewed poll |
| NEfxufh6YLw | Das passiert wenn man 1000 Knallerbsen auf den Boden wirft... | 2023-12-03 | 0:12 | 571.6k | Short: stunt | most-viewed stunt |

## 3. What could NOT be accessed or verified

- **No listening.** Audio statements come from loudness measurements and auto-subtitle tags (`[Musik]`, `[Applaus]`, `[gelächter]`). Music genre, SFX design and licensing are not verified.
- **Font families are not known.** Typography is described by look. Names such as "Poppins-like" are assumptions.
- **Colour precision:** frames are 360p H.264, thumbnails 320x180 JPEG. HEX values are within about ±5-8 per channel of the master.
- **Not inspected:** YouTube Analytics (retention, CTR, traffic sources), livestreams, community posts, the channel trailer, Instagram/Discord/TikTok.
- **Upload dates** are only known for the 19 sampled videos. The flat listing has no dates, so recency uses listing order.
- **Shorts durations** are only known for the 8 sampled Shorts.
- **Listing titles are YouTube auto-translations into English.** The info.json of all sampled videos shows German originals (high confidence). Title wording in the taxonomy therefore mixes both languages.
- Whether meme-Short footage is the creator's own or a licensed template is unknown. The people shown are not the creator (low-medium).

## 4. Content taxonomy (all uploads)

Derived by keyword rules over all titles (`SCR/scripts/classify.py`, heuristic, about 5% misfiled). "Newest 60" = how many of that format are among the 60 newest uploads of the same kind (long or Short). This shows which formats are active now.

### Long videos (218, median about 8 min)

| Format | n | Median views | Max views | Median duration | In newest 60 | State |
|---|---|---|---|---|---|---|
| Pyro news / ban politics / 2020-21 community entertainment (memes, reacts, rap) | 72 | 7.2k | 30k | 5:50 | 1 | dormant since 2022 |
| Brochure / catalogue / leak reviews per retailer | 56 | 5.0k | 30k | 8:02 | 21 | **core, active** |
| Assortment and product-video compilations, where-to-buy guides | 31 | 10.0k | 44k | 8:54 | 14 | active, strong |
| Rankings (Top 5/10/20, tier list, budget shopping lists) | 21 | 8.1k | 71k | 10:28 | 7 | active, strongest peak |
| Shopping tours / hauls / anticipation tours | 13 | 5.3k | 40k | 11:00 | 7 | active, flagship |
| Unboxings (shop/sponsor packages) | 13 | 2.3k | 7.6k | 9:58 | 8 | active, weak views |
| Midnight "Eskalation" / NYE vlogs / uncut | 6 | 2.9k | 26k | 13:47 | 2 | yearly |
| Legacy off-topic (gaming, etc.) | 4 | 2.4k | 18k | – | 0 | abandoned |
| Unclassified | 2 | – | – | – | 0 | – |

### Shorts (196)

| Format | n | Median views | Max views | In newest 60 | Notes |
|---|---|---|---|---|---|
| Memes (POV, "Markiere deinen Bro", Lidl rush) | 73 | 16k | 986k | 14 | the reach engine of the channel |
| Sponsored Lidl battery tests / hauls `[WERBUNG]` | 31 | 5.3k | 67k | 14 | sponsor deliverables 2024/25 + 2025/26 |
| Pyroland product tests | 29 | 0.9k | 17k | 29 | all from the latest season, weakest |
| Versus polls ("Was ist besser?") | 21 | 15k | 107k | 3 | community vote format |
| Haul / spend ("600 EURO gekauft") | 10 | 74k | 591k | 0 | highest median of all formats |
| NYE footage / midnight clips | 8 | 3.0k | 10k | 0 | |
| Brochure teasers | 7 | 5.9k | 19k | 0 | |
| Stunt / curiosity (1000 Knallerbsen, loudest firecracker) | 5 | 11k | 572k | 0 | viral outliers |
| Guess the battery | 5 | 6.3k | 20k | 0 | |
| Other | 7 | 2.5k | 21k | 0 | |

Key reading: the long-form audience comes for **retail intelligence** (brochures, ranges, rankings: what, where, how much). Shorts reach comes from **memes and hauls**. Sponsored formats (Lidl, Pyroland, Pyro Team Store) became a large share in 2024-2026 (high).

## 5. Per-format characteristics

Cut rate = scene changes (threshold 0.3) merged within 1 s, per minute. This suppresses firework flashes and animated graphics. Jump cuts inside identical framing are under-counted.

### 5.1 Ranking / Top-N (samples: t_BTkcPy5sw, d2ChnPz3FRg, bw7fkdSK1Vk) — confidence high (structure), medium (graphics)

| Aspect | Observation |
|---|---|
| Subject | Must-have / best-of lists per retailer or overall, with prices and where to buy |
| Structure | Claim hook (about 25 s: "Diese zehn Feuerwerksartikel dürfen an Silvester auf keinen Fall fehlen") -> membership plug -> countdown **Platz 10 … Platz 1**, about 45-55 s per rank -> recap -> comment prompt -> catchphrase -> about 20 s end screen. YouTube chapters per rank (10 and 11 chapters). |
| Duration | median 10:28 (n=21); samples 9:40 / 9:09 / 11:12 |
| Aspect | 16:9 |
| Shots | product cut-outs over night fireworks or orange fire plate; own daylight ignition close-ups (gloves, lighter); night tests (own plus credited third-party); 2025 sponsored: studio presenter holding product to camera (wood slats, red LED) |
| Pacing | 5.58 / 4.59 / 4.20 cut events/min |
| Speech | VO 146 wpm (favourite), 93 wpm (2021), 75 wpm (scripted sponsor version with long product B-roll) |
| Graphics | "PLATZ N" card; spec card "Schuss / Höhe / Brenndauer / Kosten" white on black; price "15,99 EUR"; retailer logo stickers; black silhouette teaser of the next rank; emoji pops 😍 |
| Transitions | radial zoom tunnel, whip/motion blur, white flash plus salmon light leak (2025), horizontal carousel slide between ranks (2025), pixel dissolve into end screen |
| Music | `[Musik]`/`[Applaus]` at intro and outro. No silence >1 s at −40 dBFS anywhere, so a continuous bed is likely (assumption, medium). |
| Loudness | −17.4 / −17.5 / −16.1 LUFS, LRA 5.1 / 16.6 / 9.8 |
| Restraint | medium: graphics at each rank, but test footage runs uninterrupted |
| Storytelling | countdown tension, "Platz 1" saved for the most sought-after item ("dafür müsst ihr früh aufstehen") |
| Audience expects | clear verdict, price, availability, effect proof |

Sheets: `SCR/channel/frames/t_BTkcPy5sw/sheet_scene.png`, `…/d2ChnPz3FRg/sheet_scene.png`, `…/bw7fkdSK1Vk/sheet_scene.png`; stills `SCR/channel/frames/hi/t_BTkcPy5sw_hisheet_*.png`, `d2ChnPz3FRg_hisheet_*.png`.

### 5.2 Shopping tour (sample: dru73YpbQ4Y, owner favourite) — confidence medium

| Aspect | Observation |
|---|---|
| Subject | XXL 600-800 € sales-day tour with friends, about 4:30 am start, specialist store, Lidl opening rush, Thomas Philipps, Jawoll, Netto, Aldi; haul laid out on the ground at the end |
| Structure | letterboxed cold-open teaser with "In diesem Video…" -> meme sketch wake-up ("Soldat, steh auf…") -> title card "Einkaufstour 2025/26" -> chronological stores -> car loading -> haul presentation with close-ups -> catchphrase -> end screen |
| Duration | 22:22 (format median 11:00) |
| Aspect | 16:9; cold open letterboxed about 2.39:1 |
| Shots | wide-angle action cam, handheld/chest POV, barrel distortion uncorrected; night streets and car; crowded aisles; product close-ups |
| Pacing | 7.0 cut events/min |
| Speech | spontaneous group banter, 117 wpm; 34 sound tags (`[musik]`, `[gelächter]`, `[schnauben]`) |
| Graphics | title card: "Einkaufstour" gold **#D5A50A** above "2025/26" white, geometric bold, soft glow, centred; teaser label white, bottom right |
| Transitions | TV colour-bar glitch, hard cuts |
| Grade | none visible; mixed fluorescent/night white balance; mean luma 92/255 |
| Loudness | −13.2 LUFS, LRA 9.1 |
| Audience expects | vicarious thrill, crowd chaos, "what did they get and for how much" |

Sheets: `SCR/channel/frames/dru73YpbQ4Y/sheet_scene.png`, `SCR/channel/frames/hi/dru73YpbQ4Y_hisheet_7_97_1185_1215.png`.

### 5.3 Brochure / catalogue review (sample: 9etWVDZnyOs) — confidence medium

| Aspect | Observation |
|---|---|
| Layout | face-cam PiP top-left (about 40% width, thin white border, drop shadow) in the creator's gaming room with RGB/aurora LED backlight; brochure page full height on the right with drop shadow; dark fireworks footage as background plate; "WERBUNG" top right |
| Structure | "Das Prospekt ist da, meine Freunde" -> exclusivity claim -> page-by-page with price commentary -> regional availability map -> opinion prompt -> catchphrase -> end screen |
| Pacing | page change about every 10 s; 5.9 events/min |
| Speech | 172 wpm (densest of all samples) |
| Loudness | −14.2 LUFS, LRA 4.9 (speech-dominated) |
| Restraint | high: static layout, no effects during the walkthrough |

Sheet: `SCR/channel/frames/9etWVDZnyOs/sheet_scene.png`; stills `SCR/channel/frames/hi/9etWVDZnyOs_hisheet_2_60_290_715.png`.

### 5.4 Assortment / product-video compilation / where-to-buy (samples: _Ka5c6jMYRg, ZUSKEXTAuKI, tDi6cJFBP0s) — confidence medium

| Aspect | Observation |
|---|---|
| Variants | (a) uncommented compilation of manufacturer videos (their own lower thirds: product, article no.), 17 chapters; (b) VO over product stickers plus test clips (2023); (c) meme-heavy where-to-buy guide with brochure scans and credited third-party test clips (2024) |
| Pacing | 3.2 / 13.0 / 13.0 events/min |
| Speech | 0 / 121 / 121 wpm |
| Captions | 2023: burned-in word-group captions, condensed ALL CAPS white with black stroke, keyword in yellow **#F1E11D**. 2024/25: none |
| Graphics | product stickers, full-screen emojis (🤔, 👀, 😍), retailer logo slams, "2023 vs 2024" thumbnail comparison, credit watermarks top right (e.g. "ZINK-FIREWORKS", "PYROMOTTEFULLHD") |
| Audio | compilation is the loudest file: −11.0 LUFS, LRA 14.1, true peak **+3.8 dBFS**, momentary p90 −8.1 LUFS (unprocessed bangs) |

Sheets: `SCR/channel/frames/_Ka5c6jMYRg/sheet_scene.png`, `…/ZUSKEXTAuKI/sheet_scene.png`, `…/tDi6cJFBP0s/sheet_scene.png`.

### 5.5 Unboxing (sample: OkjnKFjRFt4) — confidence medium

Cold open of the final product pile -> zoom tunnel plus membership plug -> boxes opened at a desk in the studio, with the **red neon "MEsc" script sign** (measured **#DE243C**), wooden slat wall and red LED strip -> item by item -> pile pan -> sponsor thanks -> catchphrase -> end screen. Wide-angle, 4.1 events/min (under-counts jump cuts), 138 wpm, −12.9 LUFS. "WERBUNG" in bold caps top right. Sheet: `SCR/channel/frames/OkjnKFjRFt4/sheet_scene.png`.

### 5.6 Midnight uncut (sample: y_mj09m0XB4) — confidence medium

One continuous POV take (1 detected change in 11 min): fuse chains wrapped in aluminium foil prepared under a headlamp -> phone clock 23:59 -> ignition -> about 6 min of sky -> end screen. No speech recognised. Sky section: mean luma 10.4/255, 86% of pixels ≤16, 0.16% clipped. −18.1 LUFS. Only 0.1% of momentary values are above −12 LUFS, so the bangs appear compressed by the camera AGC (assumption, medium). Sheet: `SCR/channel/frames/y_mj09m0XB4/sheet_interval.png`.

### 5.7 Pyro news (sample: sgUxhOUxqZ0, 2020) — confidence medium, dormant

VO (160 wpm) over stock/third-party fireworks footage. A disclaimer card reads "ALLE INFOS SIND UNVERBINDLICH!" in comic caps. A persistent lower bar shows a fire strip, wolf badge and "INSTA: EB.MARVIN". Includes an animated like/subscribe button and a "THANKS FOR WATCHING" sparkler template outro. 0.7 events/min, −27.4 LUFS (very quiet master). Sheets: `SCR/channel/frames/sgUxhOUxqZ0/sheet_interval.png`.

### 5.8 Shorts (composites `SCR/channel/frames/shorts_a.png`, `shorts_b.png`)

| Format (sample) | Dur | Structure | Text treatment | Pacing | Audio |
|---|---|---|---|---|---|
| Meme (zIxejdVAa0E 986k, P_L7KHcB-_Y 78k) | 7 s | one shot, caption from frame 1, loopable; animated product-cutout pile with yellow "Nur regional erhältlich" stickers | sentence case, white geometric bold with dark outline, upper third ("POV: …", "Bro und ich am 29.12 im Lidl") | 0 cuts | −17.6 / −22.6 LUFS, LRA ≤2.2 |
| Haul (nsIVE20ixMM 591k) | 31 s | rapid montage of the shopping day with running VO | word-group captions, ALL CAPS condensed white, black outline, lower middle | **31.5 events/min** | VO 220 wpm, −18.0 LUFS |
| Stunt (NEfxufh6YLw 572k) | 12 s | hook question then payoff | ALL CAPS condensed white, **keyword yellow #F4DE19**, 2 lines centre | – | −21.8 LUFS |
| Versus poll (r8A57tnbyKA 107k) | 8 s | A -> "VS" -> B -> clock -> result split | charcoal grid **#302F2F**, white "VS" with red stroke, result panels red **#B60F00** 74% / blue **#055183** 26% | motion graphic | −21.4 LUFS |
| Sponsored test (xCoOYkqzgDo) | 21 s | title card (product, Lidl logo, "Charge 2025/26", "5.99€") on teal grid **#317087**, then raw night test | geometric bold white; "Werbung" top right | 2.9 | −16.3 LUFS |
| Sponsored haul (hPWgWyzYtq8) | 54 s | face-cam top, product cut-outs on grid bottom | prices bright green **#01D402** with dark shadow | 3.3 | VO 144 wpm, −12.8 LUFS |
| Pyroland test (y1q4lpEEFcI) | 2:41 | presentation, then raw handheld night test | "Werbung" only | 3.7 | −15.7 LUFS |

## 6. Observed visual identity

### 6.1 Brand assets (high)

- **Mascot:** a black/red wolf (or husky) with glowing cyan eyes and TNT sticks in its mouth, on a fireworks/fire sky. It is the avatar. A small wolf-head badge was used in 2020-21 thumbnails and lower bars. The wolf also appears on the end-screen fire plate and in the membership plug.
- **Wordmark:** "MESC" in a heavy comic display face. Silver/chrome vertical gradient fill (measured **#ECECEC -> #C1C1C1 -> #9B9B9B**), black outline, slight italic. Shown on a red-fire band with **grey-white diagonal stripes** in the corners (banner).
- **Studio sign:** red neon script "MEsc" (**#DE243C**, halo #F26F82) in the 2025 studio. Studio accent LEDs measured **#D51C30 / #EF3A54 / #860B13**.
- **On-screen social handles:** Instagram "MESC.YT" and "MARVIN.ESB" on the end screen.

### 6.2 Colours (measured; evidence in `channel-evidence.json` -> `measured_colours`)

| Role | HEX | Evidence | Confidence |
|---|---|---|---|
| Brand red (primary accent) | **#C4131A** (rank label stroke), #DE243C (neon), #C9282A (thumbnail red accents), #B60F00 (poll red) | `hi/d2ChnPz3FRg_hi_00063.9.png`, `hi/OkjnKFjRFt4_hi_00037.0.png`, thumbnails newest 30, `hi/r8A57tnbyKA_hi_00006.6.png` | high (red recurs across banner, avatar, studio, graphics) |
| Fire dark / mid / hot | #3F0807 / #7C2A0F / #D0410A / #F3BC19 | end screen `hi/t_BTkcPy5sw_hi_00565.0.png` | high |
| Banner fire | #240E0C, #691207, #CE4E1D, #EBB864 | `thumbs/banner.png` | high |
| Avatar | #170D16 (black fur), #691113, #C32F15, #EDE3C6, cyan eyes **#3ADCEE** | `thumbs/avatar.jpg` | high |
| Text white | #FDFEFE | end screen text | high |
| Wordmark silver | #ECECEC / #C1C1C1 / #9B9B9B | banner text | high |
| Gold title | **#D5A50A** | `hi/dru73YpbQ4Y_hi_00097.0.png` | medium (1 card) |
| Highlight yellow (captions, thumbnails) | **#F4DE19** / #F1E11D / #E3D448 / #FCE608 | `hi/NEfxufh6YLw_hi_00000.7.png`, `hi/ZUSKEXTAuKI_hi_00292.9.png`, thumbnails | high |
| Thumbnail blue (2022+) | #245FA7, #3F9AE3, #577BAB, #3B465F | thumbnails newest 30 | high |
| Graphic base charcoal grid | **#302F2F** with light grid lines | `hi/d2ChnPz3FRg_hi_00003.6.png`, `_00063.9.png` | medium (2025 sponsored work) |
| Orange product plate | #8F2408 (dark, frame region) – visually bright orange/fire blur | `hi/t_BTkcPy5sw_hi_00507.3.png` | medium (measured region is the darker left side) |
| Price green | #01D402 | `hi/hPWgWyzYtq8_hi_00016.8.png` | low (1 Short) |
| Sponsor teal | #317087 / #2C6071 | `hi/xCoOYkqzgDo_hi_00001.3.png` | low (Lidl-test template) |
| 2020-21 palette | #321810, #B92F18, #FCF8F6 | old thumbnails | high |

Summary: the identity is **black + fire red/orange + white, with yellow/gold highlights**. Thumbnails add a **saturated blue** field as contrast (2022+).

### 6.3 Typography (by appearance; family names are assumptions)

| Style | Where | Treatment | Confidence |
|---|---|---|---|
| **T1 Comic heavy display** (Komika/Bangers-like) | wordmark, thumbnails, end screen ("ABONNIEREN", "WEITER SCHAUEN"), 2020-21 cards ("ALLE INFOS SIND UNVERBINDLICH!"), 2021 product cards ("THE NEW BEASTMASTER / 12,99 EUR") | ALL CAPS, white (or yellow/green in thumbnails), thick black outline plus hard drop shadow, slight tilt | high (look), low (family) |
| **T2 Geometric sans bold** (Poppins-like) | 2024-25 in-video graphics: "PLATZ 9", spec cards, "Einkaufstour 2025/26", "In diesem Video…", "Charge 2025/26", meme captions, "Werbung" (regular weight) | white; rank labels with thick red stroke #C4131A; title cards with soft glow; meme captions with dark outline, sentence case | high (look), low (family) |
| **T3 Condensed caps captions** (Bebas/Anton-like) | burned-in speech captions (2023 long, haul/stunt Shorts) | ALL CAPS white, black stroke plus soft shadow, 1-2 lines, centre/lower-middle, keyword in yellow #F4DE19 | medium |

### 6.4 Captions

- 2024-25 long videos sampled (6 of 6 recent): **no burned-in speech captions**, only cards and labels (high).
- 2023 assortment video and viral Shorts: word-group captions (2-4 words), ALL CAPS condensed, keyword in yellow (high for those samples).
- Meme Shorts: one static sentence-case line in the upper third (high).

### 6.5 Title and graphic language

- Rank cards "PLATZ N" plus black silhouette teaser of the next item (high).
- Product cut-out **stickers with white outer glow**, slight rotation, placed over night fireworks or an orange fire plate, never over flat colour (high; 8 samples).
- Spec card: product left, four lines "Schuss / Höhe / Brenndauer / Kosten" on black (medium; 2025 sponsored).
- Prices written "15,99 EUR" (2021-24) or "2,49€" (2025), German decimal comma (high).
- Retailer logos (Lidl, Aldi, Netto, Kaufland …) as hero graphics (high).
- Emojis as full-screen punchlines or stickers: 😍 🤯 😱 🤔 👀 (high).
- Arrows: thick white with black outline, or curved red (thumbnails, intro) (high).
- Sponsor label "Werbung"/"WERBUNG" top right for the whole duration (high; 6 sponsored samples).

### 6.6 Motion and transition patterns

| Pattern | Seen in | Confidence |
|---|---|---|
| Radial zoom tunnel (green/red blur) into the **membership plug** ("Kanalmitglied werden", "EMOJIS", "NEU") on a fire/wolf plate in the first 30 s | t_BT, d2Ch, Okjn, tDi6, bw7, ZUSK (+ tunnel frame in dru) | high |
| **Pixel/mosaic dissolve** into the fire end screen | 8 of 11 long samples | high |
| Whip pan / motion-blur smear | t_BT, tDi6 | medium |
| White flash plus salmon light leak | d2Ch | medium |
| Horizontal carousel slide between ranks | d2Ch | medium |
| TV colour-bar glitch | dru | low |
| Ken Burns push on product packaging | bw7 (2021) | medium |
| Sticker pop-in, product pile "rain" | t_BT, zIxe | medium |

### 6.7 End screen and CTA (high)

About 20 s fire/lava plate with the wolf, "ABONNIEREN" and "WEITER SCHAUEN" labels above the YouTube end-screen slots, and Instagram handles. Spoken outro catchphrase **"abonnieren, liken und Schwarzpulver inhalieren"** in **8 of 8** long samples with unscripted speech. It is missing only in no-speech videos and the scripted sponsor ranking, which ends with "Lasst gerne noch ein Abo da". Comment prompts are frequent ("schreibt eure Meinung in die Kommentare", "was hat gefehlt, was muss raus").

### 6.8 Thumbnails (218 viewed; high)

- **2022-2025 system:** blue radial speed-line/warp background; grey-white diagonal corner stripes (the same motif as the banner); 1-2 huge words in T1 (white, yellow, green); a big retailer logo; product cut-outs; retailer-style price tags; thick arrows; one emoji; often a black mystery box with "?"; creator reaction pose (hands on head, shock).
- **2020-2021 system:** red/orange fire background, wolf badge bottom left, white comic caps with red/black outline, politician/meme photos.
- Measured accents (newest 30): red #C9282A, blue #245FA7 / #3F9AE3, yellow #FCE608, amber #CA8627.

### 6.9 Camera, grade, footage

- Cameras: wide-angle action cam (tours, unboxing, uncut POV); face-cam in the gaming room (brochures, sponsored-haul Short); a better studio camera (2025 sponsored ranking); vertical phone (Shorts); scans/screenshots (brochures, shop pages).
- **No visible colour grade.** Mixed white balance, uncorrected lens distortion, no stabilisation look (medium-high).
- Night footage is very dark (86% of pixels ≤16 luma in the sky section). Bursts clip locally. Long tests often show blown-white cores (bw7 9:00) (high).
- Heavy use of **third-party footage**: manufacturer product videos, other creators' tests with on-screen credits, meme clips (high).

## 7. Audio observations

| id | I (LUFS) | LRA | True peak (dBFS) | Speech wpm | Notes |
|---|---|---|---|---|---|
| t_BTkcPy5sw | −17.4 | 5.1 | +0.7 | 146 | [Musik], [Applaus] |
| dru73YpbQ4Y | −13.2 | 9.1 | +1.4 | 117 | 34 tags incl. [gelächter] |
| 9etWVDZnyOs | −14.2 | 4.9 | 0.0 | 172 | |
| d2ChnPz3FRg | −17.5 | 16.6 | +0.6 | 75 | scripted VO |
| OkjnKFjRFt4 | −12.9 | 6.6 | +1.0 | 138 | |
| _Ka5c6jMYRg | −11.0 | 14.1 | +3.8 | 0 | manufacturer audio, bangs |
| ZUSKEXTAuKI | −16.4 | 8.5 | +0.3 | 121 | |
| tDi6cJFBP0s | −17.2 | 10.6 | +1.9 | 121 | |
| bw7fkdSK1Vk | −16.1 | 9.8 | +1.4 | 93 | |
| y_mj09m0XB4 | −18.1 | 6.3 | +1.1 | 0 | uncut ambience |
| sgUxhOUxqZ0 | −27.4 | 11.1 | −7.7 | 160 | 2020, very quiet |
| Shorts (8) | −12.8 … −22.6 | 1.4 … 12.5 | −9.1 … +1.1 | 0-220 | memes LRA ≤2.2 |

Findings:
- **Language:** German speech in every video with speech (high). The address "meine Freunde" recurs. The channel greeting is "Was geht ab Pyros" (channel description), and the 2020 opening is "was geht da freunde … herzlich willkommen".
- **Mastering is inconsistent:** integrated loudness spans −11 to −27 LUFS. True peak is above 0 dBFS in 12 of 19 decoded files, and one more sits at exactly 0.0, so there is no −1 dBTP limiting. Inter-sample overs after AAC decoding contribute (medium).
- **Continuous audio:** none of the 19 files has more than one silence >1 s below −40 dBFS. A music or ambience bed under the VO is likely (assumption, medium). Music is only evidenced by `[Musik]` auto-tags at intros, outros and travel scenes.
- **Fireworks transients:** in compilations they dominate (LRA 14.1, TP +3.8). In the uncut POV they are flattened (assumption: camera AGC/limiter).
- SFX (whooshes on zoom tunnels, pops on stickers) are **not verified** without listening (assumption, low).

## 8. Recurring vs incidental patterns

**Recurring (identity-defining):** fire end screen plus pixel dissolve; catchphrase outro; membership plug after a zoom tunnel; product stickers with white glow; retailer logos; rank countdown; German decimal-comma prices; emojis as punchlines; "Werbung" label on sponsored content; wide-angle POV for real-world footage; black/red/fire palette with yellow highlights; blue speed-line thumbnails (2022+).

**Incidental / episode-specific:** TV colour-bar glitch; aurora-LED gaming room (brochure only); teal sponsor title card; green price text; letterboxed cold open; carousel rank slide (sponsored production); "THANKS FOR WATCHING" template (2020 only); fire lower bar with handle (2020-21 only).

**Evolution:** 2020-21 red-fire comic look (wolf badge, lower bar) -> 2022-23 blue speed-line thumbnails and burned-in word captions -> 2024-25 cleaner geometric-sans graphics, charcoal grid templates (sponsor-driven), studio set with neon sign. The fire end screen and comic wordmark carried through all eras.

## 9. Explicit assumptions

1. Font families (Komika/Bangers-like, Poppins-like, Bebas/Anton-like): appearance only (low).
2. A music bed exists under most VO segments (medium; continuous audio plus `[Musik]` tags).
3. Bangs in POV footage are compressed by camera AGC (medium).
4. Meme-Short footage is template/third-party (low-medium).
5. The 2025 grid templates and scripted VO in Lidl content follow sponsor requirements, not the creator's default style (medium; they are absent from non-sponsored 2024-25 videos).
6. The orange "fire plate" behind product stickers is a blurred fire/curtain clip (medium).
7. Taxonomy counts carry about ±5% misclassification (medium).

## 10. Remaining uncertainties

- The exact brand red. Measured values range from #B60F00 to #DE243C, and no style guide exists. Proposed canonical value: **#C4131A** (stroke of the rank labels), to be confirmed by the owner.
- Which music/SFX libraries are used, and their licence status.
- Whether the burned-in caption style (2023) or the caption-free style (2024-25) is the owner's preferred long-form style. The favourites have no burned-in captions.
- Retention impact of the membership plug at 0:10-0:26.
- Shorts duration distribution beyond the 8 samples.

---

## 11. Derived creative requirements

Profile name proposal: `mesc_fireworks` (source/brand profile), with format profiles `mesc.ranking`, `mesc.shopping_tour`, `mesc.brochure`, `mesc.assortment`, `mesc.unboxing`, `mesc.midnight_uncut`, `mesc.short_meme`, `mesc.short_haul`, `mesc.short_versus`, `mesc.short_sponsored_test`. Owner favourites (`t_BTkcPy5sw`, `dru73YpbQ4Y`) are the reference look. Where the two differ, the ranking favourite defines graphics and the tour favourite defines real-world footage handling.

### Layer 4

**visual_style_engine**
- Palette tokens: `brand.red=#C4131A`, `brand.red_neon=#DE243C`, `fire.dark=#3F0807`, `fire.mid=#7C2A0F`, `fire.hot=#D0410A`, `fire.glow=#F3BC19`, `ink.black=#170D16`, `paper.white=#FDFEFE`, `silver=#ECECEC→#9B9B9B`, `accent.yellow=#F4DE19`, `accent.gold=#D5A50A`, `thumb.blue=#245FA7`, `base.charcoal=#302F2F`.
- **Do not grade toward a "cinematic" look.** The channel has no grade. At most, apply neutral white-balance normalisation and exposure lift on dark night shots, with a hard guard against crushing sparks. The sky section has 86% of pixels ≤16, so protect those blacks rather than lifting noise.
- Lens-distortion correction for action-cam footage: optional and off by default. The wide POV look is part of the tour format.

**motion_graphics_engine**
- Templates: `rank_card` ("PLATZ N", white geometric bold, red stroke #C4131A, centred lower third), `next_rank_teaser` (black silhouette with white glow and number), `spec_card` (Schuss/Höhe/Brenndauer/Kosten), `price_tag` (German comma format "15,99 €"), `product_sticker` (cut-out, 6-10 px white outer glow, ±5-10° tilt, pop-in), `retailer_logo_slam`, `title_card` (gold plus white glow, as in "Einkaufstour 2025/26"), `sponsor_label` ("Werbung", top right, whole duration, mandatory when the profile is sponsored), `end_screen` (fire plate, wolf, "ABONNIEREN"/"WEITER SCHAUEN", handles, ~20 s), `versus_poll` (grid, VS, clock, red/blue result split).
- Stickers sit over fireworks or fire plates, never over flat colour.

**caption_engine**
- Long form default: **no burned-in speech captions**, which matches both favourites. Optional profile `mesc.captions_wordgroup` for Shorts and caption-style long videos: 2-4 words, ALL CAPS condensed, white with black stroke, keyword in #F4DE19, lower-middle (9:16) or centre (16:9).
- Meme Shorts: one static sentence-case line, upper third, white geometric bold with dark outline, visible from frame 1.
- Text must be German with correct umlauts (Ä/Ö/Ü/ß). Prices use a decimal comma.

**transition_engine**
- Allowed set: `zoom_tunnel` (intro only), `pixel_dissolve` (only into the end screen), `whip_blur`, `flash_light_leak` (sponsor template), `carousel_slide` (between ranks), `hard_cut` (default).
- Intensity budget: transitions mark structure (intro, rank change, end), not every cut. Recent long samples are cut at 4-7 events/min.

**vfx_engine**
- Minimal. Product cut-out glow, emoji pops and the fire end-screen plate cover what the channel uses.
- **No artificial fireworks or fake bursts** over real test footage, because the effect is the product being judged. Fire/flame plates only appear in branding segments.

**music_and_sound_design**
- Target delivery loudness −14 LUFS integrated, ≤ −1 dBTP. This would fix the observed −11 to −27 LUFS spread and the overs.
- **Ducking:** under VO the bed sits below speech, and it does not duck under firework bangs in test segments. Limit bangs with a transient-safe limiter, without compressing them flat (the uncut POV problem).
- Keep natural crowd/laughter audio in tours. The membership-plug zoom gets a whoosh (assumption: verify with the owner).

**visual_asset_generation**
- Required assets: wolf mascot (from the avatar), "MESC" chrome comic wordmark, neon "MEsc" script, fire/lava plate with wolf (end screen), orange fire blur plate, charcoal grid plate, blue speed-line thumbnail background, diagonal grey-white corner stripes, retailer logos (licensing check!), emoji set.
- Thumbnail generator: blue warp background, corner stripes, 1-2 words in T1, retailer logo, product cut-outs, price tag, one emoji or arrow, optional mystery box.
- **Credit overlays** for third-party footage are a recurring requirement: small white caps text, top right.

### Layer 3

**story_architecture**
- Ranking: hook claim (≤25 s) -> (plug) -> N rank blocks (~50 s each: card, product, price, where to buy, proof footage) -> recap -> comment prompt -> catchphrase -> end screen. Save the most sought-after item for Platz 1.
- Shopping tour: teaser cold open -> wake-up/start -> title card -> chronological stores -> loading -> haul reveal -> catchphrase.
- Brochure: page order is the story; add a regional/price-analysis insert.
- Midnight uncut: no story intervention (single take).
- Shorts: the hook must be on screen in the first second (caption or question). Memes about 7 s and loopable, versus about 8 s, haul about 30 s.

**editing_intelligence**
- Respect the recurring openers and closers: catchphrase outro, about 20 s end-screen hold, membership plug slot after the hook (configurable).
- Preserve uninterrupted firework test shots from ignition to last break, and do not cut inside a burst sequence.

**pacing_intelligence**
- Measured targets (cut events/min): ranking 4-6, brochure ~6 (page-driven), tour ~7, unboxing ~4 (plus jump cuts), compilation ~3, uncut ~0, haul Short ~30, meme Short 0-1.
- Speech density: brochure up to 170 wpm, ranking ~145 wpm, tour ~120 wpm.

**broll_intelligence**
- B-roll classes: product cut-out, own test clip (day or night), third-party credited test clip, brochure page, retailer exterior/logo, emoji/meme insert, crowd/store POV.
- Prefer the creator's own test footage. When third-party footage is used, require a credit overlay and a description credit (observed practice).

**continuity_intelligence**
- Keep product identity consistent across rank card, sticker, spec card and test footage (same product name and price).
- Retailer and season label ("2025/26") must match across title card, thumbnail and VO.

**reframing_intelligence**
- 16:9 -> 9:16: for test footage, follow the burst/rocket trail (bright-region tracking). For studio/face-cam footage, centre the face.
- Graphics must be re-laid-out rather than cropped. Observed Shorts use a split (face-cam top / product grid bottom).

### Layer 2 (analysis inputs this profile depends on)

- **audio_intelligence:** detect firework transients (bangs, crackle, whistles) separately from speech. Classify the segments `speech_vo`, `crowd_banter`, `fireworks_ambience` and `music_bed` (assumed). Measure the loudness spread (observed −11 … −27 LUFS).
- **video_intelligence:** detect night/low-light scenes (mean luma <20). Use burst events (bright transient regions) as non-cut highlights, so they are not counted as scene cuts. Detect wide-angle POV.
- **content_intelligence / OCR:** read product names, prices ("4,99 €", "15,99 EUR"), retailer logos (Lidl, Aldi Nord/Süd, Netto, Kaufland, Penny, Norma, Edeka, Jawoll, Thomas Philipps) and the sponsor label "Werbung" from brochure pages and packaging. This feeds the rank, spec and price cards.
- **media_inspection reuse:** footage is 30 fps H.264 from mixed cameras (action cam, phone, face-cam). Downstream modules must reuse the inspected fps, resolution and audio layout, and must not re-probe.
