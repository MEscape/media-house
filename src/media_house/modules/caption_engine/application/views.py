"""Anti-corruption: upstream contracts -> the caption engine's own facts.

The domain never imports another module; this file is the one place that knows both shapes.
Nothing is recomputed: the transcript, sentences, key items, tracks, crops and planned
graphics are READ; only content_intelligence's pure ``commerce_facts`` is applied to the
transcript text (the same recogniser motion graphics uses, so both agree on a price).
"""

from collections.abc import Sequence
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.audio_intelligence.application.contracts import (
    AudioIntelligenceTimeline,
)
from media_house.modules.caption_engine.domain.facts import (
    BurnedInSpan,
    CoveredSpan,
    Rect,
    Sentence,
    SpokenFact,
    SpokenWord,
    StyleFacts,
    SubjectBox,
    TranscriptFacts,
    ZoneFacts,
)
from media_house.modules.caption_engine.domain.profile import CaptionProfile
from media_house.modules.caption_engine.domain.text import ends_sentence
from media_house.modules.caption_engine.domain.values import FactKind, StyleBinding
from media_house.modules.content_intelligence.application.contracts import (
    CommerceKind,
    ContentAnalysis,
    commerce_facts,
)
from media_house.modules.motion_graphics_engine.application.contracts import (
    GraphicKind,
    GraphicsPlan,
)
from media_house.modules.reframing_intelligence.application.contracts import ReframingAnalysis
from media_house.modules.video_intelligence.application.contracts import (
    EntityKind,
    OverlayKind,
    VideoAnalysis,
)
from media_house.modules.visual_style_engine.application.contracts import (
    ElementKind,
    ResolvedStyle,
    StyleRef,
    ZoneKind,
)

_COMMERCE: dict[CommerceKind, FactKind] = {
    CommerceKind.PRICE: FactKind.PRICE,
    CommerceKind.RANK: FactKind.RANK,
    CommerceKind.SHOTS: FactKind.SPEC,
    CommerceKind.EFFECTS: FactKind.SPEC,
    CommerceKind.HEIGHT: FactKind.SPEC,
    CommerceKind.DURATION: FactKind.SPEC,
    CommerceKind.CALIBER: FactKind.SPEC,
    CommerceKind.WEIGHT: FactKind.SPEC,
    CommerceKind.RETAILER: FactKind.RETAILER,
    CommerceKind.PRODUCT: FactKind.PRODUCT,
}
_COVERING: dict[GraphicKind, FactKind] = {
    GraphicKind.PRICE_TAG: FactKind.PRICE,
    GraphicKind.RANK_CARD: FactKind.RANK,
    GraphicKind.SPEC_CARD: FactKind.SPEC,
}
_SUBJECT_KINDS = (EntityKind.FACE, EntityKind.OBJECT)


def seconds(value: float) -> Fraction:
    """Upstream float seconds -> exact microseconds (never negative)."""
    return Fraction(max(0, round(value * 1_000_000)), 1_000_000)


