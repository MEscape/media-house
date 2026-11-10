"""Script parsing and relating recordings to it. Synthetic fixtures only (no client material)."""

from media_house.modules.production_project.application.contracts import (
    Exclusion,
    TranscriptLine,
    conform,
    parse_script,
)

SCRIPT = """# Transcript: Gadget week

## Lese-Hinweise
- `[OPTIONAL]` kann entfallen.

## S01 · ca. 0:00-0:08 · Cold open

Absicht: Fast start with the kettle gag.

SPRECHTEXT:
[Kettle whistles, quick cuts]
Sechs Uhr morgens. Jeden Tag derselbe Kampf mit der Kaffeemaschine.
[Record scratch]
Heute? Gibt es eine Lösung.

## S02 · ca. 0:08-0:30 · Die Zahlen

SPRECHTEXT:
Der Toaster Blitz kostet 39,99 Euro und röstet vier Scheiben gleichzeitig.
Die Heizleistung liegt bei {LEISTUNG_WATT} Watt. {KRUEMEL_FACH} Das Kabel ist einen Meter lang.
[NUR_MIT_HERSTELLER_FREIGABE]
Der Hersteller garantiert die längste Lebensdauer am ganzen Markt.
[/NUR_MIT_HERSTELLER_FREIGABE]
Und welche nehme ich? [PLATZHALTER: DEIN PICK] Den Blitz für die ganze Familie.

## S03 · ca. 0:30-0:40 · Extra

[OPTIONAL]
SPRECHTEXT:
Kleiner Spartrick zum Schluss: zwei Geräte bestellen spart die Versandkosten.
[/OPTIONAL]

---

## Fakten-Tabelle
| a | b |
"""


def test_scenes_sentences_directions_and_exclusions() -> None:
    script = parse_script(SCRIPT)

    assert [s.scene_id for s in script.scenes] == ["S01", "S02", "S03"]
    first = script.scenes[0]
    assert first.intent == "Fast start with the kettle gag."
    assert [s.text for s in first.sentences] == [
        "Sechs Uhr morgens.",
        "Jeden Tag derselbe Kampf mit der Kaffeemaschine.",
        "Heute?",
        "Gibt es eine Lösung.",
    ]  # directions in brackets are never spoken text
    by_text = {s.text: s for s in script.scenes[1].sentences}
    assert by_text["Die Heizleistung liegt bei {LEISTUNG_WATT} Watt."].exclusion is (
        Exclusion.UNFILLED_SLOT
    )
    # a slot standing for a whole sentence goes alone; the sentence after it stays usable
    assert by_text["{KRUEMEL_FACH}"].exclusion is Exclusion.UNFILLED_SLOT
    assert by_text["Das Kabel ist einen Meter lang."].usable
    gated = by_text["Der Hersteller garantiert die längste Lebensdauer am ganzen Markt."]
    assert gated.exclusion is Exclusion.APPROVAL_REQUIRED and not gated.usable
    assert "Den Blitz für die ganze Familie." in by_text  # placeholder removed, sentence kept
    assert all(s.optional for s in script.scenes[2].sentences)
    assert not any(s.optional for s in script.scenes[1].sentences)


def test_recordings_are_ordered_by_the_scene_they_speak_and_excluded_speech_is_found() -> None:
    script = parse_script(SCRIPT)
    lines = [
        TranscriptLine("b", 0.0, 4.0, "Der Toaster Blitz kostet 39,99 Euro, röstet vier Scheiben."),
        TranscriptLine(
            "b", 4.5, 8.0, "Der Hersteller garantiert die längste Lebensdauer am Markt."
        ),
        TranscriptLine("a", 0.0, 3.0, "Sechs Uhr morgens, jeden Tag der Kampf mit der Maschine."),
        TranscriptLine("c", 0.0, 2.0, "Haha, okay, nochmal."),
        TranscriptLine("b", 9.0, 10.0, "Lebensdauer Markt"),  # weak: never removes speech
        TranscriptLine("b", 10.5, 12.0, "Das Krümel Fach ist herausnehmbar."),  # slot was filled
        TranscriptLine("b", 12.5, 14.0, "Das Kabel ist einen Meter lang."),
    ]

    result = conform(script, lines, ["c", "b", "a"])

    assert result.order == ("a", "b", "c")
    assert result.unplaced == ("c",)
    assert [(m.line.asset_id, m.line.start) for m in result.excluded] == [("b", 4.5)]
    assert result.excluded[0].exclusion is Exclusion.APPROVAL_REQUIRED
    assert result.missing_scenes == ("S03",)
