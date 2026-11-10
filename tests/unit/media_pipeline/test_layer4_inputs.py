"""The edit (or the bare recording) as each Layer 4 module's own input: exact times, shared ids."""

from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace
from typing import Any, cast

from media_house.core.domain import Rational
from media_house.modules.audio_intelligence.application.contracts import AudioIntelligenceTimeline
from media_house.modules.editing_intelligence.application.contracts import (
    AudioBridge,
    BridgeKind,
    EditPlan,
    EffectIntent,
    EffectKind,
    EffectRequest,
)
from media_house.modules.media_inspection.application.contracts import InspectionResult
from media_house.modules.media_pipeline.application.edit_view import (
    EditView,
    fr,
    no_speech_view,
    view_of,
)
from media_house.modules.media_pipeline.application.layer4_inputs import (
    caption_timeline,
    graphics_brief,
    on_screen_text,
    soundtrack_timeline,
    sync_events,
    transition_sequence,
    vfx_shots,
)
from media_house.modules.media_pipeline.application.sequence import sequence_of
from media_house.modules.music_and_sound_design.application.contracts import (
    EventSource,
)
from media_house.modules.music_and_sound_design.application.contracts import (
    SyncEvent as Sync,
)
from media_house.modules.transition_engine.application.contracts import (
    TransitionEvent,
    TransitionKind,
    TransitionPlan,
)
from media_house.modules.vfx_engine.application.contracts import RequestKind
from tests.support.asset_dto import make_asset
from tests.support.inspection_fakes import audio, inspect, observed, video
from tests.unit.media_pipeline.test_continuity_task import an_edit

R = Rational


def effect(request_id: str, kind: EffectKind, intent: EffectIntent, at: R) -> EffectRequest:
    return EffectRequest(
        request_id=request_id,
        kind=kind,
        intent=intent,
        at=at,
        duration=None,
        placement_id=None,
        section_id="sec1",
        priority=0.8,
        reason=f"{kind.value} wanted",
        decision_id=None,
    )


def a_plan() -> EditPlan:
    """Two sections back to back (sec0 [0, 53/25), sec1 [53/25, 93/25)) with every request kind
    and one J cut at the boundary."""
    return replace(
        an_edit(),
        effect_requests=(
            effect("e-tr", EffectKind.TRANSITION, EffectIntent.SECTION_CHANGE, R(53, 25)),
            effect("e-gr", EffectKind.GRAPHIC, EffectIntent.SHOW_TEXT, R(3, 1)),
            effect("e-re", EffectKind.REFRAME, EffectIntent.EMPHASIS, R(60, 25)),
            effect("e-mc", EffectKind.MUSIC_CUE, EffectIntent.REVEAL, R(70, 25)),
        ),
        audio_bridges=(
            AudioBridge(
                bridge_id="b1",
                kind=BridgeKind.J_CUT,
                audio_changes_at=R(52, 25),
                picture_changes_at=R(53, 25),
                placement_ids=(),
                reason="the answer starts before its picture",
            ),
        ),
    )


def transcript(*words: tuple[str, float, float]) -> AudioIntelligenceTimeline:
    """Only what the mappers read: words and segments on the source clock, and the language."""

    def between(start: float, end: float) -> tuple[Any, ...]:
        return tuple(
            SimpleNamespace(word=SimpleNamespace(raw_word=text, start=a, end=b))
            for text, a, b in words
            if a < end and b > start
        )

    def segments(start: float, end: float) -> tuple[Any, ...]:
        return tuple(SimpleNamespace(start=a, end=b) for _t, a, b in words if a < end and b > start)

    fake = SimpleNamespace(language="de", words_between=between, segments_between=segments)
    return cast(AudioIntelligenceTimeline, fake)


def no_speech(*, with_video: bool = True) -> EditView:
    media = observed(videos=(video(),) if with_video else (), audios=(audio(),))
    found = no_speech_view("a", InspectionResult(inspect(media), make_asset("insp"), created=True))
    assert found is not None
    return found


def test_the_whole_recording_is_a_whole_number_of_frames() -> None:
    from dataclasses import replace

    media = observed(videos=(video(),), audios=(audio(),))
    media = replace(media, container=replace(media.container, duration=59.304042))
    found = no_speech_view("a", InspectionResult(inspect(media), make_asset("insp"), created=True))

    assert found is not None
    rate = found.frame_rate
    frames = (
        found.duration.numerator * rate.numerator / (found.duration.denominator * rate.denominator)
    )
    assert (
        frames == int(frames)
        and 59.2 < float(found.duration.numerator) / found.duration.denominator <= 59.304042
    )


