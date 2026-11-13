# Gap audit (P0, 2026-11-13): why the output reads as "AI cards"

Baseline v0 = the owner's v6 render (`Desktop/lidl-online-bestellen-und-liefern-lassen.mp4`, 1920x1080,
29.97 fps, 8:14, "WERBUNG" label throughout). Evidence: contact sheets (`scripts/layer5/see.py sheet`,
every 4 s for 0:00-2:00, every 8 s for 2:00-8:15) and one filmstrip at 0:24-0:25.5.
All statements are **observed from stills** unless marked. No rubric score exists yet.

| # | Cause | Verdict | Evidence | Confidence |
| --- | --- | --- | --- | --- |
| C1 | Flat template cards | **confirmed** | Rank cards (0:56, 2:00, 2:32 of sheet 2) are the same panel: product left, name + 3-4 numbers right, price in yellow, on a dark grid plate. Yellow label stickers ("Infos direkt von Lidl", "Sortiment: Über 50 Artikel", "Keine Schlange, ausschlafen") are one template. | high |
| C2 | Overlays, not part of the shot | **confirmed** | Every card sits in a fixed corner/zone or on a black plate; none tracks the speaker or a product, none is occluded by him, none continues across a cut. | high |
| C3 | Too few visuals | **confirmed** | Plain talking, no graphic: about 2:56-3:04, 3:20-3:44, 4:08-4:40 and 5:12-5:40 of sheet 2 (8 s spacing, so 25-30 s stretches). Existing measurement agrees: 50 s longest stretch without a graphic (`seltix-discovery.md`). | high |
| C4 | Dead holds | **confirmed** | Filmstrip 24.0-25.5 s: the yellow card is pixel-identical in size/position/colour from 24.024 to 24.725 s. No float, no pulse, no secondary layer. | high (one card); medium (all) |
| C5 | Identity caps limit dynamism | **confirmed for this video** | The client brief itself says: identity v2, max 3 overlays, forbidden glitch/wipe/dip-to-white/shape/mask reveal, no burned-in subtitles. Identity v2 digests are frozen. | high |
| C6 | Sound present but not designed | **not verified** | Needs the SFX stem and a cue-sheet view. Earlier pass: sync cannot be proven from the mix (`seltix-discovery.md`). Nobody has listened to the mix. | - |
| C7 | Gates check compliance, not craft | **plausible** | `quality_control` and `export_validation` check palette, zones, loudness; no gate looks at motion. To be confirmed by reading the gate code. | medium |
| C8 | Single-pass pipeline | **confirmed by design** | `scratch/run_pipeline.py` plans, renders, delivers. Nothing renders a proxy, looks, and re-plans. | high |
| C9 | Lenient judge | **not verified** | No calibrated judge exists yet (rubric + calibration come next). | - |

## What the owner can already see in v0 (additional observations)

* One fixed camera, one framing, 8 minutes. Variety comes only from cards and a few B-roll shots.
* The Packshot cards (Armageddon, Seven Kings) sit on flat black with a faint grid: no fireworks
  plate behind them although the brief says "Sticker immer über Hintergrund-Feuerwerk".
* Comparison table at about 3:04 is small, low-contrast text over the speaker: unreadable at phone size.
* End screen (6:00+) is strong; the middle is the weak part.

## Hardware (probed)

AMD Ryzen 7 PRO 5850U (8 cores / 16 threads), 31 GB RAM (WSL sees 15), **no discrete GPU** (AMD Radeon
iGPU, 1 GB), 127 GB free on C:. Consequence: SAM 2 / Depth Anything / CoTracker run on CPU only, per
moment and cached, never per frame of a whole video. Needs a decision (see `decisions.md`, D6).
No `yt-dlp` installed yet.
