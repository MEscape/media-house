"""Step 1 and 2 of the wizard: the brief, its Brief Model, the risk and conflict panel."""

import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from media_house.core.application.ports import Clock
from media_house.modules.content_intelligence.application.contracts import TextGenerator
from media_house.modules.production_project.application.ports import (
    BriefReader,
    DocumentExporter,
)
from media_house.modules.production_project.application.projects import ProjectCatalog
from media_house.modules.production_project.application.structured import (
    check_allowed,
    generate_structured,
)
from media_house.modules.production_project.application.views import (
    parsed_script,
    scan_context,
    style_facts_of,
)
from media_house.modules.production_project.domain.brief import BriefModel, Constraint, Source
from media_house.modules.production_project.domain.brief_parser import parse_brief, quote_in_brief
from media_house.modules.production_project.domain.claims import (
    ClaimRisk,
    RiskAction,
    RiskDecision,
    outdated_facts,
    quality_risks,
    scan_plan,
    scan_script,
)
from media_house.modules.production_project.domain.compliance import Override
from media_house.modules.production_project.domain.documents import (
    Document,
    kurzkonzept,
    rueckfragen,
)
from media_house.modules.production_project.domain.facts import (
    derive,
    products,
    spec_ratio_formulas,
    standard_formulas,
)
from media_house.modules.production_project.domain.numbers import fold
from media_house.modules.production_project.domain.project import (
    Generated,
    Project,
    stamp,
    touch,
    with_script,
)
from media_house.modules.production_project.domain.prompts import (
    PROMPT_VERSION,
    LlmBriefItems,
    brief_items_prompt,
)
from media_house.modules.production_project.domain.script_tools import estimate_lengths
from media_house.modules.production_project.domain.slots import standard_slots
from media_house.modules.production_project.domain.values import Artifact, ConstraintKind
from media_house.modules.style_memory.application.contracts import (
    Category,
    EventKind,
    GuidanceQuery,
    PreferenceEvent,
    PreferenceRecorder,
    Scope,
    StyleGuidance,
    Task,
)
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import (
    ConflictError,
    Err,
    NotFoundError,
    Ok,
    Result,
    ValidationError,
)
from media_house.shared.types import new_id

_KIND = {k.value: k for k in ConstraintKind}


@dataclass(frozen=True, slots=True)
class ExtractReport:
    project: Project
    #: What a model added on top of the deterministic reading (grounded items only).
    model_items: int
    #: Why a model was not used ("" = used, or none asked for).
    model_note: str


class ReadBrief:
    """Turn a brief document into text."""

    def __init__(self, reader: BriefReader) -> None:
        self._reader = reader

    def execute(self, path: Path) -> Result[str, ValidationError]:
        return self._reader.read(path)


class ExtractBrief:
    """Read the brief into the Brief Model: deterministic first, a model may add grounded items."""

    def __init__(
        self,
        projects: ProjectCatalog,
        style: StyleGuidance,
        clock: Clock,
        generator: TextGenerator | None,
    ) -> None:
        self._projects = projects
        self._style = style
        self._clock = clock
        self._generator = generator

    def execute(
        self,
        project_id: str,
        text: str,
        *,
        ideas: str = "",
        links: tuple[str, ...] = (),
        use_model: bool = False,
        cancellation: CancellationToken | None = None,
    ) -> Result[ExtractReport, NotFoundError | ConflictError | ValidationError]:
        if not text.strip():
            return Err(
                ValidationError("empty brief", field="text", user_message="Das Briefing ist leer.")
            )
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        settings = found.value.settings
        brief = parse_brief(text, reference_year=settings.year, client_hint=settings.client)
        added, note = 0, ""
        generator = "deterministic"
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if use_model and self._generator is not None and cancellation is not None:
            allowed = check_allowed(settings, self._generator)
            if isinstance(allowed, Err):
                note = (
                    allowed.error.user_message
                    if isinstance(allowed.error, ConflictError)
                    else allowed.error.reason
                )
            else:
                system, user = brief_items_prompt(text)
                answer = generate_structured(
                    self._generator, system, user, LlmBriefItems, cancellation
                )
                if isinstance(answer, Err):
                    note = answer.error.reason
                else:
                    brief, added = _merge_model_items(brief, answer.value, text)
                    generator = self._generator.identity()
        guidance = self._style.guidance(GuidanceQuery(Task.CONTEXT, settings.format))
        facts = derive(
            brief.facts, (*standard_formulas(brief.facts), *spec_ratio_formulas(brief.facts))
        )
        now = self._clock.now()
        record = Generated(
            Artifact.BRIEF,
            generator,
            PROMPT_VERSION,
            brief.source_digest,
            now,
            guidance=guidance.fingerprint,
        )

        def apply(p: Project) -> Project:
            changed = replace(
                p,
                brief_text=text,
                brief=brief,
                ideas=ideas,
                reference_links=links,
                facts=facts,
                slots=standard_slots(products(facts), facts),
                updated_at=now,
            )
            return stamp(touch(changed, Artifact.BRIEF, now=now), record)

        saved = self._projects.update(
            project_id, apply, audit=("extract_brief", brief.source_digest)
        )
        if isinstance(saved, Err):
            return saved
        return Ok(ExtractReport(saved.value, added, note))