def transcript_facts(
    timeline: AudioIntelligenceTimeline,
    asset_id: str,
    document_id: str | None,
    content: ContentAnalysis | None,
    language: str,
) -> TranscriptFacts:
    meta = timeline.transcript.metadata
    words = timeline.words
    units = (
        sorted(
            (u for u in content.units if u.span.start is not None and u.span.end is not None),
            key=lambda u: u.span.start or 0.0,
        )
        if content is not None
        else []
    )
    kinds: dict[str, list[str]] = {}
    if content is not None:
        for item in content.key_items:
            for unit_id in item.unit_ids:
                kinds.setdefault(unit_id, []).append(item.kind.value)

    def unit_of(start: float, end: float) -> str | None:
        middle = (start + end) / 2
        for u in units:
            if (u.span.start or 0.0) <= middle <= (u.span.end or 0.0):
                return u.unit_id
        return None

    ids: list[str] = []
    counter, previous_unit = 0, None
    for i, w in enumerate(words):
        unit = unit_of(w.start, w.end) if units else None
        if unit is not None:
            ids.append(f"u:{unit}")
            previous_unit = unit
            continue
        if i and (ends_sentence(words[i - 1].raw_word) or previous_unit is not None):
            counter += 1
        previous_unit = None
        ids.append(f"p:{counter}")
    spoken = tuple(
        SpokenWord(
            asset_id=asset_id,
            index=w.index,
            text=w.raw_word.strip(),
            start=seconds(w.start),
            end=seconds(w.end),
            confidence=w.confidence,
            speaker=w.word.speaker,
            suspect=w.suspect,
            timing=w.word.timing.value,
            vocal=w.emphasis_score,
            sentence_id=ids[i],
            sentence_end=i + 1 == len(words) or ids[i + 1] != ids[i],
        )
        for i, w in enumerate(words)
        if w.raw_word.strip()
    )
    sentence_ids = list(dict.fromkeys(w.sentence_id for w in spoken))
    sentences = tuple(
        Sentence(
            sentence_id=sid,
            asset_id=asset_id,
            key_kinds=tuple(sorted(set(kinds.get(sid.removeprefix("u:"), ())))),
        )
        for sid in sentence_ids
    )
    facts: list[SpokenFact] = []
    for sid in sentence_ids:
        members = [w for w in spoken if w.sentence_id == sid]
        facts += _facts(members, meta.language or language)
    return TranscriptFacts(
        asset_id=asset_id,
        document_id=document_id,
        language=meta.language or None,
        alignment=meta.alignment_method,
        words=spoken,
        sentences=sentences,
        facts=tuple(facts),
    )


def _facts(words: Sequence[SpokenWord], language: str) -> list[SpokenFact]:
    """content_intelligence's commerce facts of one sentence, as word index ranges."""
    text, spans = "", []
    for w in words:
        if text:
            text += " "
        spans.append((len(text), len(text) + len(w.text), w.index))
        text += w.text
    found: list[SpokenFact] = []
    for fact in commerce_facts(text, language):
        covered = [index for a, b, index in spans if a < fact.end and fact.start < b]
        if not covered:
            continue
        rank = (
            int(fact.value) if fact.kind is CommerceKind.RANK and fact.value is not None else None
        )
        found.append(
            SpokenFact(
                asset_id=words[0].asset_id,
                kind=_COMMERCE[fact.kind],
                first=min(covered),
                last=max(covered),
                text=fact.text,
                rank=rank,
            )
        )
    return found


def subject_boxes(
    analysis: VideoAnalysis,
    source_size: tuple[int, int] | None,
    output_size: tuple[int, int],
    framing: ReframingAnalysis | None,
) -> tuple[list[SubjectBox], str | None]:
    """Faces and objects in OUTPUT coordinates (``None`` detail = mapped; else why not)."""
    asset_id = analysis.asset_id
    same = (
        source_size is not None
        and abs(source_size[0] / source_size[1] - output_size[0] / output_size[1]) < 0.01
    )
    if not same and framing is None:
        return [], "the output crops the picture and no reframing analysis is stored"
    boxes: list[SubjectBox] = []
    for track in analysis.tracks:
        if track.kind not in _SUBJECT_KINDS:
            continue
        points = track.points
        for k, point in enumerate(points):
            t = point.time.seconds
            nxt = points[k + 1].time.seconds if k + 1 < len(points) else t + 0.2
            x0, y0, x1, y1 = point.box.x0, point.box.y0, point.box.x1, point.box.y1
            if not same and framing is not None:
                at = seconds(t)
                covering = framing.covering(asset_id, _rational(at), _rational(at + 1))
                crop = covering[0].crop_at(t) if covering else None
                if crop is None:
                    continue
                x0, x1 = (x0 - crop.x) / crop.w, (x1 - crop.x) / crop.w
                y0, y1 = (y0 - crop.y) / crop.h, (y1 - crop.y) / crop.h
            x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            boxes.append(
                SubjectBox(
                    asset_id=asset_id,
                    start=seconds(t),
                    end=seconds(max(nxt, t + 0.04)),
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    label=track.kind.value,
                )
            )
    return boxes, None


