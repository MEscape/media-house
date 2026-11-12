"""The edit view as each Layer 4 module's own input DTO (the modules never see the edit plan).

Every mapper reads the same ``EditView`` (``edit_view.py``), so clip ids (= placement ids), times,
sections and the frame rate agree across the graphics, caption, transition, VFX and sound plans
of one edit. Each table is the owning module's README "EditPlan -> DTO" mapping; facts a module
reads itself from the stored analyses (words, story, pacing) are not re-derived here.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
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
    GraphicOrder,
    GraphicsBrief,
    GraphicsPlan,
    Intent,
    ItemStatus,
    OnScreenText,
    ProductFacts,
    SlotValue,
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
    GraphicKind.URL_CARD: "typing",
}
_ENTRY_EVENTS = {"pop_in": "pop_in", "slide_in": "fly_in", "wipe_in": "fly_in"}
#: A price, a rank or a product reveal is a key moment by what it says, however calm its motion
#: (a graphic's own strength follows its animation intensity: 2 of 4 is 0.5).
_REVEAL_STRENGTH = 0.8
#: A spoken product name labels a figure said after it, at most this far ("Pure Impact. Die
#: kostet 79,99"), or just before it ("für 79,99 die Pure Impact"); never an earlier figure.
_NAME_REACH = Fraction(12)
_NAME_BEFORE = Fraction(2)
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
#: One recording's transcript (the anchor's), or every recording's by asset id (a project).
type Transcripts = AudioIntelligenceTimeline | Mapping[str, AudioIntelligenceTimeline] | None


def _by_asset(view: EditView, transcript: Transcripts) -> Mapping[str, AudioIntelligenceTimeline]:
    if transcript is None:
        return {}
    if isinstance(transcript, Mapping):
        return transcript
    return {view.anchor_asset_id: transcript}


def section_words(view: EditView, transcript: Transcripts) -> dict[str, _Heard]:
    """The words heard in each section with their timeline time (through the sound clips of
    every recording with a transcript)."""
    by_asset = _by_asset(view, transcript)
    heard: dict[str, _Heard] = {}
    for clip in view.sound():
        if clip.section_id is None or (transcript := by_asset.get(clip.asset_id)) is None:
            continue
        offset = fr(clip.timeline_start) - fr(clip.source_start)
        words = transcript.words_between(float(fr(clip.source_start)), float(fr(clip.source_end)))
        heard.setdefault(clip.section_id, []).extend(
            (w.word.raw_word, max(fr(clip.timeline_start), seconds(w.word.start) + offset))
            for w in words
        )
    return heard


def section_text(view: EditView, transcript: Transcripts) -> dict[str, str]:
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


def _named(heard: _Heard, images: Mapping[str, str]) -> tuple[str, str, Fraction] | None:
    """The product name spoken first in the words (all of its words in a row, any case), with
    the time its first word is spoken."""
    starts: list[int] = []
    text = ""
    for word, _ in heard:
        starts.append(len(text))
        text += word.casefold() + " "
    spoken = [
        (m.start(), name, asset_id)
        for name, asset_id in images.items()
        if (
            m := re.search(
                r"\b" + r"\W+".join(map(re.escape, name.casefold().split())) + r"\b", text
            )
        )
    ]
    if not spoken:
        return None
    offset, name, asset_id = min(spoken)
    index = max(i for i, at in enumerate(starts) if at <= offset)
    return name, asset_id, heard[index][1]


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
    transcript: Transcripts = None,
    video: VideoAnalysis | None = None,
    product_images: Mapping[str, str] | None = None,
    wishes: Sequence["GraphicWish"] = (),
    product_cards: Sequence[tuple[str, str, tuple[tuple[str, str], ...]]] = (),
    end_screen: bool | None = None,
) -> GraphicsBrief:
    """Sections with their rank and product facts (content's ``commerce_facts`` of the words
    heard in them, each with the time it is spoken), editorial GRAPHIC requests, the text the
    picture already shows, the delivery size and the timeline length. ``product_images`` (product
    name -> library picture): the first product of a section that speaks a name gets that name
    and picture."""
    language = next((t.language for t in _by_asset(view, transcript).values()), None)
    sections: list[BriefSection] = []
    products: list[ProductFacts] = []
    heard = section_words(view, transcript)
    for s in view.sections:
        timed = _timed_facts(heard.get(s.section_id, []), language)
        found = [
            p for p in _products(s.section_id, timed)
            if p.price_cents is not None or p.specs or p.name
        ]  # fmt: skip
        if product_images:
            # The owner named the products: a counted phrase ("25 Batterien") is none of them.
            found = [p if p.name in product_images else replace(p, name="") for p in found]
        if found and (named := _named(heard.get(s.section_id, []), product_images or {})):
            name, asset_id, said_at = named
            # The name belongs to the price it introduces, never to a figure said well before it.
            near = [
                p for p in found
                if p.timeline_at is None
                or -_NAME_BEFORE <= fr(p.timeline_at) - said_at <= _NAME_REACH
            ]  # fmt: skip
            if near:
                i = found.index(near[0])
                found[i] = replace(found[i], name=name, image_asset_ids=(asset_id,))
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
        end_screen=end_screen,
        orders=(
            *spoken_urls(heard),
            *card_orders(heard, product_cards, products),
            *wished(heard, wishes),
        ),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicWish:
    """A graphic the owner's brief asks for over named speech: fully in (``offset`` s after)
    when the ``occurrence``-th time ``say`` is spoken in the edit."""

    kind: str
    say: str
    slots: tuple[tuple[str, SlotValue], ...] = ()
    variant: str = ""
    occurrence: int = 1
    offset: float = 0.0
    duration: float | None = None
    zone: str = ""
    #: 0 small .. 2 large (``None``: the preset's default).
    emphasis: int | None = None
    #: Why it is asked for (the automatic brief names its rule); "" = the owner's brief.
    reason: str = ""


def _timeline_words(heard: Mapping[str, _Heard]) -> list[tuple[str, Fraction]]:
    return sorted((w for words in heard.values() for w in words), key=lambda w: w[1])


def _bare_word(text: str) -> str:
    return re.sub(r"\W+", "", text.casefold())


def wished(heard: Mapping[str, _Heard], wishes: Sequence[GraphicWish]) -> tuple[GraphicOrder, ...]:
    """Each wish at the time its phrase is spoken (a phrase that is not in the edit is skipped:
    that speech was cut)."""
    words = _timeline_words(heard)
    bare = [_bare_word(w) for w, _ in words]
    out: list[GraphicOrder] = []
    for n, wish in enumerate(wishes):
        phrase = [b for b in (_bare_word(w) for w in wish.say.split()) if b]
        hits = [
            i
            for i in range(len(bare) - len(phrase) + 1)
            if phrase and bare[i : i + len(phrase)] == phrase
        ]
        if len(hits) < wish.occurrence:
            continue
        at = words[hits[wish.occurrence - 1]][1] + Fraction(str(wish.offset))
        out.append(
            GraphicOrder(
                kind=GraphicKind(wish.kind),
                at=rat(max(Fraction(0), at)),
                slots=dict(wish.slots),
                variant=wish.variant,
                emphasis=wish.emphasis,
                duration=None if wish.duration is None else rat(Fraction(str(wish.duration))),
                request_id=f"wish:{n}:{wish.kind}",
                zone=wish.zone,
                priority=0.8,
                reason=wish.reason or f"the brief asks for it over '{wish.say}'",
            )
        )
    return tuple(out)


#: A product's full-screen intro (its name, picture and place) when it is first named, then its
#: data card (the facts of the brief's table) once its price is said.
_INTRO = (GraphicKind.RANK_CARD, "intro")
_INTRO_SECONDS = Fraction(13, 5)
_SHOWCASE_SECONDS = Fraction(5)
#: The facts a data card shows, in this order, under these labels (table headers vary).
_CARD_FACTS = (
    ("schuss", "Schuss"),
    ("effekthöhe", "Höhe"),
    ("höhe", "Höhe"),
    ("brenndauer", "Brenndauer"),
    ("effektmenge", "Effektmenge"),
)
_PRICE = re.compile(r"(\d{1,3}(?:\.\d{3})*),(\d{2})")
_RANK = re.compile(r"^(\d+)\s*/\s*(\d+)$")


def card_orders(
    heard: Mapping[str, _Heard],
    cards: Sequence[tuple[str, str, tuple[tuple[str, str], ...]]],
    products: Sequence[ProductFacts],
) -> tuple[GraphicOrder, ...]:
    """Every product the brief names (name, prepared cut-out, its table facts) gets the same two
    full-screen cards: an intro where it is first named, a data card where its price is said (or
    right after the intro)."""
    words = _timeline_words(heard)
    bare = [_bare_word(w) for w, _ in words]
    out: list[GraphicOrder] = []
    for n, (name, cutout, facts) in enumerate(cards):
        phrase = [b for b in (_bare_word(w) for w in name.split()) if b]
        at = next(
            (
                words[i][1]
                for i in range(len(bare) - len(phrase) + 1)
                if phrase and bare[i : i + len(phrase)] == phrase
            ),
            None,
        )
        if at is None:
            continue  # never named in the edit
        table = {label.casefold(): value for label, value in facts}
        place = next((m for v in table.values() if (m := _RANK.match(v))), None)
        rank = int(place.group(1)) if place else n + 1
        label = f"BATTERIE {place.group(0).replace(' ', '')}" if place else f"NR. {rank}"
        price = next(
            (
                int(m.group(1).replace(".", "")) * 100 + int(m.group(2))
                for key, v in table.items()
                if key.startswith("preis") and (m := _PRICE.search(v))
            ),
            None,
        )
        spoken = next((p for p in products if p.name == name and p.timeline_at is not None), None)
        if price is None and spoken is not None:
            price = spoken.price_cents
        intro: dict[str, SlotValue] = {
            "rank": rank,
            "label": label,
            "product": name,
            "image": cutout,
        }
        if price is not None:
            intro["price"] = price
        out.append(
            GraphicOrder(
                kind=_INTRO[0],
                at=rat(at),
                slots=intro,
                variant=_INTRO[1],
                duration=rat(_INTRO_SECONDS),
                request_id=f"card:{n}:intro",
                priority=0.9,
                reason=f"{name} is introduced",
            )
        )
        rows: list[tuple[str, str]] = []
        for key, fact in _CARD_FACTS:
            value = table.get(key, "").replace("ca.", "").strip().strip('"„“')
            if value and len(value) <= 12 and all(fact != have for have, _ in rows):
                rows.append((fact, value))
        if len(rows) < 2:
            continue
        show = at + _INTRO_SECONDS + Fraction(4, 5)
        if spoken is not None and spoken.timeline_at is not None:
            show = max(show, fr(spoken.timeline_at))
        slots: dict[str, SlotValue] = {
            "product": name,
            "label": label,
            "facts": tuple(rows[:4]),
            "image": cutout,
        }
        if price is not None:
            slots["price"] = price
        out.append(
            GraphicOrder(
                kind=GraphicKind.SPEC_CARD,
                at=rat(show),
                slots=slots,
                variant="showcase",
                duration=rat(_SHOWCASE_SECONDS),
                request_id=f"card:{n}:data",
                priority=0.9,
                reason=f"the facts of {name}",
            )
        )
    return tuple(out)


_DOMAIN = re.compile(r"^[a-z0-9-]+\.(de|com|net|org|eu|at|ch)$")
_JOINERS = {"slash": "/", "schrägstrich": "/", "minus": "-", "bindestrich": "-", "punkt": "."}


def spoken_urls(heard: Mapping[str, _Heard]) -> tuple[GraphicOrder, ...]:
    """An address read out ("lidl.de slash feuerwerk minus vorbestellung") is typed in on a
    URL card while it is said."""
    words = _timeline_words(heard)
    out: list[GraphicOrder] = []
    i = 0
    while i < len(words):
        token = words[i][0].casefold().strip(".,!?;:")
        if not _DOMAIN.match(token):
            i += 1
            continue
        url, j = token, i + 1
        if "/" in words[i][0]:
            url = words[i][0].casefold().strip(".,!?;:")
        while j + 1 < len(words) and words[j][0].casefold().strip(".,!?;:") in _JOINERS:
            url += _JOINERS[words[j][0].casefold().strip(".,!?;:")]
            url += words[j + 1][0].casefold().strip(".,!?;:")
            j += 2
        if url != token or "/" in url:
            out.append(
                GraphicOrder(
                    kind=GraphicKind.URL_CARD,
                    at=rat(words[i][1]),
                    slots={"url": url},
                    variant="typing",
                    emphasis=2,
                    request_id=f"url:{len(out)}",
                    priority=0.9,
                    reason=f"the address {url} is read out",
                )
            )
        i = j
    return tuple(out)


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
    transcript: Transcripts = None,
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
                speech=_speech(c, _by_asset(view, transcript).get(c.asset_id)),
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


def _speech(clip: ViewClip, transcript: AudioIntelligenceTimeline | None) -> tuple[SourceSpan, ...]:
    """Transcript segments (of the clip's own recording) inside its source range (clamped)."""
    if transcript is None:
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
                if request.content.variant in ("showcase", "gallery"):
                    kind = "showcase"  # a full-screen card: a riser leads into it
        if kind:
            reveal = event.kind is EventKind.ENTER_PEAK and (
                kind in _GRAPHIC_REVEALS.values() or kind == "showcase"
            )
            yield SyncEvent(
                event_id=f"{event.graphic_id}:{event.kind.value}:{index}",
                source=EventSource.GRAPHICS,
                kind=kind,
                timeline_at=event.timeline_at,
                # a graphic's own weight counts too: an ordered card is a moment worth a sound
                strength=max(event.strength, _REVEAL_STRENGTH)
                if reveal
                else max(event.strength, item.request.priority)
                if event.kind is EventKind.ENTER_PEAK
                else event.strength,
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