def _merge_model_items(
    brief: BriefModel, items: LlmBriefItems, text: str
) -> tuple[BriefModel, int]:
    """Add what a model found, but only items whose quote really stands in the brief and that
    the deterministic reading did not already have."""
    known = {fold(c.text) for c in brief.constraints}
    added: list[Constraint] = []
    for item in items.constraints:
        if re.search(r"thumbnail|vorschaubild|07_thumb", item.text + " " + item.quote, re.I):
            continue
        kind = _KIND.get(item.kind.casefold())
        if kind is None or not quote_in_brief(item.quote, text) or fold(item.quote) in known:
            continue
        if any(fold(item.quote) in k or k in fold(item.quote) for k in known):
            continue
        known.add(fold(item.quote))
        added.append(
            Constraint(
                f"c{len(brief.constraints) + len(added) + 1:02d}",
                kind,
                item.text.strip() or item.quote,
                "",
                Source("Modell", item.quote.strip()),
            )
        )
    return replace(brief, constraints=(*brief.constraints, *added)), len(added)


class RunRiskPanel:
    """Step 2: what the brief forbids or cannot support, found in the ideas, concepts and script
    before anything is recorded. Decisions already taken are kept."""

    def __init__(self, projects: ProjectCatalog, style: StyleGuidance, clock: Clock) -> None:
        self._projects = projects
        self._style = style
        self._clock = clock

    def execute(self, project_id: str) -> Result[Project, NotFoundError | ConflictError]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if project.brief is None:
            return Ok(project)
        guidance = self._style.guidance(
            GuidanceQuery(Task.CONCEPT, project.settings.format, project_id=project_id)
        )
        style = style_facts_of(guidance)
        ctx = scan_context(project, style)
        brief = project.brief
        risks: list[ClaimRisk] = []
        risks += scan_plan(project.ideas, "idea", brief, ctx)
        for concept in project.concepts:
            text = " ".join([concept.logline, concept.hook, concept.red_thread, concept.callback])
            risks += scan_plan(text, f"concept:{concept.concept_id}", brief, ctx)
        if project.script:
            script = parsed_script(project)
            risks += scan_script(script, ctx)
            risks += quality_risks(script, ())
        risks += outdated_facts(brief, project.settings.year)
        unique = list({r.risk_id: r for r in risks}.values())
        now = self._clock.now()

        def apply(p: Project) -> Project:
            return replace(p, risks=tuple(unique), updated_at=now)

        return self._projects.update(project_id, apply)


