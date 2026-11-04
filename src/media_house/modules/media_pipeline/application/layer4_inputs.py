"""The edit view as each Layer 4 module's own input DTO (the modules never see the edit plan).

Every mapper reads the same ``EditView`` (``edit_view.py``), so clip ids (= placement ids), times,
sections and the frame rate agree across the graphics, caption, transition, VFX and sound plans
of one edit. Each table is the owning module's README "EditPlan -> DTO" mapping; facts a module
reads itself from the stored analyses (words, story, pacing) are not re-derived here.
"""

from collections.abc import Callable, Iterable
from dataclasses import replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.audio_intelligence.application.contracts import AudioIntelligenceTimeline
from media_house.modules.caption_engine.application.contracts import (
    CaptionClip,
    CaptionPlan,
    CaptionTimeline,
    EmphasisLevel,
    RenderStatus,
)
from media_house.modules.caption_engine.application.contracts import EditRef as CaptionEditRef
from media_house.modules.content_intelligence.application.contracts import (
    CommerceFact,
    CommerceKind,
    commerce_facts,
)
from media_house.modules.media_pipeline.application.edit_view import (
    EditView,
    ViewClip,
    fr,
    rat,
    seconds,
)
from media_house.modules.motion_graphics_engine.application.contracts import (
    BriefSection,
    ChartKind,
    EditorialGraphic,
    EventKind,
    GraphicKind,
    GraphicsBrief,
    GraphicsPlan,
    Intent,
    ItemStatus,
    OnScreenText,
    ProductFacts,
    product_facts,
    rank_of,
)
from media_house.modules.motion_graphics_engine.application.contracts import (
    EditRef as GraphicsEditRef,
)
from media_house.modules.music_and_sound_design.application.contracts import (
    AudioBridge,
    BridgeKind,
    ClipAudio,
    EventSource,
    SoundtrackTimeline,
    SyncEvent,
    TimelineClip,
    TimelineSection,
)
from media_house.modules.music_and_sound_design.application.contracts import (
    EditRef as SoundEditRef,
)
from media_house.modules.transition_engine.application.contracts import (
    AudioBridgeMark,
    Bookend,
    BookendKind,
    SectionMark,
    TransitionClip,
    TransitionPlan,
    TransitionRequest,
    TransitionSequence,
)
from media_house.modules.transition_engine.application.contracts import (
    EditRef as TransitionEditRef,
)
from media_house.modules.vfx_engine.application.contracts import (
    ClipAudio as VfxClipAudio,
)
from media_house.modules.vfx_engine.application.contracts import (
    EditRef as VfxEditRef,
)
from media_house.modules.vfx_engine.application.contracts import (
    RequestKind,
    SourceSpan,
    VfxClip,
    VfxRequest,
    VfxShots,
)
from media_house.modules.video_intelligence.application.contracts import VideoAnalysis

#: Editing's effect kinds as each consumer's vocabulary (kinds another module owns are not mapped).
_VFX_REQUESTS = {"reframe": RequestKind.EMPHASIS, "music_cue": RequestKind.REVEAL}
_EDIT_SOUND_EVENTS = {"transition": "section_change", "music_cue": "reveal"}
#: A graphic's entry as music's event vocabulary (``SfxRule.kind`` of the graphics source).
_GRAPHIC_REVEALS = {
    GraphicKind.RANK_CARD: "rank_reveal",
    GraphicKind.PRICE_TAG: "price_reveal",
    GraphicKind.LOGO_STING: "logo_sting",
    GraphicKind.SILHOUETTE_TEASER: "reveal",
}
_ENTRY_EVENTS = {"pop_in": "pop_in", "slide_in": "fly_in", "wipe_in": "fly_in"}
#: Caption emphasis as an event strength (music's ``keyword`` rule takes only the strongest).
_EMPHASIS_STRENGTH = {
    EmphasisLevel.CRITICAL: 1.0,
    EmphasisLevel.HIGH: 0.9,
    EmphasisLevel.MEDIUM: 0.6,
}
#: A planned item whose output a timeline will hold (a missing renderer still means "planned").
_PLANNED = {ItemStatus.OK, ItemStatus.NOT_AVAILABLE}
#: Caption items that will be seen (a failed or impossible render falls back to nothing).
_SHOWN = {RenderStatus.RENDERED, RenderStatus.REUSED, RenderStatus.NOT_REQUESTED}


