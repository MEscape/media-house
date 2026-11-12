"""What a step needs to look at in a project, computed the same way everywhere."""

from dataclasses import dataclass, replace

from media_house.modules.production_project.domain.brief import BriefModel, Fact
from media_house.modules.production_project.domain.claims import ScanContext
from media_house.modules.production_project.domain.compliance import (
    Finding,
    LintContext,
    RuleKind,
    RuleSet,
    TimelineFacts,
    apply_overrides,
    compile_rules,
    lint_script,
    qc_timeline,
)
from media_house.modules.production_project.domain.facts import ProductRow, number_of, products
from media_house.modules.production_project.domain.project import Project
from media_house.modules.production_project.domain.script import Script, parse_script
from media_house.modules.production_project.domain.script_tools import (
    DEFAULT_WPM,
    SceneLength,
    estimate_lengths,
    resolve_script,
    total_seconds,
)
from media_house.modules.production_project.domain.slots import fill_values
from media_house.modules.style_memory.application.contracts import Guidance

_COUNT_WORDS = {2: "zwei", 3: "drei", 4: "vier", 5: "fünf", 6: "sechs", 7: "sieben"}


@dataclass(frozen=True, slots=True)
class StyleFacts:
    """The slice of the creator's style the checks need."""

    wpm: float
    target_minutes: tuple[float, float]
    forbidden_techniques: tuple[str, ...]


def brief_of(project: Project) -> BriefModel | None:
    return project.brief


def rules_of(project: Project) -> RuleSet:
    return compile_rules(project.brief) if project.brief else RuleSet(())


def timeline_findings(
    project: Project, facts: TimelineFacts, guidance: Guidance
) -> tuple[Finding, ...]:
    """The timeline against the brief and the style (one place, so the gate and the context
    experiment judge a timeline the same way)."""
    brief = project.brief
    if brief is None:
        return ()
    flagged = (
        {e.asset_id or e.file for e in project.manifest.flagged()} if project.manifest else set()
    )
    profile = guidance.profile
    return qc_timeline(
        facts,
        rules_of(project),
        allowed_transitions=profile.allowed_transitions,
        forbidden_transitions=profile.forbidden_transitions,
        overlay_limit=profile.overlay_limit(project.settings.format),
        flagged_assets=flagged,
        forbidden_words=(),
        brief=brief,
    )


def rows_of(project: Project) -> tuple[ProductRow, ...]:
    return products(project.facts)


def scan_context(project: Project, style: StyleFacts) -> ScanContext:
    rules = rules_of(project)
    rows = rows_of(project)
    return ScanContext(
        client=project.settings.client,
        product_names=tuple(r.name for r in rows),
        scope_word=_COUNT_WORDS.get(len(rows), ""),
        extra_competitors=frozenset(c.casefold() for c in project.settings.competitors),
        forbidden_techniques=style.forbidden_techniques,
        no_comparison=rules.has(RuleKind.COMPETITOR) or not rules.rules,
        no_negative=rules.has(RuleKind.NEGATIVE) or not rules.rules,
        no_third_party_brands=rules.has(RuleKind.THIRD_PARTY_BRAND) or not rules.rules,
    )


def parsed_script(project: Project) -> Script:
    return parse_script(project.script) if project.script else Script(())


def spoken_script(project: Project) -> Script:
    """The script as it will be spoken: slots and facts put in, approved gates opened."""
    return resolve_script(
        parsed_script(project),
        slots=fill_values(project.slots),
        facts=project.facts,
        approvals=project.approvals,
    )


def lengths_of(project: Project, style: StyleFacts) -> tuple[SceneLength, ...]:
    return estimate_lengths(
        spoken_script(project), wpm=style.wpm or DEFAULT_WPM, off_blocks=project.off_blocks
    )


@dataclass(frozen=True, slots=True)
class ScriptReport:
    findings: tuple[Finding, ...]
    lengths: tuple[SceneLength, ...]
    seconds: float


def script_report(project: Project, style: StyleFacts) -> ScriptReport:
    """The live lint of a script and its length estimate."""
    facts = {fact.key: fact for fact in project.facts}
    low_fact, high_fact = facts.get("target_minutes_min"), facts.get("target_minutes_max")
    if low_fact is not None and high_fact is not None:
        low, high = number_of(low_fact), number_of(high_fact)
        if low is not None and high is not None and 0 < low <= high:
            style = replace(style, target_minutes=(float(low), float(high)))
    spoken = spoken_script(project)
    lengths = lengths_of(project, style)
    seconds = total_seconds(lengths)
    ctx = LintContext(
        rules_of(project),
        project.facts,
        scan_context(project, style),
        wpm=style.wpm or DEFAULT_WPM,
        target_minutes=style.target_minutes,
    )
    found = lint_script(spoken, ctx, estimated_seconds=seconds)
    return ScriptReport(apply_overrides(found, project.overrides), lengths, seconds)


def fact_map(facts: tuple[Fact, ...]) -> dict[str, Fact]:
    return {f.key: f for f in facts}


def style_facts_of(guidance: Guidance) -> StyleFacts:
    """The pace, target length and forbidden devices of the creator's style for this format."""
    profile = guidance.profile
    norm = profile.pacing_of(guidance.format)
    fmt = profile.format(guidance.format)
    return StyleFacts(
        wpm=norm.words_per_minute if norm else DEFAULT_WPM,
        target_minutes=(fmt.target_minutes_min, fmt.target_minutes_max) if fmt else (0.0, 0.0),
        forbidden_techniques=profile.forbidden_transitions,
    )
