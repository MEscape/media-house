"""The deterministic script: scenes by function, every number from the fact table, every
unknown a slot. It is what a run produces with no model at all, and what a model's script is
checked against. It states nothing it was not given.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from media_house.modules.production_project.domain.brief import BriefModel, Fact
from media_house.modules.production_project.domain.concepts import StoryConcept
from media_house.modules.production_project.domain.facts import ProductRow
from media_house.modules.production_project.domain.script import Script, parse_script
from media_house.modules.production_project.domain.script_tools import render_script
from media_house.modules.production_project.domain.slots import slot_key
from media_house.modules.production_project.domain.values import ElementKind


@dataclass(frozen=True, slots=True)
class ScaffoldInputs:
    title: str
    products: tuple[ProductRow, ...]
    facts: tuple[Fact, ...]
    brief: BriefModel | None
    concept: StoryConcept | None
    catchphrase: str
    #: ``name`` of the optional blocks that exist (the brief's optional elements).
    spedition_block: bool = True


def scaffold_script(inputs: ScaffoldInputs) -> Script:
    """Scenes named by function, in plain words (never a motto)."""
    return parse_script(render_script(_build(inputs), title=inputs.title))


@dataclass(frozen=True, slots=True)
class SceneSpec:
    scene_id: str
    title: str
    intent: str
    function: str
    graphics: str
    spoken: tuple[str, ...]


def script_from_specs(specs: Sequence[SceneSpec]) -> Script:
    """Scenes (from the scaffold or a model) in the transcript format, read back through the
    one parser so every source gives the same structure."""
    lines: list[str] = []
    for spec in specs:
        lines.extend(
            [
                f"## {spec.scene_id} · ca. 0:00-0:01 · {spec.title}",
                f"Absicht: {spec.intent}",
                f"Funktion: {spec.function}",
            ]
        )
        if spec.graphics:
            lines.append(f"Grafik: {spec.graphics}")
        lines.extend(["", "SPRECHTEXT:", *spec.spoken, ""])
    return parse_script("\n".join(lines))


def _build(i: ScaffoldInputs) -> Script:
    specs: list[SceneSpec] = []

    def scene(
        sid: str, title: str, intent: str, function: str, graphics: str, spoken: Sequence[str]
    ) -> None:
        specs.append(SceneSpec(sid, title, intent, function, graphics, tuple(spoken)))

    concept = i.concept
    known = {fact.key: fact for fact in i.facts}
    online = "order_window" in known
    all_prices = bool(i.products) and all(r.price is not None for r in i.products)

    def fact_or_slot(stem: str, subject: str, unit: str = "") -> str:
        key = f"{stem}_{subject}"
        if key in known:
            return "{{fact:" + key + "}}"
        return "{" + slot_key(stem, subject) + "}" + (f" {unit}" if unit else "")

    sid = _ids()
    scene(
        next(sid),
        "Einstieg",
        "Ein Satz, der sofort neugierig macht.",
        "hook",
        "Titelkarte mit der Zahl der Produkte",
        [
            concept.hook
            if concept
            else "Heute geht es um {{fact:product_count}} Feuerwerksartikel. {AUFHAENGER_HOOK}"
        ],
    )
    scene(
        next(sid),
        "Zusammenhang",
        "Der Einstieg fließt aus deinem Alltag in das Thema.",
        "context",
        "",
        [
            "Ich gehe die Artikel mit dir durch: Was steht auf der Verpackung, "
            "was lässt sich vergleichen und was ist noch offen?",
            "Nicht jeder neue Name bedeutet automatisch einen neuen Effekt. "
            "Dafür müssen Aufbau und Daten zusammenpassen."
            if not all_prices
            else "Wir vergleichen die Artikel innerhalb dieses Angebots. "
            "Nicht nur nach dem Preis: Entscheidend ist, was du am Himmel sehen willst.",
        ],
    )
    scene(
        next(sid),
        "Das Angebot",
        "Was es gibt, wann man bestellen kann und was begrenzt ist.",
        "facts",
        "Quellenkarte: Region, Stand und ungeprüfte Angaben getrennt ausweisen",
        [
            *(["Bestellen kannst du vom {{fact:order_window}}."] if online else []),
            *(
                ["Geliefert wird im Zeitraum {{fact:delivery_window}}."]
                if "delivery_window" in known
                else []
            ),
            *(
                ["Pro Artikel gibt es höchstens {{fact:max_per_artikel}} Stück."]
                if "max_per_artikel" in known
                else []
            ),
            *(
                []
                if all_prices
                else [
                    "Aktuelle Preise liegen im Briefing noch nicht vor. "
                    "Deshalb gibt es hier noch keine Preisrangliste und keine Kaufempfehlung nach Budget.",
                    "Vorjahrespreise sind nur eine Orientierung für den Rückblick. "
                    "Sie sind keine Zusage für diese Saison.",
                ]
            ),
        ]
        or [
            "Die Angaben aus dem Briefing gleichen wir vor der Veröffentlichung mit den Quellen ab."
        ],
    )
    for row in i.products:
        s = row.subject
        scene(
            next(sid),
            row.name,
            f"{row.name} vorstellen: Preis, Aufbau und Effekt.",
            "product",
            f"Packshot {row.name}; Datenkarte mit Quellenstatus. "
            + (f"Preis {{{{fact:price_{s}}}}}. " if row.price is not None else "Preis: offen. ")
            + "Nur vorhandene Werte zeigen; fehlende Angaben nicht ergänzen.",
            [
                f"Schauen wir auf {row.name}."
                + (
                    f" Der Preis: {{{{fact:price_{s}}}}}."
                    if row.price is not None
                    else " Der aktuelle Preis ist noch offen."
                ),
                f"Laut Briefing sind es {fact_or_slot('shots', s, 'Schuss')}. [VERIFY]",
                f"Das angegebene Kaliber liegt bei {fact_or_slot('kaliber', s, 'Millimetern')}. [VERIFY]",
                f"Die Effektmasse: {fact_or_slot('effektmasse', s, 'Gramm')}. [VERIFY]",
                *(
                    [f"Die angegebene Brenndauer: {{{{fact:duration_{s}}}}}. [VERIFY]"]
                    if f"duration_{s}" in known
                    else []
                ),
                f"Der Effekt: {fact_or_slot('effekt', s)}.",
                *(
                    [f"Im Briefing steht dazu: {{{{fact:assessment_{s}}}}}. [VERIFY]"]
                    if f"assessment_{s}" in known
                    else [f"Für die Einordnung von {row.name}: {{{slot_key('einordnung', s)}}}."]
                ),
            ],
        )
    scene(
        next(sid),
        "Preise im Vergleich",
        "Die Produkte untereinander vergleichen, nie mit anderen Anbietern.",
        "comparison",
        "Vergleichstabelle der Produkte",
        [
            "Unter diesen {{fact:product_count}} Artikeln liegt der Preisabstand "
            "bei {{fact:price_spread}}.",
            "Zusammen kosten alle {{fact:product_count}} Artikel {{fact:price_total}}.",
        ]
        if all_prices
        else [
            "Ein fairer Preisvergleich braucht aktuelle Preise. "
            "Sobald sie vorliegen, können wir Kosten pro Schuss und pro Gramm Effektmasse berechnen.",
            "Bis dahin vergleiche ich die belegten Eigenschaften. "
            "Eine niedrige Schusszahl bedeutet nicht automatisch weniger Wirkung.",
        ],
    )
    scene(
        next(sid),
        "Technik im Vergleich",
        "Zeigen, wie die Aufbauten sich unterscheiden.",
        "comparison",
        "Vergleich der Effektmasse pro Schuss",
        [
            f"Pro Schuss steckt in {r.name} etwa "
            f"{{{{fact:mass_per_shot_{r.subject}}}}} Effektmasse."
            for r in i.products
            if f"mass_per_shot_{r.subject}" in known
        ]
        or [
            "Für den Technikvergleich brauchen wir Schusszahl, Kaliber und Effektmasse zusammen. "
            "Fehlen diese Angaben, bleibt die Bewertung offen.",
            "Eine Ähnlichkeit auf dem Karton ist noch kein Beweis für eine Umlabelung. "
            "Dafür müssen die Daten und die Herkunft überprüfbar zusammenpassen.",
        ],
    )
    scene(
        next(sid),
        "So bestellst du" if online else "Was wir noch prüfen",
        "Den Weg zur Bestellung zeigen.",
        "cta",
        "Bildschirmaufnahme der Bestellseite" if online else "Quellenvergleich und offene Punkte",
        [
            "Der Link zur Bestellung steht unten in der Beschreibung.",
            "Bestellen kannst du vom {{fact:order_window}}.",
            "Die Lieferung kommt im Zeitraum {{fact:delivery_window}}.",
        ]
        if online
        else [
            "Das ist der aktuelle Stand, noch keine vollständige Marktübersicht. "
            "Für eine endgültige Einordnung fehlen aktuelle Preise und bestätigte Listungen.",
            "Wenn die Prospekte da sind, vergleichen wir die Regionen und ergänzen die offenen Daten. "
            "Eine fehlende Angabe ist dabei noch kein Beleg, dass ein Artikel gestrichen wurde.",
        ],
    )
    if i.spedition_block:
        scene(
            next(sid),
            "Zur Spedition",
            "Optional: ein positiver Satz zur Lieferung per Spedition.",
            "facts",
            "",
            [
                "[OPTIONAL: spedition]",
                "Die Lieferung per Spedition ist für diese Menge praktisch. {SPEDITION_ERFAHRUNG}",
                "[/OPTIONAL]",
            ],
        )
    scene(
        next(sid),
        "Meine Wahl",
        "Eine ehrliche Auswahl innerhalb des Angebots.",
        "pick",
        "Das gewählte Produkt als Freisteller",
        [
            "Welcher Artikel passt zu dir? {DEIN_PICK}",
            "Meine Auswahl bleibt vorläufig, solange Daten oder aktuelle Preise fehlen.",
        ],
    )
    safety = (
        next((e.text for e in i.brief.mandatory if e.kind is ElementKind.LINE), "")
        if i.brief
        else ""
    )
    scene(
        next(sid),
        "Sicherheit",
        "Den Sicherheitshinweis sagen.",
        "facts",
        "",
        [safety or "{SICHERHEITSHINWEIS}"],
    )
    scene(
        next(sid),
        "Der Bogen zum Anfang",
        "Der Gag vom Anfang kommt zurück.",
        "callback",
        "",
        [
            "Neu auf dem Karton heißt also noch nicht automatisch neu am Himmel. "
            "Entscheidend sind die belegten Daten. Alles andere bleibt bis zur Prüfung offen."
            if concept and concept.hook.startswith("Neu auf dem Karton")
            else "{CALLBACK}"
        ],
    )
    scene(
        next(sid),
        "Abschluss",
        "Eine Frage an die Zuschauer, dann die Catchphrase.",
        "outro",
        "Kommentar-Aufruf",
        [
            "Schreib mir in die Kommentare, welcher Artikel dein Favorit ist.",
            f"Und wie immer: {i.catchphrase}.",
        ],
    )
    scene(
        next(sid),
        "Endscreen",
        "Der Endscreen des Kanals, ohne Sprechtext.",
        "outro",
        "Feuer-Endscreen mit Abonnieren-Aufruf",
        [],
    )
    return script_from_specs(specs)


def _ids() -> Iterator[str]:
    k = 1
    while True:
        yield f"S{k:02d}"
        k += 1