def _sound_ids(view: EditView) -> set[str]:
    return {c.clip_id for c in view.sound() if c.audio != "muted"}


# --- motion_graphics_engine -------------------------------------------------------------------
type _Heard = list[tuple[str, Fraction]]  # (word, timeline time it is spoken)


def section_words(
    view: EditView, transcript: AudioIntelligenceTimeline | None
) -> dict[str, _Heard]:
    """The words heard in each section with their timeline time (through the sound clips)."""
    if transcript is None:
        return {}
    heard: dict[str, _Heard] = {}
    for clip in view.sound():
        if clip.section_id is None or clip.asset_id != view.anchor_asset_id:
            continue
        offset = fr(clip.timeline_start) - fr(clip.source_start)
        words = transcript.words_between(float(fr(clip.source_start)), float(fr(clip.source_end)))
        heard.setdefault(clip.section_id, []).extend(
            (w.word.raw_word, max(fr(clip.timeline_start), seconds(w.word.start) + offset))
            for w in words
        )
    return heard


def section_text(view: EditView, transcript: AudioIntelligenceTimeline | None) -> dict[str, str]:
    """The words heard in each section (the transcript through the section's sound clips)."""
    return {
        sid: " ".join(w for w, _ in heard) for sid, heard in section_words(view, transcript).items()
    }


def _timed_facts(heard: _Heard, language: str | None) -> list[tuple[CommerceFact, Fraction]]:
    """``commerce_facts`` of the joined words, each with the time its first word is spoken."""
    text, starts = "", []
    for word, _ in heard:
        starts.append(len(text) + (1 if text else 0))
        text = f"{text} {word}" if text else word
    out = []
    for fact in commerce_facts(text, language):
        index = max(i for i, at in enumerate(starts) if at <= fact.start) if starts else 0
        out.append((fact, heard[index][1]))
    return out


def _products(section_id: str, timed: list[tuple[CommerceFact, Fraction]]) -> list[ProductFacts]:
    """One product per spoken price (the facts heard since the previous price name it); facts
    after the last price belong to it; no price at all = one product of every fact."""
    groups: list[list[tuple[CommerceFact, Fraction]]] = [[]]
    for fact, at in timed:
        groups[-1].append((fact, at))
        if fact.kind is CommerceKind.PRICE:
            groups.append([])
    if len(groups) > 1 and groups[-1]:
        groups[-2].extend(groups.pop())
    out = []
    for n, group in enumerate(g for g in groups if g):
        facts = tuple(f for f, _ in group)
        priced = [at for f, at in group if f.kind is CommerceKind.PRICE]
        product = product_facts(
            f"product:{section_id}" + (f":{n + 1}" if n else ""),
            facts,
            source=f"speech:{section_id}",
        )
        at = priced[0] if priced else group[0][1]
        specs = tuple(
            replace(spec, timeline_at=rat(next(a for f, a in group if f.text == spec.text)))
            for spec in product.specs
        )
        out.append(replace(product, specs=specs, timeline_at=rat(at), section_id=section_id))
    return out


def on_screen_text(view: EditView, video: VideoAnalysis | None) -> tuple[OnScreenText, ...]:
    """Text the anchor's picture already shows (video OCR: overlays and the text tracked in each
    shot, e.g. a price card mid-picture), mapped through its picture clips."""
    if video is None:
        return ()
    out = []
    read = [(o.text, o.first.seconds, o.last.seconds) for o in video.overlays] + [
        (i.text, i.first.seconds, i.last.seconds) for s in video.shots for i in s.text.items
    ]
    for text, first_at, last_at in read:
        if not text:
            continue
        first, last = Fraction(first_at), Fraction(last_at)
        for clip in view.picture():
            if clip.asset_id != video.asset_id:
                continue
            a, b = max(first, fr(clip.source_start)), min(last, fr(clip.source_end))
            if b < a:
                continue
            offset = fr(clip.timeline_start) - fr(clip.source_start)
            out.append(
                OnScreenText(
                    timeline_start=rat(a + offset), timeline_end=rat(b + offset), text=text
                )
            )
    return tuple(sorted(out, key=lambda t: (fr(t.timeline_start), t.text)))