# --- the shared view -------------------------------------------------------------------------
def test_the_view_keeps_placement_ids_exact_times_and_section_spans() -> None:
    plan = a_plan()

    view = view_of(plan, None)

    assert view.anchor_asset_id == "a1" and view.edit_plan_id == plan.edit_plan_id
    assert [c.clip_id for c in view.picture()] == ["clip:000:v", "clip:001:v"]
    assert [c.clip_id for c in view.sound()] == ["clip:000:a", "clip:001:a"]
    first = view.picture()[0]
    assert (first.source_start, first.source_end, first.timeline_start) == (
        R(1, 25),
        R(54, 25),
        R(0, 1),
    )
    assert [(s.section_id, s.timeline_start, s.timeline_end) for s in view.sections] == [
        ("sec0", R(0, 1), R(53, 25)),
        ("sec1", R(53, 25), R(93, 25)),
    ]
    assert view.duration == R(93, 25) and view.frame_rate == R(25, 1)


def test_the_bare_recording_is_one_clip_timeline_equal_to_source_at_the_inspected_rate() -> None:
    view = no_speech()

    [clip] = view.clips
    assert view.edit_plan_id is None and view.project_id is None and not view.sections
    assert (clip.clip_id, clip.source_start, clip.source_end, clip.timeline_start) == (
        "clip:a",
        R(0, 1),
        R(3, 1),
        R(0, 1),
    )
    assert view.frame_rate == R(30, 1) and not view.frame_rate_assumed
    assert view.sound() == (clip,)  # its own sound is what plays


def test_a_recording_without_a_picture_states_that_its_frame_rate_is_assumed() -> None:
    view = no_speech(with_video=False)

    assert view.frame_rate_assumed and not view.clips[0].picture


def test_every_dto_of_the_bare_recording_names_no_edit_and_one_clip() -> None:
    view = no_speech()

    dtos = (
        graphics_brief(view, width=1080, height=1920),
        caption_timeline(view, width=1080, height=1920),
        transition_sequence(view),
        vfx_shots(view, width=1080, height=1920),
        soundtrack_timeline(view),
    )

    assert all(d.edit is None and d.project_id is None for d in dtos)
    assert caption_timeline(view, width=1080, height=1920).clips[0].audio
    assert [c.clip_id for c in transition_sequence(view).clips] == ["clip:a"]
    assert graphics_brief(view, width=1080, height=1920).timeline_duration == R(3, 1)


# --- one mapper per module ---------------------------------------------------------------------
def test_transitions_see_the_same_clip_ids_as_continuity_plus_requests_and_bridges() -> None:
    plan = a_plan()

    sequence = transition_sequence(view_of(plan, None))

    assert [c.clip_id for c in sequence.clips] == [c.clip_id for c in sequence_of(plan).clips]
    assert sequence.edit is not None and sequence.edit.plan_version == plan.plan_version
    [request] = sequence.requests
    assert (request.request_id, request.timeline_at) == ("e-tr", R(53, 25))
    [bridge] = sequence.bridges
    assert (bridge.kind, bridge.audio_changes_at, bridge.picture_changes_at) == (
        "j_cut",
        R(52, 25),
        R(53, 25),
    )
    assert [s.section_id for s in sequence.sections] == ["sec0", "sec1"]


def test_captions_caption_the_sound_track_once_not_the_picture_and_its_linked_sound() -> None:
    timeline = caption_timeline(view_of(a_plan(), None), width=1080, height=1920)

    heard = {c.clip_id for c in timeline.clips if c.audio}
    shown = {c.clip_id for c in timeline.clips if c.picture}
    assert heard == {"clip:000:a", "clip:001:a"}
    assert shown == {"clip:000:v", "clip:001:v"}
    assert (timeline.width, timeline.height) == (1080, 1920)


def test_vfx_gets_its_own_request_kinds_and_the_speech_it_must_not_retime() -> None:
    words = transcript(("hallo", 0.5, 1.0), ("welt", 3.0, 3.5))

    shots = vfx_shots(view_of(a_plan(), None), width=1920, height=1080, transcript=words)

    assert [(r.request_id, r.kind) for r in shots.requests] == [
        ("e-re", RequestKind.EMPHASIS),
        ("e-mc", RequestKind.REVEAL),
    ]
    first = shots.clips[0]  # source [1/25, 54/25): the first word, clamped to the clip
    assert [(s.source_start, s.source_end) for s in first.speech] == [(R(1, 2), R(1, 1))]


