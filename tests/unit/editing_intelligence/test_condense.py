"""A condensed short tells the whole list: hook, premise, every item, verdict, call to action."""

from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace
from typing import cast

from media_house.modules.editing_intelligence.domain.assemble import Piece
from media_house.modules.editing_intelligence.domain.condense import condensed_cut, hook_lead_in
from media_house.modules.editing_intelligence.domain.facts import (
    AudioFacts,
    ContextFact,
    EventFact,
    PauseFact,
    StoryFacts,
    UnitFact,
    WordFact,
)
from media_house.modules.editing_intelligence.domain.model import Decision, ShortPart
from media_house.modules.editing_intelligence.domain.speech import Extent
from media_house.modules.editing_intelligence.domain.units import EditUnit
from media_house.modules.editing_intelligence.domain.values import Action, Precision


def story(*contexts: tuple[str, str]) -> StoryFacts:
    return StoryFacts(
        story_plan_id="sp",
        plan_version=1,
        stored_as=None,
        target_format="generic",
        asset_ids=("a",),
        sections=(),
        beats=(),
        key_info=(),
        constraints=(),
        contexts=tuple(
            ContextFact(section_id=s, requires_section_id=r, reason="", needed=None)
            for s, r in contexts
        ),
        links=(),
        visuals=(),
        openings=(),
        recommended_opening=None,
        order_changes=(),
        relations=(),
        target_low=None,
        target_high=None,
    )


class Script:
    """Invented sentences, one after the other, 10 s apart on one recording."""

    def __init__(self) -> None:
        self.units: list[EditUnit] = []

    def say(
        self,
        section: str,
        seconds: float,
        text: str,
        *,
        facts: tuple[str, ...] = (),
        function: str | None = None,
        clean: bool = True,
    ) -> str:
        i = len(self.units)
        uid = f"u{i}"
        start = Fraction(i * 10)
        end = start + Fraction(str(seconds))
        self.units.append(
            EditUnit(
                asset_id="a",
                asset_index=0,
                unit=UnitFact(
                    unit_id=uid,
                    segment_id=f"g{i}",
                    start=float(start),
                    end=float(end),
                    time_basis="words",
                    text=text,
                    words=len(text.split()),
                    starts_clean=clean,
                    ends_clean=True,
                    facts=facts,
                ),
                segment=None,
                section_id=section,
                priority="essential",
                function=function,
                beat_id=None,
                extent=Extent(start=start, end=end, precision=Precision.WORD_ALIGNED, words=()),
                order=i,
                source_pos=i,
            )
        )
        return uid

    def cut(self, low: float, high: float, facts: StoryFacts | None = None) -> list[str]:
        return [u.unit.unit_id for u in self.condense(low, high, facts)[0]]

    def condense(
        self, low: float, high: float, facts: StoryFacts | None = None
    ) -> tuple[tuple[EditUnit, ...], tuple[ShortPart, ...]]:
        decisions = {
            u.decision_id: cast(Decision, SimpleNamespace(action=Action.KEEP)) for u in self.units
        }
        return condensed_cut(self.units, decisions, facts or story(), low, high)


def ranking() -> tuple[Script, dict[str, str]]:
    s = Script()
    ids = {
        "hook": s.say("open", 3, "Der Wecker klingelt um vier."),
        "hook_more": s.say("open", 6, "Wieder einmal die lange Schlange vor dem Laden."),
        "greeting": s.say("intro", 2, "Hallo zusammen und willkommen."),
        "premise": s.say("intro", 3, "Heute gibt es drei neue Zelte im Test."),
        "long": s.say("intro", 12, "Bevor wir anfangen erkläre ich sehr lange wie alles abläuft."),
    }
    for n, name in ((3, "Alpha"), (2, "Beta"), (1, "Gamma")):
        sec = f"item{n}"
        ids[f"lead{n}"] = s.say(sec, 2, f"Weiter mit Zelt {name}.")
        ids[f"price{n}"] = s.say(sec, 3, f"Das kostet {n}9,99 Euro.", facts=("price",))
        ids[f"chat{n}"] = s.say(sec, 4, "Das finde ich persönlich ganz nett.")
        ids[f"spec{n}"] = s.say(sec, 3, f"Es wiegt nur {n} Kilo.", facts=("weight",))
        ids[f"ramble{n}"] = s.say(sec, 15, "Eine sehr lange Erklärung.", facts=("weight",))
    ids["question"] = s.say("ask", 1.5, "Und welches nehme ich?")
    ids["thinking"] = s.say("ask", 4, "Für das Wochenende wäre es Alpha.")
    ids["answer"] = s.say("verdict", 2.5, "Und Gamma für die große Tour.", function="payoff")
    ids["deadline"] = s.say("verdict", 3, "Bestellen geht bis Freitag.", function="payoff")
    ids["cta"] = s.say("verdict", 2, "Der Link ist in der Beschreibung.", function="payoff")
    ids["bye"] = s.say("verdict", 2, "Bis zum nächsten Mal.", function="payoff")
    return s, ids


