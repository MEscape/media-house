"""Brief text in, Brief Model out: a deterministic reading with a source quote for every item.

This is the baseline every run has, with no model at all. A model may add items on top of it
(``accept_grounded``): only an item whose quote really stands in the brief is kept, so a model can
never invent a requirement. The same text always gives the same model.
"""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from media_house.modules.production_project.domain.brief import (
    BriefModel,
    Constraint,
    Deadline,
    Fact,
    MandatoryElement,
    OpenQuestion,
    OptionalElement,
    Source,
)
from media_house.modules.production_project.domain.numbers import fold, parse_number, slugify
from media_house.modules.production_project.domain.values import ConstraintKind, ElementKind

_PAGE_BREAK = "\f"
_BULLET = re.compile(r"^\s*(?:[-*•●▪]|\d{1,2}[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ„\"(])")
_PRICE = re.compile(r"(\d{1,4}(?:[.,]\d{2}))\s*(?:€|EUR\b|Euro\b)", re.IGNORECASE)
_DATE = re.compile(r"(?<!\d)(\d{1,2})\.\s?(\d{1,2})\.(?:\s?(\d{4}))?")
_RANGE = re.compile(
    r"(?<!\d)(\d{1,2})\.(?:(\d{1,2})\.)?\s?[–—-]\s?(\d{1,2})\.(\d{1,2})\."  # noqa: RUF001
    r"(?:(\d{4}))?"
)
_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)|(?<!\d)(\d{1,2})\s?Uhr")
_URL = re.compile(r"(?:https?://|www\.)[^\s,;)]+")
_HASHTAG = re.compile(r"#[\wÄÖÜäöüß]{2,}")
_HANDLE = re.compile(r"(?<![\w.])@[\w.]{2,}")
_COUNT = re.compile(
    r"(?:max\.|maximal|höchstens|bis zu)\s*(\d{1,3})\s*(?:Stück|Stk\.?|Exemplare|Packungen)?"
    r"\s*(?:pro|je)\s*(Artikel|Kunde|Person|Bestellung|Haushalt)",
    re.IGNORECASE,
)
_COUNT_ITEM = re.compile(
    r"(?:pro|je)\s+(Artikel|Kunde|Person|Bestellung|Haushalt)\b.{0,30}?"
    r"(?:max\.|maximal|höchstens|bis zu)\s*(\d{1,3})",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"[„\"“«]([^“\"»]{8,})[“\"»]")

_HARD = re.compile(
    r"\b(muss|müssen|darf nicht|dürfen nicht|darf kein|dürfen keine|verboten|zwingend|"
    r"verpflichtend|ausnahmslos|keinesfalls|unter keinen umständen|ist untersagt|pflicht)\b",
    re.IGNORECASE,
)
_SOFT = re.compile(
    r"\b(sollte|sollten|wünscht|wünschen|bitte|möglichst|idealerweise|gerne|empfohlen|empfehlen)\b",
    re.IGNORECASE,
)
_CONDITIONAL = re.compile(
    r"(nach (?:erfolgter |vorheriger |schriftlicher )?"
    r"(?:freigabe|rücksprache|absprache|bestätigung)"
    r"|vorab abstimmen|sofern freigegeben|nur mit (?:schriftlicher )?(?:freigabe|genehmigung)"
    r"|nur bei bestätigter lage|nur möglich, wenn|voraussetzung:)",
    re.IGNORECASE,
)
_CONFIDENTIAL = re.compile(
    r"\b(vertraulich\w*|geheim\w*|nda|sperrfrist|embargo|unter verschluss|"
    r"nicht (?:gezeigt|zeigen|veröffentlicht|veröffentlichen|geteilt|weitergegeben|weitergeben)"
    r" werden)\b",
    re.IGNORECASE,
)
_OPTIONAL = re.compile(
    r"(nicht verpflichtend|kein muss|keine pflicht|optional|freiwillig|"
    r"kann (?:erwähnt|genannt|gezeigt)|wenn (?:erwähnt|genannt|gezeigt))",
    re.IGNORECASE,
)
_POSITIVE = re.compile(r"(positiv|wohlwollend|freundlich)", re.IGNORECASE)
_OPEN_MARK = re.compile(
    r"\b(tbd|noch offen|wird nachgereicht|folgt (?:noch|später)|offen)\b", re.IGNORECASE
)

