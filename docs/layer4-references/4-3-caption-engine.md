# Implementation Prompt — Layer 4, Module 19: `caption_engine`

## Project Context

You are working on an existing backend-first video production system called **Media House**.

The system processes raw footage, analyzes audio and video, develops editorial plans, generates assets, constructs timelines, and ultimately produces finished videos through DaVinci Resolve.

The first three architectural layers already exist:

- **Layer 1 — Media Foundation:** media library, inspection, asset relationships, proxy management.
- **Layer 2 — Media Intelligence:** audio and video intelligence, content intelligence, improvement systems, audio-visual alignment.
- **Layer 3 — Editorial Planning:** story architecture, editing intelligence, pacing, B-roll, continuity, reframing.
- **Layer 4 — Creative Design and Asset Generation:** visual identity, motion graphics, captions, transitions, VFX, music and sound design, asset generation.

Your task is to implement:

# `caption_engine`

Build a professional, modular, reusable caption system that decides what should be captioned, generates correctly timed captions, determines their visual presentation, identifies words deserving emphasis, and plans expressive caption and emoji animations.

**Important:** This module must integrate into the existing Media House architecture. Do not build an isolated caption application or duplicate functionality that already exists elsewhere.

---

## 1. Core Responsibility and Architectural Boundaries

The `caption_engine` owns the intelligence and planning behind captions.

It determines:

- Whether captions are needed.
- Which spoken words, phrases, or statements should become captions.
- How spoken content should be segmented.
- Which words deserve emphasis.
- Which emojis are contextually appropriate.
- When captions and emojis should appear and disappear.
- How captions should be positioned and formatted.
- Which animation presets should be requested.
- Whether captions satisfy readability and synchronization requirements.

It does **not** own the actual animation engine or final graphic rendering.

The architecture must respect these responsibilities:

| Module | Responsibility |
|---|---|
| `visual_style_engine` | Defines the channel's visual identity and design tokens |
| `caption_engine` | Decides what to caption, how to segment it, emphasize it, and plan its presentation |
| `motion_graphics_engine` | Executes text and emoji animations, keyframes, compositing, and rendering |
| `editing_intelligence` | Decides which visual elements are editorially appropriate and when they should appear |
| `timeline_builder` | Places approved caption assets or graphic events on the timeline |
| `resolve_adapter` | Executes the resulting timeline in DaVinci Resolve |

Do not create a second animation engine inside `caption_engine`.

Do not create another transcription system if reliable transcription already exists upstream.

---

## 2. Caption Strategy and Visibility Decisions

Implement a dedicated caption strategy system.

The engine must determine whether captions are needed before generating them.

Support these modes:

- `full_captions`: Captions throughout the video.
- `selective_captions`: Captions only for selected moments.
- `emphasis_only`: Only important words and statements.
- `key_information`: Important facts, numbers, specifications, and instructions.
- `disabled`: No generated captions.

### Format-specific defaults

Implement configurable policies with these initial defaults:

| Video format | Default behavior |
|---|---|
| YouTube Shorts | Full dynamic captions |
| Instagram Reels | Full dynamic captions |
| TikTok-style videos | Full dynamic captions |
| Long-form talking head | Selective emphasis captions |
| Product review | Selective important statements and specifications |
| Unboxing | Selective reactions, features, and important moments |
| Tutorial | Selective instructions and important steps |
| Documentary | Minimalist selective captions |
| Cinematic video | Disabled |

These are presets, not restrictions.

The system must support:

- Global format policies.
- Project-level overrides.
- Section-level overrides.
- Individual caption-segment overrides.
- Explicitly locked or manually approved caption decisions.

### Caption selection signals

Captions may be selected for:

- Important statements.
- Emotional moments.
- Surprising facts.
- Important numbers and statistics.
- Product names and specifications.
- Strong opinions.
- Punchlines and jokes.
- Contrasts and comparisons.
- Warnings.
- Conclusions.
- Memorable phrases.
- Dramatic reveals.
- Technical terminology.
- Difficult-to-understand speech.
- Foreign-language terms or pronunciation support.

Do not generate captions merely because speech exists.

Keep caption selection separate from caption presentation and animation decisions.

---

## 3. Transcription and Audio Intelligence Integration

Use existing transcription data from `audio_intelligence`.

The engine must:

- Import existing transcripts.
- Preserve exact word-level timestamps where available.
- Maintain sentence and phrase references.
- Support multiple speakers.
- Support overlapping speech.
- Handle incomplete sentences.
- Preserve original spoken meaning.
- Handle punctuation and capitalization intelligently.
- Correct obvious transcription errors only when supported by context.
- Preserve technical terms, product names, and proper nouns.
- Support multiple languages.
- Support optional translation without silently changing the original meaning.
- Allow manual corrections and overrides.
- Preserve references to original audio and transcript segments.

### Important

Do not independently transcribe all audio again when accurate transcription already exists.

If upstream transcription lacks required word-level timestamps, inspect the existing `audio_intelligence` capabilities and extend the correct owner where appropriate. Avoid building a duplicate transcription pipeline.

Maintain traceability between:

- Original audio.
- Original transcript.
- Transcript segments.
- Individual words.
- Generated caption segments.
- Final caption assets.

---

## 4. Caption Segmentation and Readability

Implement a dedicated segmentation engine.

It must determine how spoken words are grouped into readable caption units.

Support:

- Sentence-based segmentation.
- Phrase-based segmentation.
- Word-by-word display.
- Highlighted keywords within sentences.
- Dynamic segmentation based on speaking speed.
- Optimized line breaks.
- Multiline captions.
- Different segmentation profiles for Shorts and long-form content.

Configurable properties:

- Maximum characters per line.
- Maximum number of lines.
- Preferred words per caption.
- Maximum reading speed.
- Minimum display duration.
- Maximum display duration.
- Minimum gap between caption segments.
- Line-break optimization.
- Word grouping.
- Phrase grouping.

Prevent:

- Splitting names unnecessarily.
- Splitting numbers and related expressions.
- Awkward breaks in incomplete sentences.
- Excessive simultaneous text.
- Captions displayed too briefly to read.
- Captions remaining visible unnecessarily long.
- Text overflow and clipping.

Validate reading speed and actual display duration.

Support manual segmentation corrections and locked segments.

---

## 5. Animated Caption Styles

This is a core feature of the module.

Implement multiple reusable caption animation styles rather than one universal animation.

The caption engine plans the requested animation. `motion_graphics_engine` executes it.

### A. Word-by-word captions

- Display one word at a time.
- Highlight the currently spoken word.
- Keep previous words visible when appropriate.
- Synchronize with the actual spoken word.
- Support different emphasis levels.
- Allow individual word styling.

### B. Pop-in and pop-out

- Scale words into position.
- Support slight overshoot and bounce.
- Smooth settling animation.
- Optional scale-down or fade-out.
- Configurable intensity.

### C. Fly-in and fly-out

- Horizontal movement.
- Vertical movement.
- Diagonal entrances.
- Different entrance directions.
- Movement toward an anchor position.
- Configurable exit directions and intensity.

### D. Drop and fall animations

Support the expressive falling-word effect:

1. A word or emoji enters from above or another configured direction.
2. It accelerates toward its final position.
3. It lands with a bounce.
4. It performs a small secondary bounce.
5. It settles into its final position.

Additional options:

- Gravity-like acceleration.
- Rotation while falling.
- Squash-and-stretch on impact.
- Motion blur.
- Different fall directions.
- Adjustable impact strength.

This is particularly important for expressive Shorts captions.

### E. Additional animation styles

Support reusable presets for:

- Typewriter reveal.
- Elastic or spring animation.
- Shake and impact.
- Slide and reveal.
- Rotate and flip.
- Scale and blur reveal.
- Mask reveal.
- Staggered word entrances.
- Wave-like text movement.
- Emphasis pulse.
- Highlight sweep.
- Character-by-character animation.

All animation requests must be configurable, reusable, and deterministic.

Do not implement their keyframes or interpolation independently. Delegate execution to `motion_graphics_engine`.

---

## 6. Dedicated Emoji Intelligence

Create a dedicated `emoji_intelligence` submodule.

The objective is to select meaningful emojis that reinforce spoken content and synchronize their appearance with the relevant words or phrases.

### Emoji selection

Identify suitable emojis using:

- Word meaning.
- Phrase meaning.
- Emotional context.
- Situational context.
- Narrative context.
- Speaker delivery.
- Channel preferences.

Support:

- Multiple emojis when appropriate.
- Preferred emoji lists.
- Prohibited emoji lists.
- Different emoji styles and sets.
- Manually assigned emojis.
- Context-sensitive selection.
- Configurable emoji intensity and frequency.

Examples:

| Spoken content | Possible emoji |
|---|---|
| "That was incredible!" | 🤯 |
| "It dropped straight down." | ⬇️ |
| "This is extremely fast." | ⚡ |
| "I love this feature." | ❤️ |
| "That was a huge mistake." | ❌ |
| "This is the best option." | 🏆 |
| "It costs only ten euros." | 💶 |

These are contextual suggestions, not fixed keyword substitutions.

Avoid excessive emojis and never select an emoji that changes or distorts the intended meaning.

### Emoji timing

- Synchronize emojis with the relevant word or phrase.
- Allow an emoji to appear slightly before, during, or immediately after vocal emphasis.
- Prevent emojis from appearing too early.
- Remove them when their relevance ends.
- Support multiple emojis appearing at different moments.

### Emoji animation requests

Support:

- Fly-in from different directions.
- Drop and bounce.
- Shoot upward and fall.
- Pop and overshoot.
- Spin into position.
- Shake on impact.
- Scale up and settle.
- Bounce out.
- Fly off screen.
- Fade and shrink.
- Combined movement, rotation, scale, and opacity.

The actual animation is executed by `motion_graphics_engine`.

### Emoji positioning

Emojis may appear:

- Next to a word.
- Above a word.
- Below a word.
- As an independent visual element.
- Within an animated caption sequence.

Prevent collisions with other graphic elements and avoid covering important footage or captions.

### Emoji rendering consistency

This is an important technical requirement.

Do not rely on whichever emoji font happens to be installed on the current machine.

Use a controlled emoji asset library or a verified rendering system.

Distinguish between:

- Standard Unicode emojis rendered as graphic assets.
- Custom animated emoji assets.

Verify asset availability, rendering consistency, transparency, and compatibility across machines.

---

## 7. Context-Aware Caption Emphasis

Implement a dedicated `emphasis_intelligence` subsystem.

It must identify which words and phrases deserve visual emphasis.

Consider:

- Emotional emphasis.
- Increased vocal intensity.
- Important statements.
- Surprising facts.
- Numbers and statistics.
- Product names and specifications.
- Strong opinions.
- Punchlines.
- Contrasts and comparisons.
- Warnings.
- Conclusions.
- Memorable phrases.
- Dramatic reveals.

Use existing intelligence from:

| Module | Information consumed |
|---|---|
| `audio_intelligence` | Vocal emphasis, delivery, emotional signals |
| `content_intelligence` | Meaning, factual importance, key information |
| `story_architecture` | Narrative significance |
| `pacing_intelligence` | Timing, rhythm, and pacing |

Do not assume the loudest word is automatically the most important word.

Emphasis decisions should combine semantic, acoustic, and narrative context.

Support configurable emphasis levels, for example:

- `normal`
- `medium`
- `high`
- `critical`

Allow downstream presentation rules to map these levels to different visual treatments.

---

## 8. Caption Positioning and Visual Composition

Implement a responsive caption layout system.

Support:

- Centered layouts.
- Lower-third layouts.
- Word highlighting without moving the entire caption.
- Subject-aware positioning.
- Face avoidance.
- Product avoidance.
- Hand and important-object avoidance.
- Shorts safe areas.
- Consistent margins and alignment.
- Multiple caption layouts.
- Emoji-aware positioning.
- Multiple aspect ratios.
- Repositioning when subjects move.

Integrate with:

- `video_intelligence` for visual information.
- `reframing_intelligence` for composition and safe areas.
- `motion_graphics_engine` for final graphic composition.

Avoid covering:

- Faces.
- Important products.
- Hands demonstrating something.
- Important text in the footage.
- Other essential visual information.

The system must support responsive layouts for:

- 16:9.
- 9:16.
- 1:1.
- Custom aspect ratios.

Reflow captions where necessary rather than simply scaling the original layout.

---

## 9. Visual Identity and Style Integration

The caption engine must consume the design system provided by `visual_style_engine`.

It must not independently invent a new visual identity for each video.

Support configurable properties:

- Font family.
- Font weight.
- Font size.
- Text color.
- Highlight color.
- Outline and stroke.
- Shadow.
- Background and transparency.
- Letter spacing.
- Line spacing.
- Word spacing.
- Alignment.
- Position.
- Maximum line length.
- Animation style.
- Emoji appearance.
- Emoji size.
- Highlight behavior.

### Caption profiles

Implement reusable profiles such as:

- Standard Shorts captions.
- Energetic Shorts captions.
- Minimalist captions.
- Documentary captions.
- Product review emphasis captions.
- Tutorial captions.
- Talking-head emphasis captions.

Profiles must inherit the channel's global visual identity.

Support:

- Global channel identity.
- Format-specific caption profiles.
- Project-level overrides.
- Section-level overrides.
- Individual segment overrides.

Preserve identity versions and configuration references so existing projects remain reproducible.

Do not duplicate the visual identity system.

---

## 10. Integration with `motion_graphics_engine`

This is a strict architectural boundary.

The caption engine owns:

- Caption visibility decisions.
- Caption content.
- Word and phrase segmentation.
- Word-level timing.
- Emphasis decisions.
- Emoji selection.
- Emoji timing.
- Caption-specific layout.
- Animation requests.
- Caption readability and timing validation.

The motion graphics engine owns:

- Actual text animation execution.
- Emoji animation execution.
- Drop, bounce, fly-in, pop, and other motion presets.
- Keyframes and interpolation.
- Compositing and layering.
- Graphic rendering.
- Transparency.
- Rendered output assets.

The caption engine must send structured, versioned animation requests to `motion_graphics_engine`.

### Illustrative animation request

```json
{
  "caption_id": "caption_001",
  "text": "This is absolutely incredible!",
  "display_mode": "word_by_word",
  "words": [
    {
      "text": "This",
      "start": 1.20,
      "end": 1.42,
      "emphasis": "normal"
    },
    {
      "text": "absolutely",
      "start": 1.42,
      "end": 1.85,
      "emphasis": "normal"
    },
    {
      "text": "incredible!",
      "start": 1.85,
      "end": 2.40,
      "emphasis": "high",
      "highlight": true,
      "emoji": {
        "asset": "🤯",
        "start": 1.85,
        "end": 2.50,
        "animation": {
          "preset": "drop_bounce"
        }
      }
    }
  ],
  "animation_requests": {
    "text_preset": "pop_bounce",
    "emoji_preset": "drop_bounce"
  }
}
```

This is an illustrative contract, not a fixed schema.

Implement an actual versioned and validated interface compatible with the existing `motion_graphics_engine`.

The interface must support:

- Caption and word identifiers.
- Source transcript references.
- Word-level timestamps.
- Emphasis levels.
- Highlight states.
- Emoji asset references.
- Emoji timing.
- Text animation presets.
- Emoji animation presets.
- Layout and positioning instructions.
- Style and identity version references.
- Timeline synchronization references.

Do not pass vague natural-language animation descriptions when a structured preset and configuration can be used.

---

## 11. Integration with the Existing Media House Pipeline

Inspect the existing repository before implementing integrations.

Reuse existing services, schemas, asset references, event systems, and conventions.

### Required integrations

**`audio_intelligence`**
- Reuse transcription.
- Import word-level timestamps.
- Consume speaker and delivery information.
- Use vocal emphasis signals.

**`content_intelligence`**
- Understand semantic importance.
- Identify facts, numbers, product details, and important statements.

**`story_architecture`**
- Understand narrative importance and dramatic moments.

**`pacing_intelligence`**
- Coordinate caption duration and rhythm.

**`visual_style_engine`**
- Consume typography, colors, caption profiles, and visual identity versions.

**`video_intelligence`**
- Consume subject and scene information for positioning.

**`reframing_intelligence`**
- Consume composition and safe-area information.

**`motion_graphics_engine`**
- Send structured text and emoji animation requests.
- Receive generated graphic references and rendering results.

**`timeline_builder`**
- Supply caption timing and graphic references in the expected format.
- Do not manipulate the timeline directly.

**`resolve_adapter`**
- Do not communicate with or control DaVinci Resolve directly.

**`quality_control`**
- Provide caption-specific validation results.
- Support final-video caption checks through existing QC interfaces.

### Important

Do not reimplement any existing Layer 1–3 intelligence.

If an upstream module lacks a required capability, inspect the existing implementation and extend the correct module rather than duplicating its responsibility inside `caption_engine`.

---

## 12. Suggested Internal Module Structure

Use the following as an architectural guide. Adapt it to existing repository conventions rather than creating unnecessary directories or abstractions.

