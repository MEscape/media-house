"""The documents of a project as a small structure (headings, paragraphs, lists, tables).

Kurzkonzept and Rückfragen go to the client, the recording guide to the creator, the compliance
report to whoever signs off. One structure, written as Markdown, Word or PDF by the exporters.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from media_house.modules.production_project.domain.brief import BriefModel, Deadline, Fact
from media_house.modules.production_project.domain.compliance import Finding
from media_house.modules.production_project.domain.concepts import StoryConcept
from media_house.modules.production_project.domain.facts import display
from media_house.modules.production_project.domain.material import AssetRequirement
from media_house.modules.production_project.domain.script import Script
from media_house.modules.production_project.domain.script_tools import (
    SceneLength,
    format_clock,
    heading_title,
)
from media_house.modules.production_project.domain.values import ConstraintKind


class BlockKind(StrEnum):
    H1 = "h1"
    H2 = "h2"
    H3 = "h3"
    PARAGRAPH = "p"
    BULLETS = "bullets"
    TABLE = "table"
    QUOTE = "quote"


@dataclass(frozen=True, slots=True)
class Block:
    kind: BlockKind
    text: str = ""
    items: tuple[str, ...] = ()
    header: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class Document:
    title: str
    blocks: tuple[Block, ...]


def _h2(text: str) -> Block:
    return Block(BlockKind.H2, text)


def _p(text: str) -> Block:
    return Block(BlockKind.PARAGRAPH, text)


def _list(items: Sequence[str]) -> Block:
    return Block(BlockKind.BULLETS, items=tuple(items))


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> Block:
    return Block(BlockKind.TABLE, header=tuple(header), rows=tuple(tuple(r) for r in rows))


# --- the schedule: countdown and reverse plan ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Milestone:
    label: str
    on: str
    #: Days from today (negative = past).
    days: int
    #: ``Briefing`` (a date the client set) or ``Vorschlag`` (planned backwards from it).
    origin: str


def schedule(deadlines: Sequence[Deadline], today: date) -> tuple[Milestone, ...]:
    """The client's dates as a countdown, and a reverse plan from the first one: facts and
    Technik-Felder first, then the shoot, then the cut."""
    out: list[Milestone] = []
    dated = sorted((d for d in deadlines if d.date), key=lambda d: (d.date, d.time))
    for d in dated:
        on = date.fromisoformat(d.date)
        out.append(Milestone(f"{d.label} {d.time}".strip(), d.date, (on - today).days, "Briefing"))
    approvals = [d for d in dated if d.kind == "approval"]
    if approvals:
        first = date.fromisoformat(approvals[0].date)
        for label, back in (
            ("Rohschnitt fertig und abgegeben", 0),
            ("Alles aufgenommen und hochgeladen", 2),
            ("Fakten geprüft und Technik-Felder ausgefüllt", 3),
        ):
            on = first - timedelta(days=back)
            out.append(Milestone(label, on.isoformat(), (on - today).days, "Vorschlag"))
    return tuple(sorted(out, key=lambda m: (m.on, m.origin)))


# --- the documents --------------------------------------------------------------------------------

_HOW_RULE = {
    "label.advertising": (
        "Das Werbung-Label steht das ganze Video über im Bild und in der Beschreibung."
    ),
    "no.negative_about_client": (
        "Alle Aussagen über Produkte und Auftraggeber bleiben positiv oder neutral."
    ),
    "no.competitor_comparison": "Vergleiche gibt es nur zwischen den gezeigten Produkten.",
    "no.third_party_brands": (
        "Fremdmarken werden abgeklebt oder verpixelt, nichts Auffälliges im Bild."
    ),
    "no.illegal_pyro": "Es kommen nur zugelassene Artikel vor.",
    "publish.after_approval": "Veröffentlicht wird erst nach Ihrer schriftlichen Freigabe.",
    "publish.not_before": (
        "Der Veröffentlichungszeitpunkt wird nicht vor dem genannten Termin gesetzt."
    ),
    "confidential.item": "Das vertrauliche Material wird nicht gezeigt, bis es freigegeben ist.",
    "conditional.item": "Das Material wird nur nach Ihrer Freigabe verwendet.",
    "claim.no_superlatives": "Es gibt keine Superlative ohne Beleg.",
}


def kurzkonzept(
    brief: BriefModel,
    concept: StoryConcept | None,
    script: Script | None,
    lengths: Sequence[SceneLength],
    today: date,
) -> Document:
    """The rough one-pager for approval: the questions that matter most come first."""
    blocks: list[Block] = [Block(BlockKind.H1, f"Kurzkonzept: {brief.campaign or 'Kampagne'}")]
    urgent = [q for q in brief.open_questions if q.priority == 1]
    if urgent:
        blocks += [_h2("Vorab zu klären"), _list([q.text for q in urgent])]
    blocks.append(_h2("Idee"))
    if concept:
        blocks += [
            _p(concept.logline),
            _p(f"Einstieg: {concept.hook}"),
            _p(f"Roter Faden: {concept.red_thread}"),
        ]
    else:
        blocks.append(_p("Der Konzeptvorschlag folgt nach Klärung der offenen Punkte."))
    if script:
        seconds = {item.scene_id: item.seconds for item in lengths}
        clock = 0.0
        rows: list[tuple[str, str]] = []
        for scene in script.scenes:
            rows.append((format_clock(clock), heading_title(scene.heading) or scene.scene_id))
            clock += seconds.get(scene.scene_id, 0.0)
        blocks += [_h2("Ablauf"), _table(("Zeit", "Szene"), rows)]
    blocks.append(_h2("So setzen wir Ihre Vorgaben um"))
    blocks.append(
        _table(
            ("Vorgabe", "Art", "Umsetzung", "Quelle"),
            [
                (
                    c.text,
                    c.kind.value,
                    _HOW_RULE.get(c.rule, "Wird vor der Abgabe von Hand geprüft."),
                    f"{c.source.ref}",
                )
                for c in brief.constraints
            ],
        )
    )
    if brief.mandatory:
        blocks += [
            _h2("Pflichtelemente"),
            _list(
                [
                    f"{e.text} ({e.kind.value})"
                    + (" - folgt nach der Freigabe" if e.arrives_later else "")
                    for e in brief.mandatory
                ]
            ),
        ]
    if brief.optional:
        blocks += [_h2("Optional"), _list([f"{o.text}" for o in brief.optional])]
    stand = schedule(brief.deadlines, today)
    if stand:
        blocks += [
            _h2("Zeitplan"),
            _table(
                ("Termin", "Datum", "Tage", "Herkunft"),
                [(m.label, m.on, str(m.days), m.origin) for m in stand],
            ),
        ]
    blocks += [_h2("Fakten (noch nicht gegen die Quelle geprüft)"), _facts_table(brief.facts)]
    return Document("Kurzkonzept", tuple(blocks))


def _facts_table(facts: Sequence[Fact]) -> Block:
    return _table(
        ("Angabe", "Wert", "Quelle", "geprüft"),
        [
            (f.label, display(f), f.source.ref if f.source else "", "ja" if f.verified else "nein")
            for f in facts
            if f.kind != "product"
        ],
    )


def rueckfragen(brief: BriefModel) -> Document:
    blocks: list[Block] = [Block(BlockKind.H1, f"Rückfragen: {brief.campaign or 'Kampagne'}")]
    for priority, title in ((1, "Vor Drehbeginn"), (2, "Vor der Abgabe"), (3, "Kann warten")):
        group = [q for q in brief.open_questions if q.priority == priority]
        if not group:
            continue
        blocks.append(_h2(title))
        blocks.append(
            _table(
                ("Frage", "Warum", "Stelle im Briefing"),
                [(q.text, q.why, q.source.ref if q.source else "") for q in group],
            )
        )
    if len(blocks) == 1:
        blocks.append(_p("Keine offenen Fragen."))
    return Document("Rückfragen", tuple(blocks))


_SHOT = {
    "hook": "Nah, direkt in die Kamera, sofort loslegen.",
    "context": "Wie ein Gespräch: ruhig, aus dem Alltag erzählen.",
    "product": "Produkt in die Kamera halten, dann zeigen, was es macht.",
    "comparison": "Beide Produkte nebeneinander halten.",
    "cta": "Zum Bildschirm deuten, die Aufnahme folgt separat.",
    "pick": "Das gewählte Produkt in die Hand nehmen.",
    "outro": "Zur Kamera, Frage stellen, Catchphrase.",
}


def recording_guide(
    requirements: Sequence[AssetRequirement], script: Script, slug: str
) -> Document:
    """What to record, in which order, with a teleprompter text scene by scene."""
    blocks: list[Block] = [Block(BlockKind.H1, "Aufnahme-Leitfaden")]
    blocks.append(_h2("Reihenfolge"))
    blocks.append(
        _table(
            ("Nr.", "Datei", "Pflicht", "Wann", "Abhängig von"),
            [
                (
                    str(r.order),
                    r.title,
                    r.priority.value,
                    {
                        "anytime": "jederzeit",
                        "before_source_live": "vor Quelle live",
                        "after_source_live": "nach Quelle live",
                    }[r.timing.value],
                    ", ".join(r.depends_on) or "-",
                )
                for r in requirements
            ],
        )
    )
    for r in requirements:
        blocks.append(Block(BlockKind.H3, f"{r.order}. {r.title}"))
        blocks.append(_p(r.instruction))
        blocks.append(_p(f"Dateiname: {r.target_name_pattern}. Erlaubt: {', '.join(r.accepts)}."))
        blocks.append(_list(r.howto))
        if r.forbidden:
            blocks.append(_p("Nicht im Bild oder Ton: " + "; ".join(r.forbidden) + "."))
    blocks.append(_h2("Teleprompter"))
    for scene in script.scenes:
        spoken = [s.text for s in scene.sentences if s.usable]
        if not spoken:
            continue
        blocks.append(Block(BlockKind.H3, f"{scene.scene_id} · {heading_title(scene.heading)}"))
        if scene.function in _SHOT:
            blocks.append(_p(f"Aufnahme: {_SHOT[scene.function]}"))
        blocks.append(_p(f"Sag zuerst laut: „Szene {scene.scene_id[1:].lstrip('0') or '0'}“."))
        blocks.append(_p(" ".join(spoken)))
    blocks.append(_h2("Checkliste vor dem Dreh"))
    blocks.append(
        _list(
            [
                "Zuerst fünf Sekunden Raumton aufnehmen.",
                "Keine Logos auf der Kleidung, Marken am Set abkleben.",
                "Technik-Felder ausgefüllt und Zahlen geprüft.",
                "Dateien benennen wir für dich: lade sie einfach hoch.",
            ]
        )
    )
    return Document(f"Aufnahme-Leitfaden {slug}", tuple(blocks))


def compliance_report(findings: Sequence[Finding]) -> Document:
    blocks: list[Block] = [Block(BlockKind.H1, "Compliance-Bericht")]
    open_blockers = [f for f in findings if f.blocking]
    blocks.append(
        _p(
            f"{len(open_blockers)} blockierende Punkte offen."
            if open_blockers
            else "Keine blockierenden Punkte offen."
        )
    )
    for stage in ("script", "asset", "timeline", "metadata"):
        group = [f for f in findings if f.stage.value == stage]
        if not group:
            continue
        blocks.append(
            _h2(
                {
                    "script": "Skript",
                    "asset": "Dateien",
                    "timeline": "Schnitt",
                    "metadata": "Metadaten",
                }[stage]
            )
        )
        blocks.append(
            _table(
                ("Schwere", "Wo", "Befund", "Entscheidung"),
                [
                    (
                        f.severity.value,
                        f.where,
                        f.message,
                        f"bewusst behalten: {f.overridden}"
                        if f.overridden
                        else (f.safe_alternative or "-"),
                    )
                    for f in group
                ],
            )
        )
    return Document("Compliance-Bericht", tuple(blocks))


def to_markdown(doc: Document) -> str:
    lines: list[str] = []
    for b in doc.blocks:
        match b.kind:
            case BlockKind.H1:
                lines += [f"# {b.text}", ""]
            case BlockKind.H2:
                lines += [f"## {b.text}", ""]
            case BlockKind.H3:
                lines += [f"### {b.text}", ""]
            case BlockKind.PARAGRAPH:
                lines += [b.text, ""]
            case BlockKind.QUOTE:
                lines += [f"> {b.text}", ""]
            case BlockKind.BULLETS:
                lines += [*(f"- {i}" for i in b.items), ""]
            case BlockKind.TABLE:
                lines += [
                    "| " + " | ".join(b.header) + " |",
                    "| " + " | ".join("---" for _ in b.header) + " |",
                    *("| " + " | ".join(c.replace("|", "/") for c in row) + " |" for row in b.rows),
                    "",
                ]
    return "\n".join(lines)


def counts(brief: BriefModel) -> dict[str, int]:
    return {k.value: len(brief.constraints_of(k)) for k in ConstraintKind}
