"""The editor weighs the pacing analysis as evidence; it never takes it as an order."""

from media_house.modules.editing_intelligence.domain.facts import (
    PacingAdvice,
    PacingFacts,
    PausePacing,
)
from media_house.modules.editing_intelligence.domain.profile import EditConfig, TargetDuration
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    DurationMode,
    Executability,
    TrackKind,
)
from tests.support.edit_fakes import S, Sec, decision, edit, story, talk


def facts(
    pauses: tuple[PausePacing, ...] = (), advice: tuple[PacingAdvice, ...] = ()
) -> PacingFacts:
    return PacingFacts(
        analysis_id="pa_test",
        profile="generic",
        strategy="balanced",
        pauses=pauses,
        advice=advice,
        identity="pacing-1",
    )


def pause(start: float, end: float, verdict: str, keep: float | None) -> PausePacing:
    return PausePacing(
        asset_id="a1",
        start=start,
        end=end,
        verdict=verdict,
        keep=keep,
        reasons=("dead_air",) if verdict == "dead_air" else ("pause_before_emphasis",),
        confidence=0.9,
    )


def advice(kind: str, **kw: object) -> PacingAdvice:
    base: dict[str, object] = {
        "recommendation_id": f"rc:{kind}",
        "kind": kind,
        "asset_id": "a1",
        "section_id": None,
        "beat_id": None,
        "proposal": "do it",
        "delta_seconds": None,
        "duration": None,
        "confidence": 0.7,
        "priority": 1,
    }
    return PacingAdvice(**{**base, **kw})  # type: ignore[arg-type]


def two_sentences(gap: float):  # type: ignore[no-untyped-def]
    rec = talk([S("the screen is bright and sharp"), S("it also gets very hot", gap=gap)])
    return rec, story(rec, [Sec("all", (0, 1), "essential")])


def trimmed(plan) -> bool:  # type: ignore[no-untyped-def]
    return bool(decision(plan, "dec:0:u001").trims)


# --- silences ---------------------------------------------------------------------------------
def test_a_silence_the_pacing_analysis_calls_dead_air_is_tightened_below_the_profile_limit() -> (
    None
):
    rec, st = two_sentences(gap=0.8)  # the generic profile only trims from 0.9 s
    gap_start, gap_end = rec.spans[0][1], rec.spans[1][0]

    without = edit(st, rec)
    with_pacing = edit(st, rec, pacing=facts((pause(gap_start, gap_end, "dead_air", 0.3),)))

    assert not trimmed(without)
    assert trimmed(with_pacing)
    (trim,) = decision(with_pacing, "dec:0:u001").trims
    assert "the pacing analysis calls it dead air" in trim.reason
    assert trim.range.seconds < 0.8 - 0.2  # leaves about the 0.3 s it asked for
    assert len(with_pacing.track(TrackKind.AUDIO)) == 2


def test_a_silence_marked_meaningful_is_kept_and_the_note_says_why() -> None:
    rec, st = two_sentences(gap=2.5)  # the generic profile would trim this
    gap_start, gap_end = rec.spans[0][1], rec.spans[1][0]

    plan = edit(st, rec, pacing=facts((pause(gap_start, gap_end, "meaningful", None),)))

    assert not trimmed(plan) and len(plan.track(TrackKind.AUDIO)) == 1
    notes = [n for n in plan.pacing_notes if n.level == "pacing" and n.subject == "pause"]
    assert notes and "meaningful" in notes[0].detail and "pause_before_emphasis" in notes[0].detail


def test_the_editor_still_applies_its_own_checks_to_what_pacing_suggests() -> None:
    rec = talk([S("so what is the verdict"), S("it is excellent value", gap=0.8)])
    st = story(rec, [Sec("a", (0,), "essential"), Sec("end", (1,), "essential", "payoff")])
    start, end = rec.spans[0][1], rec.spans[1][0]

    plan = edit(st, rec, pacing=facts((pause(start, end, "dead_air", 0.3),)))

    assert not trimmed(plan)  # a pause before a payoff is never trimmed, whatever was suggested