```text
caption_engine/
│
├── caption_strategy/
│   ├── format_policies/
│   ├── caption_mode_selection/
│   └── caption_need_detection/
│
├── transcription_integration/
│   ├── transcript_import/
│   ├── word_timestamp_mapping/
│   └── speaker_mapping/
│
├── caption_segmentation/
│   ├── sentence_segmentation/
│   ├── phrase_segmentation/
│   ├── word_segmentation/
│   ├── line_break_optimization/
│   └── reading_speed_validation/
│
├── emphasis_intelligence/
│   ├── semantic_emphasis/
│   ├── acoustic_emphasis/
│   ├── narrative_emphasis/
│   └── keyword_selection/
│
├── emoji_intelligence/
│   ├── emoji_selection/
│   ├── context_matching/
│   ├── emoji_timing/
│   ├── emoji_asset_library/
│   └── emoji_validation/
│
├── caption_layout/
│   ├── typography/
│   ├── positioning/
│   ├── responsive_layout/
│   ├── safe_area_handling/
│   └── subject_avoidance/
│
├── animation_planning/
│   ├── caption_animation_requests/
│   ├── word_animation_planning/
│   ├── emoji_animation_planning/
│   └── animation_synchronization/
│
├── style_profiles/
│   ├── shorts/
│   ├── long_form/
│   ├── minimalist/
│   ├── energetic/
│   └── project_overrides/
│
├── rendering_integration/
│   ├── motion_graphics_interface/
│   ├── asset_generation/
│   └── render_caching/
│
└── validation/
    ├── timing_validation/
    ├── readability_validation/
    ├── visual_validation/
    └── synchronization_validation/
```

Do not create empty modules simply to match this tree. Implement functional components with clear ownership and tests.

---

## 13. Data Contracts and Output

Design explicit, versioned data contracts.

A caption result should retain, at minimum:

- `caption_id`
- `project_id`
- `caption_mode`
- `caption_type`
- `source_transcript_id`
- `source_segment_references`
- `speaker_reference`
- `original_text`
- `display_text`
- `language`
- `words`
- `word_timestamps`
- `emphasis_annotations`
- `emoji_annotations`
- `segmentation_configuration`
- `layout_specification`
- `style_profile_id`
- `visual_identity_version`
- `animation_requests`
- `source_media_start`
- `source_media_end`
- `timeline_start`
- `timeline_end`
- `duration`
- `frame_rate`
- `aspect_ratio`
- `motion_graphics_asset_references`
- `validation_results`
- `configuration_hash`
- `generation_version`

Keep the following time domains separate:

1. Source-media time.
2. Timeline time.
3. Caption-local animation time.

Do not assume they are interchangeable.

Preserve stable references to the original audio and transcript so that timeline changes do not destroy synchronization traceability.

The exact schema must follow existing Media House conventions and be validated at module boundaries.

---

## 14. Performance, Caching, and Reproducibility

Implement:

- Transcript import without unnecessary reprocessing.
- Cached caption selection and segmentation.
- Cached emphasis decisions where inputs have not changed.
- Cached emoji selection and timing.
- Cached rendered caption assets.
- Batch caption generation.
- Deterministic animation requests.
- Preview-quality and final-quality rendering support.
- Reuse of existing assets and templates.
- Incremental updates.
- Manual correction preservation.
- Locked caption preservation.

Re-render only affected captions when relevant dependencies change, including:

- Text.
- Word timestamps.
- Segmentation.
- Caption mode.
- Style profile.
- Visual identity version.
- Font assets.
- Emoji assets.
- Animation preset.
- Animation configuration.
- Layout.
- Resolution.
- Frame rate.

Avoid unnecessary AI/model calls for simple formatting and deterministic operations.

---

## 15. Validation and Quality Control

Implement caption-specific validation with actionable reports.

### Timing validation

- Word-to-audio synchronization.
- Caption start and end times.
- Display duration.
- Reading speed.
- Timing gaps and overlaps.
- Timeline offset correctness.
- Frame-rate consistency.

### Readability validation

- Maximum line length.
- Maximum line count.
- Reading speed.
- Minimum display duration.
- Font size.
- Contrast.
- Line breaks.
- Excessive simultaneous text.

### Visual validation

- Text clipping.
- Text overflow.
- Safe-area violations.
- Face and product obstruction.
- Emoji collisions.
- Layout conflicts.
- Incorrect aspect-ratio handling.
- Missing fonts.
- Missing emoji assets.

### Synchronization validation

- Correct word-level timing.
- Emoji synchronization.
- Animation timing.
- Original audio references.
- Timeline changes.
- Caption continuity.

Return structured results with:

- Severity.
- Affected caption or word.
- Problem description.
- Relevant time range.
- Suggested correction.
- Whether automatic correction is safe.

Support warnings and errors separately.

Manual corrections and locked captions must not be silently overwritten by automatic validation or regeneration.

---

## 16. Implementation Priorities

Implement in the following order:

| Priority | Component | Importance |
|---|---|---|
| 1 | Caption strategy and format policies | Avoid unnecessary captions |
| 2 | Transcript and word timestamp integration | Accurate synchronization |
| 3 | Caption segmentation | Readability |
| 4 | Selective caption and emphasis intelligence | Useful long-form captions |
| 5 | Visual style profile integration | Consistent identity |
| 6 | Motion graphics integration | Animated captions |
| 7 | Emoji selection and contextual matching | Expressive enhancement |
| 8 | Emoji animation planning | Drop, bounce, fly-in, and other effects |
| 9 | Responsive positioning | Avoid covering important footage |
| 10 | Validation and caching | Reliable and efficient output |

Do not attempt to implement everything as one large service.

Build the core pipeline first, then extend it in independently testable stages.

---

## 17. Strict Scope Restrictions

The following restrictions are mandatory:

- Do not build a user interface.
- Do not build another transcription engine.
- Do not reimplement audio intelligence.
- Do not reimplement content intelligence.
- Do not reimplement story architecture or pacing intelligence.
- Do not build a second animation engine.
- Do not implement graphic rendering independently of `motion_graphics_engine`.
- Do not build a separate visual identity system.
- Do not directly manipulate the editing timeline.
- Do not communicate directly with DaVinci Resolve.
- Do not build an independent video tracking system.
- Do not duplicate asset management.
- Do not generate captions automatically for every video regardless of editorial need.

This is a backend-first production module.

---

## 18. Testing Requirements

Implement meaningful unit, integration, and end-to-end tests.

At minimum, test:

1. Full captions for Shorts.
2. Full captions for Reels.
3. Selective captions for long-form videos.
4. Disabled captions.
5. Project-level and segment-level overrides.
6. Transcript reuse from `audio_intelligence`.
7. Word-level timestamp preservation.
8. Sentence and phrase segmentation.
9. Dynamic segmentation at different speaking speeds.
10. Reading-speed validation.
11. Technical terminology and proper nouns.
12. Multiple speakers and overlapping speech.
13. Manual corrections and locked captions.
14. Semantic and acoustic emphasis.
15. Context-aware emoji selection.
16. Emoji timing and asset validation.
17. Animation request generation.
18. Integration with `motion_graphics_engine`.
19. Visual identity inheritance and overrides.
20. Responsive caption layouts.
21. Subject-aware positioning.
22. Safe-area validation.
23. Source-media and timeline-time conversion.
24. Cache invalidation.
25. Deterministic regeneration.
26. Timeline builder compatibility.
27. QC reporting.
28. Existing pipeline regression tests.

Use realistic examples with short-form and long-form footage.

Verify not only that data structures are generated, but also that actual caption assets can be rendered through the existing motion graphics pipeline.

---

## 19. Definition of Done

The module is complete when:

- Caption visibility is intelligently determined by format and editorial context.
- Shorts can receive full dynamic captions by default.
- Long-form videos can receive selective captions without unnecessary permanent subtitles.
- Existing upstream transcripts and word timestamps are reused.
- Segmentation is readable and accurate.
- Important words receive contextual emphasis.
- Emojis are selected meaningfully and synchronized correctly.
- Emoji rendering is consistent across machines.
- Animated caption requests are generated correctly.
- Actual animation is executed by `motion_graphics_engine`.
- Visual identity is inherited from `visual_style_engine`.
- Captions adapt to different aspect ratios and safe areas.
- Captions remain synchronized when the timeline changes.
- Manual edits and locked segments are preserved.
- Caching and incremental regeneration work correctly.
- Validation produces actionable results.
- Output contracts are compatible with `timeline_builder`.
- Existing Media House modules continue to work without regressions.
- No duplicate transcription, animation, visual identity, asset management, or timeline systems have been introduced.

---

## Final Implementation Instruction

Before writing code, inspect the existing Media House repository and understand the current architecture, module conventions, data contracts, asset management, and integration points.

Then implement `caption_engine` as a production-ready, modular backend component.

Do not simply create directories and placeholder classes. Implement real functionality, connect it to the existing pipeline, write tests, and verify actual generated caption assets.

The central design principle is:

**Caption selection decides whether and what to display. Caption presentation decides how the content should look. Animation planning decides how it should move. `motion_graphics_engine` executes and renders those instructions.**

The result should support both clean, professional long-form YouTube videos and expressive, highly animated Shorts with synchronized bouncing words, falling emojis, flying text, and contextual visual emphasis — without sacrificing readability, synchronization, or the channel's visual identity.