def _rational(value: Fraction) -> Rational:
    return Rational(value.numerator, value.denominator)


def full_screen_spans(plan: GraphicsPlan) -> list[tuple[Fraction, Fraction]]:
    """When a planned full-screen card fills the picture (timeline seconds)."""
    out: list[tuple[Fraction, Fraction]] = []
    for g in plan.graphics:
        frames = g.frames
        if g.composition is None or not g.composition.full_frame or frames is None:
            continue
        if frames.timeline_start is None:
            continue
        start = Fraction(frames.timeline_start.numerator, frames.timeline_start.denominator)
        length = Fraction(frames.duration.numerator, frames.duration.denominator)
        out.append((start, start + length))
    return out


def covered_spans(plan: GraphicsPlan) -> list[CoveredSpan]:
    """Facts planned graphics already show (prices, ranks, specs) and when."""
    out: list[CoveredSpan] = []
    for g in plan.graphics:
        kind = _COVERING.get(g.request.kind)
        frames = g.frames
        if kind is None or frames is None or frames.timeline_start is None:
            continue
        start = Fraction(frames.timeline_start.numerator, frames.timeline_start.denominator)
        length = Fraction(frames.duration.numerator, frames.duration.denominator)
        c = g.request.content
        text = c.price_text or c.headline or (str(c.rank) if c.rank is not None else kind.value)
        out.append(
            CoveredSpan(timeline_start=start, timeline_end=start + length, kind=kind, text=text)
        )
    return out


def style_facts(style: ResolvedStyle, ref: StyleRef | None, profile: CaptionProfile) -> StyleFacts:
    captions = style.tokens.captions
    name = captions.default_look if profile.layout.look == "style" else profile.layout.look
    look = captions.line if name == "line" else captions.word_group
    kind = style.type_style(look.role)
    size = style.px(look.size)
    zones = tuple(
        ZoneFacts(
            name=z.name,
            rect=Rect(left=z.left, top=z.top, width=z.width, height=z.height),
            takes_captions=ElementKind.CAPTION in z.accepts,
            keep_out=z.kind is ZoneKind.KEEP_OUT,
        )
        for z in style.zones
    )
    zone = next(z for z in zones if z.name == look.zone)
    motion = style.tokens.motion
    return StyleFacts(
        binding=StyleBinding(
            document_id=ref.document_id if ref is not None else None,
            fingerprint=style.fingerprint,
            identity_version=style.identity_version,
            profile=style.profile,
        ),
        profile=style.profile,
        width=style.output.width,
        height=style.output.height,
        look=name,
        case=look.case,
        size_px=size,
        line_px=round(size * kind.leading),
        stroke_px=style.px(look.stroke_width),
        max_chars=look.max_chars_per_line,
        max_lines=look.max_lines,
        strip_trailing_period=look.strip_trailing_period,
        highlight_hex=style.hex(look.highlight),
        zone=zone,
        zones=zones,
        min_text_px=style.px(style.tokens.typography.min_size),
        entry_pattern=motion.entry_pattern,
        exit_pattern=motion.exit_pattern,
        intensity_ceiling=motion.intensity_ceiling,
        calm=motion.character == "calm",
        exit_seconds=Fraction(motion.exit_max).limit_denominator(1000),
    )


def burned_in_spans(analysis: VideoAnalysis) -> list[BurnedInSpan]:
    """Text the source picture already shows: captions, and lower-third text (which may be a
    caption the analysis could not tell apart; the planner checks it against the speech)."""
    return [
        BurnedInSpan(
            asset_id=analysis.asset_id,
            start=seconds(o.first.seconds),
            end=seconds(o.last.seconds),
            text=o.text,
            caption=o.kind is OverlayKind.BURNED_IN_CAPTION,
        )
        for o in analysis.overlays
        if o.kind in (OverlayKind.BURNED_IN_CAPTION, OverlayKind.LOWER_THIRD)
    ]
