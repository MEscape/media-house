"""Choosing the take: retakes, false starts, broken-off sentences, contradicted facts, order."""

from media_house.modules.production_project.domain.conform import (
    TranscriptLine,
    conform,
    contradiction,
    retakes,
    sentence_lines,
)
from media_house.modules.production_project.domain.script import parse_script


def lines(*texts: str, asset: str = "a") -> list[TranscriptLine]:
    return [TranscriptLine(asset, i * 5.0, i * 5.0 + 3.0, t) for i, t in enumerate(texts)]


def rejected(found: list[TranscriptLine]) -> list[str]:
    return [t.line.text for t in retakes(found)]


def test_a_sentence_said_again_rejects_the_first_attempt_and_what_followed_it() -> None:
    found = lines(
        "Die Versandkosten fallen pro Paket an.",
        "Wenn du zwei bestellst, dann nicht.",
        "Die Versandkosten fallen pro Paket an.",
        "Bestellst du mehrere, zahlst du nur einmal.",
    )

    assert rejected(found) == [
        "Die Versandkosten fallen pro Paket an.",
        "Wenn du zwei bestellst, dann nicht.",
    ]


def test_a_reworded_question_is_a_retake_and_filler_alone_is_not() -> None:
    assert rejected(lines("Und welche nehme ich?", "Und welche würde ich jetzt nehmen?")) == [
        "Und welche nehme ich?"
    ]
    assert (
        rejected(
            lines("Das Paket ist auf jeden Fall schwer.", "Der Link ist auf jeden Fall unten.")
        )
        == []
    )


def test_a_short_false_start_and_a_broken_off_sentence_with_its_restart_are_rejected() -> None:
    assert rejected(lines("Drei.", "Drei neue Lampen im Test.")) == ["Drei."]
    assert rejected(
        lines("Im Vergleich zur ersten würde ich so mal hin...", "Würde ich so mal zeigen.")
    ) == ["Im Vergleich zur ersten würde ich so mal hin...", "Würde ich so mal zeigen."]
    # A dramatic pause before the figure is not a broken-off sentence.
    assert rejected(lines("Zweimal davon, landest du bei...", "59,98.")) == []


def test_only_a_different_month_contradicts_the_script() -> None:
    assert contradiction("Du hast bis zum 22. Januar Zeit.", "Du hast bis zum 22. November Zeit.")
    assert not contradiction("Bis zum 22. November.", "Du hast bis zum 22. November Zeit.")
    assert not contradiction("Das sind 30 Meter.", "Die Höhe ist {HOEHE} Meter, also 40.")


def test_an_ordinal_does_not_end_a_sentence() -> None:
    words = ["Bis", "zum", "22.", "Januar.", "Von", "den", "4.", "Wenn", "wir"]

    found = sentence_lines("a", [(w, float(i), i + 0.5) for i, w in enumerate(words)])

    assert [x.text for x in found] == ["Bis zum 22. Januar.", "Von den 4.", "Wenn wir"]
    script = parse_script("## S01 · Test\n\nSPRECHTEXT:\nVom 9. bis zum 22. November. Dann los.\n")
    assert [s.text for s in script.sentences] == ["Vom 9. bis zum 22. November.", "Dann los."]


def test_a_recording_is_placed_by_its_clear_matches_not_by_a_shared_word() -> None:
    script = parse_script(
        "## S01 · Eins\n\nSPRECHTEXT:\nDie Lampe leuchtet hell und warm im Wohnzimmer.\n\n"
        "## S02 · Zwei\n\nSPRECHTEXT:\nDas Kabel ist drei Meter lang und sehr robust.\n\n"
        "## S03 · Drei\n\nSPRECHTEXT:\nDer Versand kostet extra und dauert zwei Tage.\n"
        "Die Lieferung kommt bequem direkt nach Hause.\n"
    )
    late = [
        TranscriptLine("late", 0, 2, "Die Lampe ist hell."),
        TranscriptLine("late", 3, 5, "Der Versand kostet extra und dauert zwei Tage."),
        TranscriptLine("late", 6, 8, "Die Lieferung kommt bequem direkt nach Hause."),
    ]
    middle = [TranscriptLine("middle", 0, 2, "Das Kabel ist drei Meter lang und sehr robust.")]

    assert conform(script, [*late, *middle], ["late", "middle"]).order == ("middle", "late")