def graphics_brief(
    view: EditView,
    *,
    width: int,
    height: int,
    sponsored: bool = False,
    transcript: AudioIntelligenceTimeline | None = None,
    video: VideoAnalysis | None = None,
) -> GraphicsBrief:
    """Sections with their rank and product facts (content's ``commerce_facts`` of the words
    heard in them, each with the time it is spoken), editorial GRAPHIC requests, the text the
    picture already shows, the delivery size and the timeline length."""
    language = transcript.language if transcript else None
    sections: list[BriefSection] = []
    products: list[ProductFacts] = []
    heard = section_words(view, transcript)
    for s in view.sections:
        timed = _timed_facts(heard.get(s.section_id, []), language)
        found = [
            p for p in _products(s.section_id, timed)
            if p.price_cents is not None or p.specs or p.name
        ]  # fmt: skip
        products += found
        ranked = [(f, at) for f, at in timed if f.kind is CommerceKind.RANK]
        rank = rank_of(tuple(f for f, _ in timed))
        rank_at = next((at for f, at in ranked if rank_of((f,)) == rank), None)
        sections.append(
            BriefSection(
                section_id=s.section_id,
                title=s.label,
                purpose=s.purpose,
                timeline_start=s.timeline_start,
                timeline_end=s.timeline_end,
                rank=rank,
                product_id=found[0].product_id if found else None,
                rank_at=None if rank_at is None else rat(rank_at),
            )
        )
    by_section = {s.section_id: s.product_id for s in sections}
    graphics = tuple(
        EditorialGraphic(
            request_id=e.request_id,
            intent=Intent(e.intent),
            timeline_at=e.timeline_at,
            priority=e.priority,
            concept=e.concept or e.reason,
            section_id=e.section_id,
            duration=e.duration,
            product_id=by_section.get(e.section_id or ""),
            reason=e.reason,
        )
        for e in view.effects_of("graphic")
        if e.intent in {i.value for i in Intent}
    )
    return GraphicsBrief(
        anchor_asset_id=view.anchor_asset_id,
        project_id=view.project_id,
        edit=_edit(view, GraphicsEditRef),
        frame_rate=view.frame_rate,
        width=width,
        height=height,
        timeline_duration=view.duration,
        story_format=view.story_format,
        sponsored=sponsored,
        sections=tuple(sections),
        graphics=graphics,
        products=tuple(products),
        on_screen=on_screen_text(view, video),
    )


# --- caption_engine ---------------------------------------------------------------------------
def caption_timeline(view: EditView, *, width: int, height: int) -> CaptionTimeline:
    """Every placement; its sound is captioned only where the edit plays it (the audio track,
    else the main picture), so a picture and its linked sound clip are never captioned twice."""
    heard = _sound_ids(view)
    return CaptionTimeline(
        edit=_edit(view, CaptionEditRef),
        project_id=view.project_id,
        frame_rate=view.frame_rate,
        width=width,
        height=height,
        clips=tuple(
            CaptionClip(
                clip_id=c.clip_id,
                asset_id=c.asset_id,
                source_start=c.source_start,
                source_end=c.source_end,
                timeline_start=c.timeline_start,
                picture=c.picture,
                audio=c.clip_id in heard,
                section_id=c.section_id,
                beat_function=c.beat_function,
            )
            for c in view.clips
        ),
        story_format=view.story_format,
        frame_rate_assumed=view.frame_rate_assumed,
    )


# --- transition_engine ------------------------------------------------------------------------
def transition_sequence(view: EditView, graphics: GraphicsPlan | None = None) -> TransitionSequence:
    """The picture clips (ids = placement ids, as continuity's), sections, TRANSITION requests,
    J/L bridges, and the brand bookends the graphics plan places (end screen, membership plug)."""
    return TransitionSequence(
        edit=_edit(view, TransitionEditRef),
        project_id=view.project_id,
        frame_rate=view.frame_rate,
        clips=tuple(
            TransitionClip(
                clip_id=c.clip_id,
                asset_id=c.asset_id,
                source_start=c.source_start,
                source_end=c.source_end,
                timeline_start=c.timeline_start,
                layer=c.layer,
                section_id=c.section_id,
                beat_id=c.beat_id,
                beat_function=c.beat_function,
            )
            for c in view.picture()
        ),
        sections=tuple(
            SectionMark(section_id=s.section_id, order=s.order, label=s.label, purpose=s.purpose)
            for s in view.sections
        ),
        requests=tuple(
            TransitionRequest(
                request_id=e.request_id,
                timeline_at=e.timeline_at,
                section_id=e.section_id,
                priority=e.priority,
                reason=e.reason,
                decision_id=e.decision_id,
            )
            for e in view.effects_of("transition")
        ),
        bridges=tuple(
            AudioBridgeMark(
                bridge_id=b.bridge_id,
                kind=b.kind,
                audio_changes_at=b.audio_at,
                picture_changes_at=b.picture_at,
            )
            for b in view.bridges
        ),
        bookends=_bookends(graphics),
        format=view.story_format,
        frame_rate_assumed=view.frame_rate_assumed,
    )