def test_the_graphics_brief_reads_rank_and_price_from_the_words_of_each_section() -> None:
    words = transcript(("Auf", 0.1, 0.2), ("Platz", 0.2, 0.4), ("3", 0.4, 0.5))
    words_and_price = transcript(
        ("Auf", 0.1, 0.2),
        ("Platz", 0.2, 0.4),
        ("3", 0.4, 0.5),
        ("nur", 4.5, 4.7),
        ("15,99", 4.7, 5.0),
        ("€", 5.0, 5.1),
    )

    rank_only = graphics_brief(view_of(a_plan(), None), width=1920, height=1080, transcript=words)
    brief = graphics_brief(
        view_of(a_plan(), None), width=1920, height=1080, transcript=words_and_price
    )

    assert [s.rank for s in rank_only.sections] == [3, None] and not rank_only.products
    [product] = brief.products
    assert product.price_cents == 1599 and product.product_id == "product:sec1"
    assert brief.sections[1].product_id == "product:sec1"
    [graphic] = brief.graphics
    assert (graphic.request_id, graphic.intent.value, graphic.timeline_at) == (
        "e-gr",
        "show_text",
        R(3, 1),
    )
    assert graphic.product_id == "product:sec1"


def test_sound_plays_the_sound_track_and_syncs_to_every_visual_moment() -> None:
    view = view_of(a_plan(), None)
    whip = TransitionEvent(
        transition_id="cut:clip:000:v>clip:001:v",
        kind=TransitionKind.WHIP_PAN,
        timeline_at=R(53, 25),
        intensity=3,
    )
    transitions = cast(TransitionPlan, SimpleNamespace(events=(whip,)))

    events = sync_events(view, transitions=transitions)
    timeline = soundtrack_timeline(view, events)

    assert [c.clip_id for c in timeline.clips] == ["clip:000:a", "clip:001:a"]
    assert events == (
        Sync(
            event_id="cut:clip:000:v>clip:001:v",
            source=EventSource.TRANSITION,
            kind="whip_pan",
            timeline_at=R(53, 25),
            strength=0.75,
        ),
        Sync(
            event_id="e-tr",
            source=EventSource.EDIT,
            kind="section_change",
            timeline_at=R(53, 25),
            strength=0.8,
        ),
        Sync(
            event_id="e-mc",
            source=EventSource.EDIT,
            kind="reveal",
            timeline_at=R(70, 25),
            strength=0.8,
        ),
    )
    assert timeline.events == events and timeline.bridges[0].timeline_audio_at == R(52, 25)
    assert timeline.sections[1].timeline_end == R(93, 25)


def test_every_spoken_price_is_its_own_timed_product_and_the_rank_has_its_time() -> None:
    # E2E (O2/O3): cards opened their section, and a haul's seven prices became one product.
    words = transcript(
        ("Auf", 0.1, 0.2),
        ("Platz", 0.2, 0.4),
        ("3", 0.4, 0.5),
        ("Phantom", 4.3, 4.4),
        ("9", 4.4, 4.5),
        ("nur", 4.5, 4.7),
        ("15,99", 4.7, 5.0),
        ("€", 5.0, 5.1),
        ("und", 5.2, 5.3),
        ("2,49", 5.3, 5.5),
        ("€", 5.5, 5.6),
    )
    view = view_of(a_plan(), None)
    sound = next(c for c in view.sound() if c.section_id == "sec1")
    on_timeline = fr(sound.timeline_start) - fr(sound.source_start)

    brief = graphics_brief(view, width=1920, height=1080, transcript=words)

    first, second = brief.products
    assert (first.product_id, first.price_cents, first.name) == ("product:sec1", 1599, "Phantom 9")
    assert (second.product_id, second.price_cents) == ("product:sec1:2", 249)
    assert fr(first.timeline_at) == Fraction("4.7") + on_timeline  # type: ignore[arg-type]
    assert fr(second.timeline_at) == Fraction("5.3") + on_timeline  # type: ignore[arg-type]
    assert brief.sections[0].rank == 3 and brief.sections[0].rank_at is not None


def test_text_the_picture_shows_includes_tracked_shot_text_not_only_overlays() -> None:
    # E2E regression (A): the source's own "2,29 EUR" mid-picture was read by OCR (a shot text
    # item) but is no overlay kind, so our "2,29 €" tag was drawn over it.
    def at(seconds: float) -> SimpleNamespace:
        return SimpleNamespace(seconds=seconds)

    shown = SimpleNamespace(text="2,29 EUR", first=at(1.0), last=at(1.5))
    shot = SimpleNamespace(text=SimpleNamespace(items=(shown,)))
    analysis = cast(Any, SimpleNamespace(asset_id="a", overlays=(), shots=(shot,)))

    [text] = on_screen_text(no_speech(), analysis)

    assert (text.text, fr(text.timeline_start), fr(text.timeline_end)) == (
        "2,29 EUR",
        Fraction(1),
        Fraction(3, 2),
    )