class DecideRisk:
    """Replace the wording with the safe alternative, or keep it on purpose (with a reason that
    is logged, valid for this project, and optionally remembered for the client)."""

    def __init__(
        self,
        projects: ProjectCatalog,
        recorder: PreferenceRecorder,
        clock: Clock,
    ) -> None:
        self._projects = projects
        self._recorder = recorder
        self._clock = clock

    def execute(
        self,
        project_id: str,
        risk_id: str,
        action: RiskAction,
        *,
        reason: str = "",
        replacement: str = "",
        remember_for_client: bool = False,
    ) -> Result[Project, NotFoundError | ConflictError | ValidationError]:
        if action is RiskAction.KEPT and not reason.strip():
            return Err(
                ValidationError(
                    "keeping a risk needs a reason",
                    field="reason",
                    user_message="Wenn du es bewusst behältst, schreib kurz warum.",
                )
            )
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        risk = next((r for r in found.value.risks if r.risk_id == risk_id), None)
        if risk is None:
            return Err(
                NotFoundError(f"No risk {risk_id}", user_message="Dieses Risiko gibt es nicht.")
            )
        now = self._clock.now()
        decision = RiskDecision(
            risk_id,
            action,
            reason.strip(),
            now,
            replacement.strip() or risk.safe_alternative if action is RiskAction.REPLACED else "",
            remember_for_client,
        )

        def apply(p: Project) -> Project:
            others = tuple(d for d in p.decisions if d.risk_id != risk_id)
            changed = replace(p, decisions=(*others, decision), updated_at=now)
            if action is RiskAction.REPLACED:
                if risk.where == "idea":
                    changed = touch(
                        replace(
                            changed,
                            ideas=_replace_risky_sentence(
                                p.ideas, risk.quote, decision.replacement
                            ),
                        ),
                        Artifact.CONCEPTS,
                        now=now,
                    )
                elif risk.where.startswith("concept:"):
                    cid = risk.where.removeprefix("concept:")
                    concepts = tuple(
                        replace(
                            c,
                            logline=_replace_risky_sentence(
                                c.logline, risk.quote, decision.replacement
                            ),
                            hook=_replace_risky_sentence(c.hook, risk.quote, decision.replacement),
                            red_thread=_replace_risky_sentence(
                                c.red_thread, risk.quote, decision.replacement
                            ),
                            callback=_replace_risky_sentence(
                                c.callback, risk.quote, decision.replacement
                            ),
                        )
                        if c.concept_id == cid
                        else c
                        for c in p.concepts
                    )
                    changed = touch(replace(changed, concepts=concepts), Artifact.CONCEPTS, now=now)
                elif risk.where.startswith("S") and risk.quote in p.script:
                    pattern = rf"(^## {re.escape(risk.where)}\b.*?)(?=^## S\d+\b|^---|\Z)"
                    text = re.sub(
                        pattern,
                        lambda match: _replace_risky_sentence(
                            match.group(0), risk.quote, decision.replacement
                        ),
                        p.script,
                        flags=re.M | re.S,
                    )
                    changed = with_script(changed, text, "Sichere Alternative übernommen", now=now)
            elif risk.where.startswith("S"):
                quoted = fold(risk.quote)[:40].replace(" ", "-")
                key = f"claim.{risk.kind.value}:{risk.where}:{quoted}"
                override = Override(key, reason.strip(), now, remember_for_client)
                changed = replace(changed, overrides=(*p.overrides, override))
            return changed

        saved = self._projects.update(
            project_id, apply, audit=("risk_" + action.value, f"{risk.kind.value}: {reason[:80]}")
        )
        if isinstance(saved, Ok):
            self._remember(saved.value, risk, decision)
        return saved

    def _remember(self, project: Project, risk: ClaimRisk, decision: RiskDecision) -> None:
        kept = decision.action is RiskAction.KEPT
        self._recorder.record(
            PreferenceEvent(
                event_id=f"risk-{project.project_id[:8]}-{risk.risk_id}-{new_id()[:6]}",
                kind=EventKind.CONFLICT_OVERRIDE if kept else EventKind.SUGGESTION_ACCEPTED,
                at=decision.at,
                project_id=project.project_id,
                scene_id=risk.where,
                rule_id=risk.constraint_id,
                format=project.settings.format,
                client=project.settings.client,
                category=Category.NEVER,
                signal=f"risk_{risk.kind.value}"
                if decision.remember_for_client or not kept
                else "",
                note=decision.reason or risk.safe_alternative,
                before=risk.quote,
                after=decision.replacement,
                scope_hint=Scope.CLIENT if decision.remember_for_client else Scope.PROJECT,
            )
        )


def _replace_risky_sentence(text: str, quote: str, alternative: str) -> str:
    """Replace the reviewed sentence, retaining surrounding paragraphs and sentences."""
    parts = re.split(r"((?<=[.!?])\s+|\n+)", text)
    return "".join(alternative if quote in part else part for part in parts)


@dataclass(frozen=True, slots=True)
class BriefDocuments:
    kurzkonzept: Document
    rueckfragen: Document


class BuildBriefDocuments:
    """Kurzkonzept and Rückfragen-Liste for the client (export to Word and PDF)."""

    def __init__(
        self,
        projects: ProjectCatalog,
        style: StyleGuidance,
        clock: Clock,
        exporter: DocumentExporter,
    ) -> None:
        self._projects = projects
        self._style = style
        self._clock = clock
        self._exporter = exporter

    def execute(self, project_id: str) -> Result[BriefDocuments, NotFoundError | ValidationError]:
        found = self._projects.get(project_id)
        if isinstance(found, Err):
            return found
        project = found.value
        if project.brief is None:
            return Err(ValidationError("no brief", user_message="Lies zuerst ein Briefing ein."))
        guidance = self._style.guidance(
            GuidanceQuery(Task.CONCEPT, project.settings.format, project_id=project_id)
        )
        style = style_facts_of(guidance)
        script = parsed_script(project) if project.script else None
        lengths = estimate_lengths(script, wpm=style.wpm) if script else ()
        today = _today(self._clock.now().date())
        return Ok(
            BriefDocuments(
                kurzkonzept(project.brief, project.concept(), script, lengths, today),
                rueckfragen(project.brief),
            )
        )

    def export(
        self, project_id: str, target: Path, formats: tuple[str, ...] = ("docx", "pdf")
    ) -> Result[tuple[Path, ...], NotFoundError | ValidationError]:
        built = self.execute(project_id)
        if isinstance(built, Err):
            return built
        paths: list[Path] = []
        for name, doc in (
            ("kurzkonzept", built.value.kurzkonzept),
            ("rueckfragen", built.value.rueckfragen),
        ):
            for fmt in formats:
                paths.append(self._exporter.export(doc, target / f"{name}.{fmt}", fmt))
        return Ok(tuple(paths))


def _today(value: date) -> date:
    return value