def _bookends(graphics: GraphicsPlan | None) -> tuple[Bookend, ...]:
    kinds = {
        GraphicKind.END_SCREEN: BookendKind.END_SCREEN,
        GraphicKind.MEMBERSHIP_PLUG: BookendKind.MEMBERSHIP_PLUG,
    }
    if graphics is None:
        return ()
    found = [
        Bookend(kind=kinds[g.request.kind], timeline_at=g.request.timing.timeline_start)
        for g in graphics.graphics
        if g.request.kind in kinds
        and g.status in _PLANNED
        and g.request.timing.timeline_start is not None
    ]
    return tuple(sorted(found, key=lambda b: fr(b.timeline_at)))


# --- vfx_engine -------------------------------------------------------------------------------
def vfx_shots(
    view: EditView,
    *,
    width: int,
    height: int,
    transcript: AudioIntelligenceTimeline | None = None,
) -> VfxShots:
    """The picture clips with the speech they carry (so retiming never breaks lip sync), and the
    REFRAME (emphasis) and MUSIC_CUE (reveal) requests."""
    return VfxShots(
        edit=_edit(view, VfxEditRef),
        project_id=view.project_id,
        frame_rate=view.frame_rate,
        output_width=width,
        output_height=height,
        clips=tuple(
            VfxClip(
                clip_id=c.clip_id,
                asset_id=c.asset_id,
                source_start=c.source_start,
                source_end=c.source_end,
                timeline_start=c.timeline_start,
                layer=c.layer,
                section_id=c.section_id,
                beat_id=c.beat_id,
                audio=VfxClipAudio(c.audio),
                speech=_speech(c, transcript, view.anchor_asset_id),
            )
            for c in view.picture()
        ),
        requests=tuple(
            VfxRequest(
                request_id=e.request_id,
                kind=_VFX_REQUESTS[e.kind],
                timeline_at=e.timeline_at,
                duration=e.duration,
                priority=e.priority,
                reason=e.reason,
                section_id=e.section_id,
                decision_id=e.decision_id,
            )
            for e in view.effects_of(*_VFX_REQUESTS)
        ),
        format=view.story_format,
        frame_rate_assumed=view.frame_rate_assumed,
    )


def _speech(
    clip: ViewClip, transcript: AudioIntelligenceTimeline | None, asset_id: str
) -> tuple[SourceSpan, ...]:
    """Transcript segments inside the clip's source range (clamped to it)."""
    if transcript is None or clip.asset_id != asset_id:
        return ()
    low, high = fr(clip.source_start), fr(clip.source_end)
    spans = []
    for segment in transcript.segments_between(float(low), float(high)):
        start, end = max(low, seconds(segment.start)), min(high, seconds(segment.end))
        if end > start:
            spans.append(SourceSpan(source_start=rat(start), source_end=rat(end)))
    return tuple(spans)


# --- music_and_sound_design -------------------------------------------------------------------
def soundtrack_timeline(
    view: EditView, events: tuple[SyncEvent, ...] = (), appended_until: Rational | None = None
) -> SoundtrackTimeline:
    """The clips whose sound the edit plays, sections, J/L bridges, every sync event and the end
    of the end screen appended after the content (graphics' ``timeline_end_with_bookends``).
    Speech, loud original moments and pacing cues are left empty on purpose: the planner reads
    them from the stored analyses (handed in memory by the task) and maps them through these
    clips."""
    return SoundtrackTimeline(
        edit=_edit(view, SoundEditRef),
        project_id=view.project_id,
        frame_rate=view.frame_rate,
        frame_rate_assumed=view.frame_rate_assumed,
        clips=tuple(
            TimelineClip(
                clip_id=c.clip_id,
                asset_id=c.asset_id,
                source_start=c.source_start,
                source_end=c.source_end,
                timeline_start=c.timeline_start,
                audio=ClipAudio(c.audio),
                section_id=c.section_id,
                layer=c.layer,
            )
            for c in view.sound()
        ),
        sections=tuple(
            TimelineSection(
                section_id=s.section_id,
                timeline_start=s.timeline_start,
                timeline_end=s.timeline_end,
                label=s.label,
                beat_functions=s.beat_functions,
            )
            for s in view.sections
        ),
        bridges=tuple(
            AudioBridge(
                bridge_id=b.bridge_id,
                kind=BridgeKind(b.kind),
                timeline_audio_at=b.audio_at,
                timeline_picture_at=b.picture_at,
            )
            for b in view.bridges
        ),
        events=events,
        story_format=view.story_format,
        appended_until=appended_until,
    )