def test_sound_design_hears_of_the_end_screen_appended_after_the_content() -> None:
    view = no_speech()
    content_end = max(fr(c.timeline_end) for c in view.sound())

    end = content_end + 20
    timeline = soundtrack_timeline(view, (), R(end.numerator, end.denominator))

    assert timeline.appended_until == R(end.numerator, end.denominator)
    assert timeline.timeline_end == content_end + 20 and timeline.content_end == content_end


def test_a_project_hears_each_clip_through_the_transcript_of_its_own_recording() -> None:
    words = transcript(("nur", 4.5, 4.7), ("15,99", 4.7, 5.0), ("€", 5.0, 5.1))
    anchor = view_of(a_plan(), None)
    view = replace(anchor, clips=tuple(replace(c, asset_id="member") for c in anchor.clips))

    only_anchor = graphics_brief(view, width=1920, height=1080, transcript=words)
    project = graphics_brief(view, width=1920, height=1080, transcript={"member": words})
    shots = vfx_shots(view, width=1920, height=1080, transcript={"member": words})

    assert not only_anchor.products  # the anchor's words are not the member's
    assert [p.price_cents for p in project.products] == [1599]
    assert any(c.speech for c in shots.clips)


def test_a_section_that_speaks_a_product_name_gets_its_picture_and_name() -> None:
    words = transcript(
        ("Batterie", 4.0, 4.2), ("eins:", 4.2, 4.4), ("Pure", 4.4, 4.5), ("Impact.", 4.5, 4.6),
        ("nur", 4.6, 4.7), ("15,99", 4.7, 5.0), ("€", 5.0, 5.1),
    )  # fmt: skip
    images = {"Seven Kings": "img-sk", "Pure Impact": "img-pi"}

    brief = graphics_brief(
        view_of(a_plan(), None), width=1920, height=1080, transcript=words, product_images=images
    )

    [product] = brief.products
    assert (product.name, product.image_asset_ids) == ("Pure Impact", ("img-pi",))


def test_a_price_tag_is_a_key_moment_however_calm_its_motion() -> None:
    from media_house.modules.motion_graphics_engine.application.contracts import (
        EventKind,
        GraphicKind,
        GraphicsPlan,
        ItemStatus,
    )

    def item(graphic_id: str, kind: GraphicKind) -> SimpleNamespace:
        return SimpleNamespace(
            graphic_id=graphic_id,
            status=ItemStatus.OK,
            request=SimpleNamespace(kind=kind, content=SimpleNamespace(chart=None)),
            animation=SimpleNamespace(motion=SimpleNamespace(entry="pop_in")),
        )

    def peak(graphic_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            graphic_id=graphic_id, kind=EventKind.ENTER_PEAK, timeline_at=R(1, 1), strength=0.5
        )

    plan = cast(
        GraphicsPlan,
        SimpleNamespace(
            graphics=(item("price", GraphicKind.PRICE_TAG), item("card", GraphicKind.TEXT_PANEL)),
            events=(peak("price"), peak("card")),
        ),
    )

    events = sync_events(view_of(a_plan(), None), graphics=plan)

    found = {e.event_id.split(":")[0]: (e.kind, e.strength) for e in events}

    assert found["price"] == ("price_reveal", 0.8)
    assert found["card"] == ("pop_in", 0.5)


def test_a_name_labels_only_the_price_said_with_it_and_a_count_is_no_product(
    monkeypatch: Any,
) -> None:
    from media_house.modules.media_pipeline.application import layer4_inputs

    monkeypatch.setattr(layer4_inputs, "_NAME_BEFORE", Fraction(1, 2))
    words = transcript(
        ("ab", 4.0, 4.1), ("15,99", 4.1, 4.3), ("€", 4.3, 4.4), ("frei.", 4.4, 4.5),
        ("Und", 4.5, 4.6), ("25", 4.6, 4.7), ("Batterien", 4.7, 4.8),
        ("Pure", 4.9, 5.0), ("Impact.", 5.0, 5.1),
    )  # fmt: skip

    brief = graphics_brief(
        view_of(a_plan(), None),
        width=1920,
        height=1080,
        transcript=words,
        product_images={"Pure Impact": "img-pi"},
    )

    assert [(p.name, p.price_cents) for p in brief.products] == [("", 1599)]