def test_a_silence_not_confirmed_by_the_editor_is_still_only_a_suggestion() -> None:
    rec = talk(
        [S("the screen is bright and sharp"), S("it also gets very hot", gap=0.8)], silence=0.3
    )
    st = story(rec, [Sec("all", (0, 1), "essential")])
    start, end = rec.spans[0][1], rec.spans[1][0]

    plan = edit(st, rec, pacing=facts((pause(start, end, "dead_air", 0.3),)))

    (trim,) = decision(plan, "dec:0:u001").trims
    assert trim.executability is Executability.NEEDS_REVIEW


# --- reveals ----------------------------------------------------------------------------------
def test_a_reveal_the_pacing_analysis_wants_held_is_held_where_the_recording_has_room() -> None:
    rec = talk([S("so here is the surprising result"), S("so yeah okay", gap=3.0)])
    st = story(rec, [Sec("a", (0,), "essential", "payoff"), Sec("b", (1,), "optional")])
    beat = st.beats[0].beat_id
    asked = facts(advice=(advice("hold_reveal", beat_id=beat, duration=1.0, section_id="sec0"),))

    plain = edit(st, rec)
    held = edit(st, rec, pacing=asked)

    first_plain, first_held = plain.track(TrackKind.AUDIO)[0], held.track(TrackKind.AUDIO)[0]
    assert first_held.source.end.value > first_plain.source.end.value + 0.5
    assert [d for d in held.decisions if d.action is Action.EXTEND]
    assert not [d for d in plain.decisions if d.action is Action.EXTEND]


# --- what to shorten first --------------------------------------------------------------------
def test_sections_the_pacing_analysis_wants_tightened_are_proposed_for_cutting_first() -> None:
    rec = talk(
        [
            S("the screen is bright and sharp and very colourful"),
            S("the fingerprint sensor is quick to unlock", gap=0.4, removal=0.2),
            S("the case is available in four different colours", gap=0.4, removal=0.2),
            S("the battery lasts two full days of use", gap=0.4),
        ]
    )
    sections = [
        Sec("screen", (0,), "essential"),
        Sec("sensor", (1,), "optional"),
        Sec("colours", (2,), "optional"),
        Sec("battery", (3,), "essential"),
    ]
    st = story(rec, sections)
    full = edit(st, rec).duration.estimated.value
    config = EditConfig(target=TargetDuration(high=full - 2.0, mode=DurationMode.SOFT))
    prefer_sensor = facts(advice=(advice("reduce_repetition", section_id="sec1"),))

    default = edit(st, rec, config)
    advised = edit(st, rec, config, pacing=prefer_sensor)

    assert decision(default, "dec:0:u002").action is Action.CUT  # later sections go first
    assert decision(default, "dec:0:u001").action is Action.KEEP
    assert decision(advised, "dec:0:u001").action is Action.CUT
    assert decision(advised, "dec:0:u002").action is Action.KEEP
    assert decision(advised, "dec:0:u001").executability is Executability.NEEDS_REVIEW


# --- notes and provenance ---------------------------------------------------------------------
def test_the_advice_is_passed_on_as_ranked_notes_replacing_baseline_section_notes() -> None:
    rec, st = two_sentences(gap=0.3)
    ranked = facts(
        advice=(
            advice("hold_demonstration", priority=2, proposal="hold it", delta_seconds=2.0),
            advice("establish_premise_earlier", priority=1, proposal="say it sooner"),
            advice("tighten_pause", priority=3),
        )
    )

    plan = edit(st, rec, pacing=ranked)

    notes = [n for n in plan.pacing_notes if n.level == "pacing"]
    assert [n.detail.split(":")[0] for n in notes] == [
        "establish premise earlier",
        "hold demonstration",
    ]
    assert notes[1].seconds == 2.0 and "confidence 0.70" in notes[0].detail
    assert not [n for n in plan.pacing_notes if n.level == "section"]
    stage = next(s for s in plan.stages if s.stage == "pacing")
    assert stage.state.value == "ok"


def test_without_a_pacing_analysis_the_baseline_applies_and_the_stage_says_so() -> None:
    rec, st = two_sentences(gap=0.3)

    plan = edit(st, rec)

    stage = next(s for s in plan.stages if s.stage == "pacing")
    assert stage.state.value == "not_available" and "baseline pacing notes" in stage.detail
    assert not [n for n in plan.pacing_notes if n.level == "pacing"]