def sync_events(
    view: EditView,
    *,
    graphics: GraphicsPlan | None = None,
    transitions: TransitionPlan | None = None,
    captions: CaptionPlan | None = None,
) -> tuple[SyncEvent, ...]:
    """Every visual moment of the other Layer 4 plans (and the edit's own cue requests) in music's
    event vocabulary, in timeline order. Music decides which ones get a sound (with restraint)."""
    found: list[SyncEvent] = [
        SyncEvent(
            event_id=e.request_id,
            source=EventSource.EDIT,
            kind=_EDIT_SOUND_EVENTS[e.kind],
            timeline_at=e.timeline_at,
            strength=e.priority,
        )
        for e in view.effects_of(*_EDIT_SOUND_EVENTS)
    ]
    if graphics is not None:
        found.extend(_graphic_events(graphics))
    if transitions is not None:
        found.extend(
            SyncEvent(
                event_id=t.transition_id,
                source=EventSource.TRANSITION,
                kind=t.kind.value,
                timeline_at=t.timeline_at,
                strength=t.strength,
            )
            for t in transitions.events
        )
    if captions is not None:
        found.extend(_caption_events(captions))
    return tuple(sorted(found, key=lambda e: (fr(e.timeline_at), e.event_id)))


def _graphic_events(plan: GraphicsPlan) -> Iterable[SyncEvent]:
    """A graphic's arrival (the peak of its entry) and its emphasis beats. Exits and captions'
    own graphics are not moments a sound follows."""
    items = {g.graphic_id: g for g in plan.graphics if g.status in _PLANNED}
    for index, event in enumerate(plan.events):
        item = items.get(event.graphic_id)
        if item is None or event.timeline_at is None:
            continue
        kind: str | None = None
        if event.kind is EventKind.EMPHASIS:
            kind = "highlight"
        elif event.kind is EventKind.ENTER_PEAK:
            request = item.request
            if request.kind is GraphicKind.CHART and request.content.chart is ChartKind.COUNTER:
                kind = "counter"
            else:
                entry = item.animation.motion.entry if item.animation else ""
                kind = _GRAPHIC_REVEALS.get(request.kind) or _ENTRY_EVENTS.get(entry, entry)
        if kind:
            yield SyncEvent(
                event_id=f"{event.graphic_id}:{event.kind.value}:{index}",
                source=EventSource.GRAPHICS,
                kind=kind,
                timeline_at=event.timeline_at,
                strength=event.strength,
            )


def _caption_events(plan: CaptionPlan) -> Iterable[SyncEvent]:
    """Emphasised words (``keyword``) and emoji pops of the captions that are shown."""
    for segment in plan.segments:
        if segment.render.status not in _SHOWN:
            continue
        for word in segment.words:
            strength = _EMPHASIS_STRENGTH.get(word.emphasis)
            if strength is not None:
                yield SyncEvent(
                    event_id=word.word_id,
                    source=EventSource.CAPTION,
                    kind="keyword",
                    timeline_at=word.timeline_start,
                    strength=strength,
                )
        for emoji in segment.emojis:
            if emoji.render not in _SHOWN:
                continue
            yield SyncEvent(
                event_id=emoji.emoji_id,
                source=EventSource.CAPTION,
                kind="emoji",
                timeline_at=emoji.timeline_start,
                strength=1.0,
            )


def _edit[R](view: EditView, make: Callable[..., R]) -> R | None:
    """The module's own ``EditRef`` (each module owns one with the same two fields)."""
    if view.edit_plan_id is None or view.plan_version is None:
        return None
    return make(edit_plan_id=view.edit_plan_id, plan_version=view.plan_version)


__all__ = [
    "caption_timeline",
    "graphics_brief",
    "section_text",
    "soundtrack_timeline",
    "sync_events",
    "transition_sequence",
    "vfx_shots",
]
