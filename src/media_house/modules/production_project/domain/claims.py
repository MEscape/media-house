"""Risky wording and plans: what the brief forbids or cannot support, found before it is said.

Each risk says why, how serious it is and what to say instead. A person replaces the wording or
keeps it knowingly (with a reason that is logged and valid for this project only).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from media_house.modules.production_project.domain.brief import BriefModel
from media_house.modules.production_project.domain.lexicon import (
    COMPETITORS,
    ILLEGAL_PYRO_TERMS,
    NEGATIVE_WORDS,
    PYRO_BRANDS,
    SHOWING_VERBS,
)
from media_house.modules.production_project.domain.numbers import fold
from media_house.modules.production_project.domain.script import Scene, Script
from media_house.modules.production_project.domain.values import ConstraintKind, Severity


class ClaimKind(StrEnum):
    FIRST = "first"
    EXCLUSIVITY = "exclusivity"
    GUARANTEE = "guarantee"
    SUPERLATIVE = "superlative"
    COMPETITOR = "competitor"
    NEGATIVE_CLIENT = "negative_client"
    THIRD_PARTY_BRAND = "third_party_brand"
    ILLEGAL_PYRO = "illegal_pyro"
    CONFIDENTIAL_SHOWN = "confidential_shown"
    CONDITIONAL_USED = "conditional_used"
    STYLE_CONFLICT = "style_conflict"
    OUTDATED_FACT = "outdated_fact"
    QUALITY = "quality"


class RiskAction(StrEnum):
    REPLACED = "replaced"
    KEPT = "kept"


@dataclass(frozen=True, slots=True)
class ClaimRisk:
    risk_id: str
    kind: ClaimKind
    quote: str
    #: Where it was found: ``idea``, ``concept``, ``S03`` ...
    where: str
    why: str
    severity: Severity
    safe_alternative: str
    constraint_id: str = ""


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """What the creator decided about one risk. ``KEPT`` needs a reason."""

    risk_id: str
    action: RiskAction
    reason: str
    at: datetime
    #: The wording that replaced it (for ``REPLACED``).
    replacement: str = ""
    #: Also remember the decision for this client (an override that outlives the project).
    remember_for_client: bool = False


@dataclass(frozen=True, slots=True)
class ScanContext:
    """What the scanners need to know about the project."""

    client: str
    product_names: tuple[str, ...] = ()
    #: How many items the video compares ("fünf"): the only scope a superlative may have.
    scope_word: str = ""
    extra_competitors: frozenset[str] = frozenset()
    #: Techniques the creator's style forbids (glitch ...), for the style check of ideas.
    forbidden_techniques: tuple[str, ...] = ()
    no_comparison: bool = True
    no_negative: bool = True
    no_third_party_brands: bool = True


_FIRST = re.compile(
    r"(als erste[rsn]?\b|erste[rsn]?\s+(?:überhaupt|jemals|je)\b|zum ersten mal überhaupt|"
    r"weltweit erste|noch nie dagewesen|noch nie da gewesen)"
)
_EXCLUSIVE = re.compile(
    r"(kein(?:e|er|en|em)?\s+(?:andere[rnm]?|sonst\w*)\s+(?:discounter|anbieter|händler|supermarkt|laden|markt)"
    r"|gibt es sonst nirgend\w*|sonst nirgend\w*|einzigartig|einzige[rn]?\s+\w+|"
    r"nur (?:bei|hier bei)\s+\w+)"
)
_GUARANTEE = re.compile(
    r"(garantier\w+|ohne (?:jeden )?zweifel|100\s?%\s*sicher|"
    r"das kennt (?:ihr|du|keiner) (?:garantiert )?nicht)"
)
_SUPERLATIVE = re.compile(
    r"\b((?:der|die|das|am)\s+)?(beste[rnms]?|schönste[rnms]?|günstigste[rnms]?|billigste[rnms]?|"
    r"größte[rnms]?|stärkste[rnms]?|krasseste[rnms]?|lauteste[rnms]?|beliebteste[rnms]?|"
    r"(?:am\s+(?:besten|günstigsten|billigsten|stärksten)))\b|\b(von allen|aller zeiten|"
    r"auf dem markt|weltweit|schlechthin)\b"
)
_SCOPED = re.compile(
    r"\b(unter|von|aus|in|bei)\s+(diesen|den|allen|meinen)\s+(?:\w+\s+)?"
    r"(fünf|5|vier|4|sechs|6|drei|3|produkten|artikeln|batterien)\b|"
    r"\b(dieser|der|von diesen)\s+(?:\w+\s+)?(fünf|5|vier|4|sechs|6|drei|3)\b"
)
_NEG_NEAR = re.compile("|".join(re.escape(w) for w in NEGATIVE_WORDS))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def scan_text(text: str, where: str, ctx: ScanContext) -> tuple[ClaimRisk, ...]:
    """Every risky claim in a piece of text (a creator's idea, a hook, a spoken sentence)."""
    risks: list[ClaimRisk] = []
    for sentence in _sentences(text):
        low = sentence.lower()
        risks.extend(_claims(sentence, low, where, ctx))
    return tuple(_number(risks))


def _claims(sentence: str, low: str, where: str, ctx: ScanContext) -> Iterable[ClaimRisk]:
    scoped = bool(_SCOPED.search(low))
    if m := _FIRST.search(low):
        yield _risk(
            ClaimKind.FIRST,
            sentence,
            m,
            where,
            "„Erster“ oder „noch nie dagewesen“ lässt sich nicht belegen.",
            Severity.BLOCKER,
            "Schauen wir uns an, was dieses Angebot konkret bietet.",
        )
    if m := _EXCLUSIVE.search(low):
        yield _risk(
            ClaimKind.EXCLUSIVITY,
            sentence,
            m,
            where,
            "Eine Aussage über alle anderen Anbieter ist nicht belegt "
            "und berührt das Vergleichsverbot.",
            Severity.BLOCKER,
            "Hier geht es um diese Artikel und ihre Unterschiede.",
        )
    if m := _GUARANTEE.search(low):
        yield _risk(
            ClaimKind.GUARANTEE,
            sentence,
            m,
            where,
            "Eine Garantie über das Wissen oder die Reaktion der Zuschauer kannst du nicht geben.",
            Severity.BLOCKER,
            "Vielleicht ist für dich eine neue Entdeckung dabei.",
        )
    if (m := _SUPERLATIVE.search(low)) and not scoped:
        yield _risk(
            ClaimKind.SUPERLATIVE,
            sentence,
            m,
            where,
            "Ein Superlativ ohne Beleg: nur innerhalb der gezeigten Produkte ist er erlaubt.",
            Severity.BLOCKER,
            "Ich vergleiche die Artikel innerhalb dieses Angebots.",
        )
    yield from _names(sentence, low, where, ctx)


def _names(sentence: str, low: str, where: str, ctx: ScanContext) -> Iterable[ClaimRisk]:
    client = ctx.client.casefold()
    competitors = {c for c in COMPETITORS | ctx.extra_competitors if c != client}
    if ctx.no_comparison:
        for name in sorted(competitors):
            if m := re.search(rf"(?<!\w){re.escape(name)}(?!\w)", low):
                yield _risk(
                    ClaimKind.COMPETITOR,
                    sentence,
                    m,
                    where,
                    f"„{name.title()}“ ist ein anderer Anbieter: "
                    "Vergleiche und Nennungen sind verboten.",
                    Severity.BLOCKER,
                    "Vergleiche nur innerhalb dieser Produkte.",
                )
        if m := re.search(
            r"(andere[rn]? (?:discounter|anbieter|händler|supermärkte)|konkurrenz|mitbewerber|"
            r"anderswo|bei der konkurrenz)",
            low,
        ):
            yield _risk(
                ClaimKind.COMPETITOR,
                sentence,
                m,
                where,
                "Der Bezug auf andere Anbieter ist ein Vergleich, den das Briefing verbietet.",
                Severity.BLOCKER,
                "Vergleiche nur innerhalb dieser Produkte.",
            )
    if ctx.no_negative and (n := _NEG_NEAR.search(low)):
        about = client in low or any(p.casefold() in low for p in ctx.product_names)
        yield _risk(
            ClaimKind.NEGATIVE_CLIENT,
            sentence,
            n,
            where,
            "Negative Aussage über den Auftraggeber oder seine Produkte.",
            Severity.BLOCKER if about else Severity.WARNING,
            "Neutral beschreiben oder weglassen.",
        )
    for term in ILLEGAL_PYRO_TERMS:
        if m := re.search(rf"(?<!\w){re.escape(term)}(?!\w)", low):
            yield _risk(
                ClaimKind.ILLEGAL_PYRO,
                sentence,
                m,
                where,
                "Illegale oder nicht zugelassene Pyrotechnik darf nicht vorkommen.",
                Severity.BLOCKER,
                "Nur zugelassene Artikel (CE/BAM-Kennzeichnung) zeigen und nennen.",
            )
            break
    if ctx.no_third_party_brands:
        for brand in sorted(PYRO_BRANDS):
            if m := re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", low):
                yield _risk(
                    ClaimKind.THIRD_PARTY_BRAND,
                    sentence,
                    m,
                    where,
                    f"„{brand.title()}“ ist eine Fremdmarke: sie soll nicht auffällig vorkommen.",
                    Severity.WARNING,
                    "Die Marke nicht nennen, nur das Produkt des Auftraggebers.",
                )
                break
    for technique in ctx.forbidden_techniques:
        if re.search(rf"(?<!\w){re.escape(technique.casefold())}(?!\w)", low):
            m = re.search(re.escape(technique.casefold()), low)
            if m:
                yield _risk(
                    ClaimKind.STYLE_CONFLICT,
                    sentence,
                    m,
                    where,
                    f"„{technique}“ passt nicht zu deinem Stil.",
                    Severity.WARNING,
                    "Näher am Stil: Tempo, Zoom auf das Detail, Text-Pop-in, Sound-Hit.",
                )


def _risk(
    kind: ClaimKind,
    sentence: str,
    match: re.Match[str],
    where: str,
    why: str,
    severity: Severity,
    alternative: str,
) -> ClaimRisk:
    quote = sentence[match.start() : match.end()] if len(sentence) >= match.end() else sentence
    return ClaimRisk("", kind, quote.strip() or sentence, where, why, severity, alternative)


def _number(risks: list[ClaimRisk]) -> list[ClaimRisk]:
    return [
        ClaimRisk(
            f"risk-{r.where}-{r.kind.value}-{i + 1}",
            r.kind,
            r.quote,
            r.where,
            r.why,
            r.severity,
            r.safe_alternative,
            r.constraint_id,
        )
        for i, r in enumerate(risks)
    ]


# --- ideas and plans against the brief ------------------------------------------------------------


def scan_plan(text: str, where: str, brief: BriefModel, ctx: ScanContext) -> tuple[ClaimRisk, ...]:
    """The claim scan plus what the plan does with confidential and approval-only material."""
    risks = list(scan_text(text, where, ctx))
    for sentence in _sentences(text):
        folded = fold(sentence)
        shows = any(fold(v) in folded for v in SHOWING_VERBS)
        for c in brief.constraints:
            if c.kind not in (ConstraintKind.CONFIDENTIAL, ConstraintKind.CONDITIONAL):
                continue
            hits = [k for k in c.keywords if k in folded]
            if not hits or len(hits) < min(2, len(c.keywords)):
                continue
            confidential = c.kind is ConstraintKind.CONFIDENTIAL
            if confidential and not shows:
                continue
            risks.append(
                ClaimRisk(
                    f"risk-{where}-{c.constraint_id}",
                    ClaimKind.CONFIDENTIAL_SHOWN if confidential else ClaimKind.CONDITIONAL_USED,
                    sentence,
                    where,
                    (
                        "Vertraulich laut Briefing: nicht zeigen, bevor es freigegeben ist."
                        if confidential
                        else "Nur nach Freigabe erlaubt."
                    ),
                    Severity.BLOCKER if confidential else Severity.WARNING,
                    (
                        "Ohne diese Seite erzählen: nur die freigegebenen Produkte zeigen."
                        if confidential
                        else "Als Zeile mit Freigabe einplanen, erst nach der Bestätigung sprechen."
                    ),
                    c.constraint_id,
                )
            )
    return tuple(dict.fromkeys(risks))


def scan_script(script: Script, ctx: ScanContext) -> tuple[ClaimRisk, ...]:
    """The claims in every sentence that will be spoken (excluded sentences are not)."""
    found: list[ClaimRisk] = []
    for scene in script.scenes:
        for sentence in scene.sentences:
            if sentence.usable:
                found.extend(
                    ClaimRisk(
                        f"risk-{scene.scene_id}-{sentence.index}-{r.kind.value}",
                        r.kind,
                        r.quote,
                        scene.scene_id,
                        r.why,
                        r.severity,
                        r.safe_alternative,
                    )
                    for r in scan_text(sentence.text, scene.scene_id, ctx)
                )
    return tuple(found)


def outdated_facts(brief: BriefModel, campaign_year: int) -> tuple[ClaimRisk, ...]:
    """Dates of the brief that sit in another year than the campaign."""
    risks: list[ClaimRisk] = []
    for fact in brief.facts:
        if fact.kind != "date" or fact.source is None:
            continue
        years = {int(part[:4]) for part in fact.value.split("/") if part[:4].isdigit()}
        if years and campaign_year not in years:
            risks.append(
                ClaimRisk(
                    f"risk-fact-{fact.key}",
                    ClaimKind.OUTDATED_FACT,
                    fact.source.quote,
                    "fact",
                    f"Das Datum liegt im Jahr {sorted(years)[0]}, "
                    f"die Kampagne läuft {campaign_year}.",
                    Severity.WARNING,
                    "Gegen die aktuelle Quelle prüfen, bevor es im Video steht.",
                )
            )
    return tuple(risks)


def quality_risks(script: Script, facts_filled: Sequence[str]) -> tuple[ClaimRisk, ...]:
    """Product talk that is only a number of shots: the video would say nothing about the effect."""
    risks: list[ClaimRisk] = []
    for scene in script.scenes:
        if scene.function != "product":
            continue
        text = " ".join(s.text for s in scene.sentences if s.usable)
        about_shots = bool(re.search(r"\bschuss\b|\bschüsse\b", text.lower()))
        says_effect = bool(
            re.search(r"effekt|farbe|kaliber|höhe|brenndauer|fontäne|bombett", text.lower())
        )
        if about_shots and not says_effect:
            risks.append(
                ClaimRisk(
                    f"risk-{scene.scene_id}-quality",
                    ClaimKind.QUALITY,
                    text[:120],
                    scene.scene_id,
                    "Es geht nur um die Schusszahl. Das sagt wenig über den Artikel.",
                    Severity.WARNING,
                    "Aufbau, Kaliber und Effekt beschreiben (Technik-Felder ausfüllen).",
                )
            )
    return tuple(risks)


def scene_text(scene: Scene) -> str:
    return " ".join(s.text for s in scene.sentences if s.usable)
