"""Segmentation: atoms (prices, ranks, specs, number + unit), line limits, forced breaks, timing."""

from dataclasses import replace
from fractions import Fraction

from media_house.modules.caption_engine.domain.profile import PROFILES, Segmentation
from media_house.modules.caption_engine.domain.segmentation import Limits, Token, segment, wrap
from media_house.modules.caption_engine.domain.values import Severity
from tests.support.caption_fakes import inputs, plan, transcript


def _lines(text: str, profile: str = "shorts_haul") -> list[tuple[str, ...]]:
    return [s.layout.lines for s in plan(inputs(transcript(text)), profile).segments]


def _flat(lines: list[tuple[str, ...]]) -> list[str]:
    return [" ".join(group) for group in lines]


def test_a_german_price_is_never_split() -> None:
    groups = _flat(_lines("Heute kostet die große Batterie bei Aldi nur 19,99 € im Angebot."))

    assert any("19,99 €" in g for g in groups)
    assert not any(g.endswith("19,99") for g in groups)


def test_a_rank_and_a_spec_stay_whole() -> None:
    groups = _flat(_lines("Auf Platz 3 landet die neue Rakete mit 25 Schuss und 30 Meter."))

    assert any("PLATZ 3" in g for g in groups)
    assert any("25 SCHUSS" in g for g in groups)
    assert any("30 METER" in g for g in groups)


def test_number_and_unit_stay_together_without_a_recognised_fact() -> None:
    groups = _flat(_lines("Der Akku hält ungefähr 45 Minuten durch, sagt der Hersteller."))

    assert any("45 MINUTEN" in g for g in groups)


def test_lines_respect_the_looks_characters_and_lines() -> None:
    text = (
        "Die allermeisten Feuerwerksbatterien aus dem diesjährigen Sortiment sind "
        "überraschend ordentlich verarbeitet worden."
    )
    lines = _lines(text, "generic") + _lines(text)

    assert lines
    assert all(len(line) <= 22 and len(group) <= 2 for group in lines for line in group)


def test_shorts_word_groups_have_at_most_four_words() -> None:
    p = plan(
        inputs(
            transcript("Ich habe heute für sechshundert Euro Feuerwerk gekauft und es ist krass.")
        )
    )

    assert all(len(s.words) <= 4 for s in p.segments)


def test_a_line_never_ends_on_an_article_or_preposition_when_avoidable() -> None:
    p = plan(inputs(transcript("Wir fahren jetzt mit dem Auto zu dem großen Markt in der Stadt.")))

    for s in p.segments:
        for line in s.layout.lines[:-1]:
            assert line.split()[-1].casefold() not in {"dem", "der", "zu", "mit", "in"}


def test_umlauts_and_sharp_s_are_kept_and_capitalised_by_the_german_rule() -> None:
    p = plan(inputs(transcript("Größer und lauter als die Böller, heißt es.")))
    lines = " ".join(line for s in p.segments for line in s.layout.lines)

    assert "GRÖSSER" in lines and "BÖLLER" in lines and "HEISST" in lines
    words = [w.text for s in p.segments for w in s.words]
    assert "Größer" in words and "heißt" in words  # the stored text is what was said


def test_a_pause_starts_a_new_caption() -> None:
    t = transcript("Das ist wirklich gut")
    words = tuple(
        replace(w, start=w.start + 2, end=w.end + 2) if w.index >= 2 else w for w in t.words
    )

    p = plan(inputs(replace(t, words=words)))

    assert [s.text for s in p.segments] == ["Das ist", "wirklich gut"]


def test_a_sentence_end_starts_a_new_caption() -> None:
    p = plan(inputs(transcript("Das ist gut. Wirklich.")))

    assert [s.text for s in p.segments] == ["Das ist gut.", "Wirklich."]


def test_the_trailing_full_stop_is_dropped_but_questions_stay() -> None:
    p = plan(inputs(transcript("Das ist gut. Ist das gut?")))
    shown = [w.display for s in p.segments for w in s.words]

    assert "gut" in shown and "gut?" in shown


def _token(key: int, text: str, start: float, end: float, speaker: str | None = None) -> Token:
    return Token(
        key=key,
        text=text,
        start=Fraction(start).limit_denominator(1000),
        end=Fraction(end).limit_denominator(1000),
        speaker=speaker,
        clip_id="c",
        sentence_id="s",
        sentence_end=False,
        atom=key,
    )


LIMITS = Limits(max_chars=22, max_lines=2, case="upper")


def test_a_speaker_change_breaks_the_caption() -> None:
    tokens = [_token(0, "ja", 0, 0.3, "A"), _token(1, "genau", 0.35, 0.6, "B")]
    groups = segment([tokens], Segmentation(max_words=6), LIMITS).groups

    assert [len(g.tokens) for g in groups] == [1, 1]


def test_reading_speed_and_minimum_duration_extend_into_free_time() -> None:
    tokens = [_token(0, "Feuerwerksbatterie", 0, 0.2)]
    rules = Segmentation(min_duration=1.0, reading_cps=10.0, linger=0.0)
    (group,) = segment([tokens], rules, LIMITS).groups

    assert group.end - group.start == Fraction(18, 10)  # 18 chars / 10 cps


def test_the_last_caption_is_never_held_past_the_end_of_the_timeline() -> None:
    # E2E regression: the final caption of a 30.3 s short ran to 30.636 s (linger + reading time).
    tokens = [_token(0, "dazu?", 29.9, 30.1)]
    rules = Segmentation(min_duration=1.0, reading_cps=10.0, linger=0.5)

    (free,) = segment([tokens], rules, LIMITS).groups
    (clamped,) = segment([tokens], rules, LIMITS, until=Fraction(303, 10)).groups

    assert free.end > Fraction(303, 10)
    assert clamped.end == Fraction(303, 10) and clamped.start < clamped.end


def test_overlapping_speech_trims_the_earlier_caption_or_drops_the_later() -> None:
    a = [_token(0, "erste", 0, 0.5), _token(1, "Stimme", 0.5, 1.5)]
    b = [_token(2, "zweite", 1.0, 1.4, "B")]
    trimmed = segment([a, b], Segmentation(), LIMITS)
    dropped = segment([a, b], Segmentation(overlap="drop_later"), LIMITS)

    assert [t.text for t in trimmed.groups[0].tokens] == ["erste", "Stimme"]
    assert trimmed.groups[0].end == Fraction(1) == trimmed.groups[1].start
    assert [len(g.tokens) for g in dropped.groups] == [2]
    assert dropped.dropped == (2,)


def test_wrap_balances_two_lines() -> None:
    atoms = [
        [_token(i, w, i, i + 0.1)]
        for i, w in enumerate(("DAS", "IST", "EIN", "SEHR", "LANGER", "SATZ"))
    ]

    lines = wrap(atoms, LIMITS)

    assert lines is not None and len(lines) == 2
    assert abs(len(lines[0]) - len(lines[1])) <= 6


def test_profiles_segment_short_and_long_form_differently() -> None:
    assert (
        PROFILES["shorts_haul"].segmentation.max_words < PROFILES["generic"].segmentation.max_words
    )


def test_too_fast_speech_is_reported_not_hidden() -> None:
    fast = transcript("Das ist eine unglaublich schnelle Aussage über Feuerwerk.", word=0.1)
    p = plan(inputs(fast))

    assert any(c.code == "reading.speed" and c.severity is Severity.WARNING for c in p.checks)