def test_every_item_in_order_between_hook_premise_and_verdict() -> None:
    s, ids = ranking()

    chosen = s.cut(30, 58, story(("verdict", "ask")))

    want = ["hook", "premise"]
    for n in (3, 2, 1):
        want += [f"lead{n}", f"price{n}"]
    want += ["question", "thinking", "answer", "cta"]
    assert chosen == [ids[k] for k in want]


def test_a_tight_range_keeps_the_hook_and_each_items_strongest_phrase() -> None:
    s, ids = ranking()

    chosen = s.cut(5, 13, story(("verdict", "ask")))

    assert chosen == [ids[k] for k in ("hook", "price3", "price2", "price1")]


def test_a_short_range_fills_with_more_facts_of_the_items() -> None:
    s, ids = ranking()

    chosen = s.cut(45, 58, story(("verdict", "ask")))

    assert ids["spec3"] in chosen
    assert not {ids[k] for k in ("long", "chat3", "ramble3", "greeting")} & set(chosen)


def test_ranked_sections_are_the_items_even_apart() -> None:
    s = Script()
    s.say("open", 3, "Achtung, jetzt wird es laut.")
    first = s.say("r3", 3, "Auf Platz drei.", facts=("rank",))
    s.say("talk", 3, "Das kostet 5 Euro.", facts=("price",))
    second = s.say("r2", 3, "Auf Platz zwei.", facts=("rank",))

    chosen = s.cut(5, 20)

    assert first in chosen and second in chosen
    assert len(chosen) == 3


def test_a_section_opening_on_a_caveat_is_no_item() -> None:
    s = Script()
    s.say("open", 3, "Achtung, jetzt wird es laut.")
    rules = s.say("rules", 3, "Aber es gibt Regeln.", facts=("price",), function="caveat")
    a = s.say("a", 3, "Das erste kostet 5 Euro.", facts=("price",))
    b = s.say("b", 3, "Das zweite kostet 6 Euro.", facts=("price",))

    chosen = s.cut(5, 20)

    assert rules not in chosen
    assert a in chosen and b in chosen


def test_no_items_gives_nothing() -> None:
    s = Script()
    s.say("open", 3, "Achtung, jetzt wird es laut.")
    s.say("talk", 3, "Ich erzähle einfach etwas.")

    assert s.cut(5, 20) == []


def test_the_parts_the_short_must_carry_and_those_it_does() -> None:
    s, _ = ranking()

    roomy = s.condense(30, 58, story(("verdict", "ask")))[1]
    tight = s.condense(5, 13, story(("verdict", "ask")))[1]

    names = ["hook", "premise", "item:item3", "item:item2", "item:item1", "verdict", "cta"]
    assert [p.part for p in roomy] == names
    assert all(p.present for p in roomy)
    assert [p.part for p in tight if not p.present] == ["premise", "verdict", "cta"]


def first_piece(word: float) -> Piece:
    s = Script()
    s.say("open", 3, "Der Wecker klingelt um vier.")
    at = Fraction(str(word))
    unit = replace(s.units[0], extent=Extent(at, at + 3, Precision.WORD_ALIGNED, ()))
    return Piece(
        asset_id="a",
        start=at - Fraction(1, 10),
        end=at + 3,
        start_precision=Precision.WORD_ALIGNED,
        end_precision=Precision.WORD_ALIGNED,
        cut_in=None,
        cut_out=None,
        units=(unit,),
    )


def audio(
    events: tuple[EventFact, ...] = (),
    pauses: tuple[PauseFact, ...] = (),
    words: tuple[WordFact, ...] = (),
) -> AudioFacts:
    return AudioFacts(
        asset_id="a",
        duration=60.0,
        alignment_method="forced_alignment",
        words=words,
        pauses=pauses,
        events=events,
        identity="x",
    )


def test_the_hook_opens_on_the_sound_before_its_first_word() -> None:
    piece = first_piece(6.0)
    alarm = EventFact(kind="impulse", start=3.5, end=4.0, strength=0.9)

    assert hook_lead_in(piece, audio(events=(alarm,))).start == Fraction(7, 2)
    # at most 4 s, and never back over earlier speech
    early = EventFact(kind="music", start=0.5, end=5.0, strength=0.5)
    assert hook_lead_in(piece, audio(events=(early,))).start == 2
    said = WordFact(index=0, start=2.5, end=3.0, text="hallo")
    assert hook_lead_in(piece, audio(events=(early,), words=(said,))).start == 3


def test_a_silent_head_or_no_audio_analysis_keeps_the_first_word_cut() -> None:
    piece = first_piece(4.0)
    silent = PauseFact(start=0.0, end=4.0, silence_ratio=0.97)
    onset = EventFact(kind="energy_spike", start=3.9, end=4.0, strength=1.0)
    sounding = PauseFact(start=0.0, end=4.0, silence_ratio=0.2)

    assert hook_lead_in(piece, audio(events=(onset,), pauses=(silent,))) == piece
    assert hook_lead_in(piece, None) == piece
    assert hook_lead_in(piece, audio(pauses=(sounding,))).start == 0