# (rule key, pattern, the kind it forces or None = decided by the modal words)
_RULES: tuple[tuple[str, re.Pattern[str], ConstraintKind | None], ...] = (
    (
        "label.advertising",
        re.compile(
            r"(?:(?:werbung|anzeige|werbekennzeichnung|sponsored)\b.{0,80}"
            r"(?:kennzeichn|kenntlich|einblend|sichtbar|durchgehend|gesamte)"
            r"|(?:kennzeichn|kenntlich)\w*.{0,80}(?:werbung|anzeige))",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "no.negative_about_client",
        re.compile(
            r"(?:keine negativen (?:aussagen|äußerungen)|nichts negatives|"
            r"negative\w* .{0,40}(?:über|zu|gegenüber)\b)",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "no.competitor_comparison",
        re.compile(
            r"(?:vergleich\w*|gegenüberstell\w*|bezug)\b.{0,60}"
            r"(?:anderen? (?:anbieter|händler|discounter|marken)|wettbewerb\w*|konkurrenz|"
            r"mitbewerb\w*)"
            r"|keine vergleiche mit",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "no.third_party_brands",
        re.compile(
            r"(?:fremdmarken|drittmarken|marken (?:dritter|anderer)|fremde marken|"
            r"(?:keine|nicht)\b.{0,40}(?:auffällig|erkennbar|sichtbar)\w*.{0,30}marken)",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "no.illegal_pyro",
        re.compile(
            r"(?:illegal\w*|nicht zugelassen\w*|verbotene?\w*|kategorie\s*f?\s*[34]\b|"
            r"ohne (?:ce|bam)\b|zulassung)",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "publish.after_approval",
        re.compile(
            r"(?:(?:veröffentlich\w*|upload\w*|online\w*|live\w*)\b.{0,60}"
            r"(?:freigabe|abnahme|zustimmung)|(?:freigabe|abnahme)\b.{0,60}"
            r"(?:veröffentlich|upload|online))",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "claim.no_superlatives",
        re.compile(
            r"(?:keine?\w* (?:unbelegten? )?(?:superlativ\w*|bestwerte|alleinstellungs\w*))",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "facts.prices_pending",
        re.compile(
            r"(?:alle )?preise (?:sind )?(?:offen|fehlen)|keine preisversprechen|"
            r"preise.{0,35}(?:nachgetragen|nachtragen)",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "assets.permission_required",
        re.compile(
            r"ohne (?:rückfrage|antwort|zustimmung|genehmigung).{0,70}(?:nicht|dürfen)|"
            r"nicht ohne (?:rückfrage|zustimmung|genehmigung)|"
            r"(?:nur mit|vorab).{0,35}(?:genehmigung|zustimmung|dreherlaubnis)|"
            r"dreherlaubnis.{0,40}einholen|bildrechte anfragen|quellenlog.{0,60}festhalten",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "safety.local_rules",
        re.compile(r"sicherheit und lokale regeln|nur im freien.{0,30}abstand", re.IGNORECASE),
        ConstraintKind.HARD,
    ),
    (
        "facts.no_leak",
        re.compile(r"\bkein(?:e[ns]?)? leak\b", re.IGNORECASE),
        ConstraintKind.HARD,
    ),
    (
        "facts.identity_unconfirmed",
        re.compile(
            r"keine bestätigte umlabelung|"
            r"(?:umlabelung|herkunft).{0,80}(?:offen|unbelegt|nicht belegt|prüfen|klären)|"
            r"(?:vermutlich|wahrscheinlich).{0,65}(?:altartikel|umetikettiert|dasselbe produkt)",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
    (
        "facts.verification_required",
        re.compile(
            r"vor (?:der )?(?:veröffentlichung|dem dreh).{0,180}(?:prüfen|abgleichen|nachweisen)|"
            r"(?:daten|preise|angaben).{0,90}(?:am|gegen|an der).{0,60}(?:prüfen|abgleichen)|"
            r"urteil erst nach (?:funktions?test|bestätigung)",
            re.IGNORECASE,
        ),
        ConstraintKind.HARD,
    ),
)
_NOT_BEFORE = re.compile(
    r"(?:nicht\s+(?:vor|früher als)|frühestens(?:\s+(?:am|ab|um))?|erst\s+(?:am|ab|nach))\s+"
    r"(?:\w{2,12},?\s+)?(\d{1,2})\.(\d{1,2})\.(\d{4})?",
    re.IGNORECASE,
)
_TRACKING = re.compile(
    r"(?:(?:tracking|affiliate|partner)[- ]?link|link\w*.{0,50}"
    r"(?:folgt|nachgereicht|nach (?:der )?freigabe|später|"
    r"wird (?:noch )?(?:bereitgestellt|geliefert)))",
    re.IGNORECASE,
)
_LABEL_WORD = re.compile(r"\b(Werbung|Anzeige|Sponsored)\b", re.IGNORECASE)
_DEADLINE_WORDS = re.compile(
    r"(abgabe|freigabe|rückmeldung|feedback|deadline|spätestens|termin|entwurf|abnahme|"
    r"veröffentlich\w*|upload|online)",
    re.IGNORECASE,
)
_PUBLISH_WORDS = re.compile(r"(veröffentlich\w*|upload|online|live)", re.IGNORECASE)
_FACT_LABELS: tuple[tuple[str, str, str], ...] = (
    ("bestell", "order_window", "Bestellzeitraum"),
    ("liefer", "delivery_window", "Lieferzeitraum"),
    ("aktion", "campaign_window", "Aktionszeitraum"),
    ("verfügbar", "availability_window", "Verfügbarkeit"),
)
_CLIENT = re.compile(
    r"(?:Kunde|Auftraggeber|Client|Marke|Händler)\s*[:\-]\s*([A-ZÄÖÜ][\w&.\- ]{1,30})"
)
_CAMPAIGN = re.compile(r"(?:Kampagne|Projekt|Thema|Briefing(?: zu)?)\s*[:\-]\s*(.{3,80})")
_TITLE = re.compile(
    r"^([A-ZÄÖÜ][\w&.\-]+)\s+(.{3,90}?)\s+[\u2013\u2014-]\s*"
    r"(?:Video[- ]?)?Brie(?:fing|ﬁng)$",
    re.IGNORECASE,
)
_HISTORICAL = re.compile(r"vorjahr|historisch|baseline|preis\s*20\d{2}[/\u2013-]", re.IGNORECASE)
_TABLE_HEADER = re.compile(r"^\s*Artikel\s{2,}(?:Art|Händler)\s{2,}")
_COLUMN = re.compile(r"\S(?:.*?\S)?(?=\s{2,}|$)")
_REQUIRED_HEADING = re.compile(
    r"^[^.!?]{0,50}\b(?:pflichtinhalte|pflichtangaben|pflichtteile|anforderungen|"
    r"verbindliche vorgaben)\b[^.!?]{0,40}:?$",
    re.IGNORECASE,
)
_EXCLUDED_HEADING = re.compile(
    r"^(?:thumbnails?|vorschaubilder?)(?:[- ]regeln|[- ]vorgaben)?\s*:?$",
    re.IGNORECASE,
)
_EXCLUDED_WORD = re.compile(r"\b(?:thumbnails?|vorschaubilder?)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    ref: str
    table: bool = False
    required: bool = False


def parse_brief(
    text: str, *, reference_year: int, client_hint: str = "", campaign_hint: str = ""
) -> BriefModel:
    """Read a brief. ``reference_year`` completes a date written without one (``12.11.``)."""
    table_facts, table_lines = _table_facts(text)
    units = list(_units(text, table_lines))
    # A table row's uncertainty belongs to the named article, including wrapped annotations.
    row_sources = dict.fromkeys(
        f.source for f in table_facts if f.kind == "product" and f.source is not None
    )
    units.extend(
        _Unit(" ".join(source.quote.split()), source.ref, table=True) for source in row_sources
    )
    reader = _Reader(units, reference_year)
    reader.read()
    return BriefModel(
        client=client_hint or reader.client,
        campaign=campaign_hint or reader.campaign,
        source_digest=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        constraints=tuple(reader.constraints),
        facts=tuple(_unique_facts([*table_facts, *reader.facts])),
        deadlines=tuple(reader.deadlines),
        mandatory=tuple(reader.mandatory),
        optional=tuple(reader.optional),
        open_questions=tuple(reader.questions()),
    )


def _units(text: str, table_lines: frozenset[tuple[int, int]] = frozenset()) -> Iterable[_Unit]:
    pages = text.split(_PAGE_BREAK)
    required, excluded = False, False
    for page_no, page in enumerate(pages, 1):
        original = page.splitlines()
        for line_no, raw in _joined_lines(page, page_no, table_lines):
            line = _BULLET.sub("", raw).strip()
            if not line:
                continue
            if _REQUIRED_HEADING.match(line):
                required, excluded = True, False
                continue
            if _EXCLUDED_HEADING.match(line):
                required, excluded = False, True
                continue
            heading = (
                len(line) <= 100
                and not re.search(r"[.!?]|:\s*\S", line)
                and not _BULLET.match(original[line_no - 1])
                and not original[line_no - 1].startswith(" ")
                and (line_no == 1 or not original[line_no - 2].strip())
                and not _TITLE.match(line)
            )
            if heading:
                required, excluded = False, False
            if excluded:
                continue
            is_required = required and not re.match(r"^(?:Ziel|Zielgruppe|Länge|Ton):", line)
            ref = f"S. {page_no}" if len(pages) > 1 else f"Zeile {line_no}"
            for sentence in _SENTENCE_END.split(line):
                if len(sentence.strip()) > 2 and not _EXCLUDED_WORD.search(sentence):
                    yield _Unit(
                        " ".join(sentence.split()),
                        ref,
                        (page_no, line_no) in table_lines,
                        is_required,
                    )


def _joined_lines(
    page: str, page_no: int, table_lines: frozenset[tuple[int, int]]
) -> Iterable[tuple[int, str]]:
    """Reassemble wrapped prose without merging adjacent list items or table rows."""
    pending, first, previous_width = "", 0, 0
    for line_no, raw in enumerate(page.splitlines(), 1):
        line = raw.strip()
        if not line or (page_no, line_no) in table_lines:
            if pending:
                yield first, pending
                pending = ""
            if line:
                yield line_no, raw
            continue
        continuation = bool(pending) and (
            line[0].islower()
            or pending.endswith((",", "-"))
            or (previous_width >= 70 and not pending.endswith((".", "!", "?", ":", ")")))
        )
        if continuation and not _BULLET.match(raw):
            pending += " " + line
        else:
            if pending:
                yield first, pending
            pending, first = line, line_no
        previous_width = len(line)
    if pending:
        yield first, pending


def _table_facts(text: str) -> tuple[list[Fact], frozenset[tuple[int, int]]]:
    """Read layout-preserved PDF tables by their columns, never by the presence of a price.

    Historical tables remain evidence, with distinct kinds and keys. A wrapped product name
    belongs to the preceding row until the next row has its own type/specification column.
    """
    facts: list[Fact] = []
    occupied: set[tuple[int, int]] = set()
    for page_no, page in enumerate(text.split(_PAGE_BREAK), 1):
        columns: list[tuple[str, int]] = []
        row: list[list[str]] = []
        quote: list[str] = []

        for line_no, line in enumerate(page.splitlines(), 1):
            if _TABLE_HEADER.match(line):
                _append_row(facts, columns, row, quote, page_no)
                columns = [(m.group().strip(), m.start()) for m in _COLUMN.finditer(line)]
                row, quote = [], []
                occupied.add((page_no, line_no))
                continue
            if not columns or not line.strip():
                continue
            # A section heading or page footer terminates the table. Its data stays untouched.
            if len(line) - len(line.lstrip()) < columns[0][1] or re.search(
                r"\bPage \d+ of \d+\b", line
            ):
                _append_row(facts, columns, row, quote, page_no)
                columns, row, quote = [], [], []
                continue
            cells = [
                line[start : columns[k + 1][1] if k + 1 < len(columns) else len(line)].strip()
                for k, (_label, start) in enumerate(columns)
            ]
            occupied.add((page_no, line_no))
            labels = [label.casefold() for label, _start in columns]
            shots = labels.index("schuss") if "schuss" in labels else None
            new = bool(cells[0]) and (
                bool(re.fullmatch(r"\d+|offen|—|-", cells[shots]))
                if shots is not None
                else bool(cells[1])
                and not re.match(r"(?:Vorjahr\b|im\b|und\b|mit\b|\()", cells[1])
                and (not any("Preis" in label for label, _ in columns) or bool(cells[2]))
            )
            if new:
                _append_row(facts, columns, row, quote, page_no)
                row, quote = [[] for _ in columns], []
            if row:
                for parts, value in zip(row, cells, strict=True):
                    if value:
                        parts.append(value)
                quote.append(line.strip())
        _append_row(facts, columns, row, quote, page_no)
    return facts, frozenset(occupied)


def _append_row(
    facts: list[Fact],
    columns: list[tuple[str, int]],
    row: list[list[str]],
    quote: list[str],
    page_no: int,
) -> None:
    if row:
        cells = [" ".join(parts).strip() for parts in row]
        facts.extend(_row_facts(columns, cells, Source(f"S. {page_no}", "\n".join(quote))))


def _row_facts(columns: list[tuple[str, int]], cells: list[str], source: Source) -> list[Fact]:
    name = cells[0]
    if not name or len(name) > 80:
        return []
    subject = slugify(name)
    historical = any(_HISTORICAL.search(label) for label, _ in columns)
    prefix = "historical_" if historical else ""
    out = [
        Fact(
            f"{prefix}product_{subject}",
            f"{'Vorjahr: ' if historical else ''}{name}",
            name,
            "",
            source,
            kind=f"{prefix}product",
            subject=subject,
        )
    ]
    for (label, _start), value in zip(columns[1:], cells[1:], strict=True):
        folded = fold(label)
        if folded.startswith("preis"):
            # A range or conflicting values must stay unresolved; never select one endpoint.
            prices = list(_PRICE.finditer(value))
            if len(prices) == 1 and not re.search(r"\d[,.]\d{2}\s*[\u2013\u2014-]", value):
                out.append(
                    Fact(
                        f"{prefix}price_{subject}",
                        f"Preis {'Vorjahr ' if historical else ''}{name}",
                        prices[0].group(1).replace(".", ","),
                        "€",
                        source,
                        kind=f"{prefix}price",
                        subject=subject,
                    )
                )
        if historical:
            continue
        if value and (folded in {"art", "einordnung"} or folded.startswith("anmerkung")):
            key = "effekt" if folded == "art" else "assessment"
            out.append(
                Fact(
                    f"{key}_{subject}",
                    f"{'Art' if key == 'effekt' else 'Einordnung'} {name}",
                    value,
                    "",
                    source,
                    kind="description",
                    subject=subject,
                )
            )
        spec = {
            "schuss": ("shots", "Schuss"),
            "kaliber": ("kaliber", "mm"),
            "nem": ("effektmasse", "g"),
        }.get(folded)
        if spec and re.fullmatch(r"\d+(?:[.,]\d+)?\s*(?:mm|g)?", value):
            key, unit = spec
            out.append(
                Fact(
                    f"{key}_{subject}",
                    f"{label} {name}",
                    re.sub(r"\s*(?:mm|g)$", "", value),
                    unit,
                    source,
                    kind="spec",
                    subject=subject,
                )
            )
        if folded.startswith("brenndauer"):
            for key, unit, pattern in (
                ("duration", "s", r"(\d+)\s*s\b"),
                ("height", "m", r"(\d+)\s*m\b"),
            ):
                if match := re.search(pattern, value):
                    out.append(
                        Fact(
                            f"{key}_{subject}",
                            f"{'Brenndauer' if key == 'duration' else 'Effekthöhe'} {name}",
                            match.group(1),
                            unit,
                            source,
                            approximate="ca." in value,
                            kind="spec",
                            subject=subject,
                        )
                    )
    return out


class _Reader:
    def __init__(self, units: list[_Unit], year: int) -> None:
        self._units = units
        self._year = year
        self.client = ""
        self.campaign = ""
        self.constraints: list[Constraint] = []
        self.facts: list[Fact] = []
        self.deadlines: list[Deadline] = []
        self.mandatory: list[MandatoryElement] = []
        self.optional: list[OptionalElement] = []
        self._asked: list[OpenQuestion] = []
        self._pending_optional: OptionalElement | None = None

    def read(self) -> None:
        for unit in self._units:
            self._header(unit)
            self._constraint(unit)
            self._elements(unit)
            self._optional(unit)
            self._facts(unit)
            self._dates(unit)
            self._explicit_questions(unit)

    # --- header ---------------------------------------------------------------------------------
    def _header(self, unit: _Unit) -> None:
        if not self.client and (m := _CLIENT.search(unit.text)):
            self.client = m.group(1).strip().rstrip(".")
        if not self.campaign and (m := _CAMPAIGN.search(unit.text)):
            self.campaign = m.group(1).strip().rstrip(".")
        if m := _TITLE.match(unit.text):
            if not self.client:
                self.client = m.group(1)
            if not self.campaign:
                self.campaign = f"{m.group(1)} {m.group(2)}"

    # --- constraints ----------------------------------------------------------------------------
    def _constraint(self, unit: _Unit) -> None:
        text = unit.text
        if self._pending_optional is not None and _POSITIVE.search(text):
            return  # the phrasing of an optional element ("...dann bitte positiv"), not a rule
        kind = self._modal(text) or (ConstraintKind.HARD if unit.required else None)
        matched = False
        for rule, pattern, forced in _RULES:
            if pattern.search(text):
                matched = True
                if _OPTIONAL.search(text) and forced is None:
                    continue
                self._add_constraint(unit, rule, forced or kind or ConstraintKind.HARD)
        if (m := _NOT_BEFORE.search(text)) and _PUBLISH_WORDS.search(text):
            matched = True
            until = self._iso(m.group(1), m.group(2), m.group(3), text[m.end() :])
            self._add_constraint(unit, "publish.not_before", ConstraintKind.HARD, until=until)
        if _CONFIDENTIAL.search(text) and not any(c.source.quote == text for c in self.constraints):
            matched = True
            self._add_constraint(
                unit, "confidential.item", ConstraintKind.CONFIDENTIAL, keywords=_keywords(text)
            )
        if not matched and (kind is not None) and not _OPTIONAL.search(text) and len(text) > 20:
            rule = "conditional.item" if kind is ConstraintKind.CONDITIONAL else ""
            self._add_constraint(
                unit,
                rule,
                kind,
                keywords=_keywords(text) if kind is ConstraintKind.CONDITIONAL else (),
            )

    @staticmethod
    def _modal(text: str) -> ConstraintKind | None:
        if _CONDITIONAL.search(text) and not re.search(
            r"veröffentlich\w* .{0,30}freigabe", text, re.I
        ):
            return ConstraintKind.CONDITIONAL
        if _CONFIDENTIAL.search(text):
            return ConstraintKind.CONFIDENTIAL
        if _OPTIONAL.search(text):
            return None
        if _HARD.search(text):
            return ConstraintKind.HARD
        if _SOFT.search(text):
            return ConstraintKind.SOFT
        return None

    def _add_constraint(
        self,
        unit: _Unit,
        rule: str,
        kind: ConstraintKind,
        *,
        until: str = "",
        keywords: tuple[str, ...] = (),
    ) -> None:
        if any(c.rule == rule and c.source.quote == unit.text for c in self.constraints):
            return
        self.constraints.append(
            Constraint(
                constraint_id=f"c{len(self.constraints) + 1:02d}",
                kind=kind,
                text=unit.text,
                rule=rule,
                source=Source(unit.ref, unit.text),
                keywords=keywords,
                until=until,
            )
        )

    # --- mandatory and optional elements --------------------------------------------------------
    def _elements(self, unit: _Unit) -> None:
        text = unit.text
        if _RULES[0][1].search(text):
            label = _LABEL_WORD.search(text)
            self._add_element(
                unit, ElementKind.LABEL, label.group(1) if label else "Werbung", False
            )
        optional_unit = bool(_OPTIONAL.search(text))
        for tag in _HASHTAG.findall(text):
            if not optional_unit:
                self._add_element(unit, ElementKind.HASHTAG, tag, False)
        if _HARD.search(text) or re.search(
            r"handle|verlink|markier|taggen|beschreibung", text, re.IGNORECASE
        ):
            for handle in _HANDLE.findall(text):
                self._add_element(unit, ElementKind.HANDLE, handle, False)
        if _TRACKING.search(text):
            later = bool(
                re.search(r"folgt|nachgereicht|nach (?:der )?freigabe|später|noch", text, re.I)
            )
            self._add_element(unit, ElementKind.LINK, "Tracking-Link", later)
        if (m := _QUOTED.search(text)) and re.search(r"hinweis|pflichttext|sicherheit", text, re.I):
            self._add_element(unit, ElementKind.LINE, m.group(1).strip(), False)

    def _add_element(self, unit: _Unit, kind: ElementKind, text: str, later: bool) -> None:
        if any(e.kind is kind and e.text.casefold() == text.casefold() for e in self.mandatory):
            return
        self.mandatory.append(
            MandatoryElement(
                f"m{len(self.mandatory) + 1:02d}", kind, text, later, Source(unit.ref, unit.text)
            )
        )

    def _optional(self, unit: _Unit) -> None:
        text = unit.text
        if _OPTIONAL.search(text):
            phrasing = "positiv" if _POSITIVE.search(text) else ""
            element = OptionalElement(
                f"o{len(self.optional) + 1:02d}", text, phrasing, Source(unit.ref, unit.text)
            )
            self.optional.append(element)
            self._pending_optional = element
            return
        # "... not mandatory. If it is mentioned, then positive." arrives as the next sentence.
        pending = self._pending_optional
        self._pending_optional = None
        if pending is not None and _POSITIVE.search(text):
            self.optional[-1] = OptionalElement(
                pending.element_id,
                f"{pending.text} {text}",
                "positiv",
                Source(pending.source.ref, f"{pending.source.quote} {text}"),
            )

    # --- facts ----------------------------------------------------------------------------------
    def _facts(self, unit: _Unit) -> None:
        text = unit.text
        for m in _URL.finditer(text):
            self._fact(
                unit,
                f"url_{len([f for f in self.facts if f.kind == 'url']) + 1}",
                "Link",
                m.group(0),
                "",
                "url",
            )
        for m in _COUNT.finditer(text):
            subject = m.group(2).casefold()
            self._fact(
                unit,
                f"max_per_{subject}",
                f"Höchstmenge pro {m.group(2)}",
                m.group(1),
                "Stück",
                "count",
            )
        for m in _COUNT_ITEM.finditer(text):
            subject = m.group(1).casefold()
            if not any(f.key == f"max_per_{subject}" for f in self.facts):
                self._fact(
                    unit,
                    f"max_per_{subject}",
                    f"Höchstmenge pro {m.group(1)}",
                    m.group(2),
                    "Stück",
                    "count",
                )
        for m in _RANGE.finditer(text):
            label_key, label = self._window_label(text)
            d1, m1, d2, m2, year = (
                m.group(1),
                m.group(2) or m.group(4),
                m.group(3),
                m.group(4),
                m.group(5),
            )
            start = self._iso(d1, m1, year, "")[:10]
            end = self._iso(d2, m2, year, "")[:10]
            self._fact(unit, label_key, label, f"{start}/{end}", "", "date")
        for product, price in () if unit.table else self._products(text):
            slug = slugify(product)
            self._fact(
                unit, f"product_{slug}", f"Produkt {product}", product, "", "product", subject=slug
            )
            self._fact(unit, f"price_{slug}", f"Preis {product}", price, "€", "price", subject=slug)

    def _fact(
        self,
        unit: _Unit,
        key: str,
        label: str,
        value: str,
        unit_of: str,
        kind: str,
        *,
        subject: str = "",
    ) -> None:
        self.facts.append(
            Fact(
                key,
                label,
                value,
                unit_of,
                Source(unit.ref, unit.text),
                False,
                kind=kind,
                subject=subject,
            )
        )

    @staticmethod
    def _window_label(text: str) -> tuple[str, str]:
        folded = fold(text)
        for stem, key, label in _FACT_LABELS:
            if stem in folded:
                return key, label
        return "campaign_window", "Zeitraum"

    @staticmethod
    def _products(text: str) -> list[tuple[str, str]]:
        if _HISTORICAL.search(text):
            return []
        found: list[tuple[str, str]] = []
        previous = 0
        for m in _PRICE.finditer(text):
            segment = text[previous : m.start()]
            previous = m.end()
            segment = segment.rsplit(":", 1)[-1]
            name = re.sub(r"^[\s,;|]*(?:und\s+)?(?:\d{1,2}[.)]\s*)?", "", segment)
            name = re.sub(r"[\s:;|,\-\u2013\u2014]+$", "", name)
            words = name.split()
            if (
                1 <= len(words) <= 6
                and re.search(r"[A-Za-zÄÖÜäöü]", name)
                and len(name) <= 60
                and not re.fullmatch(r"(?:oder|und|ca\.?|Fontäne|Raketenset)", name, re.I)
                and not re.search(r"\d\s*(?:Schuss|Teile|Raketen)\b", name, re.I)
            ):
                found.append((name, m.group(1).replace(".", ",")))
        return found

    # --- dates and deadlines --------------------------------------------------------------------
    def _dates(self, unit: _Unit) -> None:
        text = unit.text
        if _RANGE.search(text) or not _DEADLINE_WORDS.search(text):
            return
        if re.search(r"\bveröffentlicht\b", text, re.I) and not re.search(
            r"\b(?:muss|darf|soll|freigabe|deadline|termin|frühestens|spätestens)\b", text, re.I
        ):
            return  # Publication dates of reference videos are not this project's deadlines.
        dates = list(_DATE.finditer(text))
        for i, m in enumerate(dates):
            end = dates[i + 1].start() if i + 1 < len(dates) else len(text)
            after = text[m.end() : min(end, m.end() + 44)]
            times = _times(after) or [""]
            for time in times:
                iso = self._iso(m.group(1), m.group(2), m.group(3), "")[:10]
                kind = "publish" if _PUBLISH_WORDS.search(text) else "approval"
                label = (
                    "Veröffentlichung" if kind == "publish" else _deadline_label(text, m.start())
                )
                self.deadlines.append(
                    Deadline(
                        f"d{len(self.deadlines) + 1:02d}",
                        label,
                        iso,
                        time,
                        Source(unit.ref, text),
                        kind,
                    )
                )

    def _iso(self, day: str, month: str, year: str | None, after: str) -> str:
        y = int(year) if year else self._year
        stamp = f"{y:04d}-{int(month):02d}-{int(day):02d}"
        times = _times(after[:12])
        return f"{stamp}T{times[0]}" if times else stamp

    # --- questions ------------------------------------------------------------------------------
    def _explicit_questions(self, unit: _Unit) -> None:
        if unit.text.endswith("?") or _OPEN_MARK.search(unit.text):
            self._asked.append(
                OpenQuestion(
                    f"q-src-{len(self._asked) + 1:02d}",
                    unit.text if unit.text.endswith("?") else f"Klären: {unit.text}",
                    "Das Briefing lässt es selbst offen.",
                    1,
                    Source(unit.ref, unit.text),
                )
            )

    def questions(self) -> list[OpenQuestion]:
        out = list(self._asked)
        for c in self.constraints:
            if c.kind is ConstraintKind.CONFIDENTIAL:
                out.append(
                    OpenQuestion(
                        f"q-{c.constraint_id}",
                        f"Darf das gezeigt werden? „{_short(c.text)}“",
                        "Vertraulich laut Briefing: ohne Freigabe nicht zeigen oder teilen.",
                        1,
                        c.source,
                    )
                )
            elif c.kind is ConstraintKind.CONDITIONAL:
                out.append(
                    OpenQuestion(
                        f"q-{c.constraint_id}",
                        f"Liegt die Freigabe vor? „{_short(c.text)}“",
                        "Nur nach Bestätigung erlaubt.",
                        1,
                        c.source,
                    )
                )
        for e in self.mandatory:
            if e.arrives_later:
                out.append(
                    OpenQuestion(
                        f"q-{e.element_id}",
                        f"Wann kommt der {e.text}?",
                        "Wird erst nach der Freigabe geliefert: bis dahin steht ein Platzhalter.",
                        2,
                        e.source,
                    )
                )
        if not self.client:
            out.append(
                OpenQuestion(
                    "q-client", "Wie heißt der Auftraggeber?", "Steht nicht im Briefing.", 2
                )
            )
        return sorted(out, key=lambda q: (q.priority, q.question_id))


def _times(text: str) -> list[str]:
    out: list[str] = []
    for m in _TIME.finditer(text):
        if m.group(1) is not None:
            out.append(f"{int(m.group(1)):02d}:{m.group(2)}")
        else:
            out.append(f"{int(m.group(3)):02d}:00")
    return out


_TRAILING = re.compile(
    r"\s+(?:bis|am|um|vom|den|und|oder|spätestens|werden|ist|sind)$", re.IGNORECASE
)


def _deadline_label(text: str, at: int) -> str:
    before = text[max(0, at - 50) : at].strip()
    piece = re.split(r"[:.;]\s", before)[-1].strip(" ,:;-")
    previous = None
    while previous != piece:
        previous, piece = piece, _TRAILING.sub("", piece).strip(" ,:;-")
    words = piece.split()
    return " ".join(words[-3:]) or "Termin"


def _keywords(text: str) -> tuple[str, ...]:
    words = [w for w in fold(text).split() if len(w) > 4]
    stop = {
        "nicht",
        "werden",
        "wurde",
        "diese",
        "dieser",
        "dieses",
        "gezeigt",
        "zeigen",
        "teilen",
        "geteilt",
        "vertraulich",
        "bitte",
        "keine",
        "darf",
        "duerfen",
        "veroeffentlicht",
        "veroeffentlichen",
        "weitergegeben",
        "weitergeben",
        "freigabe",
        "vorab",
        "sofern",
    }
    return tuple(dict.fromkeys(w for w in words if w not in stop))[:6]


def _short(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _unique_facts(facts: Iterable[Fact]) -> list[Fact]:
    seen: set[tuple[str, str]] = set()
    out: list[Fact] = []
    for fact in facts:
        key = (fact.key, fact.value)
        if key not in seen:
            seen.add(key)
            out.append(fact)
    return out


# --- grounding of model-proposed items ---------------------------------------------------------


def quote_in_brief(quote: str, brief_text: str) -> bool:
    """Whether a quote really stands in the brief (case, spacing and punctuation ignored)."""
    needle = fold(quote)
    return bool(needle) and needle in fold(brief_text)


def verified_price(fact: Fact) -> bool:
    """A price fact whose text parses as a number (a model-written one that does not is refused)."""
    return fact.kind != "price" or parse_number(fact.value) is not None
