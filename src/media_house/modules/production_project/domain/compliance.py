"""The compliance engine: the brief compiled to rules, checked at three points.

1. the script linter (live while editing), 2. the asset validators (``assets.py``), 3. the
timeline QC (on the edit plan and the preview render) and the metadata check before export.

A ``BLOCKER`` stops the export; a ``WARNING`` is shown. A person may override either with a
reason: the override is logged, valid for this project, and never silent. The system advises, the
creator decides.
"""

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from fractions import Fraction

from media_house.modules.production_project.domain.brief import BriefModel, Fact
from media_house.modules.production_project.domain.claims import (
    ClaimRisk,
    ScanContext,
    scan_script,
)
from media_house.modules.production_project.domain.facts import number_of
from media_house.modules.production_project.domain.numbers import (
    fold,
    format_number,
    parse_number,
)
from media_house.modules.production_project.domain.script import Exclusion, Script
from media_house.modules.production_project.domain.script_tools import DEFAULT_WPM, words
from media_house.modules.production_project.domain.values import (
    ConstraintKind,
    ElementKind,
    Severity,
)


class Stage(StrEnum):
    SCRIPT = "script"
    ASSET = "asset"
    TIMELINE = "timeline"
    METADATA = "metadata"


class RuleKind(StrEnum):
    LABEL = "label"
    PUBLISH_GATE = "publish_gate"
    NOT_BEFORE = "not_before"
    CONFIDENTIAL = "confidential"
    CONDITIONAL = "conditional"
    COMPETITOR = "competitor"
    NEGATIVE = "negative"
    THIRD_PARTY_BRAND = "third_party_brand"
    ILLEGAL_PYRO = "illegal_pyro"
    SUPERLATIVE = "superlative"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    kind: RuleKind
    severity: Severity
    message: str
    constraint_id: str = ""
    #: A date the rule holds until (ISO), for a publishing embargo.
    until: str = ""


@dataclass(frozen=True, slots=True)
class RuleSet:
    rules: tuple[Rule, ...]

    def has(self, kind: RuleKind) -> bool:
        return any(r.kind is kind for r in self.rules)

    def of(self, kind: RuleKind) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind is kind)

    @property
    def manual(self) -> tuple[Rule, ...]:
        return self.of(RuleKind.MANUAL)


_BY_KEY: dict[str, tuple[RuleKind, Severity]] = {
    "label.advertising": (RuleKind.LABEL, Severity.BLOCKER),
    "no.competitor_comparison": (RuleKind.COMPETITOR, Severity.BLOCKER),
    "no.negative_about_client": (RuleKind.NEGATIVE, Severity.BLOCKER),
    "no.third_party_brands": (RuleKind.THIRD_PARTY_BRAND, Severity.BLOCKER),
    "no.illegal_pyro": (RuleKind.ILLEGAL_PYRO, Severity.BLOCKER),
    "publish.after_approval": (RuleKind.PUBLISH_GATE, Severity.WARNING),
    "publish.not_before": (RuleKind.NOT_BEFORE, Severity.WARNING),
    "claim.no_superlatives": (RuleKind.SUPERLATIVE, Severity.BLOCKER),
    "confidential.item": (RuleKind.CONFIDENTIAL, Severity.BLOCKER),
    "conditional.item": (RuleKind.CONDITIONAL, Severity.WARNING),
}


def compile_rules(brief: BriefModel) -> RuleSet:
    """Every constraint becomes a rule. A constraint no check understands is a MANUAL rule: it
    is listed for a person to confirm, never ignored."""
    rules: list[Rule] = []
    for c in brief.constraints:
        kind, severity = _BY_KEY.get(
            c.rule,
            (
                RuleKind.MANUAL,
                Severity.BLOCKER if c.kind is ConstraintKind.HARD else Severity.WARNING,
            ),
        )
        rules.append(
            Rule(f"rule-{c.constraint_id}", kind, severity, c.text, c.constraint_id, c.until)
        )
    return RuleSet(tuple(rules))


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: str
    rule_id: str
    stage: Stage
    severity: Severity
    #: ``S03``, ``talking_01``, ``description`` ...
    where: str
    quote: str
    message: str
    safe_alternative: str = ""
    constraint_id: str = ""
    #: The reason a person kept it anyway ("" = standing).
    overridden: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.BLOCKER and not self.overridden


