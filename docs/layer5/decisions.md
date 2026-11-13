# Decisions (layer5 craft engine)

Format: decision / alternatives / why / how to reverse.

**D1 Baseline v0 = owner's v6 render, not a fresh pipeline run.** Alt: re-run the full pipeline
(about 1 h analysis + 25 min plan + 22 min render). Why: the greenfield render was deleted on
2026-11-13, v6 is the render the owner actually rated "about 50". Reverse: re-run `scratch/run_pipeline.py`.

**D2 Client brief beats the v3 defaults for the Lidl video.** The brief (`yt-video/pipeline-context.md`)
forbids glitch, wipe, dip-to-white, shape and mask reveal, caps overlays at 3 and says no burned-in
subtitles. Master prompt section 5 would allow object-driven wipes/shapes. For the Lidl video the
brief wins (it is the client's rule); object-driven transitions stay a v3 capability, switched on per
project. Reverse: owner confirms the client allows them.

**D3 Footage is under a Lidl NDA.** Local models only, no cloud vision/LLM on Lidl footage.
Reference videos (public) may use anything.

**D4 Existing docs/layer5/ keeps its name.** It already holds the timeline-construction README and
`seltix-discovery.md`. New craft-engine docs live beside them; no rename.

**D5 Seeing tools use ffmpeg only** (`scripts/layer5/see.py`), no extra dependency. Run via
`uv.exe run --no-sync` from WSL.

**D6 (open, needs owner) No GPU.** Per-moment masks/depth/tracking on CPU are feasible (seconds-minutes
per 5 s clip) but a rented GPU would make the integration layer practical. Default until answered: CPU,
cached, hero moments only.
