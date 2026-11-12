# Final benchmarks (after implementation)

Same machine, inputs and scripts as BASELINE_BENCHMARKS.

| Area | Before | After | Change |
| --- | --- | --- | --- |
| OCR false caption spans on raw footage | 63.3 /h (30 spans, 1419 s/h) | 0 /h | −100 % |
| Caption recall (synthetic, 4 styles) | 1.00 | 0.99 / 1.00 / 1.00 / 0.95 | −0 to −5 % (rolling captions) |
| Short framing | letterboxed 16:9 inside 9:16; subject off-frame on one camera | fills 9:16; subject framed on every sampled second | fixed |
| Short captions | none ("burned-in captions already shown", false) | word-by-word with emoji (🎆 ⚖️ 📏 💥 💪); none over cards | fixed |
| Short length / end | 48.6 s incl. 20 s end screen | 45.8 s, ends on the payoff | −20 s dead end |
| Short opening | mid-product sentence | the product's introduction + its full-screen card | story thread |
| Short config | hand brief copied from the long video | no per-video keys (auto brief) | automatic |
| Resolve projects per attempt | 1 new project each | 1 own project, timeline regenerated | no garbage |
| Library renders per attempt | all kept | older ones archived (45 so far, restorable) | no garbage |
| Short loudness | −14.0 LUFS / −1.5 dBTP | −14.0 LUFS / −1.5 dBTP | unchanged |
| ASR default | large-v3 | large-v3 (turbo opt-in) | unchanged |
| B-roll retrieval | CLIP B/32 | CLIP B/32 | unchanged |
| Analysis runtime | 1.86 h for 25 recordings | unchanged by default; ASR −35 % CPU with turbo | – |

Failed tests: none in the affected suites (1335 passed); one GPU-only test cannot run here.

Remaining weaknesses: copy-written graphics are still manual (13 in the long video); the `drop`
phrase list is manual; ASR accuracy and B-roll retrieval need owner-labelled references before any
model switch; a moving 9:16 window is held per clip (no tracking inside a clip); 1080p sources are
upscaled ~1.8× in a Short. The quality judgement that matters is the owner's review of the render.