@dataclass(frozen=True, slots=True)
class Override:
    finding_id: str
    reason: str
    at: datetime
    #: Valid for this project only unless the creator also asked to remember it for the client.
    remember_for_client: bool = False


def apply_overrides(
    findings: Iterable[Finding], overrides: Iterable[Override]
) -> tuple[Finding, ...]:
    reasons = {o.finding_id: o.reason for o in overrides if o.reason.strip()}
    return tuple(
        replace(f, overridden=reasons[f.finding_id]) if f.finding_id in reasons else f
        for f in findings
    )


def blockers(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    return tuple(f for f in findings if f.blocking)


def _finding(
    rule: str,
    stage: Stage,
    severity: Severity,
    where: str,
    quote: str,
    message: str,
    alternative: str = "",
    constraint: str = "",
) -> Finding:
    key = f"{rule}:{where}:{fold(quote)[:40].replace(' ', '-')}"
    return Finding(key, rule, stage, severity, where, quote, message, alternative, constraint)


# --- 1. the script linter ----------------------------------------------------------------------

_PRICE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s?(?:€|euro\b|eur\b)", re.IGNORECASE)
_DATE = re.compile(r"(?<!\d)(\d{1,2})\.\s?(\d{1,2})\.(?!\d)")
_UNIT_NUMBER = re.compile(
    r"(\d{1,4}(?:[.,]\d+)?)\s?(%|prozent|uhr|stück|schuss|schüsse|mm|watt|kg|g|m|sekunden)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LintContext:
    rules: RuleSet
    facts: tuple[Fact, ...]
    scan: ScanContext
    #: The format's pace and target length (minutes), for the hook and the length checks.
    wpm: float = DEFAULT_WPM
    target_minutes: tuple[float, float] = (0.0, 0.0)
    #: The script as stored (placeholders intact), to name the slots still open.
    raw: Script | None = None


def lint_script(
    script: Script, ctx: LintContext, *, estimated_seconds: float = 0.0
) -> tuple[Finding, ...]:
    """Check the resolved script. Live: cheap, deterministic, no model."""
    out: list[Finding] = []
    out += _claim_findings(script, ctx)
    out += _number_findings(script, ctx)
    out += _hook_finding(script, ctx)
    out += _slot_findings(script, ctx)
    out += _gate_findings(script)
    out += _unverified_findings(script, ctx)
    out += _length_findings(estimated_seconds, ctx)
    return tuple(dict.fromkeys(out))


def _claim_findings(script: Script, ctx: LintContext) -> list[Finding]:
    active = ctx.scan
    return [_from_risk(r) for r in scan_script(script, active)]


def _from_risk(risk: ClaimRisk) -> Finding:
    return _finding(
        f"claim.{risk.kind.value}",
        Stage.SCRIPT,
        risk.severity,
        risk.where,
        risk.quote,
        risk.why,
        risk.safe_alternative,
        risk.constraint_id,
    )


def allowed_numbers(facts: Sequence[Fact]) -> tuple[set[Fraction], set[tuple[int, int]]]:
    """The numbers and dates the fact table allows a script to say."""
    numbers: set[Fraction] = set()
    dates: set[tuple[int, int]] = set()
    for f in facts:
        if f.kind == "date":
            for part in f.value.split("/"):
                if len(part) >= 10:
                    dates.add((int(part[8:10]), int(part[5:7])))
            continue
        value = number_of(f)
        if value is not None:
            numbers.add(value)
    return numbers, dates


def _number_findings(script: Script, ctx: LintContext) -> list[Finding]:
    numbers, dates = allowed_numbers(ctx.facts)
    prices = sorted(
        {value for fact in ctx.facts if fact.unit == "€" and (value := number_of(fact)) is not None}
    )
    out: list[Finding] = []
    for scene in script.scenes:
        for s in scene.sentences:
            if not s.usable:
                continue
            for m in _PRICE.finditer(s.text):
                value = parse_number(m.group(1))
                if value is not None and value not in prices:
                    near = _nearest(prices, value)
                    hint = f" Nächster Wert der Tabelle: {format_number(near)} €." if near else ""
                    out.append(
                        _finding(
                            "numbers.price_not_in_fact_table",
                            Stage.SCRIPT,
                            Severity.BLOCKER,
                            scene.scene_id,
                            m.group(0),
                            f"Der Preis „{m.group(0)}“ steht nicht in der Faktentabelle.{hint}",
                            "Den Preis aus der Faktentabelle nehmen: {{fact:price_...}}",
                        )
                    )
            for m in _DATE.finditer(s.text):
                if (int(m.group(1)), int(m.group(2))) not in dates:
                    out.append(
                        _finding(
                            "numbers.date_not_in_fact_table",
                            Stage.SCRIPT,
                            Severity.BLOCKER,
                            scene.scene_id,
                            m.group(0),
                            f"Das Datum „{m.group(0)}“ steht nicht in der Faktentabelle.",
                            "Das Datum aus der Faktentabelle nehmen.",
                        )
                    )
            for m in _UNIT_NUMBER.finditer(s.text):
                unit = m.group(2).lower()
                if unit in {"uhr", "m", "g", "sekunden"}:
                    continue
                value = parse_number(m.group(1))
                if value is not None and value > 9 and value not in numbers:
                    out.append(
                        _finding(
                            "numbers.value_not_in_fact_table",
                            Stage.SCRIPT,
                            Severity.WARNING,
                            scene.scene_id,
                            m.group(0),
                            f"„{m.group(0)}“ steht nicht in der Faktentabelle.",
                            "Als Fakt eintragen oder über ein Technik-Feld angeben.",
                        )
                    )
    return out


def _nearest(options: Sequence[Fraction], target: Fraction) -> Fraction | None:
    return min(options, key=lambda p: abs(p - target)) if options else None


def _hook_finding(script: Script, ctx: LintContext) -> list[Finding]:
    first = next((s for sc in script.scenes for s in sc.sentences if s.usable), None)
    if first is None:
        return []
    limit = int(5 * ctx.wpm / 60) + 2
    count = words(first.text)
    scene = script.scenes[0]
    if scene.function == "hook" and count <= limit * 2:
        return []
    if count <= limit:
        return []
    return [
        _finding(
            "hook.first_five_seconds",
            Stage.SCRIPT,
            Severity.WARNING,
            scene.scene_id,
            first.text,
            f"Der erste Satz hat {count} Wörter ({count / ctx.wpm * 60:.0f} s): "
            "der Hook sollte in den ersten 5 Sekunden sitzen.",
            "Mit dem Aufhänger beginnen, in einem kurzen Satz.",
        )
    ]


def _slot_findings(script: Script, ctx: LintContext) -> list[Finding]:
    open_slots: dict[str, list[str]] = {}
    for scene in script.scenes:
        for s in scene.sentences:
            if s.exclusion is Exclusion.UNFILLED_SLOT:
                for slot in re.findall(r"\{([A-Z][A-Z0-9_]*)\}", s.text):
                    open_slots.setdefault(slot, []).append(scene.scene_id)
    return [
        _finding(
            "slots.unfilled",
            Stage.SCRIPT,
            Severity.WARNING,
            ",".join(dict.fromkeys(scenes)),
            slot,
            f"Das Technik-Feld {{{slot}}} ist leer: die Sätze dazu entfallen.",
            "Das Feld ausfüllen oder leer lassen.",
        )
        for slot, scenes in sorted(open_slots.items())
    ]


def _gate_findings(script: Script) -> list[Finding]:
    return [
        _finding(
            "gate.needs_approval",
            Stage.SCRIPT,
            Severity.WARNING,
            scene.scene_id,
            s.text,
            f"Nur nach Freigabe ({s.gate or 'bedingt'}): die Zeile bleibt draußen, bis sie da ist.",
        )
        for scene in script.scenes
        for s in scene.sentences
        if s.exclusion is Exclusion.APPROVAL_REQUIRED
    ]


def _unverified_findings(script: Script, ctx: LintContext) -> list[Finding]:
    text = " ".join(s.text for sc in script.scenes for s in sc.sentences if s.usable)
    out: list[Finding] = []
    for f in ctx.facts:
        if f.verified or f.kind in {"url", "product"} or f.derived_from:
            continue
        shown = f.value.replace("/", " ")
        if f.kind == "price" and shown in text:
            out.append(
                _finding(
                    "facts.unverified",
                    Stage.SCRIPT,
                    Severity.WARNING,
                    f.key,
                    shown,
                    f"{f.label} ist noch nicht gegen eine Quelle geprüft.",
                    "In der Faktentabelle als geprüft markieren, sobald die Quelle live ist.",
                )
            )
    return out


def _length_findings(seconds: float, ctx: LintContext) -> list[Finding]:
    low, high = ctx.target_minutes
    if not seconds or not high:
        return []
    minutes = seconds / 60
    if minutes > high * 1.25:
        return [
            _finding(
                "length.too_long",
                Stage.SCRIPT,
                Severity.WARNING,
                "script",
                f"{minutes:.1f} min",
                f"Geschätzte Länge {minutes:.1f} min, Zielbereich {low:g}-{high:g} min.",
                "Optionale Blöcke ausschalten oder Szenen kürzen.",
            )
        ]
    if minutes < low * 0.6:
        return [
            _finding(
                "length.too_short",
                Stage.SCRIPT,
                Severity.WARNING,
                "script",
                f"{minutes:.1f} min",
                f"Geschätzte Länge {minutes:.1f} min, Zielbereich {low:g}-{high:g} min.",
            )
        ]
    return []


# --- 2. metadata -------------------------------------------------------------------------------


def lint_metadata(
    *,
    title: str,
    description: str,
    tags: Sequence[str],
    brief: BriefModel,
    tracking_link_resolved: bool,
    on_screen_text: Sequence[str] = (),
) -> tuple[Finding, ...]:
    """The title, description and tags against the brief's mandatory elements."""
    out: list[Finding] = []
    folded = description.casefold()
    for element in brief.mandatory:
        if element.kind in {ElementKind.HASHTAG, ElementKind.HANDLE}:
            if (
                element.text.casefold() not in folded
                and element.text.casefold() not in title.casefold()
            ):
                out.append(
                    _finding(
                        "metadata.mandatory_element",
                        Stage.METADATA,
                        Severity.BLOCKER,
                        "description",
                        element.text,
                        f"{element.text} fehlt in der Beschreibung.",
                        f"{element.text} in die Beschreibung aufnehmen.",
                        "",
                    )
                )
        elif element.kind is ElementKind.LABEL and element.text.casefold() not in folded:
            out.append(
                _finding(
                    "metadata.label",
                    Stage.METADATA,
                    Severity.BLOCKER,
                    "description",
                    element.text,
                    f"Die Kennzeichnung „{element.text}“ fehlt in der Beschreibung.",
                    f"Eine Zeile „{element.text}“ an den Anfang setzen.",
                )
            )
        elif element.kind is ElementKind.LINE and element.text.casefold() not in folded:
            out.append(
                _finding(
                    "metadata.mandatory_line",
                    Stage.METADATA,
                    Severity.BLOCKER,
                    "description",
                    element.text,
                    "Ein Pflichttext fehlt in der Beschreibung.",
                    element.text,
                )
            )
        elif element.kind is ElementKind.LINK and not tracking_link_resolved:
            out.append(
                _finding(
                    "metadata.tracking_link",
                    Stage.METADATA,
                    Severity.BLOCKER,
                    "description",
                    element.text,
                    "Der Tracking-Link ist noch nicht eingetragen.",
                    "Den Link nach der Freigabe eintragen, dann exportieren.",
                )
            )
    if len(title) > 100:
        out.append(
            _finding(
                "metadata.title_length",
                Stage.METADATA,
                Severity.BLOCKER,
                "title",
                title[:40],
                f"Der Titel hat {len(title)} Zeichen, YouTube erlaubt 100.",
            )
        )
    used = tag_length(tags)
    if used > 500:
        out.append(
            _finding(
                "metadata.tags_length",
                Stage.METADATA,
                Severity.BLOCKER,
                "tags",
                f"{used} Zeichen",
                f"Die Schlagwörter brauchen {used} Zeichen, erlaubt sind 500.",
            )
        )
    return tuple(out)


def tag_length(tags: Sequence[str]) -> int:
    """YouTube counts a tag with a space as quoted (two more characters) and a comma between."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


# --- 3. the timeline QC -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimelineFacts:
    """What the final timeline (or its preview render) does, read from the built timeline."""

    duration_seconds: float
    #: Spans (start, end) in seconds during which the advertising label is on screen.
    label_spans: tuple[tuple[float, float], ...]
    transitions: tuple[str, ...]
    #: The most overlays on screen at the same time, and per minute on average.
    max_simultaneous_overlays: int
    #: Ids of the assets the timeline uses.
    used_assets: tuple[str, ...]
    on_screen_texts: tuple[str, ...]
    #: Cuts in the picture (a measure of the rhythm); 0 when not read.
    cuts: int = 0


def qc_timeline(
    facts: TimelineFacts,
    ctx_rules: RuleSet,
    *,
    allowed_transitions: Collection[str],
    forbidden_transitions: Collection[str],
    overlay_limit: int,
    flagged_assets: Collection[str],
    forbidden_words: Collection[str],
    brief: BriefModel,
) -> tuple[Finding, ...]:
    """The timeline against the brief and the style: label for the whole video, only approved
    transitions, overlays within the profile, no flagged asset, no forbidden word on screen."""
    out: list[Finding] = []
    if ctx_rules.has(RuleKind.LABEL) and facts.duration_seconds > 0:
        gap = _first_gap(facts.label_spans, facts.duration_seconds)
        if gap is not None:
            out.append(
                _finding(
                    "timeline.label_missing",
                    Stage.TIMELINE,
                    Severity.BLOCKER,
                    f"{gap[0]:.0f}s",
                    "Werbung",
                    f"Das Werbung-Label fehlt von {gap[0]:.1f} s bis {gap[1]:.1f} s: "
                    "es muss das ganze Video über sichtbar sein.",
                    "Das Label über die gesamte Laufzeit legen.",
                )
            )
    for transition in dict.fromkeys(facts.transitions):
        bad = transition in forbidden_transitions
        unknown = allowed_transitions and transition not in allowed_transitions
        if bad or unknown:
            out.append(
                _finding(
                    "timeline.transition",
                    Stage.TIMELINE,
                    Severity.BLOCKER if bad else Severity.WARNING,
                    "timeline",
                    transition,
                    f"Der Übergang „{transition}“ passt nicht zu deinem Stil.",
                    "Harter Schnitt, Zoom oder Text-Pop-in.",
                )
            )
    if overlay_limit and facts.max_simultaneous_overlays > overlay_limit:
        out.append(
            _finding(
                "timeline.overlays",
                Stage.TIMELINE,
                Severity.WARNING,
                "timeline",
                str(facts.max_simultaneous_overlays),
                f"Bis zu {facts.max_simultaneous_overlays} Overlays gleichzeitig, "
                f"dein Profil erlaubt {overlay_limit}.",
            )
        )
    out.extend(
        _finding(
            "timeline.flagged_asset",
            Stage.TIMELINE,
            Severity.BLOCKER,
            asset,
            asset,
            "Eine markierte Datei ist im Schnitt verwendet, bevor die Markierung geklärt ist.",
            "Freigabe eintragen, verpixeln oder ersetzen.",
        )
        for asset in dict.fromkeys(facts.used_assets)
        if asset in flagged_assets
    )
    for text in facts.on_screen_texts:
        low = text.casefold()
        for word in forbidden_words:
            if re.search(rf"(?<!\w){re.escape(word.casefold())}(?!\w)", low):
                out.append(
                    _finding(
                        "timeline.forbidden_text",
                        Stage.TIMELINE,
                        Severity.BLOCKER,
                        "timeline",
                        text,
                        f"Das Wort „{word}“ steht im Bild: das Briefing verbietet es.",
                    )
                )
    return tuple(dict.fromkeys(out))


def _first_gap(spans: Sequence[tuple[float, float]], duration: float) -> tuple[float, float] | None:
    """The first stretch of the video without the label (gaps under a quarter second are
    rendering jitter)."""
    cursor = 0.0
    for start, end in sorted(spans):
        if start - cursor > 0.25:
            return cursor, start
        cursor = max(cursor, end)
    return (cursor, duration) if duration - cursor > 0.25 else None
